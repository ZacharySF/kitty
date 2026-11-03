"""Deterministic local backlinks between schema-map nodes and other local records.

Matching is token based: labels and record text are casefolded, split on
non-alphanumeric boundaries and lightly singularized, so ``Neural network``
matches ``neural-networks`` but never ``unneural``. ``[[Wikilink]]`` text naming a
node label is an explicit link and ranks first. Candidates come from the full-text
index (kitty.core.search): a phrase query finds every record that can contain the
label, over all records (no per-source, per-record or total caps), and the token
matcher above confirms each one in recency order. Only the number of references a
node or term shows is bounded (``PER_NODE``, ``PER_TERM``), and ``truncated`` says
when more exist. Snippets redact links, e-mail addresses, file paths and long
token-like strings. Nothing leaves the machine and no AI is involved; source records
are only read.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid

from kitty.core import notes, schema_maps, search
from kitty.db.connection import transaction

PER_NODE = 12
PER_TERM = 24
#: Related records: the most distinctive terms of the text that are searched, and the group size.
RELATED_TERMS = 24
RELATED_LIMIT = 12
#: Notebook graph edges (page_connections) compare at most this much text per shown page.
RECORD_CHARS = 8000
TOTAL_CHARS = 4_000_000
SNIPPET = 140
VIEWS = {'note_page': 'notes', 'study_subject': 'study', 'study_project': 'study', 'study_task': 'study',
         'calendar_event': 'calendar', 'calendar_task': 'calendar', 'job': 'jobs', 'flashcard_deck': 'flashcards'}
NAMES = {'note_page': 'note page', 'study_subject': 'study subject', 'study_project': 'study project',
         'study_task': 'study task', 'calendar_event': 'calendar event', 'calendar_task': 'calendar task',
         'job': 'job', 'flashcard_deck': 'flashcard deck'}
ORDER = list(VIEWS)
EDGE_LABEL = 'mentioned in'
STOPWORDS = frozenset('''a about above after again against all am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from further had has
have having he her here hers him his how i if in into is it its itself just me more most my no nor not now of
off on once only or other our ours out over own same she should so some such than that the their them then
there these they this those through to too under until up very was we were what when where which while who
whom why will with would you your yours new note notes page untitled todo task tasks thing things stuff idea
ideas topic topics'''.split())
TOKEN = re.compile(r'[^\W_]+')
WIKILINK = re.compile(r'\[\[([^\[\]\n|]{1,200})(?:\|[^\[\]\n]{0,200})?\]\]')
SPACE = re.compile(r'\s')
REDACT = (
    (re.compile(r'\b(?:https?|ftp|file)://\S+', re.I), '[link]'),
    (re.compile(r'\bwww\.\S+', re.I), '[link]'),
    (re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+'), '[email]'),
    (re.compile(r'(?:\b[A-Za-z]:)?(?:[\\/][\w.~-]+){2,}[\\/]?|~[\\/][\w.~/-]+'), '[path]'),
    (re.compile(r'\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b'), '[redacted]'),
)


def _singular(token):
    if len(token) > 4 and token.endswith('ies'):
        return token[:-3] + 'y'
    if len(token) > 4 and token.endswith(('ses', 'xes', 'zes', 'ches', 'shes')):
        return token[:-2]
    if len(token) > 3 and token.endswith('s') and not token.endswith(('ss', 'us', 'is')):
        return token[:-1]
    return token


def phrase(label):
    """Normalized token tuple for a label, or None when it is too short or only stopwords."""
    if not isinstance(label, str):
        return None
    tokens = TOKEN.findall(label.casefold())
    if len(''.join(tokens)) < 3 or all(token in STOPWORDS for token in tokens):
        return None
    return tuple(_singular(token) for token in tokens[:12])


def _tokens(text):
    return [(_singular(match.group().casefold()), match.start(), match.end()) for match in TOKEN.finditer(text)]


def redact(value):
    """Replace links, e-mail addresses, file paths and long token-like strings."""
    for pattern, replacement in REDACT:
        value = pattern.sub(replacement, value)
    return value


def snippet(text, start, end):
    """About SNIPPET characters of whitespace-collapsed context with sensitive spans redacted."""
    pad = max(20, (SNIPPET - (end - start)) // 2)
    left, right = max(0, start - pad), min(len(text), end + pad)
    if left:
        space = SPACE.search(text, left, start)
        left = space.end() if space else left
    if right < len(text):
        spaces = [match.start() for match in SPACE.finditer(text, end, right)]
        right = spaces[-1] if spaces else right
    value = ' '.join(redact(text[left:right]).split())
    return ('…' if left else '') + value + ('…' if right < len(text) else '')


def _exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _page_rows(conn, sql, params=()):
    """Note page rows with the text of their text/sticky elements and their outline."""
    rows = conn.execute(sql, params).fetchall()
    texts = notes.page_texts(conn, [row['block_id'] for row in rows])
    return [(row, [text for is_text, text in texts[row['block_id']][0] if is_text], texts[row['block_id']][1]) for row in rows]


def _stem(token):
    """An index prefix every raw spelling with this singular form starts with ("study" -> "stud", for "studies")."""
    return token[:-1] if len(token) >= 3 and token.endswith('y') else token


def phrase_query(tokens, column=None):
    """FTS5 phrase matching every spelling `_singular` folds onto `tokens` (a superset; the matcher confirms)."""
    expression = ' + '.join(f'"{_stem(token)}"*' for token in tokens)
    return f'{{{column}}} : ({expression})' if column else expression


_CANDIDATES = '''SELECT d.kind,d.ref,d.page_id,d.title,d.body,d.label{extra} FROM search_fts
    JOIN search_docs d ON d.id=search_fts.rowid {join} WHERE search_fts MATCH ? AND {where} ORDER BY {order}'''
#: Per backlink kind, in scan order: (FTS column or None, extra columns, join, where, recency order).
_KIND_SQL = {
    'note_page': [(None, ",CAST(json_extract(r.properties,'$.number') AS INTEGER) AS number,r.text AS root_title",
                   'JOIN blocks r ON r.id=d.page_id', "d.kind='block' AND r.type='page' AND r.parent_id IS NULL",
                   'r.updated_at DESC,number DESC,d.id')],
    'study_subject': [(None, '', '', "d.kind='study_subject'", 'CAST(d.ref AS INTEGER) DESC')],
    'study_project': [(None, '', '', "d.kind='study_project'", 'CAST(d.ref AS INTEGER) DESC')],
    'study_task': [(None, '', '', "d.kind='study_task'", 'CAST(d.ref AS INTEGER) DESC')],
    'calendar_event': [(None, '', 'JOIN calendar_events e ON e.id=d.ref', "d.kind='calendar_event'", 'e.updated_at DESC,e.id')],
    'calendar_task': [(None, '', 'JOIN calendar_tasks t ON t.id=d.ref', "d.kind='calendar_task'", 't.updated_at DESC,t.id')],
    # Card fronts link back to their deck; deck review stays in Flashcards.
    'flashcard_deck': [(None, '', '', "d.kind='flashcard_deck'", 'CAST(d.ref AS INTEGER) DESC'),
                       ('title', ',fd.name AS deck', 'JOIN flash_notes n ON n.id=d.ref JOIN flash_decks fd ON fd.id=n.deck_id',
                        "d.kind='flashcard_note'", 'n.updated_at DESC,n.id DESC')],
    'job': [(None, '', '', "d.kind='job'", 'CAST(d.ref AS INTEGER) DESC')],
}


def _record(kind, row):
    """``(id, title, text)`` of the record a candidate document belongs to."""
    if kind == 'note_page':
        return row['number'], row['root_title'], row['title'] or row['body']
    if row['kind'] == 'flashcard_note':
        return int(row['page_id']), row['deck'], row['title']
    ident = row['ref'] if kind.startswith('calendar_') else int(row['ref'])
    return ident, row['label'] if kind == 'job' else row['title'], f"{row['title']}\n{row['body']}" if row['body'] else row['title']


def _candidates(conn, kind, tokens, applyai):
    """Records of one kind that may contain `tokens`, newest first: ``(id, title, [text, ...])``."""
    current, texts = None, []
    for column, extra, join, where, order in _KIND_SQL[kind]:
        if kind == 'calendar_event' and not applyai:
            where += f' AND d.flags & {search.APPLYAI} = 0'
        sql = _CANDIDATES.format(extra=extra, join=join, where=where, order=order)
        for row in conn.execute(sql, (phrase_query(tokens, column),)):
            ident, title, text = _record(kind, row)
            if current is not None and current[0] != ident:
                yield (*current, texts)
                texts = []
            current = (ident, title)
            texts.append(text)
    if current is not None:
        yield (*current, texts)


def _kinds(applyai):
    return [kind for kind in ORDER if applyai or kind != 'job']


#: Per backlink kind: the documents it counts (a note page counts once, by its title document).
_COUNT_SQL = {kind: (sql[0][2], sql[0][3]) for kind, sql in _KIND_SQL.items()}
_COUNT_SQL['note_page'] = ('JOIN blocks r ON r.id=d.page_id', "d.kind='block' AND r.type='page' AND d.ref=d.page_id")
_COUNT_SQL['flashcard_deck'] = ('', "d.kind IN ('flashcard_deck','flashcard_note')")


def indexed(conn, applyai=True):
    """How many records of each kind the index holds: every one of them is searched."""
    counts = {}
    for kind in _kinds(applyai):
        join, where = _COUNT_SQL[kind]
        if kind == 'calendar_event' and not applyai:
            where += f' AND d.flags & {search.APPLYAI} = 0'
        counts[kind] = conn.execute(f'SELECT COUNT(*) FROM search_docs d {join} WHERE {where}').fetchone()[0]
    return {kind: count for kind, count in counts.items() if count}


def _match(text, wanted):
    """``(start, end)`` of the first token-phrase match of `wanted` in `text`, or None."""
    tokens = _tokens(text)
    for position, (token, start, _) in enumerate(tokens):
        if token == wanted[0] and tuple(item[0] for item in tokens[position:position + len(wanted)]) == wanted:
            return start, tokens[position + len(wanted) - 1][2]
    return None


def _find(conn, wanted, per_term, applyai, exclude=None):
    """At most `per_term` confirmed references per kind, newest first, and whether more exist."""
    references, more = [], False
    for kind in _kinds(applyai):
        count, seen = 0, set()
        for record_id, title, texts in _candidates(conn, kind, wanted, applyai):
            if (kind, str(record_id)) == exclude or record_id in seen:
                continue
            hit = next(((text, span) for text in texts if (span := _match(text, wanted))), None)
            if hit is None:
                continue
            if count == per_term:
                more = True
                break
            count += 1
            seen.add(record_id)
            explicit = any(phrase(link.group(1)) == wanted for text in texts for link in WIKILINK.finditer(text))
            text, (start, end) = hit
            references.append({'kind': kind, 'id': record_id, 'title': (title or '')[:200],
                               'snippet': snippet(text, start, end), 'view': VIEWS[kind],
                               'match': 'wikilink' if explicit else 'mention'})
    return references, more


def _scan(conn, terms, per_term=PER_NODE, *, applyai=True, exclude=None):
    if not terms:
        return {}, {}, {}
    search.sync(conn)
    found, truncated = {}, {}
    for key, tokens in terms.items():
        references, more = _find(conn, tokens, per_term, applyai, exclude)
        ranked = _rank(references, per_term)
        found[key], truncated[key] = ranked, more or len(ranked) < len(references)
    return found, indexed(conn, applyai), truncated


def scan(conn, terms, per_term=PER_NODE, *, applyai=True):
    """Match each ``key -> phrase`` against every indexed local record.

    Returns ``({key: [reference, ...]}, indexed)``: at most `per_term` references per
    key, explicit wikilinks first, then kinds interleaved in a stable order (notes,
    study, calendar, flashcards, jobs) and recency within a kind; ``indexed`` counts the
    records of each kind that were searched (all of them).
    """
    found, counts, _ = _scan(conn, terms, per_term, applyai=applyai)
    return found, counts


def _rank(references, limit):
    explicit = [item for item in references if item['match'] == 'wikilink']
    queues = [[item for item in references if item['match'] == 'mention' and item['kind'] == kind] for kind in ORDER]
    ordered = explicit[:limit]
    while len(ordered) < limit and any(queues):
        for queue in queues:
            if queue and len(ordered) < limit:
                ordered.append(queue.pop(0))
    return ordered


def target_id(kind, record_id):
    """Stable graph ID for a linked reference node."""
    raw = f'{kind}-{record_id}'
    if re.fullmatch(r'[A-Za-z0-9_-]{1,70}', raw):
        return f'ref-{raw}'
    return f'ref-{kind}-{hashlib.sha1(str(record_id).encode()).hexdigest()[:20]}'


def _targets(document):
    """Map a reference identity to an existing node: sourced job/subject nodes or ref-* nodes."""
    sourced = {}
    for node in document['nodes']:
        source = node.get('source')
        if source and source['kind'] == 'job':
            sourced[('job', source['id'])] = node['id']
        elif source and source['kind'] == 'subject':
            sourced[('study_subject', source['id'])] = node['id']
    return sourced


def _terms(document):
    return {node['id']: tokens for node in document['nodes']
            if not node['id'].startswith('ref-') and (tokens := phrase(node['label']))}


def _connected(document):
    return {(edge['from'], edge['to']) for edge in document['edges']} | {(edge['to'], edge['from']) for edge in document['edges']}


def _suggest(conn, document, applyai=True):
    found, scanned, truncated = _scan(conn, _terms(document), applyai=applyai)
    sourced, ids, connected = _targets(document), {node['id'] for node in document['nodes']}, _connected(document)
    result = {}
    for node_id, references in found.items():
        kept = []
        for item in references:
            target = sourced.get((item['kind'], item['id'])) or target_id(item['kind'], item['id'])
            if target == node_id:
                continue
            kept.append({**item, 'linked': target in ids and (node_id, target) in connected})
        result[node_id] = kept
    return result, scanned, truncated


def suggestions(conn, map_id, *, applyai=True):
    row = schema_maps._row(conn, map_id)
    document = json.loads(row['graph_json'])
    found, scanned, truncated = _suggest(conn, document, applyai)
    return {'map_id': row['id'], 'revision': row['revision'],
            'suggestions': [{'node_id': node['id'], 'label': node['label'], 'references': found[node['id']],
                             'truncated': truncated[node['id']]}
                            for node in document['nodes'] if found.get(node['id'])],
            'scanned': scanned}


def mentions(conn, term, exclude=None, *, applyai=True):
    """Linked mentions of an arbitrary term, e.g. a note page title or study subject."""
    term = schema_maps._text(term, 'Term', 200)
    tokens = phrase(term)
    if exclude is not None:
        kind, _, record_id = exclude.partition(':')
        if kind not in VIEWS or not record_id:
            raise ValueError('Exclude must look like <kind>:<id>')
        exclude = (kind, record_id)
    found, scanned, truncated = _scan(conn, {'term': tokens} if tokens else {}, PER_TERM, applyai=applyai, exclude=exclude)
    return {'term': term, 'references': found.get('term', []), 'truncated': truncated.get('term', False), 'scanned': scanned}


_RELATED_KINDS = "(d.kind='flashcard_note' OR (d.kind='block' AND r.type='page'))"
_RELATED_SQL = '''SELECT d.kind,d.ref,d.page_id,d.title,d.body,{rank} AS score FROM search_fts
    JOIN search_docs d ON d.id=search_fts.rowid LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id
    WHERE search_fts MATCH ? AND ''' + _RELATED_KINDS


def _related_key(row):
    return ('note_page', row['page_id']) if row['kind'] == 'block' else ('flashcard_note', row['ref'])


def _related_records(conn, keys):
    """``{key: (id, title, content)}``: a page is its title and every text block; a card all its fields."""
    pages = [ref for kind, ref in keys if kind == 'note_page']
    cards = [ref for kind, ref in keys if kind == 'flashcard_note']
    found = {}
    for start in range(0, len(pages), 500):
        chunk = pages[start:start + 500]
        marks = ','.join('?' * len(chunk))
        bodies = {}
        for row in conn.execute(f"SELECT page_id,body FROM search_docs WHERE kind='block' AND page_id IN ({marks}) "
                                "AND ref<>page_id ORDER BY id", chunk):
            bodies.setdefault(row['page_id'], []).append(row['body'])
        for row in conn.execute(f"SELECT id,text,CAST(json_extract(properties,'$.number') AS INTEGER) AS number FROM blocks "
                                f"WHERE id IN ({marks})", chunk):
            found[('note_page', row['id'])] = (row['number'], row['text'], '\n'.join([row['text'], *bodies.get(row['id'], ())]))
    for start in range(0, len(cards), 500):
        chunk = cards[start:start + 500]
        for row in conn.execute(f"SELECT ref,title,body,label FROM search_docs WHERE kind='flashcard_note' "
                                f"AND ref IN ({','.join('?' * len(chunk))})", chunk):
            content = '\n'.join(part for part in (row['title'], row['body']) if part)
            found[('flashcard_note', row['ref'])] = (int(row['ref']), row['label'], content)
    return found


def _related_source(conn, identity):
    """The excluded record's key and title (only note pages and cards have one here)."""
    if identity is None:
        return None, ''
    kind, ident = identity
    if kind == 'note_page' and ident.isdigit():
        row = conn.execute('SELECT block_id,title FROM note_page_index WHERE id=?', (int(ident),)).fetchone()
        return (('note_page', row['block_id']), row['title']) if row else (None, '')
    if kind == 'flashcard_note':
        row = conn.execute("SELECT label FROM search_docs WHERE kind='flashcard_note' AND ref=?", (ident,)).fetchone()
        return (('flashcard_note', ident), row['label']) if row else (None, '')
    return None, ''


def _contains(haystack, needle):
    return bool(needle) and any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


def _terms_of(value):
    result = {}
    for token, _, _ in _tokens(value):
        if len(token) > 2 and token not in STOPWORDS:
            result[token] = result.get(token, 0) + 1
    return result


def related(conn, text, exclude=None):
    """Linked, mentioning and related note pages and cards, ranked with bm25 over the full index.

    *Linked*: a ``[[link]]`` either way between the text and the record; *mentions*: the
    record's title appears in the text, or the excluded source's title in the record;
    *related*: records sharing the text's most distinctive terms (tf·idf against the
    index's document frequencies), ranked by bm25 summed over each record's matching
    documents and normalised to the best one. Every note page and card is searched; each
    group shows at most ``RELATED_LIMIT``.
    """
    if not isinstance(text, str) or len(text) > 20000 or "\x00" in text:
        raise ValueError("Text must be a string of at most 20000 characters")
    identity = None
    if exclude is not None:
        if not isinstance(exclude, str) or not re.fullmatch(r"(note_page|flashcard_note|flashcard_deck|study_subject|schema_node):[^:]{1,80}", exclude):
            raise ValueError("Exclude must look like <kind>:<id>")
        identity = tuple(exclude.split(":", 1))
    search.sync(conn)
    source_key, source_title = _related_source(conn, identity)
    source_phrase = phrase(source_title)
    query = _terms_of(text)
    query_tokens = tuple(token for token, _, _ in _tokens(text))
    explicit = {target for match in WIKILINK.finditer(text) if (target := phrase(match.group(1)))}
    records = conn.execute(f"SELECT COUNT(*) FROM search_docs d LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id "
                           f"WHERE {_RELATED_KINDS}").fetchone()[0]
    spellings = {}
    for raw in TOKEN.findall(text.casefold()):
        spellings.setdefault(_singular(raw), set()).add(raw)
    raws = sorted({raw for variants in spellings.values() for raw in variants})
    frequency = {}
    for start in range(0, len(raws), 500):
        chunk = raws[start:start + 500]
        frequency.update((row[0], row[1]) for row in conn.execute(
            f"SELECT term,doc FROM search_vocab WHERE term IN ({','.join('?' * len(chunk))})", chunk))
    weight = {token: (1 + math.log(count)) * (1 + math.log((records + 2) / (max(
        (frequency.get(raw, 0) for raw in spellings.get(token, ())), default=0) + 1))) for token, count in query.items()}
    top = sorted(query, key=lambda token: (-weight[token], token))[:RELATED_TERMS]
    scores: dict = {}
    if top:
        expression = ' OR '.join(f'"{_stem(token)}"*' for token in top)
        for row in conn.execute(_RELATED_SQL.format(rank=f'bm25(search_fts,{search.TITLE_WEIGHT},1.0)'), (expression,)):
            key = _related_key(row)
            scores[key] = scores.get(key, 0.0) - row['score']
    best = max(scores.values(), default=0.0) or 1.0
    linked, mentioned = set(), set()
    # Titles the text links to or mentions, and records that link to or mention the source's title.
    title_queries = [(phrase_query(target, 'title'), target, 'link') for target in explicit]
    stems = sorted({_stem(token) for token in query_tokens if token not in STOPWORDS})
    title_queries += [('{title} : (' + ' OR '.join(f'"{stem}"*' for stem in stems[start:start + 200]) + ')', None, 'mention')
                      for start in range(0, len(stems), 200)]
    for expression, target, why in title_queries:
        for row in conn.execute(_RELATED_SQL.format(rank='0'), (expression,)):
            if row['kind'] == 'block' and row['ref'] != row['page_id']:
                continue
            title = phrase(row['title'])
            if why == 'link' and title == target:
                linked.add(_related_key(row))
            elif why == 'mention' and title and _contains(query_tokens, title):
                mentioned.add(_related_key(row))
    if source_phrase:
        for row in conn.execute(_RELATED_SQL.format(rank='0'), (phrase_query(source_phrase),)):
            content = row['title'] or row['body']
            if any(phrase(match.group(1)) == source_phrase for match in WIKILINK.finditer(content)):
                linked.add(_related_key(row))
            elif _contains(tuple(token for token, _, _ in _tokens(content)), source_phrase):
                mentioned.add(_related_key(row))
    mentioned -= linked
    result = {'linked': [], 'unlinked': [], 'related': []}
    groups = (('linked', linked), ('unlinked', mentioned),
              ('related', [key for key in sorted(scores, key=lambda key: -scores[key]) if key not in linked and key not in mentioned]))
    for bucket, keys in groups:
        keys = [key for key in keys if key != source_key]
        if bucket != 'related':
            keys.sort(key=lambda key: -scores.get(key, 0.0))
        for start in range(0, len(keys), RELATED_LIMIT * 4):
            chunk = keys[start:start + RELATED_LIMIT * 4]
            found = _related_records(conn, chunk)
            for key in chunk:
                if key not in found:
                    continue
                record_id, title, content = found[key]
                bag = _terms_of(content)
                shared = sorted(query.keys() & bag.keys(), key=lambda token: (-weight[token], token))[:8]
                if bucket == 'related' and not shared:
                    continue
                result[bucket].append({'kind': key[0], 'id': record_id, 'title': title[:200],
                                       'snippet': snippet(content, 0, min(60, len(content))),
                                       'shared_terms': shared, 'score': round(scores.get(key, 0.0) / best, 6),
                                       'view': 'notes' if key[0] == 'note_page' else 'flashcards'})
            ranked = sorted(result[bucket], key=lambda item: (-item['score'], item['title'].casefold(), str(item['id'])))
            # Scores only fall from here on, so a full group cannot change further.
            if len(ranked) >= RELATED_LIMIT and (start + len(chunk) >= len(keys) or scores.get(keys[start + len(chunk)], 0.0) / best < ranked[RELATED_LIMIT - 1]['score']):
                break
        result[bucket] = sorted(result[bucket], key=lambda item: (-item['score'], item['title'].casefold(), str(item['id'])))[:RELATED_LIMIT]
    return result


def _clamp(value):
    return max(-100000.0, min(100000.0, float(value)))


def apply(conn, body, *, applyai=True):
    """Link every reference currently suggested for the accepted nodes; revision-checked and idempotent."""
    schema_maps._fields(body, {'map_id', 'revision', 'node_ids'})
    node_ids = body.get('node_ids')
    if not isinstance(node_ids, list) or not node_ids or len(node_ids) > schema_maps.MAX_NODES:
        raise ValueError(f'Select between 1 and {schema_maps.MAX_NODES} nodes to link')
    node_ids = list(dict.fromkeys(schema_maps._key(value) for value in node_ids))
    with transaction(conn, immediate=True):
        row = schema_maps._row(conn, body.get('map_id'))
        schema_maps._revision(row, body.get('revision'))
        document = json.loads(row['graph_json'])
        nodes = {node['id']: node for node in document['nodes']}
        if any(key not in nodes for key in node_ids):
            raise ValueError('A selected node is no longer in this map')
        found, _, _ = _suggest(conn, document, applyai)
        sourced, connected, changed = _targets(document), _connected(document), False
        edge_ids = {edge['id'] for edge in document['edges']}
        for node_id in node_ids:
            origin = nodes[node_id]
            pending = [item for item in found.get(node_id, []) if not item['linked']]
            for index, item in enumerate(pending):
                target = sourced.get((item['kind'], item['id'])) or target_id(item['kind'], item['id'])
                if target not in nodes:
                    if len(document['nodes']) >= schema_maps.MAX_NODES:
                        raise ValueError(f'Linking would exceed {schema_maps.MAX_NODES} nodes; link fewer nodes or split the map')
                    angle = 2 * math.pi * index / max(len(pending), 1) - math.pi / 2
                    title = item['title'].strip() or NAMES[item['kind']]
                    nodes[target] = {'id': target, 'kind': 'topic' if item['kind'] in {'note_page', 'study_subject', 'study_project', 'flashcard_deck'} else 'idea',
                                     'label': title, 'notes': redact(f"Linked from {NAMES[item['kind']]}: {title}"),
                                     'x': _clamp(origin['x'] + 300 * math.cos(angle)), 'y': _clamp(origin['y'] + 190 * math.sin(angle)),
                                     'url': '', 'source': None}
                    document['nodes'].append(nodes[target])
                    changed = True
                if (node_id, target) not in connected:
                    if len(document['edges']) >= schema_maps.MAX_EDGES:
                        raise ValueError(f'Linking would exceed {schema_maps.MAX_EDGES} connections')
                    edge_id, attempt = '', 0
                    while not edge_id or edge_id in edge_ids:
                        edge_id = f'bl-{uuid.uuid5(uuid.NAMESPACE_URL, f"{node_id}>{target}#{attempt}").hex[:24]}'
                        attempt += 1
                    edge_ids.add(edge_id)
                    document['edges'].append({'id': edge_id, 'from': node_id, 'to': target, 'label': EDGE_LABEL})
                    connected |= {(node_id, target), (target, node_id)}
                    changed = True
        if not changed:
            return schema_maps.load(conn, row['id'])
        return schema_maps._write(conn, row, row['title'], schema_maps.graph(conn, document), json.loads(row['viewport_json']))


def page_connections(conn, page_ids):
    """Derived, read-only wiki/related edges for a bounded notebook graph."""
    if not page_ids:
        return []
    if not _exists(conn, 'blocks'):
        return []
    rows = _page_rows(conn, f"SELECT id,block_id,title FROM note_page_index WHERE id IN ({','.join('?' for _ in page_ids)})", page_ids)
    records = []
    remaining = TOTAL_CHARS
    for row, canvas, outline in rows:
        content = (row['title'] + '\n' + '\n'.join(part[:RECORD_CHARS] for part in (*canvas, *outline)))[:RECORD_CHARS]
        remaining -= len(content)
        if remaining < 0:
            break
        words = {token for token, _, _ in _tokens(content) if len(token) > 2 and token not in STOPWORDS}
        explicit = {phrase(match.group(1)) for match in WIKILINK.finditer(content)}
        records.append((row['id'], phrase(row['title']), words, explicit))
    result = []
    degree = {}
    for index, (ident, title, words, explicit) in enumerate(records):
        for other, other_title, other_words, other_explicit in records[index + 1:]:
            linked = other_title is not None and other_title in explicit or title is not None and title in other_explicit
            shared = words & other_words
            related = len(shared) >= 2 and len(shared) / max(1, min(len(words), len(other_words))) >= .25
            if not linked and (not related or degree.get(ident, 0) >= 4 or degree.get(other, 0) >= 4):
                continue
            result.append({'source': f'page:{ident}', 'target': f'page:{other}',
                           'label': 'linked note' if linked else 'related: ' + ', '.join(sorted(shared)[:3]),
                           'weight': 1 if linked else .4, 'directed': False})
            degree[ident] = degree.get(ident, 0) + 1
            degree[other] = degree.get(other, 0) + 1
    return result

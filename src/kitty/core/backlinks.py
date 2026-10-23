"""Deterministic local backlinks between schema-map nodes and other local records.

Matching is token based: labels and scanned text are casefolded, split on
non-alphanumeric boundaries and lightly singularized, so ``Neural network``
matches ``neural-networks`` but never ``unneural``. ``[[Wikilink]]`` text naming a
node label is an explicit link and ranks first. Every scan is bounded per source,
per record and in total; missing tables (older databases) are skipped. Snippets
redact links, e-mail addresses, file paths and long token-like strings. Nothing
leaves the machine and no AI is involved; source records are only read.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sqlite3
import uuid

from kitty.core import calendar, schema_maps
from kitty.db.connection import transaction

PER_NODE = 12
PER_TERM = 24
RECENT = {'note_page': 1000, 'study_subject': 500, 'study_project': 500, 'study_task': 1000,
          'calendar_event': 2000, 'calendar_task': 2000, 'flashcard_deck': 3000, 'job': 2000}
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
TAG = re.compile(r'<[^>]{0,2000}>')
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


def _clean(value):
    return html.unescape(TAG.sub(' ', value or '')) if '<' in (value or '') or '&' in (value or '') else (value or '')


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


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _notes(conn):
    for row in conn.execute('SELECT id,title,document FROM note_pages ORDER BY updated_at DESC,id DESC LIMIT ?', (RECENT['note_page'],)).fetchall():
        document = _json(row['document'], {})
        elements = document.get('elements') if isinstance(document, dict) else None
        parts = [item['text'] for item in elements if isinstance(item, dict) and item.get('kind') in {'text', 'sticky'}
                 and isinstance(item.get('text'), str)] if isinstance(elements, list) else []
        if _exists(conn, 'note_blocks'):
            parts.extend(block['text'] for block in conn.execute('SELECT text FROM note_blocks WHERE page_id=? ORDER BY ordering,id', (row['id'],)))
        yield 'note_page', row['id'], row['title'], '\n'.join([row['title'], *parts])


def _subjects(conn):
    for row in conn.execute('SELECT id,name FROM study_subjects ORDER BY id DESC LIMIT ?', (RECENT['study_subject'],)).fetchall():
        yield 'study_subject', row['id'], row['name'], row['name']


def _projects(conn):
    for row in conn.execute('SELECT id,name FROM study_projects ORDER BY id DESC LIMIT ?', (RECENT['study_project'],)).fetchall():
        yield 'study_project', row['id'], row['name'], row['name']


def _study_tasks(conn):
    for row in conn.execute('SELECT id,title,checklist_json FROM study_tasks ORDER BY id DESC LIMIT ?', (RECENT['study_task'],)).fetchall():
        items = _json(row['checklist_json'], [])
        parts = [item['text'] for item in items if isinstance(item, dict) and isinstance(item.get('text'), str)] if isinstance(items, list) else []
        yield 'study_task', row['id'], row['title'], '\n'.join([row['title'], *parts])


def _events(conn, applyai=True):
    for row in conn.execute('SELECT id,title,data,source_key FROM calendar_events WHERE deleted=0 ORDER BY updated_at DESC LIMIT ?', (RECENT['calendar_event'],)).fetchall():
        if not applyai and calendar.applyai_sourced(row['source_key']):
            continue
        data = _json(row['data'], {})
        description = data.get('description') if isinstance(data, dict) and isinstance(data.get('description'), str) else ''
        yield 'calendar_event', row['id'], row['title'], f"{row['title']}\n{description}"


def _calendar_tasks(conn):
    for row in conn.execute('SELECT id,data FROM calendar_tasks WHERE deleted=0 ORDER BY updated_at DESC LIMIT ?', (RECENT['calendar_task'],)).fetchall():
        data = _json(row['data'], {})
        if isinstance(data, dict) and isinstance(data.get('title'), str):
            description = data.get('description') if isinstance(data.get('description'), str) else ''
            yield 'calendar_task', row['id'], data['title'], f"{data['title']}\n{description}"


def _decks(conn):
    decks = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM flash_decks WHERE archived=0 ORDER BY id DESC LIMIT ?', (RECENT['flashcard_deck'],)).fetchall()}
    for deck_id, name in decks.items():
        yield 'flashcard_deck', deck_id, name, name
    if not decks or not _exists(conn, 'flash_notes') or not _exists(conn, 'flash_types'):
        return
    for row in conn.execute('''SELECT n.deck_id,n.fields,t.fields AS names FROM flash_notes n JOIN flash_types t ON t.id=n.type_id
            WHERE n.archived=0 ORDER BY n.updated_at DESC,n.id DESC LIMIT ?''', (RECENT['flashcard_deck'],)).fetchall():
        fields, names = _json(row['fields'], {}), _json(row['names'], [])
        if row['deck_id'] in decks and isinstance(fields, dict) and isinstance(names, list) and names and isinstance(fields.get(names[0]), str):
            # Card fronts link back to their deck; deck review stays in Flashcards.
            yield 'flashcard_deck', row['deck_id'], decks[row['deck_id']], _clean(fields[names[0]])


def _jobs(conn):
    if not _exists(conn, 'companies'):
        return
    for row in conn.execute('''SELECT j.id,j.title,c.display_name AS company,substr(coalesce(j.jd_text,''),1,?) AS jd FROM jobs j
            JOIN companies c ON c.id=j.company_id ORDER BY j.id DESC LIMIT ?''', (RECORD_CHARS, RECENT['job'])).fetchall():
        yield 'job', row['id'], f"{row['title']} · {row['company']}", f"{row['title']}\n{row['company']}\n{_clean(row['jd'])}"


SOURCES = (('note_pages', _notes), ('study_subjects', _subjects), ('study_projects', _projects),
           ('study_tasks', _study_tasks), ('calendar_events', _events), ('calendar_tasks', _calendar_tasks),
           ('flash_decks', _decks), ('jobs', _jobs))


def _records(conn, applyai=True):
    """Yield (kind, id, title, text) for recent local records; absent or older tables are skipped.

    Without ApplyAI, jobs and calendar copies imported from ApplyAI data are omitted.
    """
    for table, source in SOURCES:
        if table == 'jobs' and not applyai:
            continue
        if not _exists(conn, table):
            continue
        try:
            rows = list(source(conn, applyai) if table == 'calendar_events' else source(conn))
        except sqlite3.OperationalError:
            continue
        yield from rows


def scan(conn, terms, per_term=PER_NODE, *, applyai=True):
    """Match each ``key -> phrase`` against bounded local records.

    Returns ``({key: [reference, ...]}, scanned)``. References are deduplicated per
    record; explicit wikilinks rank first, then kinds are interleaved in a stable
    order (notes, study, calendar, flashcards, jobs) and recency within a kind.
    """
    if not terms:
        return {}, {}
    index = {}
    for key, tokens in terms.items():
        index.setdefault(tokens[0], []).append((key, tokens))
    found = {key: {} for key in terms}
    scanned, budget = {}, TOTAL_CHARS
    for kind, record_id, title, text in _records(conn, applyai):
        text = (text or '')[:RECORD_CHARS]
        budget -= len(text)
        if budget < 0:
            break
        scanned[kind] = scanned.get(kind, 0) + 1
        explicit = set()
        for match in WIKILINK.finditer(text):
            target = phrase(match.group(1))
            if target:
                explicit.add(target)
        tokens = _tokens(text)
        for position, (token, start, _) in enumerate(tokens):
            for key, wanted in index.get(token, ()):
                bucket = found[key]
                identity = (kind, record_id)
                if identity in bucket or len(bucket) >= per_term * len(ORDER):
                    continue
                if tuple(item[0] for item in tokens[position:position + len(wanted)]) != wanted:
                    continue
                if sum(1 for ref_kind, _ in bucket if ref_kind == kind) >= per_term:
                    continue
                end = tokens[position + len(wanted) - 1][2]
                bucket[identity] = {'kind': kind, 'id': record_id, 'title': (title or '')[:200],
                                    'snippet': snippet(text, start, end), 'view': VIEWS[kind],
                                    'match': 'wikilink' if wanted in explicit else 'mention'}
    return {key: _rank(list(bucket.values()), per_term) for key, bucket in found.items()}, scanned


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
    found, scanned = scan(conn, _terms(document), applyai=applyai)
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
    return result, scanned


def suggestions(conn, map_id, *, applyai=True):
    row = schema_maps._row(conn, map_id)
    document = json.loads(row['graph_json'])
    found, scanned = _suggest(conn, document, applyai)
    return {'map_id': row['id'], 'revision': row['revision'],
            'suggestions': [{'node_id': node['id'], 'label': node['label'], 'references': found[node['id']]}
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
    found, scanned = scan(conn, {'term': tokens} if tokens else {}, PER_TERM + (1 if exclude else 0), applyai=applyai)
    references = [item for item in found.get('term', []) if (item['kind'], str(item['id'])) != exclude][:PER_TERM]
    return {'term': term, 'references': references, 'scanned': scanned}


def related(conn, text, exclude=None):
    """Rank bounded local notes/cards using deterministic TF-IDF cosine similarity."""
    if not isinstance(text, str) or len(text) > 20000 or "\x00" in text:
        raise ValueError("Text must be a string of at most 20000 characters")
    identity = None
    if exclude is not None:
        if not isinstance(exclude, str) or not re.fullmatch(r"(note_page|flashcard_note|flashcard_deck|study_subject|schema_node):[^:]{1,80}", exclude):
            raise ValueError("Exclude must look like <kind>:<id>")
        identity = tuple(exclude.split(":", 1))
    def records():
        if _exists(conn, 'note_pages'):
            # Stream documents; do not materialize the whole canvas corpus.
            for row in conn.execute('SELECT id,title,document FROM note_pages ORDER BY updated_at DESC,id DESC LIMIT 1000'):
                document = _json(row['document'], {})
                elements = document.get('elements', []) if isinstance(document, dict) else []
                parts, remaining = [row['title']], RECORD_CHARS
                for item in elements if isinstance(elements, list) else []:
                    if isinstance(item, dict) and item.get('kind') in {'text', 'sticky'} and isinstance(item.get('text'), str):
                        part = item['text'][:remaining]
                        parts.append(part)
                        remaining -= len(part)
                        if remaining <= 0:
                            break
                if remaining > 0 and _exists(conn, 'note_blocks'):
                    for block in conn.execute('SELECT text FROM note_blocks WHERE page_id=? ORDER BY ordering,id', (row['id'],)):
                        part = block['text'][:remaining]
                        parts.append(part)
                        remaining -= len(part)
                        if remaining <= 0:
                            break
                yield 'note_page', row['id'], row['title'], '\n'.join(parts)
        if _exists(conn, 'flash_notes') and _exists(conn, 'flash_decks'):
            for row in conn.execute("""SELECT n.id,n.fields,d.name FROM flash_notes n
                    JOIN flash_decks d ON d.id=n.deck_id WHERE n.archived=0 AND d.archived=0
                    ORDER BY n.updated_at DESC,n.id DESC LIMIT 1000"""):
                fields = _json(row['fields'], {})
                parts = [_clean(value[:RECORD_CHARS]) for value in fields.values() if isinstance(value, str)] if isinstance(fields, dict) else []
                title = next((part.strip() for part in parts if part.strip()), row['name'])[:200]
                yield 'flashcard_note', row['id'], title, '\n'.join(parts)
    budget = TOTAL_CHARS
    bounded = []
    source_title = ''
    for kind, record_id, title, content in records():
        content = content[:RECORD_CHARS]
        budget -= len(content)
        if budget < 0:
            break
        if (kind, str(record_id)) == identity:
            source_title = title
            continue
        bounded.append((kind, record_id, title, content))
    def terms(value):
        result = {}
        for token, _, _ in _tokens(value):
            if len(token) > 2 and token not in STOPWORDS:
                result[token] = result.get(token, 0) + 1
        return result
    query = terms(text)
    counts = [terms(content) for _, _, _, content in bounded]
    frequencies = {}
    for bag in [query, *counts]:
        for token in bag:
            frequencies[token] = frequencies.get(token, 0) + 1
    def vector(bag):
        return {token: (1 + math.log(count)) * (1 + math.log((len(counts) + 2) / (frequencies[token] + 1)))
                for token, count in bag.items()}
    query_vector = vector(query)
    query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
    explicit = {phrase(match.group(1)) for match in WIKILINK.finditer(text)}
    source_phrase = phrase(source_title)
    query_tokens = tuple(token for token, _, _ in _tokens(text))
    def contains(haystack, needle):
        return bool(needle) and any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))
    result = {'linked': [], 'unlinked': [], 'related': []}
    for (kind, record_id, title, content), bag in zip(bounded, counts):
        target = phrase(title)
        reverse_links = {phrase(match.group(1)) for match in WIKILINK.finditer(content)}
        linked = target is not None and target in explicit or source_phrase is not None and source_phrase in reverse_links
        mention = contains(query_tokens, target) or contains(tuple(token for token, _, _ in _tokens(content)), source_phrase)
        shared = sorted(query.keys() & bag.keys(), key=lambda token: (-query_vector[token], token))[:8]
        candidate = vector(bag)
        norm = math.sqrt(sum(value * value for value in candidate.values()))
        score = sum(query_vector[token] * candidate[token] for token in query.keys() & bag.keys()) / (query_norm * norm) if query_norm and norm else 0
        bucket = 'linked' if linked else 'unlinked' if mention else 'related'
        if bucket == 'related' and not shared:
            continue
        result[bucket].append({'kind': kind, 'id': record_id, 'title': title[:200],
                              'snippet': snippet(content, 0, min(60, len(content))),
                              'shared_terms': shared, 'score': round(score, 6),
                              'view': 'notes' if kind == 'note_page' else 'flashcards'})
    for bucket in result:
        result[bucket].sort(key=lambda item: (-item['score'], item['title'].casefold(), str(item['id'])))
        result[bucket] = result[bucket][:12]
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
        found, _ = _suggest(conn, document, applyai)
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
    try:
        rows = conn.execute(f"SELECT id,title,document FROM note_pages WHERE id IN ({','.join('?' for _ in page_ids)})", page_ids)
    except sqlite3.OperationalError as exc:
        if 'no such column' in str(exc) or 'no such table' in str(exc):
            return []
        raise
    records = []
    remaining = TOTAL_CHARS
    for row in rows:
        document = _json(row['document'], {})
        parts = [item.get('text', '')[:RECORD_CHARS] for item in document.get('elements', []) if item.get('kind') in {'text', 'sticky'}]
        if _exists(conn, 'note_blocks'):
            parts.extend(block['text'][:RECORD_CHARS] for block in conn.execute('SELECT text FROM note_blocks WHERE page_id=? ORDER BY ordering,id', (row['id'],)))
        content = (row['title'] + '\n' + '\n'.join(parts))[:RECORD_CHARS]
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

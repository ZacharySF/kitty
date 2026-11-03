"""Revision-checked local outlines served from the block backend.

An outline item is a text-flow block (``convert.OUTLINE_TYPES``) under its page (or
parent item). The block editor writes outlines with transactions over the blocks
socket; whole-outline saves remain for imports and portable copies and send the
smallest block transaction that reaches the saved shape. References resolve live
blocks, never copies.
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import nullcontext

from kitty.core import notes, search
from kitty.core.blocks import REF, WIKI, convert
from kitty.db.connection import transaction

MAX_BLOCKS = 2000
MAX_MENTIONS = 200
_PROP = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')


def block_id(value):
    if not isinstance(value, str):
        raise notes.NotesError('Block ID must be a UUID')
    try:
        parsed = str(uuid.UUID(value))
    except ValueError:
        raise notes.NotesError('Block ID must be a UUID') from None
    if parsed != value:
        raise notes.NotesError('Use canonical lowercase UUID block IDs')
    return parsed


def normalize(value):
    if not isinstance(value, list) or len(value) > MAX_BLOCKS:
        raise notes.NotesError('Outline is limited to 2000 blocks')
    result, ids, total = [], set(), 0
    for item in value:
        notes.keys(item, {'id', 'page_id', 'parent_id', 'text', 'ordering', 'collapsed', 'type', 'props'}, {'id', 'parent_id', 'text', 'ordering', 'collapsed'})
        bid = block_id(item['id'])
        if bid in ids:
            raise notes.NotesError('Duplicate block ID')
        ids.add(bid)
        content = notes.text(item['text'], 20000, 'Block text')
        total += len(content.encode('utf-8'))
        if total > notes.MAX_DOCUMENT_BYTES:
            raise notes.NotesError('Outline exceeds 2 MiB')
        if type(item['collapsed']) is not bool:
            raise notes.NotesError('Collapsed must be boolean')
        kind = item.get('type', 'text')
        if kind not in convert.OUTLINE_TYPES:
            raise notes.NotesError('Outline blocks are text-flow blocks')
        props = item.get('props', {})
        if not isinstance(props, dict) or any(not isinstance(key, str) or not _PROP.fullmatch(key) or key == 'collapsed' for key in props):
            raise notes.NotesError('Block properties must be an object with simple keys')
        total += len(json.dumps(props, ensure_ascii=False).encode('utf-8'))
        if total > notes.MAX_DOCUMENT_BYTES:
            raise notes.NotesError('Outline exceeds 2 MiB')
        result.append({'id': bid, 'parent_id': block_id(item['parent_id']) if item['parent_id'] is not None else None,
                       'text': content, 'ordering': notes.integer(item['ordering'], 0, MAX_BLOCKS * 10), 'collapsed': item['collapsed'],
                       'type': kind, 'props': props})
    parents = {item['id']: item['parent_id'] for item in result}
    for item in result:
        seen, parent = {item['id']}, item['parent_id']
        while parent is not None:
            if parent not in parents or parent in seen:
                raise notes.NotesError('Block parents must be on this page and cannot form a cycle')
            seen.add(parent)
            if len(seen) > 64:
                raise notes.NotesError('Outline nesting is limited to 64 levels')
            parent = parents[parent]
    return result


def save_blocks(conn, page_id, revision, blocks):
    pid, rev = notes.integer(page_id), notes.integer(revision)
    values = normalize(blocks)
    for raw in blocks:
        if 'page_id' in raw and (type(raw['page_id']) is not int or raw['page_id'] != pid):
            raise notes.NotesError('Block belongs to another page')
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        doc = notes.page_doc(conn, pid)
        root = doc.root
        if root.properties.get('revision') != rev:
            raise notes.NotesError('Page changed in another tab; local outline was not overwritten. Reload or export your draft.', 409)
        foreign = [item['id'] for item in values if item['id'] not in doc.blocks]
        for start in range(0, len(foreign), 500):
            chunk = foreign[start:start + 500]
            if conn.execute(f"SELECT 1 FROM blocks WHERE id IN ({','.join('?' * len(chunk))}) LIMIT 1", chunk).fetchone():
                raise notes.NotesError('Block ID already belongs to another page', 409)
        if any(item['id'] in doc.blocks and doc.blocks[item['id']].type not in convert.OUTLINE_TYPES for item in values):
            raise notes.NotesError('Block ID already belongs to another page', 409)
        ops = convert.sync_tree(doc, root.id, convert.outline_specs(root.id, values), lambda item: item.type in convert.OUTLINE_TYPES)
        notes.apply(conn, [*ops, {'op': 'set', 'id': root.id, 'props': {'revision': rev + 1}}], base={root.id: rev})
    return get_blocks(conn, pid)


def _render(conn, bid, seen=(), budget=None):
    budget = [200] if budget is None else budget
    if bid in seen or len(seen) >= 16:
        return '[cyclic block reference]'
    budget[0] -= 1
    if budget[0] < 0:
        return '[reference expansion limit]'
    row = conn.execute('SELECT text FROM blocks WHERE id=? AND deleted_at IS NULL', (bid,)).fetchone()
    if row is None:
        return '[deleted or unavailable block]'
    return REF.sub(lambda match: _render(conn, (match[1] or match[2]).lower(), (*seen, bid), budget), row['text'])[:20000]


def get_blocks(conn, page_id):
    doc = notes.page_doc(conn, page_id)
    blocks = convert.outline_items(doc, doc.root.properties['number'])
    targets = {str(m[1] or m[2]).lower() for block in blocks for m in REF.finditer(block['text'])}
    references, remaining = {}, notes.MAX_DOCUMENT_BYTES
    expansion_budget = [4000]
    for bid in sorted(targets)[:MAX_BLOCKS]:
        rendered = _render(conn, bid, budget=expansion_budget) if remaining > 0 else '[page reference preview limit]'
        encoded = rendered.encode('utf-8')[:max(0, remaining)]
        references[bid] = encoded.decode('utf-8', errors='ignore') if remaining > 0 else rendered
        remaining -= len(encoded)
    counts = {row['target_id']: row['count'] for row in conn.execute(
        f'''SELECT r.target_id,COUNT(*) AS count FROM block_refs r JOIN blocks t ON t.id=r.target_id
        JOIN blocks s ON s.id=r.source_id {_OUTLINE_SOURCE}
        WHERE t.page_id=? AND t.type IN ({_OUTLINE_SQL}) AND t.deleted_at IS NULL AND r.kind='block' GROUP BY r.target_id''', (doc.page_id,))}
    return {'revision': doc.root.properties['revision'], 'blocks': blocks, 'references': references, 'citation_counts': counts}


_OUTLINE_SQL = ",".join(f"'{kind}'" for kind in sorted(convert.OUTLINE_TYPES))
#: Joins `s` (a citing block) to a live note page: only outline text cites in Notes.
_OUTLINE_SOURCE = f"JOIN blocks sp ON sp.id=s.page_id AND sp.type='page' AND sp.deleted_at IS NULL AND s.type IN ({_OUTLINE_SQL}) AND s.deleted_at IS NULL"


def contexts(conn, bid, offset=0):
    bid = block_id(bid)
    offset = notes.integer(offset, 0, notes.MAX_PAGES * MAX_BLOCKS)
    rows = conn.execute('''SELECT b.id,b.page_id,b.parent_id,substr(b.text,1,2000) AS text,b.title,substr(parent.text,1,400) AS parent_text
        FROM block_refs r JOIN note_outline b ON b.id=r.source_id LEFT JOIN blocks parent ON parent.id=b.parent_id
        WHERE r.target_id=? AND r.kind='block' ORDER BY b.updated_at DESC,b.path,b.id LIMIT 500 OFFSET ?''', (bid, offset)).fetchall()
    count = conn.execute(f"SELECT COUNT(*) FROM block_refs r JOIN blocks s ON s.id=r.source_id {_OUTLINE_SOURCE} WHERE r.target_id=? AND r.kind='block'", (bid,)).fetchone()[0]
    original = conn.execute('SELECT page_id,title FROM note_outline WHERE id=?', (bid,)).fetchone()
    return {'id': bid, 'page_id': original['page_id'] if original else None, 'title': original['title'] if original else '', 'text': _render(conn, bid), 'count': count, 'contexts': [dict(row) for row in rows], 'next_offset': offset + len(rows) if offset + len(rows) < count else None}


def topics(conn, kind, topic, offset=0):
    if kind not in {'page', 'tag'}:
        raise notes.NotesError('Unknown topic kind')
    topic = notes.text(topic, 200).strip().casefold()
    offset = notes.integer(offset, 0, notes.MAX_PAGES * MAX_BLOCKS)
    rows = conn.execute('''SELECT b.id,b.page_id,b.parent_id,substr(b.text,1,2000) AS text,b.title FROM block_topics t JOIN note_outline b ON b.id=t.block_id
        WHERE t.kind=? AND t.topic=? ORDER BY b.updated_at DESC,b.path,b.id LIMIT 500 OFFSET ?''', (kind, topic, offset)).fetchall()
    count = conn.execute(f"SELECT COUNT(*) FROM block_topics t JOIN blocks s ON s.id=t.block_id {_OUTLINE_SOURCE} WHERE t.kind=? AND t.topic=?", (kind, topic)).fetchone()[0]
    return {'kind': kind, 'topic': topic, 'blocks': [dict(row) for row in rows], 'count': count, 'next_offset': offset + len(rows) if offset + len(rows) < count else None}


def mentions(conn, page_id):
    """Blocks of this page that name another note page's title without linking it.

    Candidate titles come from the search index (every page whose title words all occur
    in this page, kitty.core.search.page_titles_within); each is then confirmed with a
    case-insensitive whole-word match outside ``[[links]]``. At most ``MAX_MENTIONS`` are
    listed, longest titles first per block; ``truncated`` says when more exist.
    """
    doc = notes.page_doc(conn, page_id)
    flow = [(block, WIKI.sub('', block.text)) for block in doc.preorder(doc.page_id, types=convert.OUTLINE_TYPES)]
    rows = search.page_titles_within(conn, {token for _, plain in flow for token in search.words(plain)}, exclude=doc.page_id)
    rows.sort(key=lambda row: (-len(row['title']), row['id']))
    patterns = [(row, re.compile(r'(?<!\w)' + re.escape(row['title']) + r'(?!\w)', re.I)) for row in rows if row['title'].strip()]
    result = []
    for block, plain in flow:
        for row, pattern in patterns:
            if pattern.search(plain):
                if len(result) == MAX_MENTIONS:
                    return {'mentions': result, 'truncated': True}
                result.append({'block_id': block.id, 'page_id': row['id'], 'title': row['title'], 'text': block.text[:400]})
    return {'mentions': result, 'truncated': False}

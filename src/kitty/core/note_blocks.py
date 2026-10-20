"""Revision-checked local outlines; references resolve authoritative rows, never copies."""
from __future__ import annotations

import json
import re
import uuid
from contextlib import nullcontext

from kitty.core import notes
from kitty.db.connection import transaction

MAX_BLOCKS = 2000
REF = re.compile(r'\(\(([0-9a-fA-F-]{36})\)\)|\{\{embed:([0-9a-fA-F-]{36})\}\}')
WIKI = re.compile(r'\[\[([^\[\]\n|]{1,200})(?:\|[^\[\]\n]{0,200})?\]\]')
TAG = re.compile(r'(?<![\w#])#([\w][\w/-]{0,99})', re.UNICODE)


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
        notes.keys(item, {'id', 'page_id', 'parent_id', 'text', 'ordering', 'collapsed'}, {'id', 'parent_id', 'text', 'ordering', 'collapsed'})
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
        result.append({'id': bid, 'parent_id': block_id(item['parent_id']) if item['parent_id'] is not None else None,
                       'text': content, 'ordering': notes.integer(item['ordering'], 0, MAX_BLOCKS * 10), 'collapsed': item['collapsed']})
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


def search_text(conn, page_id, document):
    return '\n'.join([*(item.get('text', '') for item in document['elements']),
                      *(row['text'] for row in conn.execute('SELECT text FROM note_blocks WHERE page_id=? ORDER BY ordering,id', (page_id,)))])


def save_blocks(conn, page_id, revision, blocks, title=None, document=None):
    pid, rev = notes.integer(page_id), notes.integer(revision)
    values = normalize(blocks)
    for raw in blocks:
        if 'page_id' in raw and (type(raw['page_id']) is not int or raw['page_id'] != pid):
            raise notes.NotesError('Block belongs to another page')
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        current = notes.page(conn, pid)
        if current['revision'] != rev:
            raise notes.NotesError('Page changed in another tab; local outline was not overwritten. Reload or export your draft.', 409)
        heading = current['title'] if title is None else notes.text(title, 200)
        if not heading.strip():
            raise notes.NotesError('Page title is required')
        canvas = current['document'] if document is None else notes.normalize(document, conn)
        for item in values:
            existing = conn.execute('SELECT page_id FROM note_blocks WHERE id=?', (item['id'],)).fetchone()
            if existing and existing['page_id'] != pid:
                raise notes.NotesError('Block ID already belongs to another page', 409)
        # Replace rows only after all validation; deferred parent FK allows arbitrary order.
        conn.execute('DELETE FROM note_blocks WHERE page_id=?', (pid,))
        for item in values:
            conn.execute('INSERT INTO note_blocks(id,page_id,parent_id,text,ordering,collapsed) VALUES(?,?,?,?,?,?)',
                         (item['id'], pid, item['parent_id'], item['text'], item['ordering'], item['collapsed']))
            targets = {(m[1] or m[2]).lower() for m in REF.finditer(item['text'])}
            conn.executemany('INSERT INTO note_block_refs(source_id,target_id) VALUES(?,?)', [(item['id'], target) for target in targets])
            topics = {('page', m[1].strip().casefold()) for m in WIKI.finditer(item['text'])}
            topics.update(('tag', m[1].casefold()) for m in TAG.finditer(item['text']))
            conn.executemany('INSERT INTO note_block_topics(block_id,kind,topic) VALUES(?,?,?)', [(item['id'], kind, topic) for kind, topic in topics])
        conn.execute('UPDATE note_pages SET title=?,document=?,search_text=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?',
                     (heading, json.dumps(canvas, ensure_ascii=False), search_text(conn, pid, canvas), notes.now(), pid, rev))
    return get_blocks(conn, pid)


def _render(conn, bid, seen=(), budget=None):
    budget = [200] if budget is None else budget
    if bid in seen or len(seen) >= 16:
        return '[cyclic block reference]'
    budget[0] -= 1
    if budget[0] < 0:
        return '[reference expansion limit]'
    row = conn.execute('SELECT text FROM note_blocks WHERE id=?', (bid,)).fetchone()
    if row is None:
        return '[deleted or unavailable block]'
    return REF.sub(lambda match: _render(conn, (match[1] or match[2]).lower(), (*seen, bid), budget), row['text'])[:20000]


def get_blocks(conn, page_id):
    current = notes.page(conn, page_id)
    blocks = [dict(row) for row in conn.execute('SELECT * FROM note_blocks WHERE page_id=? ORDER BY ordering,id', (page_id,))]
    for block in blocks:
        block['collapsed'] = bool(block['collapsed'])
    targets = {str(m[1] or m[2]).lower() for block in blocks for m in REF.finditer(block['text'])}
    references, remaining = {}, notes.MAX_DOCUMENT_BYTES
    expansion_budget = [4000]
    for bid in sorted(targets)[:MAX_BLOCKS]:
        rendered = _render(conn, bid, budget=expansion_budget) if remaining > 0 else '[page reference preview limit]'
        encoded = rendered.encode('utf-8')[:max(0, remaining)]
        references[bid] = encoded.decode('utf-8', errors='ignore') if remaining > 0 else rendered
        remaining -= len(encoded)
    counts = {row['target_id']: row['count'] for row in conn.execute('SELECT r.target_id,COUNT(*) AS count FROM note_block_refs r JOIN note_blocks b ON b.id=r.target_id WHERE b.page_id=? GROUP BY r.target_id', (page_id,))}
    return {'revision': current['revision'], 'blocks': blocks, 'references': references, 'citation_counts': counts}


def contexts(conn, bid, offset=0):
    bid = block_id(bid)
    offset = notes.integer(offset, 0, notes.MAX_PAGES * MAX_BLOCKS)
    rows = conn.execute('''SELECT b.id,b.page_id,b.parent_id,substr(b.text,1,2000) AS text,p.title,substr(parent.text,1,400) AS parent_text FROM note_block_refs r
        JOIN note_blocks b ON b.id=r.source_id JOIN note_pages p ON p.id=b.page_id
        LEFT JOIN note_blocks parent ON parent.id=b.parent_id WHERE r.target_id=?
        ORDER BY p.updated_at DESC,b.ordering,b.id LIMIT 500 OFFSET ?''', (bid, offset)).fetchall()
    count = conn.execute('SELECT COUNT(*) FROM note_block_refs WHERE target_id=?', (bid,)).fetchone()[0]
    original = conn.execute('SELECT b.page_id,p.title FROM note_blocks b JOIN note_pages p ON p.id=b.page_id WHERE b.id=?', (bid,)).fetchone()
    return {'id': bid, 'page_id': original['page_id'] if original else None, 'title': original['title'] if original else '', 'text': _render(conn, bid), 'count': count, 'contexts': [dict(row) for row in rows], 'next_offset': offset + len(rows) if offset + len(rows) < count else None}


def topics(conn, kind, topic, offset=0):
    if kind not in {'page', 'tag'}:
        raise notes.NotesError('Unknown topic kind')
    topic = notes.text(topic, 200).strip().casefold()
    offset = notes.integer(offset, 0, notes.MAX_PAGES * MAX_BLOCKS)
    rows = conn.execute('''SELECT b.id,b.page_id,b.parent_id,substr(b.text,1,2000) AS text,p.title FROM note_block_topics t JOIN note_blocks b ON b.id=t.block_id
        JOIN note_pages p ON p.id=b.page_id WHERE t.kind=? AND t.topic=? ORDER BY p.updated_at DESC,b.ordering,b.id LIMIT 500 OFFSET ?''', (kind, topic, offset)).fetchall()
    count = conn.execute('SELECT COUNT(*) FROM note_block_topics WHERE kind=? AND topic=?', (kind, topic)).fetchone()[0]
    return {'kind': kind, 'topic': topic, 'blocks': [dict(row) for row in rows], 'count': count, 'next_offset': offset + len(rows) if offset + len(rows) < count else None}


def mentions(conn, page_id):
    notes.page(conn, page_id)
    rows = conn.execute('SELECT id,title FROM note_pages WHERE id<>? ORDER BY length(title) DESC,id LIMIT 500', (page_id,)).fetchall()
    patterns = [(row, re.compile(r'(?<!\w)' + re.escape(row['title']) + r'(?!\w)', re.I)) for row in rows if row['title'].strip()]
    result = []
    for block in conn.execute('SELECT id,text FROM note_blocks WHERE page_id=? ORDER BY ordering', (page_id,)):
        plain = WIKI.sub('', block['text'])
        for row, pattern in patterns:
            if pattern.search(plain):
                result.append({'block_id': block['id'], 'page_id': row['id'], 'title': row['title'], 'text': block['text'][:400]})
                if len(result) >= 200:
                    return {'mentions': result}
    return {'mentions': result}

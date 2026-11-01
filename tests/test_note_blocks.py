"""Authoritative outlines, atomic revisions, live references and portable copies."""
import uuid

import pytest

from kitty.core import backlinks, note_blocks, notes


def block(text='', parent=None, **extra):
    return {'id': str(uuid.uuid4()), 'parent_id': parent, 'text': text, 'ordering': 0, 'collapsed': False, **extra}


def new_page(conn, title='Page'):
    book = notes.create_notebook(conn, 'Outline tests')
    return notes.create_page(conn, book['id'], title)


def test_stable_ids_nested_collapse_and_revision_survive_reload(workspace):
    conn = workspace.conn
    page = new_page(conn)
    parent = block('Parent', collapsed=True)
    child = block('Child', parent['id'], ordering=1)
    result = note_blocks.save_blocks(conn, page['id'], page['revision'], [parent, child])
    assert result['revision'] == page['revision'] + 1
    assert [b['id'] for b in result['blocks']] == [parent['id'], child['id']]
    assert result['blocks'][1]['parent_id'] == parent['id']
    assert note_blocks.get_blocks(conn, page['id']) == result
    assert result['blocks'][0]['collapsed'] is True
    with pytest.raises(notes.NotesError, match='changed'):
        note_blocks.save_blocks(conn, page['id'], page['revision'], [])
    assert note_blocks.get_blocks(conn, page['id']) == result


def test_invalid_cross_page_parent_duplicate_ids_and_cycles_are_atomic(workspace):
    conn = workspace.conn
    one, two = new_page(conn, 'One'), new_page(conn, 'Two')
    original = block('keep me')
    note_blocks.save_blocks(conn, one['id'], 1, [original])
    for values in ([block('invalid', original['id'])], [original], [block(page_id=one['id'])]):
        with pytest.raises(notes.NotesError):
            note_blocks.save_blocks(conn, two['id'], 1, values)
    a, b = block(), block()
    a['parent_id'], b['parent_id'] = b['id'], a['id']
    with pytest.raises(notes.NotesError, match='cycle'):
        note_blocks.save_blocks(conn, one['id'], 2, [a, b])
    assert note_blocks.get_blocks(conn, one['id'])['blocks'][0]['text'] == 'keep me'
    assert notes.page(conn, two['id'])['revision'] == 1


def test_two_pages_share_live_embeds_and_original_contexts(workspace):
    conn = workspace.conn
    source, one, two = (new_page(conn, title) for title in ('Original', 'One', 'Two'))
    original = block('Authoritative first text', id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    note_blocks.save_blocks(conn, source['id'], 1, [original])
    first = block('{{embed:' + original['id'] + '}}')
    second = block('(('+ original['id'] + ')) and {{embed:' + original['id'].upper() + '}}')
    note_blocks.save_blocks(conn, one['id'], 1, [first])
    note_blocks.save_blocks(conn, two['id'], 1, [second])
    assert note_blocks.contexts(conn, original['id'])['count'] == 2
    assert {row['page_id'] for row in note_blocks.contexts(conn, original['id'])['contexts']} == {one['id'], two['id']}
    original['text'] = 'Updated authoritative text'
    note_blocks.save_blocks(conn, source['id'], 2, [original])
    for page in (one, two):
        assert note_blocks.get_blocks(conn, page['id'])['references'][original['id']] == original['text']
    assert note_blocks.get_blocks(conn, source['id'])['citation_counts'][original['id']] == 2
    assert notes.page(conn, one['id'])['document']['elements'] == []
    note_blocks.save_blocks(conn, source['id'], 3, [])
    assert 'deleted' in note_blocks.get_blocks(conn, one['id'])['references'][original['id']]


def test_cycles_and_topics_and_search_have_one_authoritative_block(workspace):
    conn = workspace.conn
    page = new_page(conn, 'Links')
    target = new_page(conn, 'Graph theory')
    a, b = block('Graph theory #Graphs [[Graph theory]]'), block()
    a['text'] += ' {{embed:' + b['id'] + '}}'
    b['text'] = '((' + a['id'] + '))'
    note_blocks.save_blocks(conn, page['id'], 1, [a, b])
    assert 'cyclic' in note_blocks.get_blocks(conn, page['id'])['references'][a['id']]
    assert note_blocks.topics(conn, 'tag', 'GRAPHS')['blocks'][0]['id'] == a['id']
    assert note_blocks.topics(conn, 'page', 'Graph theory')['blocks'][0]['id'] == a['id']
    assert note_blocks.mentions(conn, page['id'])['mentions'][0]['page_id'] == target['id']
    assert any(row['id'] == page['id'] for row in notes.listing(conn, 'Graphs')['pages'])
    assert any(ref['id'] == page['id'] for ref in backlinks.mentions(conn, 'Graph theory')['references'])
    assert notes.page(conn, page['id'])['document']['elements'] == []


def test_daily_is_idempotent_and_canvas_saves_keep_block_search(workspace):
    conn = workspace.conn
    one = notes.daily_page(conn, '2026-10-15')
    two = notes.daily_page(conn, '2026-10-15')
    assert one['id'] == two['id']
    text = block('Unique outline search term')
    note_blocks.save_blocks(conn, one['id'], one['revision'], [text])
    notes.save_page(conn, one['id'], 2, one['document'], one['title'])
    assert 'Unique outline search term' in notes.page(conn, one['id'])['search_text']
    assert note_blocks.get_blocks(conn, one['id'])['blocks'][0]['id'] == text['id']


def test_portable_copy_preserves_outline_and_remaps_internal_references(workspace, tmp_path):
    conn = workspace.conn
    page = new_page(conn)
    parent = block('Parent')
    child = block('{{embed:' + parent['id'] + '}}', parent['id'], ordering=1)
    note_blocks.save_blocks(conn, page['id'], 1, [parent, child])
    portable = notes.export_page(conn, tmp_path, page['id'])
    restored = notes.restore(conn, tmp_path, page['notebook_id'], portable)
    blocks = note_blocks.get_blocks(conn, restored['id'])['blocks']
    assert len(blocks) == 2 and blocks[0]['id'] != parent['id']
    assert blocks[1]['parent_id'] == blocks[0]['id']
    assert blocks[0]['id'] in blocks[1]['text']
    assert blocks[1]['id'] != child['id']


def test_blocks_do_not_replace_existing_canvas_and_failed_save_is_atomic(workspace):
    conn = workspace.conn
    page = new_page(conn)
    document = notes.empty_document()
    document['elements'] = [{'id': 'ink-text', 'kind': 'text', 'layer': 'default',
                             'x': 10, 'y': 20, 'w': 200, 'h': 60, 'width': 1,
                             'color': '#123456', 'text': 'Existing canvas text', 'fontSize': 20}]
    notes.save_page(conn, page['id'], 1, document, 'Both views')
    content = block('New outline text')
    note_blocks.save_blocks(conn, page['id'], 2, [content])
    before = notes.page(conn, page['id'])
    assert before['document'] == notes.normalize(document)
    assert 'Existing canvas text' in before['search_text']
    assert 'New outline text' in before['search_text']
    invalid = {**content, 'text': 'Must not persist', 'collapsed': 'yes'}
    with pytest.raises(notes.NotesError):
        note_blocks.save_blocks(conn, page['id'], 3, [invalid])
    assert notes.page(conn, page['id']) == before
    with pytest.raises(notes.NotesError):
        note_blocks.save_blocks(conn, page['id'], 3, [content, content])
    with pytest.raises(notes.NotesError):
        note_blocks.save_blocks(conn, page['id'], 3, [block('x' * 20001)])
    assert note_blocks.get_blocks(conn, page['id'])['blocks'][0]['text'] == content['text']


def test_citation_contexts_and_topic_blocks_are_fully_pageable(workspace):
    conn = workspace.conn
    original_page = new_page(conn, 'Original')
    original = block('Original text')
    note_blocks.save_blocks(conn, original_page['id'], 1, [original])
    citing_page = new_page(conn, 'Many references')
    citations = [block('(('+original['id']+')) #pagination', ordering=index) for index in range(503)]
    note_blocks.save_blocks(conn, citing_page['id'], 1, citations)
    first = note_blocks.contexts(conn, original['id'])
    rest = note_blocks.contexts(conn, original['id'], first['next_offset'])
    assert first['count'] == 503
    assert len(first['contexts']) == 500
    assert len(rest['contexts']) == 3
    assert rest['next_offset'] is None
    assert {item['id'] for item in first['contexts'] + rest['contexts']} == {item['id'] for item in citations}
    first_topic = note_blocks.topics(conn, 'tag', 'pagination')
    rest_topic = note_blocks.topics(conn, 'tag', 'pagination', first_topic['next_offset'])
    assert first_topic['count'] == 503
    assert len(first_topic['blocks']) + len(rest_topic['blocks']) == 503
    assert rest_topic['next_offset'] is None


def last_ops(conn):
    import json
    return json.loads(conn.execute('SELECT ops FROM op_log ORDER BY seq DESC LIMIT 1').fetchone()[0])['ops']


def test_whole_outline_saves_become_minimal_block_transactions(workspace):
    conn = workspace.conn
    page = new_page(conn)
    items = [block(f'Item {index}', ordering=index) for index in range(50)]
    revision = note_blocks.save_blocks(conn, page['id'], page['revision'], items)['revision']
    items[17] = {**items[17], 'text': 'Edited'}
    revision = note_blocks.save_blocks(conn, page['id'], revision, items)['revision']
    assert [(op['op'], op.get('id')) for op in last_ops(conn)][:-1] == [('text', items[17]['id'])]
    moved = items.pop(3)
    items.insert(40, moved)
    items = [{**item, 'ordering': index} for index, item in enumerate(items)]
    note_blocks.save_blocks(conn, page['id'], revision, items)
    assert [op['op'] for op in last_ops(conn)] == ['move', 'set']
    assert [b['id'] for b in note_blocks.get_blocks(conn, page['id'])['blocks']] == [item['id'] for item in items]


def test_canvas_saves_touch_only_changed_elements(workspace):
    conn = workspace.conn
    page = new_page(conn)
    document = notes.empty_document()
    document['elements'] = [{'id': f'n{index}', 'kind': 'sticky', 'layer': 'default', 'x': index * 10, 'y': 0, 'w': 50, 'h': 50,
                             'color': '#000000', 'fill': 'none', 'width': 1, 'text': f'Note {index}', 'fontSize': 20} for index in range(20)]
    notes.save_page(conn, page['id'], 1, document, 'Board')
    document['elements'][5]['x'] = 999
    notes.save_page(conn, page['id'], 2, document, 'Board')
    ops = last_ops(conn)
    assert [op['op'] for op in ops] == ['set', 'set'] and ops[0]['props'] == {'x': 999}
    assert notes.page(conn, page['id'])['document'] == notes.normalize(document)


def test_deleted_pages_go_to_trash_and_leave_the_library(workspace):
    conn = workspace.conn
    page = new_page(conn, 'Disposable')
    note_blocks.save_blocks(conn, page['id'], 1, [block('Keep in trash #gone')])
    notes.delete_page(conn, page['id'], 2)
    assert notes.listing(conn)['pages'] == []
    with pytest.raises(notes.NotesError) as missing:
        notes.page(conn, page['id'])
    assert missing.value.status == 404
    assert note_blocks.topics(conn, 'tag', 'gone')['count'] == 0
    trashed = conn.execute("SELECT COUNT(*) FROM blocks WHERE page_id=(SELECT page_id FROM blocks WHERE text='Disposable') AND deleted_at IS NOT NULL").fetchone()[0]
    assert trashed == 3


def editor_page(conn, title='Typed page'):
    """A note page whose outline was written by the block editor: typed blocks, nested."""
    from kitty.core import blocks

    page = new_page(conn, title)
    root = conn.execute("SELECT id FROM blocks WHERE type='page' AND json_extract(properties,'$.number')=?", (page['id'],)).fetchone()['id']
    heading, todo, child = (str(uuid.uuid4()) for _ in range(3))
    blocks.transact(conn, [
        {'op': 'insert', 'id': heading, 'type': 'heading_1', 'parent': root, 'text': 'Plan #editor'},
        {'op': 'insert', 'id': todo, 'type': 'to_do', 'parent': root, 'text': 'Ship [[Typed target]]', 'props': {'checked': True}},
        {'op': 'insert', 'id': child, 'type': 'bulleted_list', 'parent': todo, 'text': 'Typed target nested detail'},
    ])
    return page, heading, todo, child


def test_editor_block_types_are_part_of_the_outline_everywhere(workspace):
    conn = workspace.conn
    target = new_page(conn, 'Typed target')
    page, heading, todo, child = editor_page(conn)
    outline = note_blocks.get_blocks(conn, page['id'])['blocks']
    assert [(item['id'], item['type']) for item in outline] == [(heading, 'heading_1'), (todo, 'to_do'), (child, 'bulleted_list')]
    assert outline[1]['props'] == {'checked': True} and outline[2]['parent_id'] == todo
    assert note_blocks.topics(conn, 'tag', 'editor')['blocks'][0]['id'] == heading
    assert note_blocks.topics(conn, 'page', 'typed target')['blocks'][0]['id'] == todo
    assert note_blocks.mentions(conn, page['id'])['mentions'][0]['page_id'] == target['id']
    assert 'Typed target nested detail' in notes.page(conn, page['id'])['search_text']
    cited = block('((' + child + '))')
    citing = new_page(conn, 'Citing')
    note_blocks.save_blocks(conn, citing['id'], citing['revision'], [cited])
    assert note_blocks.contexts(conn, child)['count'] == 1
    assert note_blocks.get_blocks(conn, page['id'])['citation_counts'] == {child: 1}


def test_outline_saves_and_portable_copies_keep_block_types_and_properties(workspace, tmp_path):
    conn = workspace.conn
    page, heading, todo, child = editor_page(conn)
    before = note_blocks.get_blocks(conn, page['id'])
    # A legacy whole-outline save that only edits text keeps every type and property.
    items = [{**item, 'text': item['text'] + '!'} if item['id'] == heading else item for item in before['blocks']]
    after = note_blocks.save_blocks(conn, page['id'], before['revision'], items)
    assert [(item['type'], item['props']) for item in after['blocks']] == [('heading_1', {}), ('to_do', {'checked': True}), ('bulleted_list', {})]
    portable = notes.export_page(conn, tmp_path, page['id'])
    restored = note_blocks.get_blocks(conn, notes.restore(conn, tmp_path, page['notebook_id'], portable)['id'])['blocks']
    assert [(item['type'], item['props'], item['text']) for item in restored] == [(item['type'], item['props'], item['text']) for item in after['blocks']]
    with pytest.raises(notes.NotesError):
        note_blocks.save_blocks(conn, page['id'], after['revision'], [{**items[0], 'type': 'canvas'}])
    with pytest.raises(notes.NotesError):
        note_blocks.save_blocks(conn, page['id'], after['revision'], [{**items[0], 'props': {'not a key': 1}}])


def test_the_outline_view_lists_exactly_the_text_flow_types():
    import re
    from pathlib import Path

    from kitty.core.blocks import convert

    sql = (Path(__file__).parents[1] / 'src/kitty/db/migrations/035_text_flow_outline.sql').read_text()
    lists = re.findall(r"type IN \(([^)]*)\)", sql)
    assert len(lists) == 2
    for found in lists:
        assert set(re.findall(r"'([a-z0-9_]+)'", found)) == convert.OUTLINE_TYPES

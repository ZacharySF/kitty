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
        note_blocks.save_blocks(conn, page['id'], 3, [invalid], title='Must not persist')
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

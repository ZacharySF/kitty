"""Behavioral Kanban contracts against real SQLite migrations, without network."""
from __future__ import annotations

import sqlite3

import pytest

from kitty.core import calendar, kanban
from kitty.db import connect, migrate
from kitty.web_server import APIError
from kitty import web_kanban


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / 'kanban.db')
    migrate(connection)
    yield connection
    connection.close()


def create_card(conn, board, title='Card', column=None, **extra):
    return kanban.card_action(conn, board['id'], None, 'create', {
        'revision': board['revision'], 'column_id': column or board['columns'][0]['id'],
        'title': title, **extra,
    })


def positions(board):
    assert [c['position'] for c in board['columns']] == list(range(len(board['columns'])))
    for column in board['columns']:
        cards = sorted((c for c in board['cards'] if c['column_id'] == column['id']), key=lambda c: c['position'])
        assert [c['position'] for c in cards] == list(range(len(cards)))
    assert all(c['column_id'] in {col['id'] for col in board['columns']} for c in board['cards'])


def test_roundtrip_edit_checklist_and_metrics(conn):
    board = kanban.create_board(conn, {'title': 'Course and career'})
    assert [c['title'] for c in board['columns']] == ['To do', 'In progress', 'Done']
    board = create_card(conn, board, 'Read chapter', notes='Take notes', priority='high',
                        labels=['class', 'class', 'exam'], due_date='2099-10-14',
                        checklist=[{'title': 'Read', 'done': False}, {'title': 'Summarize', 'done': False}])
    card = board['cards'][0]
    board = kanban.card_action(conn, board['id'], card['id'], 'update', {
        'revision': board['revision'], 'checklist': [{'title': 'Read', 'done': True}, {'title': 'Summarize', 'done': False}],
    })
    persisted = kanban.detail(conn, board['id'])
    assert persisted == board
    assert persisted['cards'][0]['labels'] == ['class', 'exam']
    assert persisted['cards'][0]['notes'] == 'Take notes'
    assert persisted['metrics'] == {'cards': 1, 'archived': 0, 'due': 1, 'overdue': 0, 'checklist_done': 1, 'checklist_total': 2}
    assert kanban.list_boards(conn)[0]['cards'] == 1
    board = kanban.board_action(conn, board['id'], 'update', {'revision': board['revision'], 'title': 'Renamed', 'archived': True})
    assert board['title'] == 'Renamed' and board['archived'] is True
    assert board['cards'] == persisted['cards']
    board = kanban.board_action(conn, board['id'], 'update', {'revision': board['revision'], 'archived': False})
    assert board['archived'] is False


def test_stale_edits_movements_and_deletion_do_not_overwrite(conn):
    board = create_card(conn, kanban.create_board(conn, {'title': 'Board'}))
    card = board['cards'][0]
    stale = board['revision']
    second = connect(conn.execute('PRAGMA database_list').fetchone()['file'])
    try:
        updated = kanban.card_action(second, board['id'], card['id'], 'update', {'revision': stale, 'title': 'Newer edit'})
        for action, body in (
            ('update', {'title': 'Stale edit'}),
            ('move', {'column_id': board['columns'][1]['id'], 'position': 0}),
            ('delete', {'confirmed': True}),
        ):
            with pytest.raises(APIError) as error:
                kanban.card_action(conn, board['id'], card['id'], action, {'revision': stale, **body})
            assert error.value.status == 409
            assert kanban.detail(conn, board['id']) == updated
        with pytest.raises(APIError) as error:
            kanban.board_action(conn, board['id'], 'delete', {'revision': stale, 'confirmed': True})
        assert error.value.status == 409
        with pytest.raises(APIError) as error:
            kanban.column_action(conn, board['id'], board['columns'][0]['id'], 'update', {'revision': stale, 'title': 'Stale column'})
        assert error.value.status == 409
        with pytest.raises(APIError) as error:
            kanban.export_deadline(conn, board['id'], card['id'], {'revision': stale})
        assert error.value.status == 409
    finally:
        second.close()


def test_ordering_move_reorder_archive_and_delete(conn):
    board = kanban.create_board(conn, {'title': 'Order'})
    for title in ('A', 'B', 'C'):
        board = create_card(conn, board, title)
    first, second = board['columns'][:2]
    a, b, c = sorted(board['cards'], key=lambda card: card['position'])
    board = kanban.card_action(conn, board['id'], c['id'], 'move', {'revision': board['revision'], 'column_id': first['id'], 'position': 0})
    positions(board)
    assert [x['title'] for x in sorted(board['cards'], key=lambda x: x['position'])] == ['C', 'A', 'B']
    board = kanban.card_action(conn, board['id'], a['id'], 'move', {'revision': board['revision'], 'column_id': second['id'], 'position': 0})
    positions(board)
    board = kanban.card_action(conn, board['id'], b['id'], 'update', {'revision': board['revision'], 'archived': True})
    assert board['metrics']['cards'] == 2 and board['metrics']['archived'] == 1
    board = kanban.card_action(conn, board['id'], c['id'], 'delete', {'revision': board['revision'], 'confirmed': True})
    positions(board)
    assert next(x for x in board['cards'] if x['id'] == b['id'])['position'] == 0
    board = kanban.column_action(conn, board['id'], second['id'], 'move', {'revision': board['revision'], 'position': 0})
    positions(board)
    assert board['columns'][0]['id'] == second['id']


def test_column_deletion_rehomes_archived_cards_and_cascades_board(conn):
    board = create_card(conn, kanban.create_board(conn, {'title': 'Delete'}), archived=True)
    first, second = board['columns'][:2]
    before = kanban.detail(conn, board['id'])
    with pytest.raises(APIError):
        kanban.column_action(conn, board['id'], first['id'], 'delete', {'revision': board['revision'], 'confirmed': True})
    assert kanban.detail(conn, board['id']) == before
    board = kanban.column_action(conn, board['id'], first['id'], 'delete', {
        'revision': board['revision'], 'confirmed': True, 'destination_id': second['id'],
    })
    assert board['cards'][0]['column_id'] == second['id']
    assert board['cards'][0]['archived'] is True
    positions(board)
    result = kanban.board_action(conn, board['id'], 'delete', {'revision': board['revision'], 'confirmed': True})
    assert result == {'deleted': True}
    assert conn.execute('SELECT count(*) FROM kanban_cards').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM kanban_columns').fetchone()[0] == 0
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_cross_board_and_invalid_positions_are_atomic(conn):
    a = create_card(conn, kanban.create_board(conn, {'title': 'A'}))
    b = kanban.create_board(conn, {'title': 'B'})
    card = a['cards'][0]
    for destination, position in ((b['columns'][0]['id'], 0), (a['columns'][0]['id'], 10), (a['columns'][0]['id'], -1)):
        with pytest.raises(APIError):
            kanban.card_action(conn, a['id'], card['id'], 'move', {'revision': a['revision'], 'column_id': destination, 'position': position})
        assert kanban.detail(conn, a['id']) == a
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('UPDATE kanban_cards SET column_id=? WHERE id=?', (b['columns'][0]['id'], card['id']))
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_last_column_and_unconfirmed_deletions_are_rejected(conn):
    board = create_card(conn, kanban.create_board(conn, {'title': 'Safe'}))
    with pytest.raises(APIError):
        kanban.card_action(conn, board['id'], board['cards'][0]['id'], 'delete', {'revision': board['revision']})
    with pytest.raises(APIError):
        kanban.board_action(conn, board['id'], 'delete', {'revision': board['revision']})
    for column in board['columns'][1:]:
        board = kanban.column_action(conn, board['id'], column['id'], 'delete', {'revision': board['revision'], 'confirmed': True})
    with pytest.raises(APIError):
        kanban.column_action(conn, board['id'], board['columns'][0]['id'], 'delete', {'revision': board['revision'], 'confirmed': True})
    positions(kanban.detail(conn, board['id']))


def test_local_source_links_never_change_status(conn):
    task_id = conn.execute("INSERT INTO study_tasks(title) VALUES('Original study task')").lastrowid
    board = create_card(conn, kanban.create_board(conn, {'title': 'Links'}), source={'kind': 'study_task', 'id': task_id})
    card = board['cards'][0]
    assert card['source']['title'] == 'Original study task'
    board = kanban.card_action(conn, board['id'], card['id'], 'move', {'revision': board['revision'], 'column_id': board['columns'][2]['id'], 'position': 0})
    assert conn.execute('SELECT done,revision FROM study_tasks WHERE id=?', (task_id,)).fetchone()['done'] == 0
    assert conn.execute('SELECT revision FROM study_tasks WHERE id=?', (task_id,)).fetchone()[0] == 1
    conn.execute('DELETE FROM study_tasks WHERE id=?', (task_id,))
    assert kanban.detail(conn, board['id'])['cards'][0]['source'] is None
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_calendar_export_explicit_date_idempotence_and_preserved_edits(conn):
    board = create_card(conn, kanban.create_board(conn, {'title': 'Dates'}), 'Deadline')
    card = board['cards'][0]
    with pytest.raises(APIError):
        kanban.export_deadline(conn, board['id'], card['id'], {'revision': board['revision']})
    board = kanban.card_action(conn, board['id'], card['id'], 'update', {'revision': board['revision'], 'due_date': '2030-02-28'})
    exported = kanban.export_deadline(conn, board['id'], card['id'], {'revision': board['revision']})['event']
    assert exported['start'] == '2030-02-28' and exported['end'] == '2030-03-01'
    assert exported['source_key'] == f"kanban:card:{card['id']}:deadline"
    assert exported['all_day'] is True
    calendar.edit_event(conn, exported['id'], {'revision': exported['revision'], 'changes': {'title': 'Calendar-owned edit'}})
    board = kanban.card_action(conn, board['id'], card['id'], 'update', {'revision': board['revision'], 'due_date': '2030-03-05'})
    again = kanban.export_deadline(conn, board['id'], card['id'], {'revision': board['revision']})['event']
    assert again['id'] == exported['id'] and again['title'] == 'Calendar-owned edit'
    assert again['start'] == '2030-02-28'
    assert conn.execute('SELECT count(*) FROM calendar_events').fetchone()[0] == 1
    old_id = card['id']
    kanban.board_action(conn, board['id'], 'delete', {'revision': board['revision'], 'confirmed': True})
    newer = create_card(conn, kanban.create_board(conn, {'title': 'Replacement'}), due_date='2030-03-05')
    assert newer['cards'][0]['id'] > old_id
    assert conn.execute('SELECT count(*) FROM calendar_events').fetchone()[0] == 1


@pytest.mark.parametrize('changes', [
    {'due_date': '2030-02-30'}, {'due_date': '2030-01-01T10:00:00'}, {'due_date': '9999-12-31'},
    {'priority': 'unknown'}, {'source_url': 'javascript:alert(1)'}, {'source_url': 'https://user:password@example.com'},
    {'checklist': [{'title': 'Item', 'done': 1}]}, {'labels': ['']}, {'unsupported': True},
])
def test_invalid_edits_leave_board_unchanged(conn, changes):
    board = create_card(conn, kanban.create_board(conn, {'title': 'Validation'}))
    with pytest.raises(APIError):
        kanban.card_action(conn, board['id'], board['cards'][0]['id'], 'update', {'revision': board['revision'], **changes})
    assert kanban.detail(conn, board['id']) == board


def test_extension_routes_and_unsupported_actions(conn):
    assert web_kanban.get(None, conn, '/api/jobs', {}) is None
    assert web_kanban.post(None, conn, '/api/kanban-other', {}) is None
    board = web_kanban.post(None, conn, '/api/kanban/boards', {'title': 'Via HTTP adapter'})
    assert web_kanban.get(None, conn, f"/api/kanban/boards/{board['id']}", {}) == board
    assert web_kanban.get(None, conn, '/api/kanban', {})['boards'][0]['id'] == board['id']
    assert web_kanban.get(None, conn, '/api/kanban/sources', {}) == {'sources': []}
    for path in ('/api/kanban/unknown', f"/api/kanban/boards/{board['id']}/columns/{board['columns'][0]['id']}/calendar"):
        with pytest.raises(APIError) as error:
            web_kanban.post(None, conn, path, {'revision': board['revision']})
        assert error.value.status == 404

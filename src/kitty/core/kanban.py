"""Local Kanban with whole-board optimistic revisions and contiguous ordering.

Every mutation of an existing board requires its current revision. Archived cards
retain their ordering slot. Deleting a populated column requires an explicit
same-board destination, including for archived cards. Source references are only
links: this module never updates jobs, opportunities, study tasks, or projects.
Calendar export snapshots the supplied deadline and uses an immutable card key;
repeat export preserves the existing calendar event, including user edits.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from urllib.parse import urlsplit

from kitty.dates import now_iso
from kitty.db.connection import transaction

SOURCES = {
    'job': ('jobs', 'title', 'url'),
    'opportunity': ('opportunities', 'title', 'url'),
    'study_project': ('study_projects', 'name', None),
    'opportunity_project': ('opportunity_projects', 'title', None),
    'study_task': ('study_tasks', 'title', None),
}
PRIORITIES = ('low', 'normal', 'high', 'urgent')


def fail(message, status=400):
    from kitty.web_server import APIError
    raise APIError(status, message)


def text(value, name, maximum=20000, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        fail(f'{name} must be text of at most {maximum} characters')
    try:
        value.encode('utf-8')
    except UnicodeError:
        fail(f'{name} contains invalid Unicode')
    value = value.strip()
    if required and not value:
        fail(f'{name} cannot be empty')
    return value


def url(value):
    value = text(value, 'Source URL', 4000)
    if value:
        try:
            parts = urlsplit(value)
            parts.port
        except ValueError:
            fail('Use a valid http or https source URL')
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            fail('Use a http or https source URL without credentials')
    return value


def integer(value, name):
    if type(value) is not int or value < 0:
        fail(f'{name} must be a nonnegative integer')
    return value


def fields(body, allowed):
    if not isinstance(body, dict) or set(body) - set(allowed):
        fail('Unknown Kanban fields')


def _board(conn, ident, revision=None, check=False):
    row = conn.execute('SELECT * FROM kanban_boards WHERE id=?', (ident,)).fetchone()
    if row is None:
        fail('Board not found', 404)
    if check and (type(revision) is not int or row['revision'] != revision):
        fail('Board changed; reload before saving. Your draft has not been overwritten.', 409)
    return row


def _column(conn, board_id, ident):
    row = conn.execute('SELECT * FROM kanban_columns WHERE board_id=? AND id=?', (board_id, integer(ident, 'Column id'))).fetchone()
    if row is None:
        fail('Column not found in this board', 404)
    return row


def _card(conn, board_id, ident):
    row = conn.execute('SELECT * FROM kanban_cards WHERE board_id=? AND id=?', (board_id, ident)).fetchone()
    if row is None:
        fail('Card not found in this board', 404)
    return row


def _touch(conn, ident):
    conn.execute('UPDATE kanban_boards SET revision=revision+1,updated_at=? WHERE id=?', (now_iso(), ident))


def _ids(conn, table, scope, ident):
    return [r['id'] for r in conn.execute(f'SELECT id FROM {table} WHERE {scope}=? ORDER BY position,id', (ident,))]


def _order(conn, table, scope, ident, ids):
    # Move all slots beyond the current maximum before assigning final slots,
    # so the unique ordering constraint holds even while swapping neighbors.
    maximum = conn.execute(f'SELECT coalesce(max(position),0) FROM {table} WHERE {scope}=?', (ident,)).fetchone()[0]
    conn.execute(f'UPDATE {table} SET position=position+? WHERE {scope}=?', (maximum + len(ids) + 1, ident))
    for position, item in enumerate(ids):
        conn.execute(f'UPDATE {table} SET position=? WHERE id=?', (position, item))


def source_options(conn):
    result = []
    for kind, (table, title, link) in SOURCES.items():
        for row in conn.execute(f'SELECT id,{title} AS title' + (f',{link} AS url' if link else ",'' AS url") + f' FROM {table} ORDER BY id'):
            result.append({'kind': kind, **dict(row)})
    return result


def _decode(conn, row):
    card = dict(row)
    card['labels'] = json.loads(card.pop('labels_json'))
    card['checklist'] = json.loads(card.pop('checklist_json'))
    card['archived'] = bool(card['archived'])
    card['source'] = None
    for kind, (table, title, link) in SOURCES.items():
        ident = card.pop(kind + '_id')
        if ident is not None:
            source = conn.execute(f'SELECT id,{title} AS title' + (f',{link} AS url' if link else ",'' AS url") + f' FROM {table} WHERE id=?', (ident,)).fetchone()
            if source:
                card['source'] = {'kind': kind, **dict(source)}
    return card


def detail(conn, ident):
    if not conn.in_transaction:
        # A GET must not mix a pre-move revision with post-move card positions.
        with transaction(conn):
            return detail(conn, ident)
    board = dict(_board(conn, ident))
    board['archived'] = bool(board['archived'])
    board['columns'] = [dict(r) for r in conn.execute('SELECT * FROM kanban_columns WHERE board_id=? ORDER BY position,id', (ident,))]
    board['cards'] = [_decode(conn, r) for r in conn.execute('SELECT * FROM kanban_cards WHERE board_id=? ORDER BY column_id,position,id', (ident,))]
    active = [c for c in board['cards'] if not c['archived']]
    checklist = [i for c in active for i in c['checklist']]
    today = date.today().isoformat()
    board['metrics'] = {'cards': len(active), 'archived': len(board['cards']) - len(active),
                        'due': sum(bool(c['due_date']) for c in active),
                        'overdue': sum(bool(c['due_date'] and c['due_date'] < today) for c in active),
                        'checklist_done': sum(i['done'] for i in checklist), 'checklist_total': len(checklist)}
    return board


def list_boards(conn):
    return [{**dict(r), 'archived': bool(r['archived'])} for r in conn.execute(
        'SELECT b.*, (SELECT count(*) FROM kanban_cards c WHERE c.board_id=b.id AND c.archived=0) AS cards FROM kanban_boards b ORDER BY b.archived,b.id')]


def create_board(conn, body):
    fields(body, {'title'})
    title = text(body.get('title'), 'Board title', 500, True)
    with transaction(conn, immediate=True):
        stamp = now_iso()
        ident = conn.execute('INSERT INTO kanban_boards(title,created_at,updated_at) VALUES(?,?,?)', (title, stamp, stamp)).lastrowid
        for position, name in enumerate(('To do', 'In progress', 'Done')):
            conn.execute('INSERT INTO kanban_columns(board_id,title,position) VALUES(?,?,?)', (ident, name, position))
        result = detail(conn, ident)
    return result


def board_action(conn, ident, action, body):
    fields(body, {'revision', 'confirmed'} if action == 'delete' else {'revision', 'title', 'archived'})
    with transaction(conn, immediate=True):
        row = _board(conn, ident, body.get('revision'), True)
        if action == 'delete':
            if body.get('confirmed') is not True:
                fail('Explicit confirmation is required to delete a board and its cards')
            conn.execute('DELETE FROM kanban_boards WHERE id=?', (ident,))
            return {'deleted': True}
        title = text(body.get('title', row['title']), 'Board title', 500, True)
        archived = body.get('archived', bool(row['archived']))
        if type(archived) is not bool:
            fail('archived must be boolean')
        conn.execute('UPDATE kanban_boards SET title=?,archived=? WHERE id=?', (title, archived, ident))
        _touch(conn, ident)
        result = detail(conn, ident)
    return result


def column_action(conn, board_id, ident, action, body):
    allowed = {'create': {'revision', 'title'}, 'update': {'revision', 'title'},
               'move': {'revision', 'position'}, 'delete': {'revision', 'confirmed', 'destination_id'}}
    fields(body, allowed[action])
    with transaction(conn, immediate=True):
        _board(conn, board_id, body.get('revision'), True)
        ids = _ids(conn, 'kanban_columns', 'board_id', board_id)
        if action == 'create':
            title = text(body.get('title'), 'Column title', 500, True)
            conn.execute('INSERT INTO kanban_columns(board_id,title,position) VALUES(?,?,?)', (board_id, title, len(ids)))
        else:
            _column(conn, board_id, ident)
            if action == 'update':
                conn.execute('UPDATE kanban_columns SET title=? WHERE id=?', (text(body.get('title'), 'Column title', 500, True), ident))
            elif action == 'move':
                position = integer(body.get('position'), 'Position')
                if position >= len(ids):
                    fail('Column position is outside this board')
                ids.remove(ident)
                ids.insert(position, ident)
                _order(conn, 'kanban_columns', 'board_id', board_id, ids)
            else:
                if body.get('confirmed') is not True:
                    fail('Explicit confirmation is required to delete a column')
                if len(ids) == 1:
                    fail('A board must keep at least one column')
                cards = _ids(conn, 'kanban_cards', 'column_id', ident)
                if cards:
                    destination = body.get('destination_id')
                    _column(conn, board_id, destination)
                    if destination == ident:
                        fail('Choose a different destination column')
                    offset = len(_ids(conn, 'kanban_cards', 'column_id', destination))
                    for position, card in enumerate(cards, offset):
                        conn.execute('UPDATE kanban_cards SET column_id=?,position=? WHERE id=?', (destination, position, card))
                conn.execute('DELETE FROM kanban_columns WHERE id=?', (ident,))
                ids.remove(ident)
                _order(conn, 'kanban_columns', 'board_id', board_id, ids)
        _touch(conn, board_id)
        result = detail(conn, board_id)
    return result


def _card_fields(conn, body):
    result = {}
    for key, value in body.items():
        if key in ('title', 'notes'):
            result[key] = text(value, key, 500 if key == 'title' else 20000, key == 'title')
        elif key == 'priority':
            if value not in PRIORITIES:
                fail('Unknown card priority')
            result[key] = value
        elif key == 'archived':
            if type(value) is not bool:
                fail('archived must be boolean')
            result[key] = value
        elif key == 'due_date':
            if value is None or value == '':
                result[key] = None
            else:
                if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    fail('Due date must be YYYY-MM-DD; no date is guessed')
                try:
                    parsed = date.fromisoformat(value)
                    parsed + timedelta(days=1)
                except (ValueError, OverflowError):
                    fail('Use a valid due date with an exportable next day')
                result[key] = parsed.isoformat()
        elif key == 'labels':
            if not isinstance(value, list) or len(value) > 100:
                fail('Labels must be an array of at most 100 strings')
            result['labels_json'] = json.dumps(list(dict.fromkeys(text(v, 'Label', 100, True) for v in value)))
        elif key == 'checklist':
            if not isinstance(value, list) or len(value) > 200:
                fail('Checklist must be an array of at most 200 items')
            items = []
            for item in value:
                if not isinstance(item, dict) or set(item) != {'title', 'done'} or type(item['done']) is not bool:
                    fail('Checklist items require title and boolean done')
                items.append({'title': text(item['title'], 'Checklist title', 500, True), 'done': item['done']})
            result['checklist_json'] = json.dumps(items)
        elif key == 'source_url':
            result[key] = url(value)
        elif key == 'source':
            result.update({kind + '_id': None for kind in SOURCES})
            if value is not None:
                if not isinstance(value, dict) or set(value) != {'kind', 'id'} or not isinstance(value['kind'], str) or value['kind'] not in SOURCES:
                    fail('Select a known local source')
                ident = integer(value['id'], 'Source id')
                table = SOURCES[value['kind']][0]
                if not conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (ident,)).fetchone():
                    fail('Source no longer exists', 404)
                result[value['kind'] + '_id'] = ident
    return result


CARD_FIELDS = {'title', 'notes', 'priority', 'labels', 'due_date', 'checklist', 'archived', 'source', 'source_url'}


def card_action(conn, board_id, ident, action, body):
    allowed = {'create': CARD_FIELDS | {'revision', 'column_id'}, 'update': CARD_FIELDS | {'revision'},
               'move': {'revision', 'column_id', 'position'}, 'delete': {'revision', 'confirmed'}}
    fields(body, allowed[action])
    with transaction(conn, immediate=True):
        _board(conn, board_id, body.get('revision'), True)
        if action == 'create':
            column = _column(conn, board_id, body.get('column_id'))
            data = _card_fields(conn, body)
            if not data.get('title'):
                fail('A card title is required')
            data.update(board_id=board_id, column_id=column['id'], position=len(_ids(conn, 'kanban_cards', 'column_id', column['id'])))
            conn.execute(f"INSERT INTO kanban_cards({','.join(data)}) VALUES({','.join('?' for _ in data)})", tuple(data.values()))
        else:
            card = _card(conn, board_id, ident)
            if action == 'update':
                data = _card_fields(conn, body)
                if not data:
                    fail('Select card fields to update')
                conn.execute(f"UPDATE kanban_cards SET {','.join(k+'=?' for k in data)} WHERE id=?", (*data.values(), ident))
            elif action == 'delete':
                if body.get('confirmed') is not True:
                    fail('Explicit confirmation is required to delete a card')
                conn.execute('DELETE FROM kanban_cards WHERE id=?', (ident,))
                _order(conn, 'kanban_cards', 'column_id', card['column_id'], _ids(conn, 'kanban_cards', 'column_id', card['column_id']))
            else:
                destination = _column(conn, board_id, body.get('column_id'))['id']
                position = integer(body.get('position'), 'Position')
                target_ids = _ids(conn, 'kanban_cards', 'column_id', destination)
                if destination == card['column_id']:
                    target_ids.remove(ident)
                if position > len(target_ids):
                    fail('Card position is outside the destination column')
                if destination != card['column_id']:
                    conn.execute('UPDATE kanban_cards SET column_id=?,position=? WHERE id=?', (destination, len(target_ids), ident))
                    _order(conn, 'kanban_cards', 'column_id', card['column_id'], _ids(conn, 'kanban_cards', 'column_id', card['column_id']))
                target_ids.insert(position, ident)
                _order(conn, 'kanban_cards', 'column_id', destination, target_ids)
        _touch(conn, board_id)
        result = detail(conn, board_id)
    return result


def export_deadline(conn, board_id, ident, body):
    from kitty.core.calendar import create_event
    fields(body, {'revision'})
    # Calendar owns its transaction. Release the snapshot transaction before
    # invoking it; the immutable source key makes repeat exports non-overwriting.
    with transaction(conn, immediate=True):
        _board(conn, board_id, body.get('revision'), True)
        card = _decode(conn, _card(conn, board_id, ident))
        if not card['due_date']:
            fail('Set an explicit due date before exporting to Calendar')
        payload = {'title': card['title'] + ' — deadline', 'start': card['due_date'],
                   'end': (date.fromisoformat(card['due_date']) + timedelta(days=1)).isoformat(),
                   'all_day': True, 'timezone': 'UTC', 'description': card['notes'],
                   'source_url': card['source_url'] or (card['source']['url'] if card['source'] else ''),
                   'source_key': f'kanban:card:{ident}:deadline'}
    return {'event': create_event(conn, payload)}

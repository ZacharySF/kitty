"""/api/kanban extension; all edits carry the current whole-board revision."""
from __future__ import annotations

import re

from kitty.core import kanban as core


def get(app, conn, path, query):
    if path != '/api/kanban' and not path.startswith('/api/kanban/'):
        return None
    if query:
        core.fail('Kanban endpoints do not accept query parameters')
    if path == '/api/kanban':
        return {'boards': core.list_boards(conn)}
    if path == '/api/kanban/sources':
        return {'sources': core.source_options(conn)}
    match = re.fullmatch(r'/api/kanban/boards/([1-9][0-9]*)', path)
    if match:
        return core.detail(conn, int(match[1]))
    core.fail('Unknown Kanban endpoint', 404)


def post(app, conn, path, body):
    if path != '/api/kanban' and not path.startswith('/api/kanban/'):
        return None
    if path == '/api/kanban/boards':
        return core.create_board(conn, body)
    match = re.fullmatch(r'/api/kanban/boards/([1-9][0-9]*)(?:/(delete))?', path)
    if match:
        return core.board_action(conn, int(match[1]), match[2] or 'update', body)
    match = re.fullmatch(r'/api/kanban/boards/([1-9][0-9]*)/(columns|cards)(?:/([1-9][0-9]*)(?:/(move|delete|calendar))?)?', path)
    if match:
        board, resource, ident, action = match.groups()
        board, ident = int(board), int(ident) if ident else None
        action = action or ('create' if ident is None else 'update')
        if resource == 'cards' and action == 'calendar':
            return core.export_deadline(conn, board, ident, body)
        if resource == 'columns' and action == 'calendar':
            core.fail('Columns do not export to Calendar', 404)
        handler = core.column_action if resource == 'columns' else core.card_action
        return handler(conn, board, ident, action, body)
    core.fail('Unknown Kanban endpoint', 404)

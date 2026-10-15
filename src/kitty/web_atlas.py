"""Read-only /api/atlas: bounded recent entities that fan out of the home graph.

Each item names the workspace view it belongs to, so the 3D atlas can attach it
to that section's hub and navigate there. Only ids, titles and short details are
returned: no file paths, URLs, notes bodies or provider tokens. Tables that an
older database does not have yet are skipped instead of failing the request.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from kitty.core import calendar

LIMIT = 40
EVENT_WINDOW = timedelta(days=30)
LABEL_LIMIT = 120
# kind -> (plural label, default workspace view id in web/src/lib/workspace.ts)
KINDS: dict[str, tuple[str, str]] = {
    'subject': ('Study subjects', 'study'),
    'deck': ('Flashcard decks', 'flashcards'),
    'notebook': ('Notebooks', 'notes'),
    'schema': ('Schema maps', 'schema'),
    'board': ('Kanban boards', 'kanban'),
    'event': ('Upcoming events', 'calendar'),
    'job': ('Saved & queued jobs', 'favorites'),
    'application': ('Applications', 'pipeline'),
    'opportunity': ('Opportunities', 'opportunities'),
}


def _text(value, limit=LABEL_LIMIT) -> str:
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _item(kind: str, ref, label, detail='', view: str | None = None) -> dict:
    return {'id': f'{kind}:{ref}', 'kind': kind, 'ref': ref, 'label': _text(label) or 'Untitled',
            'detail': _text(detail), 'view': view or KINDS[kind][1]}


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    try:
        return [tuple(row) for row in conn.execute(sql, (*params, LIMIT))]
    except sqlite3.OperationalError as exc:
        if 'no such' in str(exc):  # table/column absent in an older database
            return []
        raise


def _subjects(conn, now):
    return [_item('subject', ident, name) for ident, name in _rows(
        conn, 'SELECT id, name FROM study_subjects ORDER BY name COLLATE NOCASE, id LIMIT ?')]


def _decks(conn, now):
    rows = _rows(conn, 'SELECT d.id, d.name, (SELECT COUNT(*) FROM flash_notes n WHERE n.deck_id = d.id'
                       ' AND n.archived = 0) FROM flash_decks d WHERE d.archived = 0'
                       ' ORDER BY d.name COLLATE NOCASE, d.id LIMIT ?')
    return [_item('deck', ident, name, f'{notes} notes') for ident, name, notes in rows]


def _notebooks(conn, now):
    rows = _rows(conn, 'SELECT id, title, subject FROM note_notebooks ORDER BY updated_at DESC, id DESC LIMIT ?')
    return [_item('notebook', ident, title, subject) for ident, title, subject in rows]


def _schemas(conn, now):
    rows = _rows(conn, 'SELECT id, title, mode FROM schema_maps ORDER BY updated_at DESC, id DESC LIMIT ?')
    return [_item('schema', ident, title, f'{mode} map') for ident, title, mode in rows]


def _boards(conn, now):
    rows = _rows(conn, 'SELECT b.id, b.title, (SELECT COUNT(*) FROM kanban_cards c WHERE c.board_id = b.id'
                       ' AND c.archived = 0) FROM kanban_boards b WHERE b.archived = 0'
                       ' ORDER BY b.updated_at DESC, b.id DESC LIMIT ?')
    return [_item('board', ident, title, f'{cards} cards') for ident, title, cards in rows]


def _events(conn, now):
    from kitty.web_server import APIError
    try:
        occurrences = calendar.occurrences(conn, now.isoformat(), (now + EVENT_WINDOW).isoformat())
    except sqlite3.OperationalError as exc:
        if 'no such' in str(exc):
            return []
        raise
    except APIError:  # e.g. too many occurrences to expand; the atlas stays usable
        return []
    items, seen = [], set()
    for event in occurrences:
        if event['id'] in seen:
            continue
        seen.add(event['id'])
        start = str(event['start'])
        when = start[:10] if event['all_day'] else start[:16].replace('T', ' ')
        items.append(_item('event', event['id'], event['title'], when))
        if len(items) == LIMIT:
            break
    return items


def _jobs(conn, now):
    rows = _rows(conn, "SELECT j.id, j.title, c.display_name, j.favorited_at IS NOT NULL FROM jobs j"
                       " JOIN companies c ON c.id = j.company_id"
                       " WHERE j.favorited_at IS NOT NULL OR j.queue_state = 'queued'"
                       " ORDER BY COALESCE(j.favorited_at, j.updated_at) DESC, j.id DESC LIMIT ?")
    return [_item('job', ident, f'{company} — {title}', 'saved' if saved else 'queued',
                  'favorites' if saved else 'queue') for ident, title, company, saved in rows]


def _applications(conn, now):
    # ref is the job id: opening an application opens its job, like saved jobs.
    rows = _rows(conn, "SELECT a.job_id, j.title, c.display_name, a.status FROM applications a"
                       " JOIN jobs j ON j.id = a.job_id JOIN companies c ON c.id = j.company_id"
                       " ORDER BY a.last_activity_at DESC, a.id DESC LIMIT ?")
    return [_item('application', job, f'{company} — {title}', status) for job, title, company, status in rows]


def _opportunities(conn, now):
    rows = _rows(conn, "SELECT id, title, category, status FROM opportunities WHERE status <> 'skipped'"
                       " ORDER BY favorite DESC, updated_at DESC, id DESC LIMIT ?")
    return [_item('opportunity', ident, title, f'{category} · {status}')
            for ident, title, category, status in rows]


COLLECTORS = {'subject': _subjects, 'deck': _decks, 'notebook': _notebooks, 'schema': _schemas,
              'board': _boards, 'event': _events, 'job': _jobs, 'application': _applications,
              'opportunity': _opportunities}


def atlas(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    moment = now or datetime.now(UTC)
    items = [item for kind in KINDS for item in COLLECTORS[kind](conn, moment)]
    return {'limit': LIMIT,
            'kinds': [{'kind': kind, 'label': label, 'view': view} for kind, (label, view) in KINDS.items()],
            'items': items}


def get(app, conn, path, query):
    if path != '/api/atlas':
        return None
    app._no_query(query)
    return atlas(conn)


def post(app, conn, path, body):
    return None

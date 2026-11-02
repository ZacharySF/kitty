"""Every successful browser mutation as one append-only ``record_events`` row.

``WebApplication.mutate`` calls :func:`capture` after a POST handler succeeds, on the
same connection. A row holds only route-derived identifiers (section, entity, entity
id, action and the request path), never titles or bodies; ``/api/brain`` resolves
labels from live rows when it reads them. A capture failure is logged and never fails
the user's request.

Resolution: :data:`ROUTES` names the entity and action of every known content route;
an unknown route under a content prefix falls back to its shape
``/api/<section>/<resource>/<id?>/<action?>``. The entity id comes from the path, then
the result (``id`` or the ``id`` of its single nested object), then the body's ``id``.
A ``save`` without any id is recorded as ``create``.

Not captured: :data:`NON_CONTENT_PREFIXES` (settings, accounts, automation, AI,
terminals…), :data:`SKIP` routes inside content sections (view state, previews,
practice and timer controls), mutations that only start a background task, and
mutations whose core already appends to its own history (``flash_reviews``,
``status_events``, ``study_sessions``): those logs are their edits.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any, Callable, NamedTuple

log = logging.getLogger(__name__)

#: Brain section ids (workspace view ids in web/src/lib/workspace.ts).
SECTIONS = ("calendar", "reminders", "opportunities", "study", "flashcards", "notes",
            "quick-notes", "schema", "documents", "jobs", "applications")
#: First path segment after /api/ -> section, for routes that change content.
SECTION_PREFIXES = {
    "notes": "notes", "documents": "documents", "quick-notes": "quick-notes",
    "flashcards": "flashcards", "study": "study", "calendar": "calendar", "schema": "schema",
    "opportunities": "opportunities", "jobs": "jobs", "job-tools": "jobs", "reminders": "reminders",
}
#: First path segments whose mutations are settings, accounts, controls or tooling.
NON_CONTENT_PREFIXES = frozenset({
    "reload", "modules", "account", "ai", "automation", "questions", "resumes", "profile",
    "sources", "finder", "reports", "mail", "triage", "postings", "dashboard", "job-options",
    "pipeline", "backlinks", "atlas", "brain", "desktop", "terminal", "extensions", "tasks",
    "bootstrap",
})
#: Non-content routes inside content sections.
SKIP = tuple(re.compile(pattern) for pattern in (
    r"/api/notes/pages/[0-9]+/viewport",          # pan and zoom of the canvas view
    r"/api/notes/(daily|resolve)",                # get-or-create while navigating; new pages are records
    r"/api/notes/(preferences|cleanup)",          # notes settings, unused reference-file cleanup
    r"/api/flashcards/(review|reveal|export|media)",  # practice controls, export, media upload
    r"/api/flashcards/(grade|undo)",              # flash_reviews is their history
    r"/api/calendar/(time|availability|import/preview|reminders/dismiss)",  # computed answers, dismissal
    r"/api/calendar/google/.*",                   # provider credentials and sync controls
    r"/api/study/(timer|settings)",               # timer controls (study_sessions keeps the result), settings
    r"/api/documents/(prepare|topics)",           # extraction preview, topic suggestions
    r"/api/opportunities/(settings|discover|sources/.*)",  # discovery settings and runs
    r"/api/jobs/metadata",                        # posting title lookup
))

ID = r"(?P<id>[1-9][0-9]{0,17})"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,100}")
_WORD = re.compile(r"[a-z][a-z_-]{0,39}")


class Event(NamedTuple):
    section: str
    entity: str
    entity_id: str | None
    action: str


class Route(NamedTuple):
    pattern: re.Pattern
    #: A name, or ``(match, body) -> name``.
    entity: str | Callable[[re.Match, dict], str]
    action: str | Callable[[re.Match, dict], str]
    #: ``(conn, match, body, result) -> id`` overriding the default resolution.
    ident: Callable[..., Any] | None = None
    #: ``(body) -> bool``: the core already logs this mutation in its own history.
    logged: Callable[[dict], bool] | None = None


def _word(value, default: str) -> str:
    return value if isinstance(value, str) and _WORD.fullmatch(value) else default


def _body_action(default: str = "save"):
    return lambda match, body: _word(body.get("action"), default)


def _group(match, body) -> str:
    return match[1]


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _calendar_undone(conn, match, body, result):
    if conn is None or type(body.get("id")) is not int:
        return None
    row = conn.execute("SELECT entity, entity_id FROM calendar_history WHERE id = ?", (body["id"],)).fetchone()
    return (row[0], row[1]) if row else None


def _single_id(conn, match, body, result):
    ids = body.get("ids")
    return ids[0] if isinstance(ids, list) and len(ids) == 1 else None


def _route(pattern, entity, action, ident=None, logged=None) -> Route:
    return Route(re.compile(pattern), entity, action, ident, logged)


ROUTES = (
    _route(rf"/api/notes/pages/{ID}/blocks", "page", "outline"),
    _route(rf"/api/notes/pages/{ID}/organize", "page", "organize"),
    _route(rf"/api/notes/pages/{ID}/delete", "page", "delete"),
    _route(rf"/api/notes/pages/{ID}", "page", "save"),
    _route(r"/api/notes/pages", "page", "create"),
    _route(r"/api/notes/import", "page", "import"),
    _route(r"/api/notes/attachments", "attachment", "upload"),
    _route(r"/api/notes/reorder", lambda match, body: {"notebooks": "notebook", "folders": "folder"}.get(
        body.get("kind"), "page"), "reorder"),
    _route(rf"/api/notes/notebooks/{ID}", "notebook", "save"),
    _route(r"/api/notes/notebooks", "notebook", "create"),
    _route(rf"/api/notes/folders/{ID}/delete", "folder", "delete"),
    _route(rf"/api/notes/folders/{ID}", "folder", "save"),
    _route(r"/api/notes/folders", "folder", "create"),
    _route(rf"/api/documents/{ID}/note", "document", "to-note"),
    _route(rf"/api/documents/{ID}", "document", "save"),
    _route(r"/api/documents", "document", "create"),
    _route(r"/api/documents/import", "document", "import"),
    _route(r"/api/quick-notes/(create|save|delete)", "note", _group),
    _route(r"/api/flashcards/(deck|type|note)", _group, "save"),
    _route(r"/api/flashcards/action", lambda match, body: "note" if body.get("action") in {"delete", "archive"}
           else "card", _body_action()),
    _route(r"/api/flashcards/import", "deck", "import",
           ident=lambda conn, match, body, result: body.get("deck_id")),
    _route(r"/api/calendar/calendars/delete", "calendar", "delete"),
    _route(r"/api/calendar/calendars", "calendar", "save"),
    _route(r"/api/calendar/events/(?P<id>[A-Za-z0-9_-]{1,100})", "event", "save"),
    _route(r"/api/calendar/events", "event", "create"),
    _route(r"/api/calendar/tasks", "task", "save"),
    _route(r"/api/calendar/undo", "event", "undo", ident=_calendar_undone),
    _route(r"/api/calendar/import(-jobs)?", "event", "import"),
    _route(r"/api/study/(subjects|projects|targets|tasks|exams|plan)",
           lambda match, body: _singular(match[1]), _body_action()),
    _route(r"/api/study/sessions", "session", _body_action(), logged=lambda body: body.get("id") is None),
    _route(r"/api/study/guide", "guide", "save"),
    _route(r"/api/study/generate-plan", "plan", "generate"),
    _route(r"/api/schema/(create|import|add-node|save|delete|undo|import-jobs)", "map", _group),
    _route(r"/api/schema/backlinks/apply", "map", "link"),
    _route(r"/api/opportunities", "opportunity", "create"),
    _route(r"/api/opportunities/import", "opportunity", "import"),
    _route(r"/api/opportunities/favorites", "opportunity", "favorite"),
    _route(rf"/api/opportunities/{ID}(?:/(delete|project|items|project-details|calendar))?", "opportunity",
           lambda match, body: match[2] or "save"),
    _route(r"/api/jobs/add", "job", "create"),
    _route(r"/api/jobs/bulk", "job", _body_action(), ident=_single_id),
    _route(rf"/api/jobs/{ID}", "job", _body_action(), logged=lambda body: body.get("action") in {"status", "applied"}),
    _route(rf"/api/job-tools/{ID}", "job", _body_action(), logged=lambda body: body.get("action") == "undo"),
    _route(r"/api/reminders", "reminder", "create"),
    _route(rf"/api/reminders/{ID}/complete", "reminder", "complete"),
)


def _token(value) -> str | None:
    """An id worth storing: a positive integer or a short opaque token, never free text."""
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and _TOKEN.fullmatch(value):
        return value
    return None


def _result_id(result) -> str | None:
    if not isinstance(result, dict):
        return None
    found = _token(result.get("id"))
    if found:
        return found
    nested = [value for value in result.values() if isinstance(value, dict) and "id" in value]
    return _token(nested[0]["id"]) if len(nested) == 1 else None


def _shape(path: str, section: str) -> tuple[str, str | None, str]:
    """Entity, id and action of ``/api/<section>/<resource>/<id?>/<action?>``."""
    segments = [part for part in path.split("/")[3:] if part]
    index = next((i for i, part in enumerate(segments) if re.fullmatch(r"[0-9]+", part)), None)
    head = segments if index is None else segments[:index]
    entity = _singular(head[0]) if head else _singular(section)
    tail = segments[index + 1:] if index is not None else segments[1:]
    action = tail[-1] if tail else "save"
    return (_word(entity, "item"), segments[index] if index is not None else None, _word(action, "save"))


def classify(path: str, body: Any, result: Any, conn: sqlite3.Connection | None = None) -> Event | None:
    """The event a successful mutation of ``path`` records, or None when it is not captured."""
    parts = path.split("/")
    if len(parts) < 3 or parts[1] != "api" or parts[2] not in SECTION_PREFIXES:
        return None
    if any(pattern.fullmatch(path) for pattern in SKIP):
        return None
    if isinstance(result, dict) and "task" in result:
        return None  # background work owns its outcome
    body = body if isinstance(body, dict) else {}
    section = SECTION_PREFIXES[parts[2]]
    route = next(((route, match) for route in ROUTES if (match := route.pattern.fullmatch(path))), None)
    if route is None:
        entity, path_id, action = _shape(path, section)
        ident = path_id or _result_id(result) or _token(body.get("id"))
    else:
        route, match = route
        if route.logged and route.logged(body):
            return None
        entity = route.entity if isinstance(route.entity, str) else route.entity(match, body)
        action = route.action if isinstance(route.action, str) else route.action(match, body)
        path_id = match.groupdict().get("id")
        if route.ident:
            found = route.ident(conn, match, body, result)
            if isinstance(found, tuple):
                entity, found = found
            ident = _token(found)
        else:
            ident = path_id or _result_id(result) or _token(body.get("id"))
    if action == "save" and path_id is None and body.get("id") is None:
        action = "create"
    return Event(section, entity, ident, action)


def append(conn: sqlite3.Connection, event: Event, path: str) -> None:
    conn.execute("INSERT INTO record_events(at, section, entity, entity_id, action, route) VALUES (?, ?, ?, ?, ?, ?)",
                 (datetime.now(UTC).isoformat(timespec="milliseconds"), *event, path))


def capture(conn: sqlite3.Connection, path: str, body: Any, result: Any) -> None:
    """Record one successful mutation; failures are logged, never raised."""
    try:
        event = classify(path, body, result, conn)
        if event is not None:
            append(conn, event, path)
    except Exception:
        log.warning("Browser edit was not recorded", exc_info=True)

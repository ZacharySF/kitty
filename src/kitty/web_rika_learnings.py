"""Rika's career memory over HTTP (kitty.core.rika.learnings), on the Rika page's Memory tab.

GET  /api/rika/learnings               ->  {learnings, jobs, refreshed_at, forgotten}  refreshed first
POST /api/rika/learnings {text}        ->  {learning}  a note; 409 for a duplicate or past the cap
POST /api/rika/learnings/<id>/pin {pinned: bool}  ->  {learning}
POST /api/rika/learnings/<id>/forget {}           ->  {forgotten: id}  for good

The front (rika-server) does not claim these paths (server/src/rika/mod.rs `route`), so
it forwards them here. Unknown fields, wrong types and unknown ids are refused.
"""
from __future__ import annotations

import re

from kitty.core.rika import learnings

PREFIX = "/api/rika/learnings"
_ITEM = re.compile(re.escape(PREFIX) + r"/([1-9][0-9]{0,17})/(pin|forget)", re.ASCII)


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def _keys(body: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if set(body) - allowed or required - set(body):
        raise _error(400, "Unexpected or missing memory request fields")


def _call(function, *args):
    try:
        return function(*args)
    except learnings.Refused as exc:
        raise _error(409, str(exc)) from None
    except ValueError as exc:
        raise _error(400, str(exc)) from None
    except LookupError as exc:
        raise _error(404, str(exc)) from None


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    if query:
        raise _error(400, "This route does not accept query parameters")
    learnings.refresh(conn, app.config.resume_terms, modules=app.modules)
    return learnings.overview(conn)


def post(app, conn, path, body):
    if path == PREFIX:
        _keys(body, {"text"}, {"text"})
        return {"learning": _call(learnings.add_note, conn, body["text"])}
    match = _ITEM.fullmatch(path)
    if match is None:
        return None
    ident = int(match[1])
    if match[2] == "pin":
        _keys(body, {"pinned"}, {"pinned"})
        return {"learning": _call(learnings.pin, conn, ident, body["pinned"])}
    _keys(body, set())
    return {"forgotten": _call(learnings.forget, conn, ident)}

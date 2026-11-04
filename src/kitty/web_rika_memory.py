"""Rika's routing memory (design Q13), memory index and preferences (Q18) over HTTP.

GET  /api/rika/routes  ->  {routes: [Route], summary}
    Routes most used first (shape in kitty.core.rika.routes.listing); the summary has
    today's requests, tokens and cost, the cached share of input tokens and the average
    cost per request over the last 14 local days.
POST /api/rika/routes/forget {signature}  ->  the GET shape (404 unknown signature)
GET  /api/rika/memory[?q=<text>]  ->  {preferences, index: {sources, total}, results, notes_shared}
    With q the index is refreshed first and results are the matches, best first.
POST /api/rika/preferences {text}  ->  {preference}
POST /api/rika/preferences/<id> {text}  ->  {preference}
POST /api/rika/preferences/<id>/delete {}  ->  {deleted: id}
POST /api/rika/memory/rebuild {}  ->  {index}

Writes run under the application's mutation lock (the web server holds it for every POST).
"""
from __future__ import annotations

import re

from kitty.core.rika import memory, routes
from kitty.core.rika.workspace_tools import _allow_ai

PREFIX = "/api/rika"
QUERY_MAX = 300
_ID = r"(\d{1,18})"


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def _keys(body: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if set(body) - allowed or required - set(body):
        raise _error(400, "Unexpected or missing Rika request fields")


def _routes(conn) -> dict:
    return {"routes": routes.listing(conn), "summary": routes.summary(conn)}


def _memory(app, conn, query: str | None) -> dict:
    results = []
    if query is not None and query.strip():
        memory.refresh(conn, modules=app.modules)
        results = memory.search(conn, query)
    return {"preferences": memory.preferences(conn), "index": memory.index(conn), "results": results,
            "notes_shared": _allow_ai(conn)}


def get(app, conn, path, query):
    if path == PREFIX + "/routes":
        app._no_query(query)
        return _routes(conn)
    if path == PREFIX + "/memory":
        if set(query) - {"q"}:
            raise _error(400, "Memory accepts only q")
        text = query.get("q")
        if text is not None and (len(text) > QUERY_MAX or "\x00" in text):
            raise _error(400, f"Search for at most {QUERY_MAX} characters")
        return _memory(app, conn, text)
    return None


def _preference(call, *args):
    try:
        return call(*args)
    except LookupError as exc:
        raise _error(404, str(exc)) from None
    except ValueError as exc:
        raise _error(400, str(exc)) from None


def post(app, conn, path, body):
    if not path.startswith(PREFIX + "/"):
        return None
    if path == PREFIX + "/routes/forget":
        _keys(body, {"signature"}, {"signature"})
        if not isinstance(body["signature"], str) or len(body["signature"]) > 2000:
            raise _error(400, "signature must be a route's signature")
        try:
            routes.forget(conn, body["signature"])
        except LookupError as exc:
            raise _error(404, str(exc)) from None
        return _routes(conn)
    if path == PREFIX + "/preferences":
        _keys(body, {"text"}, {"text"})
        return {"preference": _preference(lambda text: memory.add_preference(conn, text, source="added"), body["text"])}
    match = re.fullmatch(PREFIX + "/preferences/" + _ID, path)
    if match:
        _keys(body, {"text"}, {"text"})
        return {"preference": _preference(memory.update_preference, conn, int(match[1]), body["text"])}
    match = re.fullmatch(PREFIX + "/preferences/" + _ID + "/delete", path)
    if match:
        _keys(body, set())
        return {"deleted": _preference(memory.delete_preference, conn, int(match[1]))}
    if path == PREFIX + "/memory/rebuild":
        _keys(body, set())
        return {"index": memory.rebuild(conn, modules=app.modules)}
    return None

"""Always-on core: search over the full-text index (kitty.core.search) and its scheduled check.

GET /api/search?q=<text>[&limit=1-100][&kinds=note_page,job,...]
    ``{query, hits: [{kind, id, title, title_marks, snippet, view, match}], total, truncated}``.
    ``title_marks`` and ``snippet`` are ``[[text, is_hit], ...]`` segments. Only the
    kinds of the active modules are searched (jobs and ApplyAI calendar copies need
    ApplyAI; everything else needs the workspace module).

The oracle (``search.check``: rebuild from snapshots and the log, compare, repair and log
any difference) runs ``CHECK_DELAY_SECONDS`` after start and every ``CHECK_EVERY_SECONDS``,
like compaction (kitty.web_blocks); writes hold the server's mutation lock.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing

from kitty.core import search
from kitty.db import connect

CHECK_DELAY_SECONDS = 5 * 60
CHECK_EVERY_SECONDS = 6 * 3600


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    threading.Thread(target=_check_forever, args=(app,), name="kitty-search-index-check", daemon=True).start()


def _check_forever(app) -> None:
    if app.streams_closed.wait(CHECK_DELAY_SECONDS):
        return
    while True:
        try:
            with closing(connect(app.settings.db_path)) as conn:
                search.check(conn, lock=app.mutation_lock)
        except sqlite3.OperationalError:
            pass  # Busy or locked: the next run retries.
        if app.streams_closed.wait(CHECK_EVERY_SECONDS):
            return


def get(app, conn, path, query):
    if path != "/api/search":
        return None
    if set(query) - {"q", "limit", "kinds"} or "q" not in query:
        raise _error(400, "Search takes q and optional limit and kinds")
    limit = query.get("limit", "20")
    if not re.fullmatch(r"[1-9][0-9]{0,2}", limit):
        raise _error(400, f"Limit must be 1-{search.MAX_LIMIT}")
    kinds = query["kinds"].split(",") if "kinds" in query else None
    try:
        return search.search(conn, query["q"], limit=int(limit), kinds=kinds, modules=app.modules)
    except ValueError as exc:
        raise _error(400, str(exc)) from None


def post(app, conn, path, body):
    return None

"""Always-on core: the search index's scheduled check (kitty.core.search).

``GET /api/search`` is answered by rika-server (server/src/search); this worker keeps only
the oracle (``search.check``: rebuild from snapshots and the log, compare, repair and log any
difference), which runs ``CHECK_DELAY_SECONDS`` after start and every ``CHECK_EVERY_SECONDS``,
like compaction (kitty.web_blocks); writes hold the server's mutation lock.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing

from kitty.core import search
from kitty.db import connect

CHECK_DELAY_SECONDS = 5 * 60
CHECK_EVERY_SECONDS = 6 * 3600


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
    return None


def post(app, conn, path, body):
    return None

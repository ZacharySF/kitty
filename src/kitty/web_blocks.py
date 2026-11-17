"""Workspace module: the block history's compaction, on the worker's scheduler.

The block routes (pages, history, Trash, agent sessions, citations and the block API's
reads and writes under ``/api/blocks``) and the live channel's block transactions are
answered by rika-server (server/src/blocks/). The block engine they share,
kitty.core.blocks, stays here for its other callers: Rika's tools and the MCP server
(kitty.core.agent_access), ``kitty notes``, the Notes, Documents and tag routes, captures,
peer merges and this compaction.

Compaction (the build's history retention window, 30 days by default, then daily
snapshots; expired Trash purged) runs a minute after start and every six hours.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing

from kitty.core.blocks import history
from kitty.db import connect

COMPACT_DELAY_SECONDS = 60
COMPACT_EVERY_SECONDS = 6 * 3600


def start(app) -> None:
    threading.Thread(target=_compact_forever, args=(app,), name="kitty-blocks-compaction", daemon=True).start()


def _compact_forever(app) -> None:
    """Fold history older than the retention window and purge expired Trash, now and then."""
    if app.streams_closed.wait(COMPACT_DELAY_SECONDS):
        return
    while True:
        try:
            with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
                history.compact(conn)
            app.live.nudge()
        except sqlite3.OperationalError:
            pass  # Busy or locked: the next run retries.
        if app.streams_closed.wait(COMPACT_EVERY_SECONDS):
            return


def get(app, conn, path, query):
    """The front answers every block route; nothing reaches the worker."""
    return None


def post(app, conn, path, body):
    return None

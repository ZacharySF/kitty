"""Workspace module: block pages, history and Trash over HTTP.

Block transactions travel over the core live channel (kitty.web_live): a window sends
``txn`` messages there and every committed change is pushed to every window.

Pages, history and Trash (kitty.core.blocks.history); a page is ``page=<block id>`` or
``type=page|document|quick_note&number=<n>``:

GET  /api/blocks/page?<page>                    what the block editor loads: root, live blocks
                                                in reading order, revision, the ``seq`` it reflects,
                                                and tag ``suggestions``/``tags`` (names) for its blocks
GET  /api/blocks/history?<page>                 editing sessions and daily snapshots
GET  /api/blocks/history/preview?<page>&seq=<n>  the page as it was after transaction n
POST /api/blocks/history/restore  {<page>, "seq"}  appends a transaction restoring it
GET  /api/blocks/trash                           blocks in Trash, newest first
POST /api/blocks/trash/restore    {"id"}         brings one back

Compaction (the build's history retention window, 30 days by default, then daily
snapshots; expired Trash purged) runs a minute after start and every six hours.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing

from kitty.core import blocks
from kitty.core.blocks import history, tagging
from kitty.db import connect
from kitty.db.connection import transaction

COMPACT_DELAY_SECONDS = 60
COMPACT_EVERY_SECONDS = 6 * 3600
ROOT_TYPES = frozenset({"page", "document", "quick_note"})


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


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


def _page(conn, values: dict) -> str:
    """The page root named by ``page=<block id>`` or ``type`` plus ``number`` (Trash included)."""
    if "page" in values:
        return blocks.block_id(values["page"])
    kind, number = values.get("type"), values.get("number")
    if isinstance(number, str) and re.fullmatch(r"[1-9][0-9]{0,15}", number):
        number = int(number)
    if kind not in ROOT_TYPES or type(number) is not int:
        raise blocks.BlockError("Name the page as page=<block id> or type=page|document|quick_note with its number")
    row = conn.execute("SELECT id FROM blocks WHERE type=? AND parent_id IS NULL AND json_extract(properties,'$.number')=?",
                       (kind, number)).fetchone()
    if row is None:
        raise blocks.BlockError("Page not found", 404)
    return row["id"]


def _sequence(value) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,18}", value):
        value = int(value)
    if type(value) is not int:
        raise blocks.BlockError("Choose a sequence number from the page's history")
    return value


def get(app, conn, path, query):
    try:
        if path == "/api/blocks/page":
            if set(query) - {"page", "type", "number"}:
                raise blocks.BlockError("Unexpected page parameters")
            with transaction(conn):
                state = blocks.page_state(conn, _page(conn, query))
                state.update(tagging.page_suggestions(conn, state))
            return state
        if path == "/api/blocks/history":
            if set(query) - {"page", "type", "number"}:
                raise blocks.BlockError("Unexpected history parameters")
            return history.page_history(conn, _page(conn, query))
        if path == "/api/blocks/history/preview":
            if set(query) - {"page", "type", "number", "seq"}:
                raise blocks.BlockError("Unexpected preview parameters")
            return history.preview(conn, _page(conn, query), _sequence(query.get("seq")))
        if path == "/api/blocks/trash":
            if query:
                raise blocks.BlockError("Trash takes no parameters")
            return history.trash(conn)
    except blocks.BlockError as exc:
        raise _error(exc.status, str(exc)) from None
    return None


def post(app, conn, path, body):
    try:
        if path == "/api/blocks/history/restore":
            if set(body) - {"page", "type", "number", "seq"}:
                raise blocks.BlockError("Unexpected restore fields")
            return history.restore(conn, _page(conn, body), _sequence(body.get("seq")))
        if path == "/api/blocks/trash/restore":
            if set(body) != {"id"}:
                raise blocks.BlockError("Restore needs the block id from Trash")
            return history.restore_from_trash(conn, body["id"])
        return None
    except blocks.BlockError as exc:
        raise _error(exc.status, str(exc)) from None



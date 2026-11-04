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

The block API (kitty.core.block_api; what the MCP notes tools and ``kitty notes`` use).
Reads report the ``seq`` they reflect and cite blocks as ``((id@seq))``; writes are one
block transaction each and return ``{txn_id, page_id, seq, revision, pages, blocks}``.
``revision`` (optional) refuses a write to a page edited since (409). A page is a root
block ID or a note page number; ``blocks`` is ``[{type?, text?, props?, children?} | text]``:

GET  /api/blocks/pages?q=&kind=&limit=           note pages, documents and quick notes
GET  /api/blocks/search?q=&limit=                blocks matching in the full-text index
GET  /api/blocks/backlinks?id=<block|page>       references, [[links]], mentions, tagged blocks
POST /api/blocks/pages/create  {title, notebook?, blocks?}
POST /api/blocks/insert  {blocks, page? | parent? | after? | before?, revision?}
POST /api/blocks/update  {id, text?, type?, props?, revision?}
POST /api/blocks/move    {id, page? | parent? | after? | before?, revision?}
POST /api/blocks/delete  {id, revision?}           to Trash
POST /api/blocks/inbox   {blocks}                  appended to the Inbox page

Quick notes (``/api/quick-notes/create``) and tags (``/api/tags``, ``/api/tags/attach``)
keep their own routes.

Compaction (the build's history retention window, 30 days by default, then daily
snapshots; expired Trash purged) runs a minute after start and every six hours.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing

from kitty.core import block_api, blocks
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


def _limit(query: dict, default: int) -> int:
    value = query.get("limit", str(default))
    if not re.fullmatch(r"[1-9][0-9]{0,2}", value):
        raise blocks.BlockError(f"Limit must be 1-{block_api.MAX_LIST}")
    return int(value)


def _fields(body: dict, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if set(body) - allowed or required - set(body):
        raise blocks.BlockError(f"Expected fields: {', '.join(sorted(allowed))}")
    return body


_PLACE = {"page", "parent", "after", "before", "revision"}



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
        if path == "/api/blocks/pages":
            _fields(query, {"q", "kind", "limit"})
            return block_api.list_pages(conn, query.get("q", ""), kind=query.get("kind"), limit=_limit(query, 50))
        if path == "/api/blocks/search":
            _fields(query, {"q", "limit"}, {"q"})
            return block_api.search_blocks(conn, query["q"], limit=_limit(query, 20))
        if path == "/api/blocks/backlinks":
            _fields(query, {"id"}, {"id"})
            return block_api.backlinks(conn, query["id"])
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
        if path == "/api/blocks/pages/create":
            _fields(body, {"title", "notebook", "blocks"}, {"title"})
            return block_api.create_page(conn, body["title"], notebook=body.get("notebook"), blocks=body.get("blocks"))
        if path == "/api/blocks/insert":
            _fields(body, {"blocks"} | _PLACE, {"blocks"})
            return block_api.insert_blocks(conn, body["blocks"], **{key: body.get(key) for key in _PLACE})
        if path == "/api/blocks/update":
            _fields(body, {"id", "text", "type", "props", "revision"}, {"id"})
            return block_api.update_block(conn, body["id"], text=body.get("text"), kind=body.get("type"),
                                          props=body.get("props"), revision=body.get("revision"))
        if path == "/api/blocks/move":
            _fields(body, {"id"} | _PLACE, {"id"})
            return block_api.move_block(conn, body["id"], **{key: body.get(key) for key in _PLACE})
        if path == "/api/blocks/delete":
            _fields(body, {"id", "revision"}, {"id"})
            return block_api.delete_block(conn, body["id"], revision=body.get("revision"))
        if path == "/api/blocks/inbox":
            _fields(body, {"blocks"}, {"blocks"})
            return block_api.add_to_inbox(conn, body["blocks"])
        return None
    except blocks.BlockError as exc:
        raise _error(exc.status, str(exc)) from None



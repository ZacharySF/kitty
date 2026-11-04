"""What a paired client can do (platform design Q7): capture into rika! and read titles.

- ``capture``: a quick note (`note`), text-flow blocks appended to the Inbox page
  (`append`), or a snapshot of the web page the user is reading (`mirror`).
- ``read``: recent page titles and open task titles (`recent`) for an inbox view.

Every write is one block transaction (`blocks.transact`), so it lands in the op log
and is pushed live to every open window. The Inbox page is a note page flagged
``inbox`` in the "Inbox" notebook; it is created on the first capture.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any
from urllib.parse import urlsplit

from kitty.core import blocks, notes, quick_notes
from kitty.core.clients import ClientError, now
from kitty.db.connection import transaction

INBOX_TITLE = "Inbox"
#: Block types a client may append to the Inbox page.
CAPTURE_TYPES = frozenset({"text", "heading_1", "heading_2", "heading_3", "to_do", "bulleted_list", "numbered_list",
                           "quote", "callout", "code"})
MAX_BLOCKS = 100
MAX_TEXT = 20_000
MAX_PAGE_TEXT = 200_000
MAX_PARAGRAPHS = 2_000
MAX_URL = 2_048
MAX_TITLE = 300


def _keys(body: Any, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(body, dict) or set(body) - allowed or required - set(body):
        raise ClientError(f"Expected fields: {', '.join(sorted(allowed))}")
    return body


def _text(value: Any, label: str, maximum: int = MAX_TEXT, *, required: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise ClientError(f"{label} must be text of at most {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ClientError(f"{label} must be valid Unicode") from None
    if required and not value.strip():
        raise ClientError(f"{label} is required")
    return value


def _apply(conn: sqlite3.Connection, ops: list[dict]) -> None:
    try:
        blocks.transact(conn, ops)
    except blocks.BlockError as exc:
        raise ClientError(str(exc), exc.status) from None


def note(conn: sqlite3.Connection, body: Any) -> dict:
    """A new quick note holding the captured text."""
    body = _keys(body, {"text", "tint"}, {"text"})
    text = _text(body["text"], "Note text", quick_notes.MAX_BODY, required=True)
    try:
        return quick_notes.create(conn, text, body.get("tint", "rose"))
    except quick_notes.QuickNoteError as exc:
        raise ClientError(str(exc), exc.status) from None


def _inbox(conn: sqlite3.Connection) -> tuple[str, int, list[dict]]:
    """The Inbox page's block ID and number, plus the operations that create it when missing."""
    row = conn.execute("SELECT id, json_extract(properties,'$.number') AS number FROM blocks WHERE type='page' "
                       "AND parent_id IS NULL AND deleted_at IS NULL AND json_extract(properties,'$.inbox')=1 "
                       "ORDER BY number LIMIT 1").fetchone()
    if row is not None:
        return row["id"], row["number"], []
    if notes.page_count(conn) >= notes.MAX_PAGES:
        raise ClientError("Page limit reached; rika! cannot create the Inbox page", 409)
    book = conn.execute("SELECT id FROM note_notebooks WHERE title=? ORDER BY id LIMIT 1", (INBOX_TITLE,)).fetchone()
    notebook = book["id"] if book else notes.create_notebook(conn, INBOX_TITLE)["id"]
    number, ops = notes.new_page_ops(conn, notebook, INBOX_TITLE, extra={"inbox": True})
    return ops[0]["id"], number, ops


def _insert(parent: str, kind: str, text: str, props: dict | None = None) -> dict:
    op = {"op": "insert", "id": str(uuid.uuid4()), "type": kind, "parent": parent, "text": text}
    if props:
        op["props"] = props
    return op


def append(conn: sqlite3.Connection, body: Any) -> dict:
    """Append ``blocks: [{type?, text}]`` to the end of the Inbox page in one transaction."""
    items = _keys(body, {"blocks"}, {"blocks"})["blocks"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BLOCKS:
        raise ClientError(f"Send 1–{MAX_BLOCKS} blocks")
    specs = []
    for item in items:
        item = _keys(item, {"type", "text"}, {"text"})
        kind = item.get("type", "text")
        if kind not in CAPTURE_TYPES:
            raise ClientError(f"Block type must be one of: {', '.join(sorted(CAPTURE_TYPES))}")
        specs.append((kind, _text(item["text"], "Block text")))
    with transaction(conn, immediate=True):
        page_id, number, ops = _inbox(conn)
        added = [_insert(page_id, kind, text) for kind, text in specs]
        _apply(conn, ops + added)
    return {"page": number, "blocks": [op["id"] for op in added]}


def _url(value: Any) -> str:
    url = _text(value, "URL", MAX_URL, required=True).strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ClientError("URL must be an http or https address") from None
    if parts.scheme not in {"http", "https"} or not parts.netloc or any(char.isspace() for char in url):
        raise ClientError("URL must be an http or https address")
    return url


def mirror(conn: sqlite3.Connection, body: Any) -> dict:
    """Append a snapshot of a web page to the Inbox: its linked title, the selection and the readable text.

    The snapshot is a callout titled with the page (linked, ``source_url`` and
    ``captured_at`` properties); the selection becomes a quote and the readable text a
    collapsed "Page text" toggle with one paragraph per block.
    """
    body = _keys(body, {"url", "title", "text", "selection"}, {"url", "title"})
    url = _url(body["url"])
    title = " ".join(_text(body["title"], "Title", MAX_TITLE, required=True).split())
    selection = _text(body.get("selection", ""), "Selection").strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", _text(body.get("text", ""), "Page text", MAX_PAGE_TEXT))]
    paragraphs = [part for part in paragraphs if part]
    if len(paragraphs) > MAX_PARAGRAPHS:
        raise ClientError(f"Page text is limited to {MAX_PARAGRAPHS} paragraphs")
    if any(len(part) > MAX_TEXT for part in paragraphs):
        raise ClientError(f"Each page text paragraph is limited to {MAX_TEXT} characters")
    with transaction(conn, immediate=True):
        page_id, number, ops = _inbox(conn)
        length = len(title.encode("utf-16-le")) // 2
        head = _insert(page_id, "callout", title, {"source_url": url, "captured_at": now(),
                                                   "marks": [{"s": 0, "e": length, "t": "a", "href": url}]})
        added = [head]
        if selection:
            added.append(_insert(head["id"], "quote", selection))
        if paragraphs:
            fold = _insert(head["id"], "toggle", "Page text", {"collapsed": True})
            added += [fold, *(_insert(fold["id"], "text", part) for part in paragraphs)]
        _apply(conn, ops + added)
    return {"page": number, "block": head["id"]}


def recent(conn: sqlite3.Connection, limit: int = 20) -> dict:
    """Titles for an inbox view: recently edited pages and open tasks, newest first."""
    pages = [{"number": row["id"], "title": row["title"], "updated_at": row["updated_at"]} for row in conn.execute(
        "SELECT id, title, updated_at FROM note_page_index ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))]
    tasks = []
    for row in conn.execute("SELECT id, data FROM calendar_tasks WHERE deleted=0 ORDER BY updated_at DESC"):
        data = json.loads(row["data"])
        if data.get("status") != "done":
            tasks.append({"id": row["id"], "title": data.get("title", ""), "due": data.get("due") or None})
            if len(tasks) >= limit:
                break
    inbox = conn.execute("SELECT json_extract(properties,'$.number') FROM blocks WHERE type='page' AND parent_id IS NULL "
                         "AND deleted_at IS NULL AND json_extract(properties,'$.inbox')=1 ORDER BY 1 LIMIT 1").fetchone()
    return {"inbox": inbox[0] if inbox else None, "pages": pages, "tasks": tasks}

"""Quick notes: plain-text scratch notes; stale revisions never overwrite edits.

Each quick note is a ``quick_note`` root block (its text is the body) addressed by
its integer ``number``; every change is a block transaction.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import nullcontext
from typing import Any

from kitty.core import blocks
from kitty.db.connection import transaction

TINTS = ("rose", "lilac", "sky", "mint", "honey", "frost")
MAX_BODY = 64 * 1024
_SELECT = "SELECT * FROM blocks WHERE type='quick_note' AND parent_id IS NULL AND deleted_at IS NULL"


class QuickNoteError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _id(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 2**53 - 1:
        raise QuickNoteError("Expected a quick note ID")
    return value


def _body(value: Any) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise QuickNoteError("Quick note text must be text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise QuickNoteError("Quick note text must be valid Unicode") from None
    if size > MAX_BODY:
        raise QuickNoteError("Quick notes are limited to 64 KiB")
    return value


def _tint(value: Any) -> str:
    if value not in TINTS:
        raise QuickNoteError(f"Tint must be one of {', '.join(TINTS)}")
    return value


def _row(row: sqlite3.Row) -> dict:
    props = json.loads(row["properties"])
    return {"id": props["number"], "body": row["text"], "tint": props["tint"], "pinned": bool(props.get("pinned")),
            "revision": props["revision"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _apply(conn: sqlite3.Connection, ops: list[dict]) -> None:
    try:
        blocks.transact(conn, ops)
    except blocks.BlockError as exc:
        raise QuickNoteError(str(exc), exc.status) from None


def list_notes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(f"{_SELECT} ORDER BY json_extract(properties,'$.pinned') DESC, updated_at DESC, "
                        "json_extract(properties,'$.number') DESC")
    return [_row(row) for row in rows]


def _block(conn: sqlite3.Connection, note_id: Any) -> sqlite3.Row:
    row = conn.execute(f"{_SELECT} AND json_extract(properties,'$.number')=?", (_id(note_id),)).fetchone()
    if row is None:
        raise QuickNoteError("Quick note not found", 404)
    return row


def get(conn: sqlite3.Connection, note_id: Any) -> dict:
    return _row(_block(conn, note_id))


def create(conn: sqlite3.Connection, body: Any = "", tint: Any = "rose") -> dict:
    text, color = _body(body), _tint(tint)
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        number = conn.execute("SELECT COALESCE(MAX(json_extract(properties,'$.number')),0)+1 FROM blocks "
                              "WHERE type='quick_note' AND parent_id IS NULL").fetchone()[0]
        _apply(conn, [{"op": "insert", "id": str(uuid.uuid4()), "type": "quick_note", "parent": None, "text": text,
                       "props": {"number": number, "tint": color, "pinned": False, "revision": 1}}])
    return get(conn, number)


def _current(conn: sqlite3.Connection, note_id: Any, revision: Any) -> sqlite3.Row:
    row = _block(conn, note_id)
    if type(revision) is not int or json.loads(row["properties"])["revision"] != revision:
        raise QuickNoteError("This quick note changed in another window; reload it before saving.", 409)
    return row


def save(conn: sqlite3.Connection, note_id: Any, revision: Any, *, body: Any = None,
         tint: Any = None, pinned: Any = None) -> dict:
    text = None if body is None else _body(body)
    color = None if tint is None else _tint(tint)
    if pinned is not None and type(pinned) is not bool:
        raise QuickNoteError("Pinned must be true or false")
    with transaction(conn, immediate=True):
        row = _current(conn, note_id, revision)
        props = {"revision": revision + 1}
        if color is not None:
            props["tint"] = color
        if pinned is not None:
            props["pinned"] = pinned
        ops = [{"op": "set", "id": row["id"], "props": props}]
        if text is not None and text != row["text"]:
            ops.insert(0, {"op": "text", "id": row["id"], "text": text})
        _apply(conn, ops)
    return get(conn, note_id)


def delete(conn: sqlite3.Connection, note_id: Any, revision: Any) -> None:
    """Move a quick note to Trash."""
    with transaction(conn, immediate=True):
        row = _current(conn, note_id, revision)
        _apply(conn, [{"op": "delete", "id": row["id"]}])

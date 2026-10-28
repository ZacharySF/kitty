"""Quick notes: plain-text scratch notes; stale revisions never overwrite edits."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from kitty.db.connection import transaction

TINTS = ("rose", "lilac", "sky", "mint", "honey", "frost")
MAX_BODY = 64 * 1024


class QuickNoteError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    note = dict(row)
    note["pinned"] = bool(note["pinned"])
    return note


def list_notes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM quick_notes ORDER BY pinned DESC, updated_at DESC, id DESC")
    return [_row(row) for row in rows]


def get(conn: sqlite3.Connection, note_id: Any) -> dict:
    row = conn.execute("SELECT * FROM quick_notes WHERE id=?", (_id(note_id),)).fetchone()
    if row is None:
        raise QuickNoteError("Quick note not found", 404)
    return _row(row)


def create(conn: sqlite3.Connection, body: Any = "", tint: Any = "rose") -> dict:
    text, color, stamp = _body(body), _tint(tint), _now()
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO quick_notes(body, tint, created_at, updated_at) VALUES (?,?,?,?)",
            (text, color, stamp, stamp),
        )
    return get(conn, cursor.lastrowid)


def _current(conn: sqlite3.Connection, note_id: Any, revision: Any) -> dict:
    note = get(conn, note_id)
    if type(revision) is not int or note["revision"] != revision:
        raise QuickNoteError("This quick note changed in another window; reload it before saving.", 409)
    return note


def save(conn: sqlite3.Connection, note_id: Any, revision: Any, *, body: Any = None,
         tint: Any = None, pinned: Any = None) -> dict:
    text = None if body is None else _body(body)
    color = None if tint is None else _tint(tint)
    if pinned is not None and type(pinned) is not bool:
        raise QuickNoteError("Pinned must be true or false")
    with transaction(conn, immediate=True):
        note = _current(conn, note_id, revision)
        conn.execute(
            "UPDATE quick_notes SET body=?, tint=?, pinned=?, revision=revision+1, updated_at=? WHERE id=?",
            (note["body"] if text is None else text, color or note["tint"],
             int(note["pinned"] if pinned is None else pinned), _now(), note["id"]),
        )
    return get(conn, note["id"])


def delete(conn: sqlite3.Connection, note_id: Any, revision: Any) -> None:
    with transaction(conn, immediate=True):
        note = _current(conn, note_id, revision)
        conn.execute("DELETE FROM quick_notes WHERE id=?", (note["id"],))

"""The one-way migration of existing notes into blocks (run by migration 033).

Before converting anything it writes a full JSON export of every legacy notes
table next to the database (``exports/notes-before-blocks-<time>.json``, owner-only).
Each note page, document and quick note then becomes one root block written by
one transaction, so the operation log starts with the complete history of every
page. The legacy tables are kept, read-only, for one release.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from kitty.core.blocks import convert, store
from kitty.core.blocks.convert import insert_ops
from kitty.core.blocks.order import keys_between

TABLES = ("note_folders", "note_notebooks", "note_pages", "note_blocks", "note_block_refs", "note_block_topics",
          "note_daily", "note_attachments", "documents", "document_originals", "quick_notes", "note_preferences")
CONTENT = ("note_pages", "documents", "quick_notes")


def export_legacy(conn: sqlite3.Connection) -> dict:
    """Every row of every legacy notes table, exactly as stored."""
    return {"format": "kitty-notes-export", "version": 1, "exported_at": store.now(),
            "tables": {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in TABLES}}


def write_export(conn: sqlite3.Connection, data: dict) -> Path | None:
    """Durably write `data` beside the database file; None for an in-memory database."""
    database = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not database:
        return None
    folder = Path(database).parent / "exports"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"notes-before-blocks-{stamp}.json"
    temporary = target.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    directory = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return target


def _root(page_id: str, kind: str, text: str, props: dict, created: str, updated: str) -> dict:
    return {"op": "insert", "id": page_id, "type": kind, "parent": None, "key": "a0", "props": props, "text": text,
            "created": created, "updated": updated}


def page_ops(page_id: str, row: dict, outline: list[dict], daily: str | None) -> list[dict]:
    """The transaction that recreates one legacy note page as blocks."""
    document = json.loads(row["document"])
    props = {"number": row["id"], "notebook_id": row["notebook_id"], "revision": row["revision"],
             "viewport": json.loads(row["viewport"]), "viewport_revision": row["viewport_revision"],
             "favorite": row["favorite"], "position": row["position"]}
    if row["last_opened"] is not None:
        props["last_opened"] = row["last_opened"]
    if daily:
        props["daily"] = daily
    created, updated = row["created_at"], row["updated_at"]
    canvas_id = convert.canvas_block_id(page_id)
    try:
        elements = convert.canvas_specs(page_id, document)
        settings = convert.canvas_settings(document)
    except (KeyError, TypeError, AttributeError):
        # A document older code wrote in a shape the converter does not know is kept verbatim.
        elements, settings = [], {"raw": document}
    ops = [_root(page_id, "page", row["title"], props, created, updated),
           {"op": "insert", "id": canvas_id, "type": "canvas", "parent": page_id, "key": "a0", "props": settings,
            "text": "", "created": created, "updated": updated}]
    items = [{"id": item["id"], "parent_id": item["parent_id"], "text": item["text"], "ordering": item["ordering"],
              "collapsed": bool(item["collapsed"])} for item in outline]
    specs = convert.outline_specs(page_id, items)
    keys = iter(keys_between("a0", None, sum(1 for spec in specs if spec["parent"] == page_id)))
    for op in insert_ops(specs, created, updated):
        if op["parent"] == page_id:
            op["key"] = next(keys)
        ops.append(op)
    ops.extend(insert_ops(elements, created, updated))
    return ops


def convert_pages(conn: sqlite3.Connection) -> None:
    daily = {row["page_id"]: row["date"] for row in conn.execute("SELECT date,page_id FROM note_daily")}
    for row in conn.execute("SELECT * FROM note_pages ORDER BY id").fetchall():
        outline = conn.execute("SELECT * FROM note_blocks WHERE page_id=? ORDER BY ordering,id", (row["id"],)).fetchall()
        store.transact(conn, page_ops(str(uuid.uuid4()), dict(row), [dict(item) for item in outline], daily.get(row["id"])))


def convert_documents(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT * FROM documents ORDER BY id").fetchall():
        page_id = str(uuid.uuid4())
        props = {"number": row["id"], "format": row["format"], "notebook_id": row["notebook_id"], "revision": row["revision"]}
        specs = [{"id": str(uuid.uuid4()), "parent": page_id, "type": chunk["type"], "props": {"sep": chunk["sep"]}, "text": chunk["text"]}
                 for chunk in convert.split_document(row["content"], row["format"] == "markdown")]
        store.transact(conn, [_root(page_id, "document", row["title"], props, row["created_at"], row["updated_at"]),
                              *insert_ops(specs, row["created_at"], row["updated_at"])])


def convert_quick_notes(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT * FROM quick_notes ORDER BY id").fetchall():
        props = {"number": row["id"], "tint": row["tint"], "pinned": bool(row["pinned"]), "revision": row["revision"]}
        store.transact(conn, [_root(str(uuid.uuid4()), "quick_note", row["body"], props, row["created_at"], row["updated_at"])])


def migrate_notes(conn: sqlite3.Connection) -> None:
    """Export, then convert every note page, document and quick note into blocks."""
    if any(conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in CONTENT):
        write_export(conn, export_legacy(conn))
    convert_pages(conn)
    convert_documents(conn)
    convert_quick_notes(conn)

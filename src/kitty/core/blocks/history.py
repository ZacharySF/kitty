"""Version history, point-in-time restore, compaction and Trash (Q15, Q20).

Every transaction stays in ``op_log`` for the retention window (30 days), so any
moment in it can be previewed and restored exactly. ``compact`` folds older rows
into one snapshot per page per day (``page_snapshots``), then deletes them below a
horizon that only moves forward (the op_log trigger refuses any other delete).
Restoring never rewrites the past: it appends a transaction that turns the
current page into the chosen version. Trash is every block with ``deleted_at``;
it follows the same window and is purged when it expires.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from kitty.core.blocks.convert import props_diff
from kitty.core.blocks.pagedoc import BlockError, OrderedPageDoc, block_id
from kitty.core.blocks.store import load_page, replay, transact
from kitty.db.connection import transaction

#: Every transaction is kept this long (Q20); a setting of the build file once builds exist (Q8).
RETENTION_DAYS = 30
SESSION_GAP = timedelta(minutes=10)


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _known(conn: sqlite3.Connection, page_id: str) -> None:
    if not conn.execute("SELECT 1 FROM op_log WHERE page_id=? UNION ALL SELECT 1 FROM page_snapshots WHERE page_id=? LIMIT 1",
                        (page_id, page_id)).fetchone():
        raise BlockError("Page has no history", 404)


def page_history(conn: sqlite3.Connection, page_id: str) -> dict:
    """Editing sessions (per device, split by 10-minute pauses, newest first) and daily snapshots.

    Each session lists its transactions as ``moments`` ({seq, at}, oldest first): every one
    is a point the page can be previewed and restored at.
    """
    page_id = block_id(page_id)
    _known(conn, page_id)
    sessions: list[dict] = []
    for row in conn.execute("SELECT seq,device_id,at,ops FROM op_log WHERE page_id=? ORDER BY seq", (page_id,)):
        counts: dict[str, int] = {}
        for op in json.loads(row["ops"])["ops"]:
            counts[op["op"]] = counts.get(op["op"], 0) + 1
        current = sessions[-1] if sessions else None
        if current is None or current["device_id"] != row["device_id"] or _when(row["at"]) - _when(current["end"]) > SESSION_GAP:
            current = {"device_id": row["device_id"], "start": row["at"], "end": row["at"], "first_seq": row["seq"],
                       "last_seq": row["seq"], "transactions": 0, "ops": {}, "moments": []}
            sessions.append(current)
        current.update(end=row["at"], last_seq=row["seq"], transactions=current["transactions"] + 1)
        current["moments"].append({"seq": row["seq"], "at": row["at"]})
        for kind, count in counts.items():
            current["ops"][kind] = current["ops"].get(kind, 0) + count
    snapshots = [dict(row) for row in conn.execute("SELECT day,seq FROM page_snapshots WHERE page_id=? ORDER BY seq DESC", (page_id,))]
    horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
    return {"page_id": page_id, "horizon_seq": horizon, "sessions": sessions[::-1], "snapshots": snapshots}


def _version(conn: sqlite3.Connection, page_id: str, seq) -> OrderedPageDoc:
    page_id = block_id(page_id)
    if type(seq) is not int or seq < 0:
        raise BlockError("Choose a sequence number from the page's history")
    _known(conn, page_id)
    horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
    if seq <= horizon and not conn.execute("SELECT 1 FROM page_snapshots WHERE page_id=? AND seq<=?", (page_id, seq)).fetchone():
        raise BlockError("That moment was compacted; the oldest kept versions are daily snapshots", 410)
    return replay(conn, page_id, seq)


def preview(conn: sqlite3.Connection, page_id: str, seq) -> dict:
    """The page as it was after transaction `seq` (live and trashed blocks, reading order)."""
    doc = _version(conn, page_id, seq)
    ordered = sorted(doc.blocks.values(), key=lambda item: (doc.depth(item.id), item.order_key, item.id))
    return {"page_id": doc.page_id, "seq": seq, "blocks": [asdict(item) for item in ordered]}


def restore(conn: sqlite3.Connection, page_id: str, seq) -> dict:
    """Append one transaction that makes the page look as it did after `seq`."""
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        target = _version(conn, page_id, seq)
        working = load_page(conn, target.page_id)
        ops: list[dict] = []
        at = "restore"

        def emit(op: dict) -> None:
            working.apply(op, at)
            ops.append(op)

        for item in sorted(target.blocks.values(), key=lambda item: target.depth(item.id)):
            current = working.blocks.get(item.id)
            if current is None:
                if item.parent_id is not None and item.parent_id not in working.blocks:
                    continue
                if conn.execute("SELECT 1 FROM blocks WHERE id=? AND page_id<>?", (item.id, target.page_id)).fetchone():
                    continue  # The block now lives on another page; leave it there.
                emit({"op": "insert", "id": item.id, "type": item.type, "parent": item.parent_id, "key": item.order_key,
                      "props": item.properties, "text": item.text, "created": item.created_at})
                current = working.blocks[item.id]
            if not current.live and item.live:
                emit({"op": "restore", "id": item.id})
            if current.parent_id is not None and (current.parent_id != item.parent_id or current.order_key != item.order_key):
                emit({"op": "move", "id": item.id, "parent": item.parent_id, "key": item.order_key})
            wanted = {key: value for key, value in item.properties.items() if key != "revision"}
            if "revision" in current.properties and current.parent_id is None:
                wanted["revision"] = current.properties["revision"]
            changes = props_diff(current.properties, wanted)
            if changes or current.type != item.type:
                emit({"op": "set", "id": item.id, "props": changes, **({"type": item.type} if current.type != item.type else {})})
            if current.text != item.text:
                emit({"op": "text", "id": item.id, "text": item.text})
        for current in sorted(working.blocks.values(), key=lambda item: working.depth(item.id)):
            wanted = target.blocks.get(current.id)
            parent = working.blocks.get(current.parent_id) if current.parent_id else None
            if current.live and (wanted is None or not wanted.live) and (parent is None or parent.live):
                emit({"op": "delete", "id": current.id})
        if not ops:
            raise BlockError("The page already looks like that version", 409)
        return transact(conn, ops)


def trash(conn: sqlite3.Connection, limit: int = 500) -> dict:
    """Blocks in Trash (top-most only), newest deletion first, with the page they belong to."""
    rows = conn.execute(
        "SELECT b.id,b.type,b.page_id,b.text,b.deleted_at,r.type AS root_type,r.text AS root_text,"
        "json_extract(r.properties,'$.number') AS number FROM blocks b JOIN blocks r ON r.id=b.page_id "
        "LEFT JOIN blocks p ON p.id=b.parent_id WHERE b.deleted_at IS NOT NULL AND (p.id IS NULL OR p.deleted_at IS NULL) "
        "ORDER BY b.deleted_at DESC,b.id LIMIT ?", (limit,))
    return {"items": [{"id": row["id"], "type": row["type"], "page_id": row["page_id"], "deleted_at": row["deleted_at"],
                       "title": (row["text"] or row["root_text"])[:200],
                       "root": {"type": row["root_type"], "number": row["number"], "title": row["root_text"][:200]}} for row in rows]}


def restore_from_trash(conn: sqlite3.Connection, bid) -> dict:
    return transact(conn, [{"op": "restore", "id": block_id(bid)}])


def compact(conn: sqlite3.Connection, now: datetime | None = None, days: int = RETENTION_DAYS) -> dict:
    """Purge expired Trash, then fold op_log rows older than the window into daily snapshots."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        expired = [row["id"] for row in conn.execute(
            "SELECT b.id FROM blocks b LEFT JOIN blocks p ON p.id=b.parent_id WHERE b.deleted_at IS NOT NULL "
            "AND b.deleted_at<? AND (p.id IS NULL OR p.deleted_at IS NULL OR p.deleted_at>=?)", (cutoff, cutoff))]
        if expired:
            transact(conn, [{"op": "purge", "id": bid} for bid in expired], at=now.isoformat())
        horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
        last = conn.execute("SELECT MAX(seq) FROM op_log WHERE seq>? AND at<?", (horizon, cutoff)).fetchone()[0]
        folded = snapshots = 0
        if last is not None:
            pages = [row[0] for row in conn.execute("SELECT DISTINCT page_id FROM op_log WHERE seq>? AND seq<=?", (horizon, last))]
            for page_id in pages:
                if not conn.execute("SELECT 1 FROM blocks WHERE page_id=? LIMIT 1", (page_id,)).fetchone():
                    # Purged for good: its history leaves with it.
                    conn.execute("DELETE FROM page_snapshots WHERE page_id=?", (page_id,))
                    continue
                doc = replay(conn, page_id, horizon)
                rows = conn.execute("SELECT seq,at,ops FROM op_log WHERE page_id=? AND seq>? AND seq<=? ORDER BY seq",
                                    (page_id, horizon, last)).fetchall()
                for index, row in enumerate(rows):
                    for op in json.loads(row["ops"])["ops"]:
                        doc.apply(op, row["at"])
                    day = row["at"][:10]
                    if index + 1 == len(rows) or rows[index + 1]["at"][:10] != day:
                        conn.execute("INSERT OR REPLACE INTO page_snapshots(page_id,day,seq,state) VALUES (?,?,?,?)",
                                     (page_id, day, row["seq"], doc.encode()))
                        snapshots += 1
            conn.execute("UPDATE block_history SET horizon_seq=?,horizon_at=? WHERE id=1", (last, cutoff))
            folded = conn.execute("DELETE FROM op_log WHERE seq<=?", (last,)).rowcount
    return {"folded": folded, "snapshots": snapshots, "purged": len(expired), "horizon_seq": max(horizon, last or 0)}

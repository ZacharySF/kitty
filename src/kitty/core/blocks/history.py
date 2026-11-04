"""Version history, point-in-time restore, compaction and Trash (Q15, Q20).

Every transaction stays in ``op_log`` for the retention window (30 days), so any
moment in it can be previewed and restored exactly. ``compact`` folds older rows
into one snapshot per page per day (``page_snapshots``), then deletes them below a
horizon that only moves forward (the op_log trigger refuses any other delete).
Restoring never rewrites the past: it appends a transaction that turns the
current page into the chosen version. Trash is every block with ``deleted_at``;
it follows the same window and is purged when it expires.

History groups transactions into sessions per device *and actor* (the person, an
agent session, a paired client, rika! itself or a paired device; kitty.core.blocks.actor)
and can be filtered by actor. ``citation`` tells whether a block pinned as
``((id@seq))`` changed since and how. Undoing one agent session is
kitty.core.blocks.sessions.
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from kitty.core.blocks import crdt
from kitty.core.blocks.actor import COMPACTION, KINDS, from_row
from kitty.core.blocks.convert import props_diff
from kitty.core.blocks.pagedoc import (
    Block,
    BlockError,
    OrderedPageDoc,
    block_id,
    is_edit_key,
)
from kitty.core.blocks.store import device_id, load_page, replay, transact
from kitty.db.connection import transaction


def retention_days(conn: sqlite3.Connection) -> int:
    """Every transaction is kept this long (Q20): build ``settings.history.retention_days`` (Q8)."""
    return conn.execute("SELECT history_retention_days FROM build_state WHERE id=1").fetchone()[0]


SESSION_GAP = timedelta(minutes=10)


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _known(conn: sqlite3.Connection, page_id: str) -> None:
    if not conn.execute("SELECT 1 FROM op_log WHERE page_id=? UNION ALL SELECT 1 FROM page_snapshots WHERE page_id=? LIMIT 1",
                        (page_id, page_id)).fetchone():
        raise BlockError("Page has no history", 404)


def _matches(actor, kind: str | None, session: str | None) -> bool:
    """Whether a row's actor (or, for a merge, one it carried) is of `kind` / in `session`."""
    leaves = (actor, *actor.via)
    return (kind is None or any(item.kind == kind for item in leaves)) and \
        (session is None or any(item.session == session for item in leaves))


def page_history(conn: sqlite3.Connection, page_id: str, *, actor: str | None = None, session: str | None = None) -> dict:
    """Editing sessions (per device and actor, split by 10-minute pauses, newest first) and daily snapshots.

    Each session names its ``actor`` ({kind, name, session, via?}) and lists its transactions
    as ``moments`` ({seq, at}, oldest first): every one is a point the page can be previewed
    and restored at. ``actor`` (a kind) and ``session`` keep only the matching transactions;
    a merge matches by the actors it carried too.
    """
    page_id = block_id(page_id)
    if actor is not None and actor not in KINDS:
        raise BlockError(f"Filter by one of: {', '.join(KINDS)}")
    _known(conn, page_id)
    sessions: list[dict] = []
    for row in conn.execute("SELECT seq,device_id,at,ops,actor,actor_name,actor_session,actor_via FROM op_log "
                            "WHERE page_id=? ORDER BY seq", (page_id,)):
        who = from_row(row)
        if not _matches(who, actor, session):
            continue
        counts: dict[str, int] = {}
        for op in json.loads(row["ops"])["ops"]:
            counts[op["op"]] = counts.get(op["op"], 0) + 1
        current = sessions[-1] if sessions else None
        described = who.as_dict()
        if current is None or current["device_id"] != row["device_id"] or current["actor"] != described \
                or _when(row["at"]) - _when(current["end"]) > SESSION_GAP:
            current = {"device_id": row["device_id"], "actor": described, "start": row["at"], "end": row["at"],
                       "first_seq": row["seq"], "last_seq": row["seq"], "transactions": 0, "ops": {}, "moments": []}
            sessions.append(current)
        current.update(end=row["at"], last_seq=row["seq"], transactions=current["transactions"] + 1)
        current["moments"].append({"seq": row["seq"], "at": row["at"]})
        for kind, count in counts.items():
            current["ops"][kind] = current["ops"].get(kind, 0) + count
    snapshots = [dict(row) for row in conn.execute("SELECT day,seq FROM page_snapshots WHERE page_id=? ORDER BY seq DESC", (page_id,))]
    horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
    try:  # merged sessions carry the paired device that made them
        devices = {row[0]: row[1] for row in conn.execute("SELECT device_id, name FROM peers")}
    except sqlite3.OperationalError:  # before migration 040
        devices = {}
    return {"page_id": page_id, "horizon_seq": horizon, "sessions": sessions[::-1], "snapshots": snapshots, "devices": devices}


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


MAX_CITATIONS = 50
_WORDS = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)
_MENTIONS = ("SELECT MAX(o.seq) FROM op_log o, json_each(CAST(o.ops AS TEXT), '$.ops') j WHERE o.page_id=? AND o.seq>? "
             "AND (json_extract(j.value, '$.id')=? OR (json_extract(j.value, '$.op')='adopt' "
             "AND EXISTS (SELECT 1 FROM json_each(j.value, '$.blocks') b WHERE json_extract(b.value, '$.id')=?)))")


def _content(item: Block | None) -> dict | None:
    """What a citation quotes: type, text, content properties (no view state, tags or revision), Trash."""
    if item is None:
        return None
    return {"type": item.type, "text": item.text, "deleted": not item.live,
            "props": {key: value for key, value in sorted(item.properties.items()) if is_edit_key(key) and key != "revision"}}


def word_diff(before: str, after: str) -> list[dict]:
    """``[{op: equal|delete|insert, text}]`` turning `before` into `after`, word by word."""
    old, new = _WORDS.findall(before), _WORDS.findall(after)
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in {"equal", "delete", "replace"} and i2 > i1:
            out.append({"op": "equal" if tag == "equal" else "delete", "text": "".join(old[i1:i2])})
        if tag in {"insert", "replace"} and j2 > j1:
            out.append({"op": "insert", "text": "".join(new[j1:j2])})
    return out


def _parse_cite(value) -> tuple[str, int]:
    if isinstance(value, str):
        match = re.fullmatch(r"\(?\(?([0-9a-f-]{36})@([0-9]{1,18})\)?\)?", value.strip())
        if match:
            return block_id(match[1]), int(match[2])
    raise BlockError("A pinned citation is id@seq, as in ((id@seq))")


def citations(conn: sqlite3.Connection, cites) -> dict:
    """Pinned citations ``((id@seq))`` checked against now: has the cited block changed since?

    For each: ``then`` (the block as of `seq`, rebuilt like ``preview``) and ``now``
    ({type, text, props, deleted}), ``changed``, ``seq`` (the newest transaction that
    changed it; the cited one when unchanged), ``diff`` (``word_diff`` of the text) and
    ``status``: unchanged, changed, deleted (in Trash), purged, compacted (that moment
    is folded into a daily snapshot) or unknown (no such block at that moment).
    """
    if not isinstance(cites, list) or not 1 <= len(cites) <= MAX_CITATIONS:
        raise BlockError(f"Check 1-{MAX_CITATIONS} citations at a time")
    pinned = [_parse_cite(value) for value in cites]
    versions: dict[tuple[str, int], OrderedPageDoc | None] = {}
    out = []
    with (nullcontext() if conn.in_transaction else transaction(conn)):
        for bid, seq in pinned:
            row = conn.execute("SELECT * FROM blocks WHERE id=?", (bid,)).fetchone()
            now = Block(row["id"], row["type"], row["parent_id"], row["page_id"], row["order_key"], json.loads(row["properties"]),
                        row["text"], row["created_at"], row["updated_at"], row["deleted_at"]) if row else None
            page_id = now.page_id if now else None
            if page_id is None:  # Purged: the page it was on at `seq` (the newest log row that named it).
                found = conn.execute("SELECT o.page_id FROM op_log o, json_each(CAST(o.ops AS TEXT), '$.ops') j "
                                     "WHERE o.seq<=? AND json_extract(j.value, '$.id')=? ORDER BY o.seq DESC LIMIT 1",
                                     (seq, bid)).fetchone()
                page_id = found[0] if found else None
            entry = {"cite": f"(({bid}@{seq}))", "id": bid, "cited_seq": seq, "page_id": page_id, "seq": seq, "changed": False,
                     "then": None, "now": _content(now), "diff": []}
            out.append(entry)
            if page_id is None:
                entry["status"] = "unknown"
                continue
            if (page_id, seq) not in versions:
                try:
                    versions[page_id, seq] = _version(conn, page_id, seq)
                except BlockError as exc:
                    if exc.status not in {404, 410}:
                        raise
                    versions[page_id, seq] = None
            doc = versions[page_id, seq]
            then = doc.blocks.get(bid) if doc is not None else None
            if then is None:
                entry["status"] = "compacted" if doc is None and 0 < seq <= conn.execute(
                    "SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0] else "unknown"
                continue
            entry["then"] = _content(then)
            if entry["then"] == entry["now"]:
                entry["status"] = "unchanged"
                continue
            entry["changed"] = True
            entry["status"] = "purged" if now is None else "deleted" if not now.live else "changed"
            entry["seq"] = conn.execute(_MENTIONS, (page_id, seq, bid, bid)).fetchone()[0] or conn.execute(
                "SELECT COALESCE(MAX(seq), ?) FROM op_log WHERE page_id=? AND seq>?", (seq, page_id, seq)).fetchone()[0]
            entry["diff"] = word_diff(then.text, now.text if now else "")
    return {"citations": out}


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


def compact(conn: sqlite3.Connection, now: datetime | None = None, days: int | None = None) -> dict:
    """Purge expired Trash, then fold op_log rows older than the window into daily snapshots."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days(conn) if days is None else days)).isoformat()
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        expired = [row["id"] for row in conn.execute(
            "SELECT b.id FROM blocks b LEFT JOIN blocks p ON p.id=b.parent_id WHERE b.deleted_at IS NOT NULL "
            "AND b.deleted_at<? AND (p.id IS NULL OR p.deleted_at IS NULL OR p.deleted_at>=?)", (cutoff, cutoff))]
        if expired:
            transact(conn, [{"op": "purge", "id": bid} for bid in expired], at=now.isoformat(), actor=COMPACTION)
        horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
        last = conn.execute("SELECT MAX(seq) FROM op_log WHERE seq>? AND at<?", (horizon, cutoff)).fetchone()[0]
        acknowledged = _acknowledged(conn)
        if last is not None and acknowledged is not None:
            # Never fold rows a paired device has not confirmed it holds (Q16).
            last = min(last, acknowledged) if acknowledged > horizon else None
        folded = snapshots = 0
        if last is not None:
            _cover_crdt(conn, horizon, last, now.isoformat())
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


def _acknowledged(conn: sqlite3.Connection) -> int | None:
    """The highest sequence every paired device has acknowledged; None without paired devices."""
    try:
        count, lowest = conn.execute("SELECT COUNT(*), MIN(acked_seq) FROM peers").fetchone()
    except sqlite3.OperationalError:  # before migration 040
        return None
    return lowest if count else None


def _cover_crdt(conn: sqlite3.Connection, horizon: int, last: int, at: str) -> None:
    """Rewrite the Loro snapshot of every page whose op_log tail is about to be folded."""
    try:
        rows = conn.execute("SELECT c.page_id FROM page_crdt c WHERE c.snapshot_seq<? AND EXISTS (SELECT 1 FROM op_log o "
                            "WHERE o.page_id=c.page_id AND o.seq>? AND o.seq<=? AND o.crdt IS NOT NULL)", (last, horizon, last)).fetchall()
    except sqlite3.OperationalError:  # before migration 040
        return
    peer = crdt.peer_of(device_id(conn))
    for (page_id,) in rows:
        doc = crdt.load(conn, page_id, peer)
        newest = conn.execute("SELECT MAX(seq) FROM op_log WHERE page_id=? AND crdt IS NOT NULL", (page_id,)).fetchone()[0]
        crdt.snapshot(conn, page_id, doc, newest, at)
        crdt.keep(peer, page_id, doc)

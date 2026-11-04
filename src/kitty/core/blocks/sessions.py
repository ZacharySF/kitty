"""Agent sessions and undoing everything one of them did (paper §14.3).

Every ``op_log`` row names its actor (kitty.core.blocks.actor); an agent's rows share
its session. ``list_sessions`` lists the sessions in the kept history (merges from a
paired device included, by the actors they carried). ``undo_session`` is
history.restore filtered by actor: it computes the inverse of every transaction of one
session and applies them as ONE new forward transaction; the past is never rewritten.

How the inverse is computed. Each block a session transaction touched has a state
before and after it (rebuilt from the log with ``replay``), compared field by field:
its place (page, parent, order key), type, text, each property (the page ``revision``
aside) and whether it is live. Walking the session's transactions newest first over
the blocks' current state:

- a field the transaction changed that still holds the value it left goes back to the
  value it found;
- a field that already holds the value it found (reverted by hand, or by an earlier
  undo) is left alone;
- a field that holds anything else was changed later by someone else: it is kept and
  reported as a conflict, never overwritten. That also stops every older change of the
  session to the same field, so the person's edit survives;
- a block the session created goes to Trash only when nothing about it changed since,
  and only if nobody added blocks under it.

The inverse is planned against the current pages (so moves, restores and Trash stay
valid) and then applied with ``transact`` in the same immediate transaction; nothing is
written when nothing is left to revert. ``dry_run`` returns the same report without
writing. The undo is recorded under whoever asked for it; an agent undoing its own
session records it as ``<session>:undo`` so the session's own rows stay exactly what it did.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import asdict
from typing import Any

from kitty.core.blocks import actor as actors
from kitty.core.blocks.actor import Actor, from_row
from kitty.core.blocks.pagedoc import Block, BlockError, OrderedPageDoc
from kitty.core.blocks.store import load_page, replay, transact
from kitty.db.connection import transaction

MAX_SESSIONS = 200
#: Conflicts and reverted blocks listed in a report (the counts are always exact).
MAX_LISTED = 200
_SNIPPET = 120


def _session(value: Any) -> str:
    if not isinstance(value, str) or not actors._SESSION.fullmatch(value):
        raise BlockError("Name the agent session by its ID (from the sessions list or History)")
    return value


# Sessions -------------------------------------------------------------------------------

def _carried(row: sqlite3.Row) -> list[Actor]:
    """The sessions a log row belongs to: its own, or for a merge the ones it carried."""
    who = from_row(row)
    return [item for item in (who.via if who.kind == "peer" else (who,)) if item.session]


def list_sessions(conn: sqlite3.Connection, *, kind: str | None = "agent", limit: int = 50) -> dict:
    """Sessions in the kept history, most recent first: actor, first/last time, transactions and pages.

    ``kind`` (default ``agent``; None for every kind with sessions) filters by actor kind.
    A session that reached this device through a paired device lists ``device`` too.
    """
    if kind is not None and kind not in actors.KINDS:
        raise BlockError(f"Filter by one of: {', '.join(actors.KINDS)}")
    if type(limit) is not int or not 1 <= limit <= MAX_SESSIONS:
        raise BlockError(f"Limit must be 1-{MAX_SESSIONS}")
    found: dict[str, dict] = {}
    pages: dict[str, dict[str, None]] = {}
    with (nullcontext() if conn.in_transaction else transaction(conn)):
        rows = conn.execute("SELECT seq,txn_id,page_id,at,device_id,actor,actor_name,actor_session,actor_via FROM op_log "
                            "WHERE actor_session IS NOT NULL OR actor_via IS NOT NULL ORDER BY seq").fetchall()
        for row in rows:
            for item in _carried(row):
                if kind is not None and item.kind != kind:
                    continue
                entry = found.get(item.session)
                if entry is None:
                    entry = found[item.session] = {"session": item.session, "actor": item.as_dict(), "start": row["at"],
                                                   "end": row["at"], "last_seq": row["seq"], "transactions": set(),
                                                   "device": None if row["actor"] != "peer" else row["actor_name"]}
                entry.update(end=row["at"], last_seq=row["seq"])
                entry["transactions"].add(row["txn_id"])
                pages.setdefault(item.session, {})[row["page_id"]] = None
        listed = sorted(found.values(), key=lambda entry: entry["last_seq"], reverse=True)[:limit]
        for entry in listed:
            entry["transactions"] = len(entry["transactions"])
            entry["pages"] = [_page_summary(conn, page_id) for page_id in list(pages[entry["session"]])[:20]]
            entry["page_count"] = len(pages[entry["session"]])
    return {"sessions": listed}


def _page_summary(conn: sqlite3.Connection, page_id: str) -> dict:
    row = conn.execute("SELECT type, substr(text,1,200) AS title, json_extract(properties,'$.number') AS number, deleted_at "
                       "FROM blocks WHERE id=?", (page_id,)).fetchone()
    if row is None:
        return {"page_id": page_id, "type": None, "title": None, "number": None, "deleted": True}
    return {"page_id": page_id, "type": row["type"], "title": row["title"], "number": row["number"],
            "deleted": row["deleted_at"] is not None}


# Block states ---------------------------------------------------------------------------

def _fields(item: Block) -> dict[str, Any]:
    """One block's state, field by field (properties as canonical JSON; the revision aside)."""
    state: dict[str, Any] = {"place": (item.page_id, item.parent_id, item.order_key), "type": item.type,
                             "text": item.text, "live": item.live}
    for key, value in item.properties.items():
        if key != "revision":
            state["props." + key] = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return state


def _differs(before: dict, after: dict) -> list[str]:
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


class _Txn:
    def __init__(self, txn_id: str, seq: int):
        self.txn_id = txn_id
        self.seq = seq
        #: block id -> [state before, state after] (None: absent from every page it touched)
        self.changes: dict[str, list[dict | None]] = {}

    def note(self, bid: str, before: dict | None, after: dict | None) -> None:
        entry = self.changes.setdefault(bid, [None, None])
        if before is not None:
            entry[0] = before
        if after is not None:
            entry[1] = after


def _session_rows(conn: sqlite3.Connection, session: str) -> tuple[list[sqlite3.Row], list[dict]]:
    """The session's log rows, and merges that carried it together with other actors (not undoable)."""
    rows = conn.execute("SELECT seq,txn_id,page_id,at,ops,actor,actor_name,actor_session,actor_via FROM op_log "
                        "WHERE actor_session=? OR (actor='peer' AND actor_via LIKE ?) ORDER BY seq",
                        (session, f'%"session":"{session}"%')).fetchall()
    mine, mixed = [], []
    for row in rows:
        if row["actor_session"] == session:
            mine.append(row)
            continue
        carried = from_row(row).via
        if not any(item.session == session for item in carried):
            continue
        if all(item.session == session for item in carried):
            mine.append(row)
        else:
            mixed.append({"seq": row["seq"], "page_id": row["page_id"], "device": row["actor_name"],
                          "reasons": ["merged from a paired device together with other people's changes"]})
    return mine, mixed


def _history(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[_Txn]:
    """Each session transaction with every block state it changed, before and after.

    Each touched page is rebuilt once, from just before the session's first row on it,
    and rolled forward through every row (anyone's) up to its last one.
    """
    txns: dict[str, _Txn] = {}
    spans: dict[str, list[int]] = {}
    for row in rows:
        txns.setdefault(row["txn_id"], _Txn(row["txn_id"], row["seq"]))
        span = spans.setdefault(row["page_id"], [row["seq"], row["seq"]])
        span[1] = row["seq"]
    owned = {row["seq"]: txns[row["txn_id"]] for row in rows}
    for page_id, (first, last) in spans.items():
        doc = replay(conn, page_id, first - 1)
        states = {bid: _fields(item) for bid, item in doc.blocks.items()}
        for row in conn.execute("SELECT seq,at,ops FROM op_log WHERE page_id=? AND seq>=? AND seq<=? ORDER BY seq",
                                (page_id, first, last)):
            doc.changed.clear()
            doc.removed.clear()
            for op in json.loads(row["ops"])["ops"]:
                doc.apply(op, row["at"])
            txn = owned.get(row["seq"])
            for bid in doc.changed | doc.removed:
                item = doc.blocks.get(bid)
                after = _fields(item) if item is not None else None
                if txn is not None:
                    txn.note(bid, states.get(bid), after)
                if after is None:
                    states.pop(bid, None)
                else:
                    states[bid] = after
    return sorted(txns.values(), key=lambda txn: txn.seq)


def _current(conn: sqlite3.Connection, ids: list[str]) -> dict[str, Block]:
    found: dict[str, Block] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for row in conn.execute(f"SELECT * FROM blocks WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            found[row["id"]] = Block(row["id"], row["type"], row["parent_id"], row["page_id"], row["order_key"],
                                     json.loads(row["properties"]), row["text"], row["created_at"], row["updated_at"],
                                     row["deleted_at"])
    return found


# The plan -------------------------------------------------------------------------------

def _props(state: dict) -> dict[str, Any]:
    return {key.removeprefix("props."): json.loads(value) for key, value in state.items() if key.startswith("props.")}


class _Plan:
    """Desired block states after the undo, conflicts found on the way, then the operations."""

    def __init__(self, conn: sqlite3.Connection, current: dict[str, Block]):
        self.conn = conn
        self.current = current
        self.now = {bid: _fields(item) for bid, item in current.items()}
        self.wanted: dict[str, dict | None] = {bid: dict(state) for bid, state in self.now.items()}
        self.conflicts: dict[str, dict] = {}
        #: Blocks with at least one field planned to go back, and those the session created.
        self.touched: set[str] = set()
        self.created: set[str] = set()
        self.pages: dict[str, OrderedPageDoc] = {}
        self.ops: list[dict] = []

    def conflict(self, bid: str, reason: str, fields: list[str] | None = None) -> None:
        entry = self.conflicts.setdefault(bid, {"id": bid, "reasons": [], "fields": []})
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)
        entry["fields"] = sorted(set(entry["fields"]) | {field.partition(".")[0] for field in fields or []})

    def revert(self, txn: _Txn) -> None:
        for bid, (before, after) in txn.changes.items():
            if before is None and after is None:
                continue
            wanted = self.wanted.get(bid)
            if before is None:
                # The session created this block (here, or on the device it was merged from).
                if wanted is None:
                    continue  # Purged since.
                if wanted == after:
                    self.wanted[bid] = {**wanted, "live": False}
                    self.touched.add(bid)
                    self.created.add(bid)
                elif wanted != {**after, "live": False}:
                    self.conflict(bid, "changed by someone else after the agent wrote it", _differs(after, wanted))
                continue
            if after is None:
                if wanted is None:
                    self.wanted[bid] = dict(before)
                    self.touched.add(bid)
                else:
                    self.conflict(bid, "came back after the agent removed it")
                continue
            if wanted is None:
                self.conflict(bid, "was removed for good since")
                continue
            for field in _differs(before, after):
                if wanted.get(field) == after.get(field):
                    if before.get(field) is None:
                        wanted.pop(field, None)
                    else:
                        wanted[field] = before[field]
                    self.touched.add(bid)
                elif wanted.get(field) != before.get(field):
                    self.conflict(bid, "changed by someone else since", [field])

    # Operations against the current pages ---------------------------------------------

    def page(self, page_id: str) -> OrderedPageDoc:
        if page_id not in self.pages:
            self.pages[page_id] = load_page(self.conn, page_id)
        return self.pages[page_id]

    def block(self, bid: str | None) -> Block | None:
        if bid is None:
            return None
        for doc in self.pages.values():
            if bid in doc.blocks:
                return doc.blocks[bid]
        row = self.conn.execute("SELECT page_id FROM blocks WHERE id=?", (bid,)).fetchone()
        return self.page(row["page_id"]).blocks.get(bid) if row is not None else None

    def emit(self, bid: str, op: dict) -> bool:
        """Apply `op` to the planned pages and keep it; one that cannot apply becomes a conflict."""
        try:
            item, parent = self.block(bid), self.block(op.get("parent"))
            if op["op"] == "insert":
                if parent is None:
                    raise BlockError("its parent is gone")
                self.page(parent.page_id).apply(op, "undo")
            elif item is None:
                raise BlockError("it is gone")
            elif op["op"] == "move" and parent is not None and parent.page_id != item.page_id:
                source, destination = self.page(item.page_id), self.page(parent.page_id)
                rows = [asdict(item), *(asdict(child) for child in source.descendants(bid))]
                for row in rows:
                    del row["page_id"]
                source.apply({"op": "release", "id": bid}, "undo")
                destination.apply({"op": "adopt", "parent": parent.id, "key": op["key"], "blocks": rows}, "undo")
            else:
                self.page(item.page_id).apply(op, "undo")
        except BlockError as exc:
            self.conflict(bid, f"could not be put back: {exc}")
            return False
        self.ops.append(op)
        return True

    def depth(self, bid: str) -> int:
        level, seen = 0, set()
        state = self.wanted.get(bid) or self.now.get(bid)
        while state is not None and state["place"][1] is not None and bid not in seen:
            seen.add(bid)
            bid = state["place"][1]
            level += 1
            state = self.wanted.get(bid) or self.now.get(bid)
        return level

    def build(self) -> None:
        ordered = sorted((bid for bid in self.touched if self.wanted.get(bid) != self.now.get(bid)),
                         key=lambda bid: (self.depth(bid), bid))
        for bid in ordered:  # Removed for good by the session: inserted again, parents first.
            state = self.wanted[bid]
            if self.now.get(bid) is None and state is not None:
                _, parent, key = state["place"]
                self.emit(bid, {"op": "insert", "id": bid, "type": state["type"], "parent": parent, "key": key,
                                "props": _props(state), "text": state["text"]})
        for bid in ordered:  # Back from Trash, parents first.
            item, state = self.block(bid), self.wanted[bid]
            if item is not None and state is not None and state["live"] and not item.live:
                self.emit(bid, {"op": "restore", "id": bid})
        for bid in ordered:  # Back to the old place.
            item, state = self.block(bid), self.wanted[bid]
            if item is None or state is None or item.parent_id is None:
                continue
            page_id, parent, key = state["place"]
            if (item.page_id, item.parent_id, item.order_key) != (page_id, parent, key):
                target = self.block(parent)
                if target is None or not target.live:
                    self.conflict(bid, "its old place is gone", ["place"])
                    continue
                self.emit(bid, {"op": "move", "id": bid, "parent": parent, "key": key})
        for bid in ordered:  # Type, properties and text.
            item, state = self.block(bid), self.wanted[bid]
            if item is None or state is None:
                continue
            now = _fields(item)
            props = {key.removeprefix("props."): (json.loads(state[key]) if key in state else None)
                     for key in now.keys() | state.keys() if key.startswith("props.") and now.get(key) != state.get(key)}
            if props or now["type"] != state["type"]:
                self.emit(bid, {"op": "set", "id": bid, "props": props,
                                **({"type": state["type"]} if now["type"] != state["type"] else {})})
            if now["text"] != state["text"]:
                self.emit(bid, {"op": "text", "id": bid, "text": state["text"]})
        for bid in ordered:  # To Trash, parents first; never with someone else's blocks inside.
            item, state = self.block(bid), self.wanted[bid]
            if item is None or state is None or state["live"] or not item.live:
                continue
            kept = [child.id for child in self.page(item.page_id).descendants(bid)
                    if child.live and (self.wanted.get(child.id) or {"live": True})["live"]]
            if kept:
                self.conflict(bid, "holds blocks someone else added or changed since")
                continue
            self.emit(bid, {"op": "delete", "id": bid})

    def outcome(self) -> list[dict]:
        """Every block the planned operations change, with what goes back (``created``: to Trash)."""
        found = []
        for bid in sorted(self.touched):
            item = self.block(bid)
            before, after = self.now.get(bid), (_fields(item) if item is not None else None)
            if after == before:
                continue
            if bid in self.created and after is not None and not after["live"]:
                fields = ["created"]
            else:
                fields = sorted({field.partition(".")[0] for field in _differs(before or {}, after or {})})
            shown = item or self.current.get(bid)
            found.append({"id": bid, "page_id": shown.page_id if shown else None, "text": _snippet(shown), "fields": fields})
        return found


def _snippet(item: Block | None) -> str:
    return (item.text if item is not None else "")[:_SNIPPET]


def undo_session(conn: sqlite3.Connection, session: Any, *, dry_run: bool = False) -> dict:
    """Revert everything one agent session did, as one new transaction (see the module docs).

    Returns ``{session, actor, transactions, applied, dry_run, reverted: [{id, page_id,
    text, fields}], conflicts: [{id, page_id, text, reasons, fields} | {seq, page_id,
    device, reasons}], reverted_count, conflict_count}`` plus ``txn_id``, ``seq`` and
    ``revisions`` when it applied. Nothing left to revert is not an error: ``applied``
    is false and the conflicts say why.
    """
    session = _session(session)
    if not isinstance(dry_run, bool):
        raise BlockError("dry_run is true or false")
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        rows, mixed = _session_rows(conn, session)
        if not rows and not mixed:
            raise BlockError("No kept history belongs to that session", 404)
        who = next((item for row in rows for item in _carried(row) if item.session == session), None)
        txns = _history(conn, rows)
        plan = _Plan(conn, _current(conn, sorted({bid for txn in txns for bid in txn.changes})))
        for txn in reversed(txns):
            plan.revert(txn)
        plan.build()
        reverted = plan.outcome()
        conflicts = [{**entry, "page_id": plan.current[bid].page_id if bid in plan.current else None,
                      "text": _snippet(plan.current.get(bid))} for bid, entry in sorted(plan.conflicts.items())] + mixed
        report = {"session": session, "actor": who.as_dict() if who else None, "transactions": len(txns),
                  "dry_run": dry_run, "applied": False, "reverted_count": len(reverted), "conflict_count": len(conflicts),
                  "reverted": reverted[:MAX_LISTED], "conflicts": conflicts[:MAX_LISTED]}
        if dry_run or not plan.ops:
            return report
        mine = actors.current()
        undoer = Actor(mine.kind, mine.name, f"{session}:undo"[:actors.MAX_SESSION]) if mine.session == session else mine
        result = transact(conn, plan.ops, actor=undoer)
    return {**report, "applied": True, "txn_id": result["txn_id"], "seq": result["seq"], "revisions": result["revisions"]}

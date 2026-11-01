"""Server-applied transactions: page documents -> append-only op log -> projection.

`transact` is the only writer of `blocks`, `op_log`, `block_refs`, `block_topics`,
`block_tags` and `tag_rejections` (the last two through kitty.core.blocks.tagging).
Inside one SQLite transaction it loads each touched page document, applies every
operation (so cycles, limits and stale revisions are refused before anything is
written), appends one `op_log` row per touched page, and rewrites only the
projection rows that changed. Readers use the projection; history and the tests
rebuild a page from the log with `replay`.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from kitty.core.blocks import tagging
from kitty.core.blocks.order import key_between
from kitty.core.blocks.pagedoc import Block, BlockError, OrderedPageDoc, block_id, is_edit_key
from kitty.core.blocks.tokens import REF, TAG, WIKI
from kitty.db.connection import transaction

MAX_OPS = 20_000
LOG_FORMAT = 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def device_id(conn: sqlite3.Connection) -> str:
    """This database's device identity, recorded on every op_log row."""
    return conn.execute("SELECT device_id FROM block_history WHERE id=1").fetchone()[0]


def _block(row: sqlite3.Row) -> Block:
    return Block(row["id"], row["type"], row["parent_id"], row["page_id"], row["order_key"],
                 json.loads(row["properties"]), row["text"], row["created_at"], row["updated_at"], row["deleted_at"])


def load_page(conn: sqlite3.Connection, page_id: str) -> OrderedPageDoc:
    """The current projection of one page (deleted blocks included)."""
    rows = conn.execute("SELECT * FROM blocks WHERE page_id=?", (page_id,))
    return OrderedPageDoc(page_id, {row["id"]: _block(row) for row in rows})


#: Block types whose children belong to another UI (the canvas), not to a page loader.
OPAQUE_TYPES = frozenset({"canvas"})
_STATE_FIELDS = ("id", "type", "parent_id", "order_key", "properties", "text", "created_at", "updated_at")


def page_state(conn: sqlite3.Connection, page_id: str) -> dict:
    """One live page for an editor: root, live blocks in reading order, revision and the log sequence it reflects.

    Read in one snapshot, so every change after ``seq`` is one this page does not yet contain.
    Children of a canvas are left to the canvas UI; the canvas block itself is listed.
    """
    with (nullcontext() if conn.in_transaction else transaction(conn)):
        seq = conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log").fetchone()[0]
        doc = load_page(conn, block_id(page_id))
    root = doc.root
    if root is None or not root.live:
        raise BlockError("Page not found", 404)
    listed: list[dict] = []

    def walk(parent_id: str) -> None:
        for child in doc.children(parent_id):
            listed.append({key: getattr(child, key) for key in _STATE_FIELDS})
            if child.type not in OPAQUE_TYPES:
                walk(child.id)

    walk(root.id)
    state = {key: getattr(root, key) for key in _STATE_FIELDS}
    return {"page_id": root.id, "seq": seq, "revision": root.properties.get("revision", 1), "root": state, "blocks": listed}


def replay(conn: sqlite3.Connection, page_id: str, upto: int | None = None) -> OrderedPageDoc:
    """Rebuild one page from its newest snapshot at or before `upto` plus the log after it."""
    limit = upto if upto is not None else 2**63 - 1
    snapshot = conn.execute("SELECT seq,state FROM page_snapshots WHERE page_id=? AND seq<=? ORDER BY seq DESC LIMIT 1",
                            (page_id, limit)).fetchone()
    doc = OrderedPageDoc.decode(page_id, snapshot["state"]) if snapshot else OrderedPageDoc(page_id)
    after = snapshot["seq"] if snapshot else 0
    for row in conn.execute("SELECT at,ops FROM op_log WHERE page_id=? AND seq>? AND seq<=? ORDER BY seq", (page_id, after, limit)):
        for op in json.loads(row["ops"])["ops"]:
            doc.apply(op, row["at"])
    doc.changed.clear()
    doc.removed.clear()
    return doc


def references(text: str) -> tuple[set[str], set[tuple[str, str]]]:
    """Block references and (kind, topic) mentions written in one block's text."""
    refs = {(match[1] or match[2]).lower() for match in REF.finditer(text)}
    topics = {("page", match[1].strip().casefold()) for match in WIKI.finditer(text)}
    topics.update(("tag", match[1].casefold()) for match in TAG.finditer(text))
    return refs, topics


def _is_edit(op: dict) -> bool:
    return not (op["op"] == "set" and "type" not in op and not any(is_edit_key(key) for key in op.get("props", {})))


class _Transaction:
    def __init__(self, conn: sqlite3.Connection, at: str):
        self.conn = conn
        self.at = at
        self.docs: dict[str, OrderedPageDoc] = {}
        self.logged: dict[str, list[dict]] = {}
        self.created: set[str] = set()

    def doc(self, page_id: str) -> OrderedPageDoc:
        if page_id not in self.docs:
            self.docs[page_id] = load_page(self.conn, page_id)
        return self.docs[page_id]

    def page_of(self, bid: str) -> str:
        for page_id, doc in self.docs.items():
            if bid in doc.blocks:
                return page_id
        row = self.conn.execute("SELECT page_id FROM blocks WHERE id=?", (bid,)).fetchone()
        if row is None:
            raise BlockError("Block not found", 404)
        self.doc(row["page_id"])
        return row["page_id"]

    def record(self, page_id: str, op: dict) -> None:
        self.doc(page_id).apply(op, self.at)
        self.logged.setdefault(page_id, []).append(op)

    def place(self, doc: OrderedPageDoc, parent_id: str, op: dict, moving: str | None = None) -> str:
        siblings = [item for item in doc.children(parent_id) if item.id != moving]
        if "after" in op and "before" in op:
            raise BlockError("Use either after or before")
        if "after" in op:
            if op["after"] is None:
                return key_between(None, siblings[0].order_key if siblings else None)
            index = next((i for i, item in enumerate(siblings) if item.id == op["after"]), None)
            if index is None:
                raise BlockError("The sibling to place after is not under that parent")
            following = siblings[index + 1].order_key if index + 1 < len(siblings) else None
            return key_between(siblings[index].order_key, following)
        if "before" in op:
            index = next((i for i, item in enumerate(siblings) if item.id == op["before"]), None)
            if index is None:
                raise BlockError("The sibling to place before is not under that parent")
            return key_between(siblings[index - 1].order_key if index else None, siblings[index].order_key)
        return key_between(siblings[-1].order_key if siblings else None, None)

    def apply(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            raise BlockError("Each operation must be an object")
        op = dict(raw)
        kind = op.get("op")
        if kind in {"release", "adopt"}:
            raise BlockError("Move blocks between pages with a move operation")
        if kind == "insert":
            if op.get("parent") is None:
                page_id = block_id(op.get("id"))
                if page_id in self.docs or self.conn.execute("SELECT 1 FROM blocks WHERE id=?", (page_id,)).fetchone():
                    raise BlockError("Block already exists", 409)
                self.docs[page_id] = OrderedPageDoc(page_id)
                self.created.add(page_id)
            else:
                page_id = self.page_of(block_id(op["parent"]))
            if "key" not in op:
                op["key"] = self.place(self.doc(page_id), op.get("parent"), op) if op.get("parent") else key_between(None, None)
            op.pop("after", None)
            op.pop("before", None)
            self.record(page_id, op)
            return
        target = block_id(op.get("id"))
        source = self.page_of(target)
        if kind != "move":
            self.record(source, op)
            return
        parent = block_id(op.get("parent"))
        destination = self.page_of(parent)
        if "key" not in op:
            op["key"] = self.place(self.doc(destination), parent, op, moving=target)
        op.pop("after", None)
        op.pop("before", None)
        if source == destination:
            self.record(source, op)
            return
        moving = self.doc(source).get(target)
        rows = [asdict(moving), *(asdict(item) for item in self.doc(source).descendants(target))]
        for row in rows:
            del row["page_id"]
        self.record(source, {"op": "release", "id": target})
        self.record(destination, {"op": "adopt", "parent": parent, "key": op["key"], "blocks": rows})

    def check_base(self, base: Any) -> None:
        if base is None:
            return
        if not isinstance(base, dict):
            raise BlockError("Base revisions must map page IDs to revisions")
        for page_id, revision in base.items():
            root = self.doc(block_id(page_id)).root
            if root is None:
                raise BlockError("Page not found", 404)
            if type(revision) is not int or root.properties.get("revision") != revision:
                raise BlockError("Page changed in another window; reload before saving", 409)

    def bump(self) -> dict[str, int | None]:
        revisions: dict[str, int | None] = {}
        for page_id, ops in self.logged.items():
            root = self.docs[page_id].root
            if root is None:
                revisions[page_id] = None
                continue
            current = root.properties.get("revision", 1)
            explicit = any(op["op"] == "set" and op.get("id") == page_id and "revision" in op.get("props", {}) for op in ops)
            if page_id not in self.created and not explicit and any(_is_edit(op) for op in ops):
                current += 1
                self.record(page_id, {"op": "set", "id": page_id, "props": {"revision": current}})
            revisions[page_id] = current
        return revisions

    def write(self, device: str, txn_id: str) -> dict[str, int]:
        conn, seqs = self.conn, {}
        projected: list[Block] = []
        for page_id in self.logged:
            self.docs[page_id].check()
        for page_id, ops in self.logged.items():
            doc = self.docs[page_id]
            payload = json.dumps({"v": LOG_FORMAT, "ops": ops}, ensure_ascii=False, separators=(",", ":")).encode()
            seqs[page_id] = conn.execute("INSERT INTO op_log(device_id,txn_id,page_id,at,ops) VALUES (?,?,?,?,?)",
                                         (device, txn_id, page_id, self.at, payload)).lastrowid
            if doc.removed:
                conn.executemany("DELETE FROM blocks WHERE id=?", [(bid,) for bid in doc.removed])
            changed = [doc.blocks[bid] for bid in doc.changed]
            projected.extend(changed)
            conn.executemany(
                "INSERT INTO blocks(id,type,parent_id,page_id,order_key,properties,text,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET type=excluded.type,parent_id=excluded.parent_id,"
                "page_id=excluded.page_id,order_key=excluded.order_key,properties=excluded.properties,text=excluded.text,"
                "created_at=excluded.created_at,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at",
                [(item.id, item.type, item.parent_id, item.page_id, item.order_key,
                  json.dumps(item.properties, ensure_ascii=False, separators=(",", ":")), item.text,
                  item.created_at, item.updated_at, item.deleted_at) for item in changed])
            conn.executemany("DELETE FROM block_refs WHERE source_id=?", [(item.id,) for item in changed])
            conn.executemany("DELETE FROM block_topics WHERE block_id=?", [(item.id,) for item in changed])
            for item in changed:
                if not item.live:
                    continue
                refs, topics = references(item.text)
                conn.executemany("INSERT INTO block_refs(source_id,target_id,kind) VALUES (?,?,'block')", [(item.id, ref) for ref in refs])
                conn.executemany("INSERT INTO block_topics(block_id,kind,topic) VALUES (?,?,?)", [(item.id, kind, topic) for kind, topic in topics])
        tagging.project(conn, projected, self.at)
        return seqs


def transact(conn: sqlite3.Connection, ops: Iterable[Any], *, base: Any = None, txn_id: str | None = None,
             at: str | None = None, suggest: bool = False) -> dict:
    """Apply one transaction atomically and return ``{txn_id, seq, revisions}`` keyed by page.

    ``base`` maps page IDs to the revision the caller last saw; a mismatch is a 409
    and nothing is written. Every content edit bumps the touched page's ``revision``
    (view-state properties in VIEW_KEYS and tag states do not).

    Auto-tags (Q13) ride along: *always attach* tags join the transaction's own
    operations, listed under ``tagged`` ({page_id: [ops]}) with their names under
    ``tags``; ``suggest`` also returns ``suggestions`` for every changed text block
    (kitty.core.blocks.tagging).
    """
    ops = list(ops) if not isinstance(ops, (str, bytes, dict)) else None
    if not ops or len(ops) > MAX_OPS:
        raise BlockError(f"A transaction carries 1–{MAX_OPS} operations")
    txn_id = block_id(txn_id) if txn_id is not None else str(uuid.uuid4())
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        if conn.execute("SELECT 1 FROM op_log WHERE txn_id=? LIMIT 1", (txn_id,)).fetchone():
            raise BlockError("Transaction already applied", 409)
        work = _Transaction(conn, at or now())
        work.check_base(base)
        for op in ops:
            work.apply(op)
        tags = tagging.annotate(conn, work.docs, work.record, suggest=suggest)
        revisions = work.bump()
        seqs = work.write(device_id(conn), txn_id)
    return {"txn_id": txn_id, "seq": seqs, "revisions": revisions, **tags}

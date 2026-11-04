"""Server-applied transactions: page documents -> append-only op log -> projection.

`transact` is the only writer of `blocks`, `op_log`, `block_refs`, `block_topics`,
`block_tags` and `tag_rejections` (the last two through kitty.core.blocks.tagging) and of
the block documents of the search index (kitty.core.blocks.searchdocs); merges from
paired devices (kitty.core.blocks.merge) write through the same
`_Transaction`. Inside one SQLite transaction it loads each touched page document
(its projection plus its Loro document, kitty.core.blocks.crdt), applies every
operation (so cycles, limits and stale revisions are refused before anything is
written), appends one `op_log` row per touched page carrying the readable operations,
the Loro update bytes and the actor that made it (kitty.core.blocks.actor), and rewrites
only the projection rows that changed.
Readers use the projection; history and the tests rebuild a page from the log's
operations with `replay`, which needs no Loro.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from kitty.core.blocks import actor as actors
from kitty.core.blocks import crdt, searchdocs, tagging
from kitty.core.blocks.actor import Actor
from kitty.core.blocks.crdt import LoroPageDoc
from kitty.core.blocks.order import key_between
from kitty.core.blocks.pagedoc import (
    Block,
    BlockError,
    OrderedPageDoc,
    block_id,
    is_edit_key,
)
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


def _rows(conn: sqlite3.Connection, page_id: str) -> tuple[dict[str, Block], dict[str, str]]:
    blocks, nodes = {}, {}
    cursor = conn.execute("SELECT * FROM blocks WHERE page_id=?", (page_id,))
    has_node = any(column[0] == "node" for column in cursor.description)
    for row in cursor:
        blocks[row["id"]] = _block(row)
        if has_node and row["node"]:
            nodes[row["id"]] = row["node"]
    return blocks, nodes


def load_page(conn: sqlite3.Connection, page_id: str) -> OrderedPageDoc:
    """The current projection of one page (deleted blocks included)."""
    return OrderedPageDoc(page_id, _rows(conn, page_id)[0])


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


def _has_crdt(conn: sqlite3.Connection) -> bool:
    """Whether the schema has the Loro tables (migrations before 038 transact without them)."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='page_crdt'").fetchone() is not None


def _has_actor(conn: sqlite3.Connection) -> bool:
    """Whether op_log records actors (migrations before 050 transact without them)."""
    return conn.execute("SELECT 1 FROM pragma_table_info('op_log') WHERE name='actor'").fetchone() is not None


class _Transaction:
    def __init__(self, conn: sqlite3.Connection, at: str):
        self.conn = conn
        self.at = at
        self.docs: dict[str, LoroPageDoc] = {}
        self.logged: dict[str, list[dict]] = {}
        self.created: set[str] = set()
        self.crdt = _has_crdt(conn)
        self.actors = _has_actor(conn)
        self.peer = crdt.peer_of(device_id(conn))
        self._titles: dict[Any, str | None] = {}

    def notebook_title(self, notebook_id: Any) -> str | None:
        if notebook_id not in self._titles:
            row = self.conn.execute("SELECT title FROM note_notebooks WHERE id=?", (notebook_id,)).fetchone() \
                if isinstance(notebook_id, int) else None
            self._titles[notebook_id] = row[0] if row else None
        return self._titles[notebook_id]

    def doc(self, page_id: str) -> LoroPageDoc:
        if page_id not in self.docs:
            blocks, nodes = _rows(self.conn, page_id)
            self.docs[page_id] = crdt.open_page(self.conn, page_id, self.peer, blocks=blocks, nodes=nodes,
                                                notebook_title=self.notebook_title) if self.crdt else OrderedPageDoc(page_id, blocks)
        return self.docs[page_id]

    def new_page(self, page_id: str) -> LoroPageDoc:
        """A page created in this transaction (continuing its Loro document if one was ever made)."""
        if not self.crdt:
            self.docs[page_id] = OrderedPageDoc(page_id)
            return self.docs[page_id]
        doc = crdt.open_page(self.conn, page_id, self.peer, blocks={}, nodes={}, notebook_title=self.notebook_title)
        doc.ensure()
        self.docs[page_id] = doc
        return doc

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
        if kind in {"release", "adopt", "put"}:
            raise BlockError("Move blocks between pages with a move operation" if kind != "put" else "Unknown block operation")
        if kind == "insert":
            if op.get("parent") is None:
                page_id = block_id(op.get("id"))
                if page_id in self.docs or self.conn.execute("SELECT 1 FROM blocks WHERE id=?", (page_id,)).fetchone():
                    raise BlockError("Block already exists", 409)
                self.new_page(page_id)
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

    def separate_keys(self) -> None:
        """Give a block placed with a sibling's key (a window with a stale copy) a key of its own.

        Same rule as ``crdt.project``: siblings sort by (key, ID) and a key not above the
        previous one moves between it and the next larger key.
        """
        for page_id, ops in list(self.logged.items()):
            doc = self.docs[page_id]
            parents = {op.get("parent") for op in ops if op["op"] in {"insert", "move", "adopt"} and op.get("parent")}
            for parent in sorted(parent for parent in parents if parent in doc.blocks and doc.blocks[parent].live):
                kids = doc.children(parent, deleted=True)
                previous: str | None = None
                for index, kid in enumerate(kids):
                    key = kid.order_key
                    if previous is not None and key <= previous:
                        following = next((other.order_key for other in kids[index + 1:] if other.order_key > previous), None)
                        key = key_between(previous, following)
                        self.record(page_id, {"op": "move", "id": kid.id, "parent": parent, "key": key})
                    previous = key

    def bump(self, edited: set[str] | None = None) -> dict[str, int | None]:
        """Bump the revision of every page this transaction edited (`edited`: decided by the caller)."""
        revisions: dict[str, int | None] = {}
        for page_id, ops in self.logged.items():
            root = self.docs[page_id].root
            if root is None:
                revisions[page_id] = None
                continue
            current = root.properties.get("revision", 1)
            explicit = any(op["op"] == "set" and op.get("id") == page_id and "revision" in op.get("props", {}) for op in ops)
            changed = page_id in edited if edited is not None else any(_is_edit(op) for op in ops)
            if page_id not in self.created and not explicit and changed:
                current += 1
                self.record(page_id, {"op": "set", "id": page_id, "props": {"revision": current}})
            revisions[page_id] = current
        return revisions

    def write(self, device: str, txn_id: str, actor: Actor, via: dict[str, tuple[Actor, ...]] | None = None) -> dict[str, int]:
        """Append the log rows and rewrite the projection; `via` gives a peer merge's carried actors per page."""
        conn, seqs = self.conn, {}
        projected: list[Block] = []
        removed: list[str] = []
        for page_id in self.logged:
            self.docs[page_id].check()
        columns = "device_id,txn_id,page_id,at,ops" + (",crdt" if self.crdt else "") + \
            (",actor,actor_name,actor_session,actor_via" if self.actors else "")
        insert = f"INSERT INTO op_log({columns}) VALUES ({','.join('?' * len(columns.split(',')))})"
        for page_id, ops in self.logged.items():
            doc = self.docs[page_id]
            payload = json.dumps({"v": LOG_FORMAT, "ops": ops}, ensure_ascii=False, separators=(",", ":")).encode()
            values: tuple = (device, txn_id, page_id, self.at, payload)
            update = doc.finish() if self.crdt else None
            if self.crdt:
                values += (update,)
            if self.actors:
                values += (Actor(actor.kind, actor.name, actor.session, via[page_id]) if via and via.get(page_id) else actor).columns()
            seqs[page_id] = conn.execute(insert, values).lastrowid
            if self.crdt:
                crdt.store(conn, doc, seqs[page_id], update, self.at)
                if doc.loro is not None:
                    crdt.keep(self.peer, page_id, doc.loro)
            if doc.removed:
                # Scoped to this page: the same ID may be landing on another page in this transaction.
                conn.executemany("DELETE FROM blocks WHERE id=? AND page_id=?", [(bid, page_id) for bid in doc.removed])
                removed.extend(doc.removed)
            changed = [doc.blocks[bid] for bid in doc.changed]
            projected.extend(changed)
            rows = [(item.id, item.type, item.parent_id, item.page_id, item.order_key,
                     json.dumps(item.properties, ensure_ascii=False, separators=(",", ":")), item.text,
                     item.created_at, item.updated_at, item.deleted_at) for item in changed]
            if self.crdt:
                conn.executemany(
                    "INSERT INTO blocks(id,type,parent_id,page_id,order_key,properties,text,created_at,updated_at,deleted_at,node) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET type=excluded.type,parent_id=excluded.parent_id,"
                    "page_id=excluded.page_id,order_key=excluded.order_key,properties=excluded.properties,text=excluded.text,"
                    "created_at=excluded.created_at,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at,node=excluded.node",
                    [(*row, doc.nodes.get(row[0])) for row in rows])
                conn.executemany("UPDATE blocks SET node=? WHERE id=?",
                                 [(doc.nodes.get(bid), bid) for bid in doc.renodes if bid not in doc.changed and bid in doc.blocks])
            else:
                conn.executemany(
                    "INSERT INTO blocks(id,type,parent_id,page_id,order_key,properties,text,created_at,updated_at,deleted_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET type=excluded.type,parent_id=excluded.parent_id,"
                    "page_id=excluded.page_id,order_key=excluded.order_key,properties=excluded.properties,text=excluded.text,"
                    "created_at=excluded.created_at,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at", rows)
            conn.executemany("DELETE FROM block_refs WHERE source_id=?", [(item.id,) for item in changed])
            conn.executemany("DELETE FROM block_topics WHERE block_id=?", [(item.id,) for item in changed])
            for item in changed:
                if not item.live:
                    continue
                refs, topics = references(item.text)
                conn.executemany("INSERT INTO block_refs(source_id,target_id,kind) VALUES (?,?,'block')", [(item.id, ref) for ref in refs])
                conn.executemany("INSERT INTO block_topics(block_id,kind,topic) VALUES (?,?,?)", [(item.id, kind, topic) for kind, topic in topics])
        tagging.project(conn, projected, self.at)
        searchdocs.project(conn, projected, removed)
        return seqs


def transact(conn: sqlite3.Connection, ops: Iterable[Any], *, base: Any = None, txn_id: str | None = None,
             at: str | None = None, suggest: bool = False, actor: Actor | None = None) -> dict:
    """Apply one transaction atomically and return ``{txn_id, seq, revisions}`` keyed by page.

    ``base`` maps page IDs to the revision the caller last saw; a mismatch is a 409
    and nothing is written. Every content edit bumps the touched page's ``revision``
    (view-state properties in VIEW_KEYS and tag states do not).

    Auto-tags (Q13) ride along: *always attach* tags join the transaction's own
    operations, listed under ``tagged`` ({page_id: [ops]}) with their names under
    ``tags``; ``suggest`` also returns ``suggestions`` for every changed text block
    (kitty.core.blocks.tagging).

    Its log rows record ``actor`` (default: the one set with ``actor.acting``, else the
    person at this device).
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
        if work.crdt:
            work.separate_keys()
        tags = tagging.annotate(conn, work.docs, work.record, suggest=suggest)
        revisions = work.bump()
        seqs = work.write(device_id(conn), txn_id, actor or actors.current())
    return {"txn_id": txn_id, "seq": seqs, "revisions": revisions, **tags}

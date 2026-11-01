"""Merging another device's changes into this device's pages (Q5, Q16).

A paired device sends Loro update bytes per page. ``merge`` imports them into each
page's Loro document, turns the merged document back into blocks (``crdt.project``)
and applies the difference to the projection as one ordinary transaction: one
``op_log`` row per page with readable operations (``put`` for a block that changed,
a ``text`` splice before it when its text did, ``purge`` for one that is gone) and the
Loro update bytes. So History, live push to every window, block refs, topics and
tags see a merge exactly like a local edit, and windows update live. *Always* tags
attach to merged text like to typed text; the revision windows use to detect stale
edits moves only when content changed.

Merging is deterministic: the projection is a function of the Loro document alone,
and every fix-up below is computed from it, so two devices that hold the same
changes show identical pages whatever order the changes arrived in.

- A page that arrives for the first time gets this device's next page number and is
  filed under its own notebook of the same title (created if missing).
- A block moved to two different pages on two devices exists on both after the
  merge; the copy on the page with the smaller ID keeps the block ID and the other
  becomes a copy with its own ID (written into the document, so every device agrees).

``export``, ``vv`` and ``seed`` are what the sync protocol needs from the other side.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Iterable

from loro import ExportMode, VersionVector

from kitty.core.blocks import crdt
from kitty.core.blocks import store as _store
from kitty.core.blocks import tagging
from kitty.core.blocks.crdt import LOCAL_KEYS, LoroPageDoc, canonical
from kitty.core.blocks.pagedoc import Block, is_edit_key
from kitty.db.connection import transaction

#: Root types the Notes, Documents and Quick notes APIs address by number.
NUMBERED = frozenset({"page", "document", "quick_note"})
#: Root types filed in a notebook.
FILED = frozenset({"page", "document"})
DEFAULT_NOTEBOOK = "Notes"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _put(item: Block, *, text: bool) -> dict:
    op = {"op": "put", "id": item.id, "type": item.type, "parent": item.parent_id, "key": item.order_key,
          "props": item.properties, "created": item.created_at, "updated": item.updated_at, "deleted": item.deleted_at}
    if text:
        op["text"] = item.text
    return op


def _splice(before: str, after: str) -> dict:
    start = 0
    limit = min(len(before), len(after))
    while start < limit and before[start] == after[start]:
        start += 1
    end = 0
    while end < len(before) - start and end < len(after) - start and before[-1 - end] == after[-1 - end]:
        end += 1
    return {"at": start, "del": len(before) - start - end, "ins": after[start:len(after) - end]}


def _edits(old: Block, new: Block) -> bool:
    """Whether turning `old` into `new` changes content (not only tag states or view state)."""
    if (old.type, old.parent_id, old.order_key, old.text, old.deleted_at) != (new.type, new.parent_id, new.order_key, new.text, new.deleted_at):
        return True
    keys = (old.properties.keys() | new.properties.keys())
    return any(is_edit_key(key) and canonical(old.properties.get(key)) != canonical(new.properties.get(key)) for key in keys)


def diff(view: dict[str, Block], merged: dict[str, Block]) -> tuple[list[dict], bool]:
    """Operations that turn `view` into `merged` (parents first), and whether content changed."""
    ops: list[dict] = []
    edited = False
    children: dict[str | None, list[Block]] = {}
    for item in merged.values():
        children.setdefault(item.parent_id, []).append(item)
    stack = sorted(children.get(None, []), key=lambda item: item.id, reverse=True)
    while stack:
        item = stack.pop()
        stack.extend(sorted(children.get(item.id, []), key=lambda child: (child.order_key, child.id), reverse=True))
        old = view.get(item.id)
        if old is None:
            ops.append(_put(item, text=True))
            edited = True
            continue
        if old.text != item.text:
            ops.append({"op": "text", "id": item.id, **_splice(old.text, item.text)})
        if (old.type, old.parent_id, old.order_key, canonical(old.properties), old.created_at, old.updated_at, old.deleted_at,
                old.text) != (item.type, item.parent_id, item.order_key, canonical(item.properties), item.created_at,
                              item.updated_at, item.deleted_at, item.text):
            ops.append(_put(item, text=False))
            edited = edited or _edits(old, item)
    removed = view.keys() - merged.keys()
    for bid in sorted(removed):
        if view[bid].parent_id not in removed:
            ops.append({"op": "purge", "id": bid})
            edited = True
    return ops, edited


class _Merge:
    def __init__(self, conn: sqlite3.Connection, work: _store._Transaction):
        self.conn = conn
        self.work = work
        #: Pages this merge reconciled (a merge may also rename copies on other pages).
        self.reconciled: set[str] = set()
        self._numbers: dict[str, int] = {}
        self._notebooks: dict[str, int] = {}

    def number(self, kind: str) -> int:
        if kind not in self._numbers:
            self._numbers[kind] = self.conn.execute(
                "SELECT COALESCE(MAX(CAST(json_extract(properties,'$.number') AS INTEGER)),0) FROM blocks "
                "WHERE type=? AND parent_id IS NULL", (kind,)).fetchone()[0]
        self._numbers[kind] += 1
        return self._numbers[kind]

    def notebook(self, title: str | None) -> int:
        """This device's notebook titled `title` (the first, or a new one)."""
        title = (title or DEFAULT_NOTEBOOK)[:200]
        if title not in self._notebooks:
            row = self.conn.execute("SELECT id FROM note_notebooks WHERE title=? ORDER BY id LIMIT 1", (title,)).fetchone()
            if row is None:
                stamp = self.work.at
                row = (self.conn.execute("INSERT INTO note_notebooks(title,subject,project,created_at,updated_at) VALUES (?,?,?,?,?)",
                                         (title, "", "", stamp, stamp)).lastrowid,)
            self._notebooks[title] = row[0]
        return self._notebooks[title]

    def reconcile(self, doc: LoroPageDoc, notebook_before: str | None) -> None:
        """Bring the projection of `doc` to what its (merged) Loro document says."""
        page_id = doc.page_id
        merged = crdt.project(doc.loro, page_id)
        # Copy IDs and distinct keys depend on other nodes; written into the document they
        # stay what this projection says however later local edits move the neighbours.
        crdt.materialize(doc.loro, merged.fixes)
        for bid, item in merged.blocks.items():
            old = doc.blocks.get(bid)
            if old is not None:
                item.properties.update((key, value) for key, value in old.properties.items() if key in LOCAL_KEYS)
        root = merged.blocks.get(page_id)
        if root is not None:
            old_root = doc.blocks.get(page_id)
            if old_root is None:
                root.properties["revision"] = 1
                if root.type in NUMBERED:
                    root.properties["number"] = self.number(root.type)
            if root.type in FILED and (old_root is None or (merged.notebook != notebook_before and merged.notebook)):
                root.properties["notebook_id"] = self.notebook(merged.notebook)
        ops, edited = diff(doc.blocks, merged.blocks)
        if edited and root is not None and doc.blocks.get(page_id) is not None:
            # Windows holding the old revision must reload; the bump rides in the root's
            # put so the root keeps the merged ``updated`` (the transaction time is local).
            root.properties["revision"] = int(doc.blocks[page_id].properties.get("revision", 1)) + 1
            ops, _ = diff(doc.blocks, merged.blocks)
        doc.mirror = False
        try:
            for op in ops:
                self.work.record(page_id, op)
        finally:
            doc.mirror = True
        before = doc.nodes
        doc.nodes = dict(merged.nodes)
        doc.renodes = {bid for bid, name in doc.nodes.items() if before.get(bid) != name}
        self.work.logged.setdefault(page_id, [])
        self.reconciled.add(page_id)

    def separate_copies(self) -> None:
        """A block ID on two pages: the page with the smaller ID keeps it; the other copy gets its own ID."""
        while True:
            fresh: dict[str, set[str]] = {}
            for page_id in self.reconciled:
                doc = self.work.docs[page_id]
                for bid in doc.blocks:
                    fresh.setdefault(bid, set()).add(page_id)
            ids = sorted(fresh)
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                for row in self.conn.execute(f"SELECT id, page_id FROM blocks WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                    if row["page_id"] not in self.work.docs or row["id"] in self.work.docs[row["page_id"]].blocks:
                        fresh[row["id"]].add(row["page_id"])
            clashes = {bid: pages for bid, pages in fresh.items() if len(pages) > 1}
            if not clashes:
                return
            renamed: set[str] = set()
            for bid, pages in sorted(clashes.items()):
                for page_id in sorted(pages)[1:]:
                    doc = self.work.doc(page_id)
                    doc.ensure()
                    meta = doc.tree.get_meta(crdt.parse_node(doc.nodes[bid]))
                    meta.insert("id", crdt.copy_id(bid, page_id))
                    renamed.add(page_id)
            for page_id in sorted(renamed):
                doc = self.work.docs[page_id]
                self.reconcile(doc, crdt.notebook_of(doc.loro, page_id))


def merge(conn: sqlite3.Connection, updates: dict[str, Iterable[bytes]], *, origin: str, at: str | None = None) -> dict:
    """Import Loro updates (``{page_id: [bytes]}``) from device `origin` as one transaction.

    Returns ``{"seq": {page_id: seq}, "pages": [page_id, ...] (changed), "revisions"}``.
    """
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        work = _store._Transaction(conn, at or _now())
        if not work.crdt:
            raise RuntimeError("Peer sync needs migration 038")
        merging = _Merge(conn, work)
        for page_id in sorted(updates):
            blobs = [bytes(blob) for blob in updates[page_id] if blob]
            if not blobs:
                continue
            doc = work.doc(page_id)
            doc.ensure()
            notebook_before = crdt.notebook_of(doc.loro, page_id)
            vv = doc.loro.oplog_vv
            doc.loro.import_batch(blobs)
            if doc.loro.oplog_vv == vv:
                continue
            merging.reconcile(doc, notebook_before)
        if not merging.reconciled:
            return {"seq": {}, "pages": [], "revisions": {}}
        merging.separate_copies()
        tagging.annotate(conn, work.docs, work.record)
        revisions = work.bump(set())
        seqs = work.write(origin, str(uuid.uuid4()))
    return {"seq": seqs, "pages": sorted(merging.reconciled), "revisions": revisions}


# What the other side of a sync needs ------------------------------------------------

def vv(conn: sqlite3.Connection, page_id: str) -> bytes | None:
    """The page's version vector (Loro encoding), or None when it has no Loro document."""
    row = conn.execute("SELECT vv FROM page_crdt WHERE page_id=?", (page_id,)).fetchone()
    return bytes(row[0]) if row else None


def seed(conn: sqlite3.Connection, page_ids: Iterable[str]) -> int:
    """Give each page that has none a Loro document made from its projection; returns how many."""
    count = 0
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        work = _store._Transaction(conn, _now())
        for page_id in page_ids:
            if conn.execute("SELECT 1 FROM page_crdt WHERE page_id=?", (page_id,)).fetchone():
                continue
            doc = work.doc(page_id)
            if not doc.blocks:
                continue
            doc.ensure()
            seq = conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log WHERE page_id=?", (page_id,)).fetchone()[0]
            crdt.snapshot(conn, page_id, doc.loro, seq, work.at)
            conn.executemany("UPDATE blocks SET node=? WHERE id=?", [(doc.nodes[bid], bid) for bid in doc.renodes])
            crdt.keep(work.peer, page_id, doc.loro)
            count += 1
    return count


def export(conn: sqlite3.Connection, page_id: str, since: bytes | None) -> bytes | None:
    """The page's Loro changes a holder of version vector `since` lacks (all of it for None)."""
    peer = crdt.peer_of(_store.device_id(conn))
    doc = crdt.load(conn, page_id, peer)
    if doc is None:
        return None
    try:
        if since is None:
            return doc.export(ExportMode.Snapshot())
        known = VersionVector.decode(since)
        if known.includes_vv(doc.oplog_vv):
            return b""
        return doc.export(ExportMode.Updates(known))
    finally:
        crdt.keep(peer, page_id, doc)

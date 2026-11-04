"""Loro page documents: what devices merge (platform design Q5, build step 3).

Every page is one Loro document. Its ``blocks`` tree (LoroTree) holds one node per
block; the tree gives structure and Kleppmann's move operation, so concurrent moves
never make a cycle (A into B on one device, B into A on another leaves one of them
moved). Each node's metadata map holds the block:

- ``id`` (the block UUID), ``type``, ``key`` (kitty's fractional order key),
  ``created``, ``updated`` and ``deleted`` (its Trash stamp): last-writer-wins values;
- ``props``: a map, so two devices setting different properties both keep theirs;
- ``text``: a LoroText, so concurrent typing in one block interleaves instead of one
  side winning.

Sibling order is kitty's own fractional key rather than Loro's fractional index: the
editor makes keys before the server sees them (it echoes keystrokes locally), and
the key space it shares with ``order.py`` stays the one in the log. Loro's index is
switched off. Two devices inserting at the same spot make the same key; the
projection orders equal keys by block ID and gives the later ones distinct keys
between their neighbours, so the editor can always insert between any two blocks.

The server is still the single writer on each device (Q6). ``LoroPageDoc`` is the
``PageDoc`` transactions apply operations to: it validates them exactly like
``OrderedPageDoc`` against the projection, then mirrors the result into the page's
Loro document (a text splice stays a splice). A page with no Loro document yet is
seeded from its projection on its first operation. The SQLite projection stays the
read path; ``project`` turns a merged document back into blocks, and it is a pure
function of the document, so every device that holds the same changes shows the
same page.

Some properties belong to one device's copy and never sync (``LOCAL_KEYS``): the
revision windows use to detect stale edits, the page number the Notes API addresses
a page by, the notebook row it is filed in, and view state such as the canvas
viewport. A page carries its notebook's title instead (``notebook``), so a device
files an arriving page under its own notebook of that name.

Storage: ``page_crdt`` keeps a snapshot; each ``op_log`` row written after it carries
its transaction's Loro update bytes (``crdt``), so a page loads as snapshot + tail and
a snapshot is rewritten every ``SNAPSHOT_EVERY`` rows.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from loro import ExportMode, LoroDoc, LoroMap, LoroText, TreeID, ValueOrContainer, VersionVector

from kitty.core.blocks import order
from kitty.core.blocks.pagedoc import Block, OrderedPageDoc

#: Properties of one device's copy of a block: never written to the Loro document.
LOCAL_KEYS = frozenset({"revision", "number", "notebook_id", "viewport", "viewport_revision", "last_opened"})
#: The synced stand-in for a root block's local ``notebook_id``: the notebook's title.
NOTEBOOK = "notebook"
TREE = "blocks"
SNAPSHOT_EVERY = 64
#: Texts longer than this are diffed line by line (Loro's character diff is quadratic).
LINE_DIFF_ABOVE = 50_000
#: Namespace for the IDs given to extra copies of one block (see ``project``).
COPY_NAMESPACE = uuid.UUID("5f0d1d5e-8a1b-4c7e-9a37-6b2f4f3c2a11")

NotebookTitle = Callable[[Any], "str | None"]


def peer_of(device: str) -> int:
    """This device's Loro peer ID, derived from its 128-bit device ID (never 0)."""
    return (int(device[:16], 16) >> 1) or 1


def node_name(node: TreeID) -> str:
    return f"{node.counter}@{node.peer}"


def parse_node(name: str) -> TreeID:
    counter, peer = name.split("@")
    return TreeID(int(peer), int(counter))


def _node_order(name: str) -> tuple[int, int]:
    counter, peer = name.split("@")
    return int(peer), int(counter)


def canonical(value: Any) -> str:
    """JSON for comparing property values (``True``, ``1`` and ``1.0`` differ)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def synced(item: Block, notebook_title: NotebookTitle | None = None) -> dict[str, Any]:
    """The properties of `item` that live in the Loro document."""
    props = {key: value for key, value in item.properties.items() if key not in LOCAL_KEYS and key != NOTEBOOK}
    if item.parent_id is None and notebook_title is not None and item.properties.get("notebook_id") is not None:
        title = notebook_title(item.properties["notebook_id"])
        if title:
            props[NOTEBOOK] = title
    return props


def new_doc(peer: int) -> LoroDoc:
    doc = LoroDoc()
    doc.peer_id = peer
    doc.get_tree(TREE).disable_fractional_index()
    return doc


def _child(meta: LoroMap, key: str, kind: type) -> Any:
    found = meta.get(key)
    if ValueOrContainer.is_container(found) and isinstance(found.container, kind):
        return found.container
    return meta.insert_container(key, kind())


class LoroPageDoc(OrderedPageDoc):
    """A page document whose applied operations are mirrored into its Loro document.

    ``nodes`` maps block IDs to the tree nodes they were projected from. With
    ``mirror`` off (a merge applying an already-merged result) operations only change
    the projection.
    """

    def __init__(self, page_id: str, blocks: dict[str, Block] | None, nodes: dict[str, str], *, loro: LoroDoc | None,
                 peer: int, notebook_title: NotebookTitle | None = None):
        super().__init__(page_id, blocks)
        self.nodes = nodes
        self.loro = loro
        self.peer = peer
        self.notebook_title = notebook_title
        self.mirror = True
        #: The Loro document was made from the projection during this transaction.
        self.seeded = False
        #: Blocks whose ``node`` changed without the block itself changing.
        self.renodes: set[str] = set()
        self.start_vv: VersionVector = loro.oplog_vv if loro is not None else VersionVector()
        self._touched: dict[str, None] | None = None
        self._gone: list[list[Block]] = []

    @property
    def tree(self):
        return self.loro.get_tree(TREE)

    # Change tracking ----------------------------------------------------------------

    def _touch(self, bid: str) -> None:
        super()._touch(bid)
        if self._touched is not None:
            self._touched[bid] = None

    def _forget(self, subtree: list[Block]) -> None:
        super()._forget(subtree)
        if self._touched is not None:
            self._gone.append(subtree)
            for item in subtree:
                self._touched.pop(item.id, None)

    def apply(self, op: dict, at: str) -> None:
        if not self.mirror:
            super().apply(op, at)
            return
        self.ensure()
        self._touched, self._gone = {}, []
        try:
            super().apply(op, at)
            touched, gone = list(self._touched), self._gone
        finally:
            self._touched, self._gone = None, []
        tree = self.tree
        for subtree in gone:
            name = self.nodes.get(subtree[0].id)
            if name is not None and not tree.is_node_deleted(parse_node(name)):
                tree.delete(parse_node(name))
            for item in subtree:
                if self.nodes.pop(item.id, None) is not None:
                    self.renodes.discard(item.id)
        splice = op if op.get("op") == "text" and "text" not in op else None
        for bid in sorted(touched, key=self.depth):
            self._write(self.blocks[bid], splice if splice is not None and splice.get("id") == bid else None)

    # Mirroring ----------------------------------------------------------------------

    def ensure(self) -> None:
        """Give the page a Loro document, seeding it from the projection if it has none."""
        if self.loro is not None:
            return
        self.loro = new_doc(self.peer)
        if self.blocks:
            for item in sorted(self.blocks.values(), key=lambda item: (self.depth(item.id), item.order_key, item.id)):
                self._create(item)
                self.renodes.add(item.id)
            self.loro.commit()
            self.seeded = True
        self.start_vv = self.loro.oplog_vv

    def _create(self, item: Block) -> None:
        tree = self.tree
        parent = None if item.parent_id is None else parse_node(self.nodes[item.parent_id])
        node = tree.create(parent)
        meta = tree.get_meta(node)
        for key, value in (("id", item.id), ("type", item.type), ("key", item.order_key),
                           ("created", item.created_at), ("updated", item.updated_at)):
            meta.insert(key, value)
        if item.deleted_at is not None:
            meta.insert("deleted", item.deleted_at)
        props = meta.insert_container("props", LoroMap())
        for key, value in synced(item, self.notebook_title).items():
            props.insert(key, value)
        text = meta.insert_container("text", LoroText())
        if item.text:
            text.insert(0, item.text)
        self.nodes[item.id] = node_name(node)

    def _write(self, item: Block, splice: dict | None) -> None:
        name = self.nodes.get(item.id)
        if name is None:
            self._create(item)
            self.renodes.add(item.id)
            return
        tree, node = self.tree, parse_node(name)
        meta = tree.get_meta(node)
        if item.parent_id is not None:
            parent = parse_node(self.nodes[item.parent_id])
            if tree.parent(node) != parent:
                tree.mov(node, parent)
        have = meta.get_deep_value()
        for key, value in (("id", item.id), ("type", item.type), ("key", item.order_key),
                           ("created", item.created_at), ("updated", item.updated_at)):
            if have.get(key) != value:
                meta.insert(key, value)
        if have.get("deleted") != item.deleted_at:
            if item.deleted_at is None:
                meta.delete("deleted")
            else:
                meta.insert("deleted", item.deleted_at)
        want, old = synced(item, self.notebook_title), have.get("props") if isinstance(have.get("props"), dict) else {}
        if canonical(want) != canonical(old):
            props = _child(meta, "props", LoroMap)
            for key in old.keys() - want.keys():
                props.delete(key)
            for key, value in want.items():
                if key not in old or canonical(old[key]) != canonical(value):
                    props.insert(key, value)
        before = have.get("text")
        if before != item.text:
            text = _child(meta, "text", LoroText)
            if splice is not None and isinstance(before, str):
                at, length, insert = splice["at"], splice.get("del", 0), splice.get("ins", "")
                if before[:at] + insert + before[at + length:] == item.text:
                    text.splice(at, length, insert)
                    return
            if len(item.text) > LINE_DIFF_ABOVE:
                text.update_by_line(item.text)
            else:
                text.update(item.text)

    def finish(self) -> bytes | None:
        """Commit; the Loro update bytes of this transaction (None when the document did not change)."""
        if self.loro is None:
            return None
        self.loro.commit()
        if self.loro.oplog_vv == self.start_vv:
            return None
        return self.loro.export(ExportMode.Updates(self.start_vv))


# Projection -------------------------------------------------------------------------

@dataclass
class Projected:
    """A page as its Loro document says it is (no local properties)."""

    blocks: dict[str, Block] = field(default_factory=dict)
    nodes: dict[str, str] = field(default_factory=dict)
    #: The root's synced notebook title.
    notebook: str | None = None
    #: Nodes whose projected ``id`` or ``key`` differs from the one stored (a copy's
    #: own ID, a key made distinct): ``{node: {field: value}}``. A merge writes them into
    #: the document, so they stay put however the neighbours change later.
    fixes: dict[str, dict[str, str]] = field(default_factory=dict)


def _string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def copy_id(bid: str, where: str) -> str:
    """The ID an extra copy of block `bid` gets (`where`: its node, or the page holding it)."""
    return str(uuid.uuid5(COPY_NAMESPACE, f"{bid}/{where}"))


def project(doc: LoroDoc, page_id: str) -> Projected:
    """The blocks of `page_id` in `doc`, deterministically.

    - The root is the tree root whose ``id`` is the page (the oldest such node).
    - A block ID held by two nodes (one block re-created on two devices) keeps the
      oldest node; the others get ``copy_id``, so nothing is dropped.
    - A block is in Trash when it or an ancestor is: a block added on one device under
      a block deleted on another goes to Trash with it, and restoring the parent brings
      it back.
    - Siblings sort by (key, ID); a key equal to or below the previous sibling's gets
      a key between its neighbours.

    Copy IDs and distinct keys depend on other nodes, so they are listed in ``fixes``.
    """
    roots = [item for item in doc.get_tree(TREE).get_value_with_meta() if item.get("meta", {}).get("id") == page_id]
    if not roots:
        return Projected()
    root = min(roots, key=lambda item: _node_order(item["id"]))
    entries: list[tuple[dict, str | None]] = []
    stack: list[tuple[dict, str | None]] = [(root, None)]
    while stack:
        node, parent = stack.pop()
        entries.append((node, parent))
        stack.extend((child, node["id"]) for child in node.get("children", ()))
    holders: dict[str, list[str]] = {}
    for node, _parent in entries:
        holders.setdefault(_string(node["meta"].get("id")), []).append(node["id"])
    ids: dict[str, str] = {}
    for bid, names in holders.items():
        names.sort(key=lambda name: (name != root["id"], _node_order(name)))
        for index, name in enumerate(names):
            keep = index == 0 and (_uuid(bid) and (bid != page_id or name == root["id"]))
            ids[name] = bid if keep else copy_id(bid, name)
    ids[root["id"]] = page_id
    result = Projected(notebook=_string(root["meta"].get("props", {}).get(NOTEBOOK)) or None)

    def build(node: dict, parent: str | None, deleted: str | None, key: str) -> None:
        meta = node["meta"]
        bid = ids[node["id"]]
        if parent is not None and meta.get("id") != bid:
            result.fixes.setdefault(node["id"], {})["id"] = bid
        if parent is not None and meta.get("key") != key:
            result.fixes.setdefault(node["id"], {})["key"] = key
        own = meta.get("deleted")
        deleted = own if isinstance(own, str) and own else deleted
        props = meta.get("props") if isinstance(meta.get("props"), dict) else {}
        props = {name: value for name, value in props.items() if name != NOTEBOOK}
        created = _string(meta.get("created"))
        result.blocks[bid] = Block(bid, _string(meta.get("type"), "text") or "text", parent, page_id, key, props,
                                   _string(meta.get("text")), created, _string(meta.get("updated"), created), deleted)
        result.nodes[bid] = node["id"]
        children = node.get("children", ())
        for child, child_key in zip(children, _distinct_keys(children, ids)):
            build(child, bid, deleted, child_key)

    root_key = _string(root["meta"].get("key"))
    build(root, None, None, root_key if _valid_key(root_key) else order.key_between(None, None))
    return result


def materialize(doc: LoroDoc, fixes: dict[str, dict[str, str]]) -> None:
    """Write a projection's ``fixes`` into the document (as this device's changes)."""
    tree = doc.get_tree(TREE)
    for name, fields in fixes.items():
        meta = tree.get_meta(parse_node(name))
        for key, value in fields.items():
            meta.insert(key, value)


def notebook_of(doc: LoroDoc, page_id: str) -> str | None:
    """The synced notebook title of the page's root (without projecting the page)."""
    tree = doc.get_tree(TREE)
    found = [node for node in tree.roots if tree.get_meta(node).get_value().get("id") == page_id]
    if not found:
        return None
    props = tree.get_meta(min(found, key=lambda node: (node.peer, node.counter))).get("props")
    if not ValueOrContainer.is_container(props):
        return None
    title = props.container.get_value().get(NOTEBOOK)
    return title if isinstance(title, str) and title else None


def _valid_key(key: str) -> bool:
    try:
        order.validate(key)
    except ValueError:
        return False
    return True


def _distinct_keys(children: Any, ids: dict[str, str]) -> list[str]:
    """Order keys for `children` (in their given order) that are distinct and keep their sort order."""
    entries = []
    for index, child in enumerate(children):
        key = _string(child["meta"].get("key"))
        entries.append((key if _valid_key(key) else None, ids[child["id"]], index))
    ordered = sorted(entries, key=lambda entry: (entry[0] is None, entry[0] or "", entry[1]))
    keys: list[str] = [""] * len(entries)
    previous: str | None = None
    for position, (key, _bid, index) in enumerate(ordered):
        if key is None or (previous is not None and key <= previous):
            following = next((other for other, _b, _i in ordered[position + 1:] if other is not None and (previous is None or other > previous)), None)
            key = order.key_between(previous, following)
        keys[index] = key
        previous = key
    return keys


# Storage ----------------------------------------------------------------------------

#: Documents of recently written pages, by (peer, page). A transaction takes its page's
#: document out and puts it back only after writing, so a failed transaction cannot
#: leave changes behind; the stored version vector says whether it is still current
#: (another process may have written the page since).
_CACHE: OrderedDict[tuple[int, str], LoroDoc] = OrderedDict()
_CACHE_LOCK = threading.Lock()
CACHE_SIZE = 32


def keep(peer: int, page_id: str, doc: LoroDoc) -> None:
    """Return a document to the cache once its changes are written."""
    with _CACHE_LOCK:
        _CACHE[(peer, page_id)] = doc
        _CACHE.move_to_end((peer, page_id))
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


def load(conn: sqlite3.Connection, page_id: str, peer: int) -> LoroDoc | None:
    """The page's Loro document (snapshot + the op_log tail after it), or None."""
    row = conn.execute("SELECT vv, snapshot_seq FROM page_crdt WHERE page_id=?", (page_id,)).fetchone()
    if row is None:
        return None
    with _CACHE_LOCK:
        cached = _CACHE.pop((peer, page_id), None)
    if cached is not None and cached.oplog_vv == VersionVector.decode(row["vv"]):
        return cached
    doc = LoroDoc()
    doc.import_(conn.execute("SELECT snapshot FROM page_crdt WHERE page_id=?", (page_id,)).fetchone()[0])
    tail = [item[0] for item in conn.execute(
        "SELECT crdt FROM op_log WHERE page_id=? AND seq>? AND crdt IS NOT NULL ORDER BY seq", (page_id, row["snapshot_seq"]))]
    if tail:
        doc.import_batch(tail)
    doc.peer_id = peer
    doc.get_tree(TREE).disable_fractional_index()
    return doc


def store(conn: sqlite3.Connection, doc: LoroPageDoc, seq: int, update: bytes | None, at: str) -> None:
    """Record a transaction's effect on the page's Loro document (its op_log row is `seq`)."""
    if doc.loro is None:
        return
    row = conn.execute("SELECT tail FROM page_crdt WHERE page_id=?", (doc.page_id,)).fetchone()
    vv = doc.loro.oplog_vv.encode()
    if row is None or doc.seeded or row["tail"] + 1 >= SNAPSHOT_EVERY:
        snapshot(conn, doc.page_id, doc.loro, seq, at)
    elif update is not None:
        conn.execute("UPDATE page_crdt SET tail=tail+1, vv=?, updated_at=? WHERE page_id=?", (vv, at, doc.page_id))


def snapshot(conn: sqlite3.Connection, page_id: str, loro: LoroDoc, seq: int, at: str) -> None:
    """Rewrite the page's snapshot; `seq` is the newest op_log row it includes."""
    conn.execute("INSERT INTO page_crdt(page_id,snapshot,snapshot_seq,tail,vv,updated_at) VALUES (?,?,?,0,?,?) "
                 "ON CONFLICT(page_id) DO UPDATE SET snapshot=excluded.snapshot,snapshot_seq=excluded.snapshot_seq,"
                 "tail=0,vv=excluded.vv,updated_at=excluded.updated_at",
                 (page_id, loro.export(ExportMode.Snapshot()), seq, loro.oplog_vv.encode(), at))


def open_page(conn: sqlite3.Connection, page_id: str, peer: int, *, blocks: dict[str, Block], nodes: dict[str, str],
              notebook_title: NotebookTitle | None) -> LoroPageDoc:
    """A transaction's document for one page: its projection plus its Loro document."""
    loro = load(conn, page_id, peer)
    doc = LoroPageDoc(page_id, blocks, nodes, loro=loro, peer=peer, notebook_title=notebook_title)
    if loro is not None and any(bid not in nodes for bid in blocks):
        # Projection rows without their node (written by older code): find them by ID.
        found = project(loro, page_id)
        for bid in blocks:
            if bid not in nodes and bid in found.nodes:
                nodes[bid] = found.nodes[bid]
                doc.renodes.add(bid)
    return doc

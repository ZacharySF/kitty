"""One page's blocks in memory, and the operations that change them.

`PageDoc` is the seam the platform design (Q5) asks for: the server applies every
operation to a page document before it reaches the log and the projection. Build
step 1 has a single writer (Q6), so `OrderedPageDoc` applies operations in the
server's total order and needs no merging. Peer sync (build step 3) puts a Loro
document behind the same interface; the operation vocabulary below is what both
implement and what the operation log records.

Operations (each targets one block of this page):

- ``insert``  {id, type, parent, key, props?, text?, created?, updated?}
- ``set``     {id, props?, type?}: a ``None`` property value removes the key
- ``text``    {id, text} replaces; {id, at, del, ins} splices (code points)
- ``move``    {id, parent, key}: refused when it would create a cycle
- ``delete``  {id}: moves the block and its live subtree to Trash (``deleted_at``)
- ``restore`` {id}: brings back everything that one delete sent to Trash
- ``purge``   {id}: removes the block and its subtree for good
- ``release`` {id} / ``adopt`` {parent, key, blocks}: the two halves of a move
  between pages, logged on the source and the destination page respectively.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Protocol

from kitty.core.blocks import order

#: Property keys that record how a page is viewed, not what it contains: changing
#: only these is not an edit (no revision bump, no ``updated_at`` change).
VIEW_KEYS = frozenset({"viewport", "viewport_revision", "favorite", "last_opened", "position"})
#: A block's state for one tag (Q13): ``tag_<tag id without dashes>`` is ``manual``,
#: ``auto`` or ``accepted`` (attached) or ``rejected``. Tagging is not an edit either:
#: attaching a tag while someone types must not move the page revision under them.
TAG_KEY = re.compile(r"tag_[0-9a-f]{32}\Z")
TAG_STATES = frozenset({"manual", "auto", "accepted", "rejected"})
MAX_PAGE_BLOCKS = 10_000
MAX_DEPTH = 64
MAX_TEXT_BYTES = 1024 * 1024
MAX_PROPERTY_BYTES = 2 * 1024 * 1024
_TYPE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def is_edit_key(key: str) -> bool:
    """Whether changing property `key` edits the block (rather than its view state or tags)."""
    return key not in VIEW_KEYS and not TAG_KEY.match(key)


class BlockError(ValueError):
    """A transaction the server refuses; ``status`` maps to the HTTP/WebSocket reply."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Block:
    id: str
    type: str
    parent_id: str | None
    page_id: str
    order_key: str
    properties: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    created_at: str = ""
    updated_at: str = ""
    deleted_at: str | None = None

    @property
    def live(self) -> bool:
        return self.deleted_at is None

    def row(self) -> tuple:
        return (self.type, self.parent_id, self.order_key, json.dumps(self.properties, sort_keys=True),
                self.text, self.created_at, self.updated_at, self.deleted_at)


def block_id(value: Any) -> str:
    """A canonical lowercase UUID, or BlockError."""
    if isinstance(value, str):
        try:
            parsed = str(uuid.UUID(value))
        except ValueError:
            parsed = None
        if parsed == value:
            return value
    raise BlockError("Block IDs are canonical lowercase UUIDs")


def _key(value: Any) -> str:
    try:
        return order.validate(value)
    except ValueError:
        raise BlockError("Invalid order key") from None


def _type(value: Any) -> str:
    if not isinstance(value, str) or not _TYPE.fullmatch(value):
        raise BlockError("Block type must be a short lowercase name")
    return value


def _props(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) or not _KEY.fullmatch(key) for key in value):
        raise BlockError("Block properties must be an object with simple keys")
    if any(TAG_KEY.match(key) and item is not None and (not isinstance(item, str) or item not in TAG_STATES)
           for key, item in value.items()):
        raise BlockError("A tag state is manual, auto, accepted or rejected")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise BlockError("Block text must be a string without NUL")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise BlockError("Block text must be valid Unicode") from None
    if size > MAX_TEXT_BYTES:
        raise BlockError("Block text is limited to 1 MiB")
    return value


def _stamp(value: Any, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise BlockError("Timestamps must be ISO strings")
    return value


class PageDoc(Protocol):
    """What the transaction applier needs from a page document."""

    page_id: str
    blocks: dict[str, Block]
    changed: set[str]
    removed: set[str]

    def apply(self, op: dict, at: str) -> None: ...

    def check(self) -> None: ...

    def encode(self) -> bytes: ...


class OrderedPageDoc:
    """A page document whose operations arrive in the single writer's total order."""

    def __init__(self, page_id: str, blocks: dict[str, Block] | None = None):
        self.page_id = page_id
        self.blocks: dict[str, Block] = blocks or {}
        self._kids: dict[str | None, set[str]] = {}
        for item in self.blocks.values():
            self._kids.setdefault(item.parent_id, set()).add(item.id)
        #: Block IDs inserted or updated, and removed, since the doc was loaded.
        self.changed: set[str] = set()
        self.removed: set[str] = set()

    # Reading -------------------------------------------------------------------------

    @property
    def root(self) -> Block | None:
        return self.blocks.get(self.page_id)

    def get(self, bid: str) -> Block:
        found = self.blocks.get(bid)
        if found is None:
            raise BlockError("Block not found on this page", 404)
        return found

    def children(self, parent_id: str | None, *, deleted: bool = False) -> list[Block]:
        found = (self.blocks[bid] for bid in self._kids.get(parent_id, ()))
        return sorted((item for item in found if deleted or item.live), key=lambda item: (item.order_key, item.id))

    def descendants(self, bid: str) -> Iterator[Block]:
        stack = list(self._kids.get(bid, ()))
        while stack:
            current = self.blocks[stack.pop()]
            yield current
            stack.extend(self._kids.get(current.id, ()))

    def preorder(self, parent_id: str, *, types: frozenset[str] | None = None) -> list[Block]:
        """Live descendants of `parent_id` in reading order, optionally only of `types`."""
        result: list[Block] = []

        def walk(pid: str) -> None:
            for child in self.children(pid):
                if types is None or child.type in types:
                    result.append(child)
                    walk(child.id)

        walk(parent_id)
        return result

    def depth(self, bid: str) -> int:
        level, current = 0, self.blocks[bid]
        while current.parent_id is not None:
            level += 1
            current = self.blocks[current.parent_id]
        return level

    def rows(self) -> dict[str, tuple]:
        return {bid: item.row() for bid, item in self.blocks.items()}

    def encode(self) -> bytes:
        return json.dumps([asdict(item) for item in self.blocks.values()], ensure_ascii=False, sort_keys=True).encode()

    @classmethod
    def decode(cls, page_id: str, data: bytes) -> "OrderedPageDoc":
        return cls(page_id, {item["id"]: Block(**item) for item in json.loads(data)})

    # Writing -------------------------------------------------------------------------

    def _attach(self, item: Block) -> None:
        self.blocks[item.id] = item
        self._kids.setdefault(item.parent_id, set()).add(item.id)
        self.changed.add(item.id)
        self.removed.discard(item.id)

    def _detach(self, bid: str) -> list[Block]:
        """Remove `bid` and its subtree; returns them parents-first."""
        top = self.blocks[bid]
        subtree = [top, *self.descendants(bid)]
        self._kids.get(top.parent_id, set()).discard(bid)
        for item in subtree:
            del self.blocks[item.id]
            self._kids.pop(item.id, None)
            self.changed.discard(item.id)
            self.removed.add(item.id)
        return subtree

    def _live_parent(self, value: Any) -> Block:
        parent = self.blocks.get(block_id(value))
        if parent is None:
            raise BlockError("Parent block not found on this page", 404)
        if not parent.live:
            raise BlockError("Parent block is in Trash; restore it first", 409)
        return parent

    def apply(self, op: dict, at: str) -> None:
        if not isinstance(op, dict):
            raise BlockError("Each operation must be an object")
        kind = op.get("op")
        handler = getattr(self, f"_op_{kind}", None) if isinstance(kind, str) and kind.isalpha() else None
        if handler is None:
            raise BlockError("Unknown block operation")
        handler(op, at)

    def _op_insert(self, op: dict, at: str) -> None:
        bid = block_id(op.get("id"))
        if bid in self.blocks:
            raise BlockError("Block already exists", 409)
        kind, key = _type(op.get("type")), _key(op.get("key"))
        props, text = _props(op.get("props", {})), _text(op.get("text", ""))
        if op.get("parent") is None:
            if bid != self.page_id or self.blocks:
                raise BlockError("Only a page's own root block has no parent")
            parent_id = None
        else:
            parent = self._live_parent(op["parent"])
            if self.depth(parent.id) + 1 > MAX_DEPTH:
                raise BlockError(f"Blocks nest at most {MAX_DEPTH} levels")
            parent_id = parent.id
        if len(self.blocks) >= MAX_PAGE_BLOCKS:
            raise BlockError(f"A page holds at most {MAX_PAGE_BLOCKS} blocks")
        created = _stamp(op.get("created"), at)
        self._attach(Block(bid, kind, parent_id, self.page_id, key, dict(props), text, created,
                           _stamp(op.get("updated"), created), None))

    def _op_set(self, op: dict, at: str) -> None:
        item = self.get(block_id(op.get("id")))
        props = _props(op.get("props", {}))
        if "type" in op:
            item.type = _type(op["type"])
        for key, value in props.items():
            if value is None:
                item.properties.pop(key, None)
            else:
                item.properties[key] = value
        if "type" in op or any(is_edit_key(key) for key in props):
            item.updated_at = at
        self.changed.add(item.id)

    def _op_text(self, op: dict, at: str) -> None:
        item = self.get(block_id(op.get("id")))
        if "text" in op:
            item.text = _text(op["text"])
        else:
            start, length, insert = op.get("at"), op.get("del", 0), _text(op.get("ins", ""))
            if type(start) is not int or type(length) is not int or not 0 <= start <= len(item.text) or not 0 <= length <= len(item.text) - start:
                raise BlockError("Text splice is outside the block's text")
            item.text = _text(item.text[:start] + insert + item.text[start + length:])
        item.updated_at = at
        self.changed.add(item.id)

    def _op_move(self, op: dict, at: str) -> None:
        item = self.get(block_id(op.get("id")))
        if item.parent_id is None:
            raise BlockError("A page's root block cannot be moved")
        parent = self._live_parent(op.get("parent"))
        cursor: Block | None = parent
        while cursor is not None:
            if cursor.id == item.id:
                raise BlockError("Moving a block inside itself would create a cycle")
            cursor = self.blocks.get(cursor.parent_id) if cursor.parent_id else None
        height = max((self.depth(child.id) for child in self.descendants(item.id)), default=self.depth(item.id)) - self.depth(item.id)
        if self.depth(parent.id) + 1 + height > MAX_DEPTH:
            raise BlockError(f"Blocks nest at most {MAX_DEPTH} levels")
        self._kids[item.parent_id].discard(item.id)
        item.parent_id, item.order_key, item.updated_at = parent.id, _key(op.get("key")), at
        self._kids.setdefault(parent.id, set()).add(item.id)
        self.changed.add(item.id)

    def _op_delete(self, op: dict, at: str) -> None:
        item = self.get(block_id(op.get("id")))
        if not item.live:
            raise BlockError("Block is already in Trash", 409)
        for target in (item, *self.descendants(item.id)):
            if target.live:
                target.deleted_at = at
                self.changed.add(target.id)

    def _op_restore(self, op: dict, at: str) -> None:
        item = self.get(block_id(op.get("id")))
        if item.live:
            raise BlockError("Block is not in Trash", 409)
        stamp = item.deleted_at
        for target in (item, *self.descendants(item.id)):
            if target.deleted_at == stamp:
                target.deleted_at = None
                self.changed.add(target.id)

    def _op_purge(self, op: dict, at: str) -> None:
        bid = block_id(op.get("id"))
        if bid in self.blocks:  # Replaying after compaction may find it already gone.
            self._detach(bid)

    def _op_release(self, op: dict, at: str) -> None:
        item = self.get(block_id(op.get("id")))
        if item.parent_id is None:
            raise BlockError("A page's root block cannot be moved")
        self._detach(item.id)

    def _op_adopt(self, op: dict, at: str) -> None:
        rows = op.get("blocks")
        if not isinstance(rows, list) or not rows:
            raise BlockError("Adopt needs the moved blocks")
        parent = self._live_parent(op.get("parent"))
        incoming: list[Block] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise BlockError("Adopted blocks must be objects")
            try:
                item = Block(**{**row, "page_id": self.page_id})
            except TypeError:
                raise BlockError("Adopted blocks have unexpected fields") from None
            block_id(item.id)
            if item.id in self.blocks:
                raise BlockError("Block already exists", 409)
            if index == 0:
                item.parent_id, item.order_key, item.updated_at = parent.id, _key(op.get("key")), at
            incoming.append(item)
        if len(self.blocks) + len(incoming) > MAX_PAGE_BLOCKS:
            raise BlockError(f"A page holds at most {MAX_PAGE_BLOCKS} blocks")
        for item in incoming:
            self._attach(item)
        if max(self.depth(item.id) for item in incoming) > MAX_DEPTH:
            raise BlockError(f"Blocks nest at most {MAX_DEPTH} levels")

    def check(self) -> None:
        """Invariants after a whole transaction: live blocks hang under live parents."""
        for bid in self.changed:
            item = self.blocks[bid]
            if item.live and item.parent_id is not None and not self.blocks[item.parent_id].live:
                raise BlockError("A block cannot be restored inside a page or block that is in Trash", 409)
            size = len(json.dumps(item.properties, ensure_ascii=False).encode("utf-8"))
            if size > MAX_PROPERTY_BYTES:
                raise BlockError("Block properties are limited to 2 MiB")

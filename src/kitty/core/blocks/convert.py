"""Legacy note shapes <-> blocks, shared by the migration and the Notes adapters.

The existing Notes UI still speaks three whole-document shapes: a canvas JSON
document, a flat outline list and a Markdown string. Each maps losslessly onto
blocks, and a whole-document save becomes the smallest transaction that turns
the page's current blocks into the saved shape (`sync_tree`).

- **Canvas:** a page's ``canvas`` block holds the document settings (version,
  grid, snap, background, layers). Its children are the elements in paint order
  (the order key is the z order). Each element keeps its fields as properties,
  including its ``x``, ``y``, ``w`` and ``h`` geometry; text goes to the block
  text. Consecutive pen/highlighter strokes on one layer form one ``ink`` block
  (Q18): the legacy canvas has no stroke timestamps, so a run of adjacent strokes
  stands in for one drawing burst.
- **Outline:** each item is a text-flow block (``OUTLINE_TYPES``: the block editor's
  text, headings, lists, to-dos, quotes and so on) under the page (or its parent
  item) with ``collapsed`` as a property. ``ordering`` is the item's position in
  reading order; items carry their ``type`` and other ``props`` so a whole-outline
  save (imports, portable copies) keeps them.
- **Documents:** Markdown splits into one block per paragraph-level chunk
  (heading, code, list, quote, divider, text). Each block keeps the exact
  separator that followed it, so joining the blocks returns the original bytes.
"""
from __future__ import annotations

import difflib
import re
import uuid
from typing import Callable, Iterable

from kitty.core.blocks.order import keys_between
from kitty.core.blocks.pagedoc import Block, OrderedPageDoc

#: Fixed namespace so a canvas element always maps to the same block ID.
NAMESPACE = uuid.UUID("5d2b9c1e-4a7f-4e4b-9b39-0c7f0c3b6a11")
ELEMENT_TYPES = {"pen": "ink", "highlighter": "ink", "text": "text_box", "sticky": "sticky", "rect": "shape",
                 "ellipse": "shape", "line": "shape", "arrow": "shape", "connector": "connector", "image": "image"}
TEXT_KINDS = frozenset({"text", "sticky"})
CANVAS_SETTINGS = ("version", "grid", "snap", "background", "layers")
CANVAS_TYPES = frozenset(ELEMENT_TYPES.values())
#: Text-flow block types: what the block editor writes on a page (Q17), and what
#: outline readers (backlinks, topics, mentions, search) treat as a page's text.
OUTLINE_TYPES = frozenset({"text", "heading_1", "heading_2", "heading_3", "to_do", "bulleted_list", "numbered_list",
                           "quote", "callout", "code", "divider", "toggle", "page_link"})
INK_STROKES = 64
INK_POINTS = 20_000
MAX_DOCUMENT_CHUNKS = 2000


def canvas_block_id(page_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{page_id}/canvas"))


def _element_block_id(page_id: str, element_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{page_id}/element/{element_id}"))


def _ink_block_id(page_id: str, first_stroke: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{page_id}/ink/{first_stroke}"))


def canvas_settings(document: dict) -> dict:
    return {key: document[key] for key in CANVAS_SETTINGS if key in document}


def _ink_spec(page_id: str, canvas_id: str, strokes: list[dict]) -> dict:
    left = min(item["x"] for item in strokes)
    top = min(item["y"] for item in strokes)
    right = max(item["x"] + item["w"] for item in strokes)
    bottom = max(item["y"] + item["h"] for item in strokes)
    return {"id": _ink_block_id(page_id, strokes[0]["id"]), "parent": canvas_id, "type": "ink", "text": "",
            "props": {"layer": strokes[0]["layer"], "x": left, "y": top, "w": right - left, "h": bottom - top, "strokes": strokes}}


def canvas_specs(page_id: str, document: dict) -> list[dict]:
    """Desired child blocks of the page's canvas block, in paint order."""
    canvas_id, specs, run, points = canvas_block_id(page_id), [], [], 0
    for element in document["elements"]:
        if element["kind"] in {"pen", "highlighter"}:
            size = len(element.get("points", ()))
            if run and (run[-1]["layer"] != element["layer"] or len(run) >= INK_STROKES or points + size > INK_POINTS):
                specs.append(_ink_spec(page_id, canvas_id, run))
                run, points = [], 0
            run.append(element)
            points += size
            continue
        if run:
            specs.append(_ink_spec(page_id, canvas_id, run))
            run, points = [], 0
        props = {key: value for key, value in element.items() if key not in {"id", "text"}}
        props["element_id"] = element["id"]
        specs.append({"id": _element_block_id(page_id, element["id"]), "parent": canvas_id,
                      "type": ELEMENT_TYPES[element["kind"]], "props": props, "text": element.get("text", "")})
    if run:
        specs.append(_ink_spec(page_id, canvas_id, run))
    return specs


def canvas_document(canvas: Block | None, elements: Iterable[Block]) -> dict:
    """The legacy canvas JSON document for a canvas block and its children."""
    settings = canvas.properties if canvas is not None else {}
    if "raw" in settings:
        return settings["raw"]
    document = {"version": 1, "grid": True, "snap": False,
                "layers": [{"id": "default", "name": "Drawing", "visible": True, "locked": False}]}
    document.update({key: settings[key] for key in CANVAS_SETTINGS if key in settings})
    output = []
    for item in elements:
        if item.type == "ink":
            output.extend(item.properties["strokes"])
            continue
        props = dict(item.properties)
        element = {"id": props.pop("element_id"), **props}
        if element["kind"] in TEXT_KINDS:
            element["text"] = item.text
        output.append(element)
    document["elements"] = output
    return document


def outline_specs(page_id: str, items: list[dict]) -> list[dict]:
    """Desired outline blocks for a normalized legacy outline, parents before children."""
    ordered = sorted(items, key=lambda item: (item["ordering"], item["id"]))
    children: dict[str | None, list[dict]] = {}
    for item in ordered:
        children.setdefault(item["parent_id"], []).append(item)
    result: list[dict] = []

    def walk(parent: str | None) -> None:
        for item in children.get(parent, ()):
            result.append({"id": item["id"], "parent": item["parent_id"] or page_id, "type": item.get("type", "text"),
                           "props": {**item.get("props", {}), "collapsed": item["collapsed"]}, "text": item["text"]})
            walk(item["id"])

    walk(None)
    return result


def outline_items(doc: OrderedPageDoc, number: int) -> list[dict]:
    """The legacy outline list (reading order, ``ordering`` = position) for a page."""
    return [{"id": item.id, "page_id": number, "parent_id": None if item.parent_id == doc.page_id else item.parent_id,
             "text": item.text, "ordering": index, "collapsed": bool(item.properties.get("collapsed", False)), "type": item.type,
             "props": {key: value for key, value in item.properties.items() if key != "collapsed"}}
            for index, item in enumerate(doc.preorder(doc.page_id, types=OUTLINE_TYPES))]


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_KINDS = (
    (re.compile(r"^ {0,3}#{1,6}(\s|$)"), "heading"),
    (_FENCE, "code"),
    (re.compile(r"^ {0,3}>"), "quote"),
    (re.compile(r"^ {0,3}([-*_])( *\1){2,} *$"), "divider"),
    (re.compile(r"^ *([-+*]|\d{1,9}[.)])( +|$)"), "list"),
)


def _chunk_type(text: str, markdown: bool) -> str:
    if markdown:
        first = text.split("\n", 1)[0]
        for pattern, kind in _KINDS:
            if pattern.match(first):
                return kind
    return "text"


def _terminator(line: str) -> int:
    return 2 if line.endswith("\r\n") else 1 if line.endswith(("\n", "\r")) else 0


def split_document(content: str, markdown: bool = True) -> list[dict]:
    """Paragraph-level chunks ``{type, text, sep}``; ``join_document`` restores `content` exactly."""
    chunks: list[dict] = []
    body: list[str] = []
    gap: list[str] = []
    fence: str | None = None

    def flush() -> None:
        text = "".join(body)
        cut = _terminator(text) if body else 0
        chunks.append({"text": text[:len(text) - cut], "sep": text[len(text) - cut:] + "".join(gap)})

    for line in content.splitlines(keepends=True):
        if fence is not None:
            body.append(line)
            if line.strip().startswith(fence):
                fence = None
            continue
        if not line.strip():
            gap.append(line)
            continue
        if gap:
            flush()
            body, gap = [], []
        opening = _FENCE.match(line)
        if opening:
            fence = opening[1]
        body.append(line)
    if body or gap:
        flush()
    while len(chunks) > MAX_DOCUMENT_CHUNKS:
        tail = chunks.pop()
        chunks[-1] = {"text": chunks[-1]["text"] + chunks[-1]["sep"] + tail["text"], "sep": tail["sep"]}
    for chunk in chunks:
        chunk["type"] = _chunk_type(chunk["text"], markdown)
    return chunks


def join_document(chunks: Iterable[Block]) -> str:
    return "".join(item.text + item.properties.get("sep", "") for item in chunks)


def document_specs(doc: OrderedPageDoc, content: str, markdown: bool) -> list[dict]:
    """Desired chunk blocks for `content`, reusing the IDs of unchanged chunks."""
    current = doc.children(doc.page_id)
    wanted = split_document(content, markdown)
    ids: list[str | None] = [None] * len(wanted)
    matcher = difflib.SequenceMatcher(None, [item.text for item in current], [chunk["text"] for chunk in wanted], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"equal", "replace"}:
            for offset in range(min(i2 - i1, j2 - j1)):
                ids[j1 + offset] = current[i1 + offset].id
    return [{"id": bid or str(uuid.uuid4()), "parent": doc.page_id, "type": chunk["type"],
             "props": {"sep": chunk["sep"]}, "text": chunk["text"]} for bid, chunk in zip(ids, wanted)]


def props_diff(old: dict, new: dict) -> dict:
    """A ``set`` operation's props turning `old` into `new` (``None`` removes a key)."""
    changes = {key: value for key, value in new.items() if key not in old or old[key] != value}
    changes.update({key: None for key in old if key not in new})
    return changes


def insert_ops(specs: list[dict], created: str | None = None, updated: str | None = None) -> list[dict]:
    """Insert operations for new blocks, keyed per parent in the order they are listed."""
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec["parent"]] = counts.get(spec["parent"], 0) + 1
    keys = {parent: iter(keys_between(None, None, count)) for parent, count in counts.items()}
    stamps = {key: value for key, value in (("created", created), ("updated", updated)) if value is not None}
    return [{"op": "insert", "id": spec["id"], "type": spec["type"], "parent": spec["parent"], "key": next(keys[spec["parent"]]),
             "props": spec["props"], "text": spec["text"], **stamps} for spec in specs]


def _longest_increasing(keys: list[str]) -> set[int]:
    """Indexes of one longest strictly increasing subsequence of `keys`."""
    import bisect

    tails: list[str] = []
    tail_index: list[int] = []
    previous = [-1] * len(keys)
    for index, key in enumerate(keys):
        position = bisect.bisect_left(tails, key)
        if position == len(tails):
            tails.append(key)
            tail_index.append(index)
        else:
            tails[position] = key
            tail_index[position] = index
        previous[index] = tail_index[position - 1] if position else -1
    result, cursor = set(), tail_index[-1] if tail_index else -1
    while cursor >= 0:
        result.add(cursor)
        cursor = previous[cursor]
    return result


def sync_tree(doc: OrderedPageDoc, root_id: str, desired: list[dict], scope: Callable[[Block], bool]) -> list[dict]:
    """Operations that turn the live in-`scope` blocks under `root_id` into `desired`.

    `desired` lists ``{id, parent, type, props, text}`` parents before children and
    siblings in order. Unchanged blocks produce no operation; reordering keeps the
    longest run of already-ordered siblings and only re-keys the others; blocks no
    longer wanted go to Trash. Properties are replaced as a whole.
    """
    existing = {item.id: item for item in doc.descendants(root_id) if item.live and scope(item)}
    groups: dict[str, list[str]] = {}
    for spec in desired:
        groups.setdefault(spec["parent"], []).append(spec["id"])
    keys: dict[str, str] = {}
    for parent, ids in groups.items():
        current = [doc.blocks[bid] if bid in doc.blocks and doc.blocks[bid].parent_id == parent else None for bid in ids]
        stable = [index for index, item in enumerate(current) if item is not None]
        kept = {stable[i] for i in _longest_increasing([current[index].order_key for index in stable])}
        siblings = sorted(item.order_key for item in doc.children(parent)) if parent in doc.blocks else []
        floor = None
        index = 0
        while index < len(ids):
            if index in kept:
                floor = keys[ids[index]] = current[index].order_key
                index += 1
                continue
            end = index
            while end < len(ids) and end not in kept:
                end += 1
            ceiling = current[end].order_key if end < len(ids) else None
            if floor is None and ceiling is None and siblings:
                floor = siblings[-1]
            for bid, key in zip(ids[index:end], keys_between(floor, ceiling, end - index)):
                keys[bid] = key
            floor = keys[ids[end - 1]]
            index = end
    ops: list[dict] = []
    wanted = {spec["id"] for spec in desired}
    for spec in desired:
        bid, item = spec["id"], doc.blocks.get(spec["id"])
        if item is None:
            ops.append({"op": "insert", "id": bid, "type": spec["type"], "parent": spec["parent"], "key": keys[bid],
                        "props": spec["props"], "text": spec["text"]})
            continue
        if not item.live:
            ops.append({"op": "restore", "id": bid})
            existing.update((child.id, child) for child in doc.descendants(bid)
                            if child.deleted_at == item.deleted_at and scope(child))
        if item.parent_id != spec["parent"] or item.order_key != keys[bid]:
            ops.append({"op": "move", "id": bid, "parent": spec["parent"], "key": keys[bid]})
        if item.type != spec["type"] or item.properties != spec["props"]:
            ops.append({"op": "set", "id": bid, "props": props_diff(item.properties, spec["props"]),
                        **({"type": spec["type"]} if item.type != spec["type"] else {})})
        if item.text != spec["text"]:
            ops.append({"op": "text", "id": bid, "text": spec["text"]})
    doomed = {bid for bid in existing if bid not in wanted}
    ops.extend({"op": "delete", "id": bid} for bid in sorted(doomed)
               if existing[bid].parent_id not in doomed)
    return ops

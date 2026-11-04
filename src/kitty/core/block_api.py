"""The block API: what agents (MCP), the CLI and HTTP clients read and write in rika!'s notes.

Paper §13 step 2 ("block read, write and search tools are added to the API"). The MCP
server, ``kitty notes`` and the ``/api/blocks`` routes are thin consumers of these
functions; none of them touches the database any other way.

Reads
    `list_pages`, `get_page` (the block tree with IDs, types, properties, text and
    order), `search_blocks` (the full-text index, one hit per block) and `backlinks`
    (``((references))``, ``[[links]]``, unlinked title mentions and tagged blocks). Each
    runs in one read snapshot and reports the op-log sequence ``seq`` it reflects; every
    block it returns carries its citation ``((id@seq))``: the block as of that sequence,
    which History (`kitty.core.blocks.history.preview`) can show again. A pinned
    citation written into a block's text is a block reference like ``((id))``
    (kitty.core.blocks.tokens), so it shows up in backlinks.

Writes
    `create_page`, `insert_blocks` (``append_blocks`` is inserting at the end),
    `update_block`, `move_block`, `delete_block` (to Trash), `add_to_inbox`,
    `quick_note` and `tag_block`. Each is one ``blocks.transact`` call inside one
    immediate SQLite transaction with its lookups, so the op log, History, the live push
    to every open window, tags and the search index all see it like an edit typed in
    the editor. Each returns a receipt: ``txn_id``, ``seq`` (the transaction's op-log
    sequence), the page's ``revision`` afterwards, ``pages`` ({page id: {seq, revision}})
    and the written ``blocks`` with their citations. An optional ``revision`` is the
    page revision the caller last read: a different current revision refuses the write
    (409) instead of overwriting someone's edit.

    Writes reach note pages only, and only text-flow blocks (the block editor's types,
    `WRITE_TYPES`) with a few plain properties (`WRITE_PROPS`). Documents keep their
    exact-byte Markdown chunks and quick notes their own editor; canvas elements belong
    to the canvas. Tags attach through `tag_block` (never as raw properties).

Everything raises ``BlockError`` with an HTTP-like ``status`` on a refusal. Only blocks
are read or written: no job, application, profile, answer or resume data.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import nullcontext
from typing import Any

from kitty.core import ingress, notes, quick_notes, search
from kitty.core.blocks import BlockError, block_id, convert, tags, transact
from kitty.core.blocks.pagedoc import TAG_KEY, VIEW_KEYS
from kitty.core.blocks.store import OPAQUE_TYPES, page_state
from kitty.core.blocks.tokens import WIKI
from kitty.core.clients import ClientError
from kitty.db.connection import transaction

#: Roots the API reads: note pages, documents and quick notes.
ROOT_TYPES = ("page", "document", "quick_note")
#: Block types the API writes: the editor's text-flow types (a page link needs its target page).
WRITE_TYPES = frozenset(convert.OUTLINE_TYPES - {"page_link"})
#: Properties the API writes, with their value type (``None`` removes one).
WRITE_PROPS: dict[str, type] = {"checked": bool, "collapsed": bool, "language": str}
#: Properties that are per-window view state, not content: left out of reads.
HIDDEN_PROPS = VIEW_KEYS | {"viewport_revision"}
MAX_WRITE_BLOCKS = 500
MAX_TEXT = 100_000
MAX_TITLE = 200
MAX_LIST = 200
MAX_QUERY = 200
#: Text of a block shown in search hits and backlinks.
EXCERPT = 400
DEFAULT_NOTEBOOK = "Notes"


def cite(bid: str, seq: int) -> str:
    """A pinned citation: block `bid` as of op-log sequence `seq`."""
    return f"(({bid}@{seq}))"


def _read(conn: sqlite3.Connection):
    return nullcontext() if conn.in_transaction else transaction(conn)


def _write(conn: sqlite3.Connection):
    """An immediate transaction, or the caller's (the agent mediator journals inside it)."""
    return nullcontext() if conn.in_transaction else transaction(conn, immediate=True)


def _seq(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log").fetchone()[0]


def _title(kind: str, text: str) -> str:
    return (text.strip().split("\n", 1)[0] if kind == "quick_note" else text)[:MAX_TITLE]


def _uuid(value: Any, what: str) -> str:
    try:
        return block_id(value.lower() if isinstance(value, str) else value)
    except BlockError:
        raise BlockError(f"{what} must be a block ID (a UUID)") from None


def _root(conn: sqlite3.Connection, page: Any) -> sqlite3.Row:
    """The live root a page reference names: a root block ID, or a note page number."""
    if type(page) is int or (isinstance(page, str) and page.isdigit() and len(page) <= 15):
        row = conn.execute("SELECT * FROM blocks WHERE type='page' AND parent_id IS NULL AND deleted_at IS NULL "
                           "AND json_extract(properties,'$.number')=?", (int(page),)).fetchone()
    elif isinstance(page, str):
        try:
            bid = block_id(page.lower())
        except BlockError:
            raise BlockError("Name a page by its block ID or its note page number") from None
        row = conn.execute("SELECT * FROM blocks WHERE id=? AND parent_id IS NULL AND deleted_at IS NULL",
                           (bid,)).fetchone()
        if row is not None and row["type"] not in ROOT_TYPES:
            row = None
    else:
        raise BlockError("Name a page by its block ID or its note page number")
    if row is None:
        raise BlockError("Page not found", 404)
    return row


def _notebooks(conn: sqlite3.Connection) -> dict[int, str]:
    return {row["id"]: row["title"] for row in conn.execute("SELECT id, title FROM note_notebooks")}


def _summary(row: sqlite3.Row, notebooks: dict[int, str]) -> dict:
    props = json.loads(row["properties"])
    return {"id": row["id"], "type": row["type"], "number": props.get("number"), "title": _title(row["type"], row["text"]),
            "notebook": notebooks.get(props.get("notebook_id")), "inbox": bool(props.get("inbox")),
            "revision": props.get("revision", 1), "updated_at": row["updated_at"]}


# ------------------------------------------------------------------------------- reads

def list_pages(conn: sqlite3.Connection, query: str = "", *, kind: str | None = None, limit: int = 50) -> dict:
    """Live note pages, documents and quick notes, most recently edited first.

    `query` keeps roots whose title contains it (case-insensitive); `kind` keeps one root
    type. ``total`` counts every match and ``truncated`` says more exist than `limit`.
    """
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise BlockError(f"The title filter is at most {MAX_QUERY} characters")
    if kind is not None and kind not in ROOT_TYPES:
        raise BlockError(f"Page kind is one of: {', '.join(ROOT_TYPES)}")
    if type(limit) is not int or not 1 <= limit <= MAX_LIST:
        raise BlockError(f"Limit must be 1-{MAX_LIST}")
    wanted = query.strip().casefold()
    kinds = (kind,) if kind else ROOT_TYPES
    with _read(conn):
        seq, notebooks = _seq(conn), _notebooks(conn)
        rows = conn.execute(f"SELECT * FROM blocks WHERE parent_id IS NULL AND deleted_at IS NULL AND type IN "
                            f"({','.join('?' * len(kinds))}) ORDER BY updated_at DESC, id", kinds).fetchall()
    found = [_summary(row, notebooks) for row in rows if wanted in _title(row["type"], row["text"]).casefold()]
    return {"seq": seq, "pages": [{**page, "cite": cite(page["id"], seq)} for page in found[:limit]],
            "total": len(found), "truncated": len(found) > limit}


def _tag_names(conn: sqlite3.Connection, page_id: str) -> dict[str, list[str]]:
    """Attached tag names per block of one page."""
    names: dict[str, list[str]] = {}
    for row in conn.execute("SELECT t.block_id, g.text FROM block_tags t JOIN blocks b ON b.id=t.block_id "
                            "JOIN blocks g ON g.id=t.tag_id AND g.deleted_at IS NULL WHERE b.page_id=? ORDER BY g.text",
                            (page_id,)):
        names.setdefault(row["block_id"], []).append(row["text"])
    return names


def _props(props: dict) -> dict:
    return {key: value for key, value in props.items() if key not in HIDDEN_PROPS and not TAG_KEY.match(key)}


def get_page(conn: sqlite3.Connection, page: Any) -> dict:
    """One page as a tree: ``{seq, revision, page, blocks}``.

    Each block is ``{id, type, text, props, tags, order, updated_at, cite, children}`` in
    reading order (``order`` is its fractional order key among its siblings). A canvas
    and its elements are left out; ``page.canvas_elements`` counts them.
    """
    with _read(conn):
        root = _root(conn, page)
        state = page_state(conn, root["id"])
        names = _tag_names(conn, root["id"])
        live = conn.execute("SELECT COUNT(*) FROM blocks WHERE page_id=? AND deleted_at IS NULL", (root["id"],)).fetchone()[0]
        summary = _summary(root, _notebooks(conn))
    seq = state["seq"]
    nodes: dict[str, dict] = {}
    top: list[dict] = []
    canvases = 0
    for item in state["blocks"]:
        if item["type"] in OPAQUE_TYPES:
            canvases += 1
            continue
        node = {"id": item["id"], "type": item["type"], "text": item["text"], "props": _props(item["properties"]),
                "tags": names.get(item["id"], []), "order": item["order_key"], "updated_at": item["updated_at"],
                "cite": cite(item["id"], seq), "children": []}
        nodes[item["id"]] = node
        (top if item["parent_id"] == root["id"] else nodes[item["parent_id"]]["children"]).append(node)
    summary.update(cite=cite(root["id"], seq), props=_props(state["root"]["properties"]),
                   canvas_elements=live - 1 - len(nodes) - canvases)
    return {"seq": seq, "revision": state["revision"], "page": summary, "blocks": top}


def _excerpt(conn: sqlite3.Connection, ids: list[str]) -> dict[str, sqlite3.Row]:
    found: dict[str, sqlite3.Row] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        found.update((row["id"], row) for row in conn.execute(
            f"SELECT b.id, b.type, b.page_id, b.text, r.type AS root_type, r.text AS root_text FROM blocks b "
            f"JOIN blocks r ON r.id=b.page_id WHERE b.id IN ({','.join('?' * len(chunk))})", chunk))
    return found


def _source(row: sqlite3.Row, seq: int) -> dict:
    return {"block_id": row["id"], "type": row["type"], "page_id": row["page_id"], "root_type": row["root_type"],
            "page_title": _title(row["root_type"], row["root_text"]), "text": row["text"][:EXCERPT],
            "cite": cite(row["id"], seq)}


def search_blocks(conn: sqlite3.Connection, text: str, *, limit: int = 20) -> dict:
    """Blocks of note pages, documents and quick notes matching `text` in the full-text index.

    Ranked like Ctrl K (word prefixes first, then substrings from three characters), one
    hit per block: ``{block_id, type, page_id, root_type, number, page_title, text,
    snippet, match, cite}``; ``snippet`` marks the matched words with ``**``.
    """
    with _read(conn):
        seq = _seq(conn)
        try:
            found = search.block_hits(conn, text, limit=limit)
        except ValueError as exc:
            raise BlockError(str(exc)) from None
        rows = _excerpt(conn, [hit["block_id"] for hit in found["hits"]])
    hits = []
    for hit in found["hits"]:
        snippet = "".join(f"**{part}**" if marked else part for part, marked in hit["snippet"])
        hits.append({**_source(rows[hit["block_id"]], seq), "number": hit["number"], "snippet": snippet, "match": hit["match"]})
    return {"seq": seq, "query": text, "hits": hits, "total": found["total"], "truncated": found["truncated"]}


def _listed(conn: sqlite3.Connection, sql: str, params: tuple, seq: int) -> dict:
    ids = list(dict.fromkeys(row[0] for row in conn.execute(sql, params)))
    rows = _excerpt(conn, ids[:MAX_LIST])
    return {"blocks": [_source(rows[bid], seq) for bid in ids[:MAX_LIST]], "count": len(ids), "truncated": len(ids) > MAX_LIST}


#: Sources of backlinks: live blocks of live note pages, documents and quick notes.
_LIVE_SOURCE = ("JOIN blocks s ON s.id={column} AND s.deleted_at IS NULL "
                "JOIN blocks sp ON sp.id=s.page_id AND sp.deleted_at IS NULL AND sp.type IN ('page','document','quick_note')")


def backlinks(conn: sqlite3.Connection, target: Any) -> dict:
    """What points at a block or a page: ``{seq, target, references, links, mentions, tagged}``.

    - ``references``: blocks whose text cites it as ``((id))`` / ``((id@seq))`` or embeds it
      (for a page: any of its blocks);
    - ``links``: blocks linking a note page's title as ``[[Title]]``;
    - ``mentions``: blocks on other pages naming a note page's title as whole words outside a
      link (unlinked mentions);
    - ``tagged``: for a tag, the blocks it is attached to.

    Each is ``{blocks, count, truncated}`` (at most 200 blocks listed, newest index order).
    """
    with _read(conn):
        seq = _seq(conn)
        if type(target) is int or (isinstance(target, str) and target.isdigit()):
            row = _root(conn, target)
        else:
            row = conn.execute("SELECT * FROM blocks WHERE id=? AND deleted_at IS NULL", (_uuid(target, "The target"),)).fetchone()
            if row is None:
                raise BlockError("Block not found", 404)
        is_root = row["parent_id"] is None
        empty = {"blocks": [], "count": 0, "truncated": False}
        result = {"seq": seq, "references": empty, "links": empty, "mentions": empty, "tagged": empty}
        refs = ("SELECT r.source_id FROM block_refs r " + _LIVE_SOURCE.format(column="r.source_id") +
                " WHERE r.kind='block' AND r.target_id" + (" IN (SELECT id FROM blocks WHERE page_id=? AND deleted_at IS NULL)"
                                                          if is_root else "=?") + " AND s.page_id<>? ORDER BY s.updated_at DESC, s.id")
        result["references"] = _listed(conn, refs, (row["id"], row["id"] if is_root else ""), seq)
        if is_root and row["type"] == "page" and row["text"].strip():
            topic = row["text"].strip().casefold()
            result["links"] = _listed(conn, "SELECT t.block_id FROM block_topics t " + _LIVE_SOURCE.format(column="t.block_id") +
                                      " WHERE t.kind='page' AND t.topic=? ORDER BY s.updated_at DESC, s.id", (topic,), seq)
            pattern = re.compile(r"(?<!\w)" + re.escape(row["text"].strip()) + r"(?!\w)", re.I)
            candidates = [bid for bid, page_id in search.phrase_blocks(conn, row["text"]) if page_id != row["id"]]
            texts = _excerpt(conn, candidates)
            unlinked = [bid for bid in candidates if pattern.search(WIKI.sub("", texts[bid]["text"]))]
            result["mentions"] = {"blocks": [_source(texts[bid], seq) for bid in unlinked[:MAX_LIST]],
                                  "count": len(unlinked), "truncated": len(unlinked) > MAX_LIST}
        if is_root and row["type"] == "tag":
            result["tagged"] = _listed(conn, "SELECT t.block_id FROM block_tags t " + _LIVE_SOURCE.format(column="t.block_id") +
                                       " WHERE t.tag_id=? ORDER BY s.updated_at DESC, s.id", (row["id"],), seq)
        notebooks = _notebooks(conn)
    kind = row["type"] if is_root else "block"
    head = _summary(row, notebooks) if is_root else {"id": row["id"], "type": row["type"], "page_id": row["page_id"],
                                                      "text": row["text"][:EXCERPT]}
    return {**result, "target": {**head, "kind": kind, "cite": cite(row["id"], seq)}}


def list_tags(conn: sqlite3.Connection) -> dict:
    """The tag setting and every tag: ``{mode, tags: [{id, name, keywords, aliases, mode, count}]}``."""
    listed = tags.list_tags(conn)
    keep = ("id", "name", "keywords", "aliases", "effective_mode", "count")
    return {"mode": listed["mode"], "tags": [{key: tag[key] for key in keep} for tag in listed["tags"]]}


# ------------------------------------------------------------------------------ writes

def _text(value: Any, what: str = "Block text", maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise BlockError(f"{what} must be text of at most {maximum} characters")
    return value


def _kind(value: Any) -> str:
    if value not in WRITE_TYPES:
        raise BlockError(f"Block type must be one of: {', '.join(sorted(WRITE_TYPES))}")
    return value


def _write_props(value: Any, *, removing: bool = False) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(WRITE_PROPS):
        raise BlockError(f"Block properties are: {', '.join(WRITE_PROPS)}")
    for key, item in value.items():
        if not (removing and item is None) and type(item) is not WRITE_PROPS[key]:
            raise BlockError(f"Property {key} must be {WRITE_PROPS[key].__name__}")
        if key == "language" and isinstance(item, str) and len(item) > 40:
            raise BlockError("A code language name is at most 40 characters")
    return dict(value)


def _specs(value: Any) -> list[dict]:
    """Validated block specs: ``[{type?, text?, props?, children?}]`` (children nest)."""
    count = 0

    def one(item: Any, depth: int) -> dict:
        nonlocal count
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or set(item) - {"type", "text", "props", "children"}:
            raise BlockError("Each block is {type?, text?, props?, children?} or a string")
        count += 1
        if count > MAX_WRITE_BLOCKS:
            raise BlockError(f"Write at most {MAX_WRITE_BLOCKS} blocks at once")
        if depth > 16:
            raise BlockError("Nested blocks go at most 16 levels deep")
        children = item.get("children", [])
        if not isinstance(children, list):
            raise BlockError("children is a list of blocks")
        return {"type": _kind(item.get("type", "text")), "text": _text(item.get("text", "")),
                "props": _write_props(item.get("props")), "children": [one(child, depth + 1) for child in children]}

    if not isinstance(value, list) or not value:
        raise BlockError("Send a non-empty list of blocks")
    return [one(item, 0) for item in value]


def _insert_ops(parent: str, specs: list[dict], position: dict) -> tuple[list[dict], list[str]]:
    """Insert operations for `specs` under `parent`: the first at `position`, each next one after it."""
    ops: list[dict] = []
    written: list[str] = []

    def emit(under: str, spec: dict, where: dict) -> str:
        bid = str(uuid.uuid4())
        op = {"op": "insert", "id": bid, "type": spec["type"], "parent": under, "text": spec["text"], **where}
        if spec["props"]:
            op["props"] = spec["props"]
        ops.append(op)
        written.append(bid)
        for child in spec["children"]:
            emit(bid, child, {})
        return bid

    where = position
    for spec in specs:
        where = {"after": emit(parent, spec, where)}
    return ops, written


def _block(conn: sqlite3.Connection, bid: Any, what: str = "Block") -> sqlite3.Row:
    """A live block with its root's type (``root_type``) and text (``root_text``)."""
    row = conn.execute("SELECT b.*, r.type AS root_type, r.text AS root_text, r.deleted_at AS root_deleted FROM blocks b "
                       "JOIN blocks r ON r.id=b.page_id WHERE b.id=?", (_uuid(bid, what),)).fetchone()
    if row is None or row["deleted_at"] is not None or row["root_deleted"] is not None:
        raise BlockError(f"{what} not found", 404)
    return row


def _note_page(row: sqlite3.Row) -> None:
    if row["root_type"] != "page":
        raise BlockError("Only note pages are written here: documents keep their Markdown editor and quick notes their own")


def _parent(conn: sqlite3.Connection, bid: Any) -> sqlite3.Row:
    """A block new blocks may go under: a note page's root or one of its text-flow blocks."""
    row = _block(conn, bid, "Parent block")
    _note_page(row)
    if row["parent_id"] is not None and row["type"] not in convert.OUTLINE_TYPES:
        raise BlockError("Blocks go under a page or a text block, not into a canvas")
    return row


def _editable(conn: sqlite3.Connection, bid: Any) -> sqlite3.Row:
    """A text-flow block of a note page (a page's root is edited only by renaming it)."""
    row = _block(conn, bid)
    _note_page(row)
    if row["parent_id"] is not None and row["type"] not in convert.OUTLINE_TYPES:
        raise BlockError("Only text blocks are edited here; canvas elements belong to the canvas")
    return row


def _base(page_id: str, revision: Any) -> dict | None:
    if revision is None:
        return None
    if type(revision) is not int or revision < 1:
        raise BlockError("revision is the page revision you last read (a positive integer)")
    return {page_id: revision}


def _receipt(result: dict, page_id: str, written: list[str]) -> dict:
    seq = max(result["seq"].values())
    return {"txn_id": result["txn_id"], "page_id": page_id, "seq": seq, "revision": result["revisions"].get(page_id),
            "pages": {pid: {"seq": result["seq"][pid], "revision": result["revisions"].get(pid)} for pid in result["seq"]},
            "blocks": [{"id": bid, "cite": cite(bid, seq)} for bid in written]}


def _position(conn: sqlite3.Connection, page: Any, parent: Any, after: Any, before: Any) -> tuple[str, str, dict]:
    """``(page id, parent id, position)`` for new or moved blocks."""
    if after is not None and before is not None:
        raise BlockError("Use either after or before")
    sibling = after if after is not None else before
    if sibling is not None:
        row = _block(conn, sibling, "Sibling block")
        if row["parent_id"] is None:
            raise BlockError("after/before name a block inside a page, not the page")
        anchor = _parent(conn, row["parent_id"])
        if parent is not None and _uuid(parent, "Parent") != anchor["id"]:
            raise BlockError("That sibling is not under that parent")
        where = {"after": row["id"]} if after is not None else {"before": row["id"]}
    elif parent is not None:
        anchor, where = _parent(conn, parent), {}
    elif page is not None:
        anchor, where = _parent(conn, _root(conn, page)["id"]), {}
    else:
        raise BlockError("Say where: a page, a parent block, or a sibling to go after or before")
    if page is not None and anchor["page_id"] != _root(conn, page)["id"]:
        raise BlockError("That block is not on that page")
    return anchor["page_id"], anchor["id"], where


def insert_blocks(conn: sqlite3.Connection, blocks: Any, *, page: Any = None, parent: Any = None, after: Any = None,
                  before: Any = None, revision: Any = None) -> dict:
    """Insert blocks after or before a sibling, or at the end of a parent block or page (one transaction)."""
    specs = _specs(blocks)
    with _write(conn):
        page_id, parent_id, where = _position(conn, page, parent, after, before)
        ops, written = _insert_ops(parent_id, specs, where)
        result = transact(conn, ops, base=_base(page_id, revision))
    return _receipt(result, page_id, written)


def append_blocks(conn: sqlite3.Connection, page: Any, blocks: Any, *, parent: Any = None, revision: Any = None) -> dict:
    """Append blocks at the end of a page (or of one of its blocks)."""
    return insert_blocks(conn, blocks, page=page, parent=parent, revision=revision)


def update_block(conn: sqlite3.Connection, block: Any, *, text: Any = None, kind: Any = None, props: Any = None,
                 revision: Any = None) -> dict:
    """Replace a block's text, change its type or set/remove properties; on a page's root, rename the page."""
    if text is None and kind is None and props is None:
        raise BlockError("Change the text, the type or properties")
    with _write(conn):
        row = _editable(conn, block)
        ops: list[dict] = []
        if row["parent_id"] is None:
            if kind is not None or props is not None:
                raise BlockError("A page's own block only takes a new title (text)")
            title = _text(text, "Page title", MAX_TITLE)
            if not title.strip():
                raise BlockError("A page title is required")
            ops.append({"op": "text", "id": row["id"], "text": title})
        else:
            changes = _write_props(props, removing=True)
            if kind is not None or changes:
                ops.append({"op": "set", "id": row["id"], **({"type": _kind(kind)} if kind is not None else {}),
                            **({"props": changes} if changes else {})})
            if text is not None:
                ops.append({"op": "text", "id": row["id"], "text": _text(text)})
        if not ops:
            raise BlockError("Change the text, the type or properties")
        result = transact(conn, ops, base=_base(row["page_id"], revision))
    return _receipt(result, row["page_id"], [row["id"]])


def move_block(conn: sqlite3.Connection, block: Any, *, page: Any = None, parent: Any = None, after: Any = None,
               before: Any = None, revision: Any = None) -> dict:
    """Move a block (and its children) after/before a sibling or to the end of a parent or page, on any note page."""
    with _write(conn):
        row = _editable(conn, block)
        if row["parent_id"] is None:
            raise BlockError("A page's own block cannot be moved")
        destination, parent_id, where = _position(conn, page, parent, after, before)
        if where.get("after") == row["id"] or where.get("before") == row["id"]:
            raise BlockError("A block cannot be placed next to itself")
        result = transact(conn, [{"op": "move", "id": row["id"], "parent": parent_id, **where}],
                          base=_base(row["page_id"], revision))
    return {**_receipt(result, row["page_id"], [row["id"]]), "destination": destination}


def delete_block(conn: sqlite3.Connection, block: Any, *, revision: Any = None) -> dict:
    """Move a block and its children to Trash (restorable from Trash or History for the retention window)."""
    with _write(conn):
        row = _editable(conn, block)
        if row["parent_id"] is None:
            raise BlockError("Delete pages in rika!, not through the block API")
        result = transact(conn, [{"op": "delete", "id": row["id"]}], base=_base(row["page_id"], revision))
    return _receipt(result, row["page_id"], [row["id"]])


def _notebook(conn: sqlite3.Connection, value: Any) -> int:
    """A notebook ID: given, found by title (created when missing), or the first one (``Notes`` when none)."""
    try:
        if value is None:
            row = conn.execute("SELECT id FROM note_notebooks ORDER BY id LIMIT 1").fetchone()
            return row["id"] if row else notes.create_notebook(conn, DEFAULT_NOTEBOOK)["id"]
        if type(value) is int:
            if not conn.execute("SELECT 1 FROM note_notebooks WHERE id=?", (value,)).fetchone():
                raise BlockError("Notebook not found", 404)
            return value
        title = _text(value, "Notebook title", MAX_TITLE).strip()
        if not title:
            raise BlockError("A notebook title is required")
        row = conn.execute("SELECT id FROM note_notebooks WHERE title=? COLLATE NOCASE ORDER BY id LIMIT 1", (title,)).fetchone()
        return row["id"] if row else notes.create_notebook(conn, title)["id"]
    except notes.NotesError as exc:
        raise BlockError(str(exc), exc.status) from None


def create_page(conn: sqlite3.Connection, title: Any, *, notebook: Any = None, blocks: Any = None) -> dict:
    """A new note page titled `title` in `notebook` (ID or title), optionally with blocks (one transaction)."""
    title = _text(title, "Page title", MAX_TITLE)
    if not title.strip():
        raise BlockError("A page title is required")
    specs = _specs(blocks) if blocks else []
    with _write(conn):
        if notes.page_count(conn) >= notes.MAX_PAGES:
            raise BlockError("Page limit reached", 409)
        number, ops = notes.new_page_ops(conn, _notebook(conn, notebook), title)
        page_id = ops[0]["id"]
        added, written = _insert_ops(page_id, specs, {})
        result = transact(conn, ops + added)
    return {**_receipt(result, page_id, written), "number": number, "cite": cite(page_id, max(result["seq"].values()))}


def add_to_inbox(conn: sqlite3.Connection, blocks: Any) -> dict:
    """Append blocks to the end of the Inbox page (created on first use, as captures do)."""
    specs = _specs(blocks)
    with _write(conn):
        try:
            page_id, number, ops = ingress.inbox_page(conn)
        except ClientError as exc:
            raise BlockError(str(exc), exc.status) from None
        added, written = _insert_ops(page_id, specs, {})
        result = transact(conn, ops + added)
    return {**_receipt(result, page_id, written), "number": number}


def quick_note(conn: sqlite3.Connection, text: Any, *, tint: Any = "rose") -> dict:
    """A new quick note holding `text`."""
    with _write(conn):
        try:
            note = quick_notes.create(conn, _text(text, "Quick note text", quick_notes.MAX_BODY), tint)
        except quick_notes.QuickNoteError as exc:
            raise BlockError(str(exc), exc.status) from None
        bid = conn.execute("SELECT id FROM blocks WHERE type='quick_note' AND parent_id IS NULL "
                           "AND json_extract(properties,'$.number')=?", (note["id"],)).fetchone()["id"]
        row = conn.execute("SELECT seq, txn_id FROM op_log WHERE page_id=? ORDER BY seq DESC LIMIT 1", (bid,)).fetchone()
    return {"txn_id": row["txn_id"], "page_id": bid, "seq": row["seq"], "revision": note["revision"], "number": note["id"],
            "pages": {bid: {"seq": row["seq"], "revision": note["revision"]}}, "blocks": [{"id": bid, "cite": cite(bid, row["seq"])}]}


def tag_block(conn: sqlite3.Connection, block: Any, tag: Any, *, create: bool = False) -> dict:
    """Attach a tag (its ID, or a name or alias, with or without ``#``) to a block by hand.

    `create` makes a tag of that name when none exists. Tagging is not an edit: the page
    revision stays where it was.
    """
    if type(create) is not bool:
        raise BlockError("create is true or false")
    with _write(conn):
        row = _editable(conn, block)
        if row["parent_id"] is None:
            raise BlockError("Tag blocks within a page, not the page itself")
        if isinstance(tag, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", tag):
            tag_id = tags.get(conn, tag.lower())["id"]
        else:
            name = tag[1:] if isinstance(tag, str) and tag.startswith("#") else tag
            tag_id = tags.find(conn, name)
            if tag_id is None:
                if not create:
                    raise BlockError(f"No tag is named {name!r}; list_tags shows them, or create it", 404)
                tag_id = tags.create(conn, {"name": name})["id"]
        attached = tags.attach(conn, row["id"], tag_id)
        revision = json.loads(conn.execute("SELECT properties FROM blocks WHERE id=?", (row["page_id"],)).fetchone()[0]).get("revision", 1)
        txn = conn.execute("SELECT txn_id FROM op_log WHERE seq=?", (max(attached["seq"].values()),)).fetchone()[0]
    seq = max(attached["seq"].values())
    return {"txn_id": txn, "page_id": row["page_id"], "seq": seq, "revision": revision, "tag": tag_id,
            "pages": {pid: {"seq": value, "revision": revision if pid == row["page_id"] else None} for pid, value in attached["seq"].items()},
            "blocks": [{"id": row["id"], "cite": cite(row["id"], seq)}]}

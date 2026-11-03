"""The block half of the search index: one ``search_docs`` row per searchable block.

`store` calls `project` in the same SQLite transaction as every block transaction
(after the projection rows are written), so the full-text index (kitty.core.search)
never lags the blocks it covers. A document is a pure function of one block:

- a ``page`` or ``document`` root: its title (the root's text);
- a ``quick_note`` root: its body (the root's text);
- a ``tag`` root: its name as the title, its keywords and aliases as the body;
- a live text-flow block (``TEXT_TYPES``: outline/editor types, canvas text boxes and
  stickies, document chunks) with any text: that text as the body.

Deleted blocks (Trash) have no document; restoring one writes it again.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, NamedTuple

from kitty.core.blocks.convert import OUTLINE_TYPES
from kitty.core.blocks.pagedoc import Block

#: Child blocks whose text is page text: the editor's text-flow types, canvas text boxes and
#: stickies, and the paragraph-level chunks a document is stored as (convert.split_document).
TEXT_TYPES = OUTLINE_TYPES | {"text_box", "sticky", "heading", "list"}
TITLED_ROOTS = frozenset({"page", "document"})


class Doc(NamedTuple):
    """One indexed record: ``(kind, ref)`` is its identity; ``title`` and ``body`` are indexed."""
    kind: str
    ref: str
    page_id: str | None
    type: str
    title: str
    body: str
    label: str
    flags: int = 0


COLUMNS = Doc._fields


def block_doc(block: Block) -> Doc | None:
    """The document for one block, or None when the block is not searchable."""
    if not block.live:
        return None
    if block.parent_id is None:
        if block.type in TITLED_ROOTS:
            return Doc("block", block.id, block.page_id, block.type, block.text, "", block.text[:200])
        if block.type == "quick_note":
            return Doc("block", block.id, block.page_id, block.type, "", block.text, block.text.strip().split("\n", 1)[0][:200])
        if block.type == "tag":
            terms = [term for key in ("keywords", "aliases") for term in (block.properties.get(key) or [])
                     if isinstance(term, str)]
            return Doc("block", block.id, block.page_id, block.type, block.text, "\n".join(terms), block.text[:200])
        return None
    if block.type in TEXT_TYPES and block.text.strip():
        return Doc("block", block.id, block.page_id, block.type, "", block.text, "")
    return None


def has_index(conn: sqlite3.Connection) -> bool:
    """Whether the schema has the search index (migrations before 046 transact without it)."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_docs'").fetchone() is not None


_UPSERT = (f"INSERT INTO search_docs({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))}) "
           "ON CONFLICT(kind,ref) DO UPDATE SET page_id=excluded.page_id,type=excluded.type,title=excluded.title,"
           "body=excluded.body,label=excluded.label,flags=excluded.flags WHERE page_id IS NOT excluded.page_id "
           "OR type IS NOT excluded.type OR title IS NOT excluded.title OR body IS NOT excluded.body "
           "OR label IS NOT excluded.label OR flags IS NOT excluded.flags")


def write(conn: sqlite3.Connection, docs: Iterable[Doc], gone: Iterable[tuple[str, str]]) -> None:
    """Upsert `docs` and delete the ``(kind, ref)`` identities in `gone`; unchanged rows are not touched."""
    conn.executemany("DELETE FROM search_docs WHERE kind=? AND ref=?", list(gone))
    conn.executemany(_UPSERT, list(docs))


def project(conn: sqlite3.Connection, changed: Iterable[Block], removed: Iterable[str]) -> None:
    """Fold one transaction's changed and removed (purged or moved away) blocks into the index."""
    changed = list(changed)
    present = {block.id for block in changed}
    docs, gone = [], [("block", bid) for bid in removed if bid not in present]
    for block in changed:
        doc = block_doc(block)
        if doc is None:
            gone.append(("block", block.id))
        else:
            docs.append(doc)
    if (docs or gone) and has_index(conn):
        write(conn, docs, gone)

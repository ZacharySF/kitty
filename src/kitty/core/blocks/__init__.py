"""The block backend (platform design build step 1).

Everything rika! writes in Notes is a block (Q3): one typed row in ``blocks`` with a
parent, a fractional order key, JSON properties and plain text. The local server
applies every change as a transaction (Q6); each transaction appends to the
append-only ``op_log`` and updates the projection tables that readers use.

- ``order``: fractional order keys.
- ``pagedoc``: the ``PageDoc`` seam and the operation vocabulary.
- ``store``: ``transact``, ``load_page``, ``page_state`` (what an editor loads) and ``replay``.
- ``convert``: legacy canvas / outline / Markdown shapes <-> blocks.
- ``legacy``: the one-way migration of existing notes, with its JSON export.
- ``tokens``: ``((ref))``, ``[[page]]`` and ``#tag`` tokens in block text.
- ``searchdocs``: each block's search-index document, folded inside every transaction
  (kitty.core.search holds the index and its check).
- ``autotag`` (pure keyword matcher), ``tagging`` (auto-tags on the transaction path) and
  ``tags`` (tag records, the Tags setting, tag pages): platform design Q13.
"""
from kitty.core.blocks import order
from kitty.core.blocks.pagedoc import VIEW_KEYS, Block, BlockError, OrderedPageDoc, PageDoc, block_id
from kitty.core.blocks.store import REF, TAG, WIKI, device_id, load_page, now, page_state, references, replay, transact

__all__ = [
    "Block", "BlockError", "OrderedPageDoc", "PageDoc", "REF", "TAG", "VIEW_KEYS", "WIKI", "block_id",
    "device_id", "load_page", "now", "order", "page_state", "references", "replay", "transact",
]

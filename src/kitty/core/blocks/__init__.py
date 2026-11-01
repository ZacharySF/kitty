"""The block backend (platform design build step 1).

Everything kitty! writes in Notes is a block (Q3): one typed row in ``blocks`` with a
parent, a fractional order key, JSON properties and plain text. The local server
applies every change as a transaction (Q6); each transaction appends to the
append-only ``op_log`` and updates the projection tables that readers use.

- ``order``: fractional order keys.
- ``pagedoc``: the ``PageDoc`` seam and the operation vocabulary.
- ``store``: ``transact``, ``load_page``, ``page_state`` (what an editor loads) and ``replay``.
- ``convert``: legacy canvas / outline / Markdown shapes <-> blocks.
- ``legacy``: the one-way migration of existing notes, with its JSON export.
"""
from kitty.core.blocks import order
from kitty.core.blocks.pagedoc import VIEW_KEYS, Block, BlockError, OrderedPageDoc, PageDoc, block_id
from kitty.core.blocks.store import REF, TAG, WIKI, device_id, load_page, now, page_state, references, replay, transact

__all__ = [
    "Block", "BlockError", "OrderedPageDoc", "PageDoc", "REF", "TAG", "VIEW_KEYS", "WIKI", "block_id",
    "device_id", "load_page", "now", "order", "page_state", "references", "replay", "transact",
]

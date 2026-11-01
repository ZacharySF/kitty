"""Auto-tags on the transaction path (platform design Q13).

``store.transact`` calls two hooks:

- ``annotate`` after a transaction's operations are applied: it scans each changed
  text block of a note page against the tag vocabulary (one Aho-Corasick pass per block),
  attaches tags whose mode is *always attach* (``tag_<id>: "auto"``), drops automatic
  attachments whose keyword is gone, and returns *suggest* matches for the editor. The
  attachments it adds are operations of the same transaction, so they are logged and
  pushed like the edit itself.
- ``project`` after the projection rows are written: it rewrites ``block_tags`` and
  ``tag_rejections`` for the changed blocks from their ``tag_*`` properties and ``#name``
  tokens, and refreshes the ``#name`` attachments of tag records that changed.

The vocabulary is cached per database and rebuilt only when ``tag_settings.vocabulary``
moves (every transaction that changes a tag record moves it). Before migration 037 the
tables do not exist and both hooks do nothing.
"""
from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from kitty.core.blocks.autotag import Match, Tag, Vocabulary, normalize_term, utf16
from kitty.core.blocks.convert import OUTLINE_TYPES
from kitty.core.blocks.pagedoc import TAG_KEY, Block, OrderedPageDoc

MODES = ("suggest", "always")
ATTACHED = frozenset({"manual", "auto", "accepted"})
#: Text-flow blocks whose words are matched; code, dividers and page links are not prose.
TAGGABLE = OUTLINE_TYPES - {"code", "divider", "page_link"}
_CACHE: OrderedDict[tuple[str, int], Vocabulary] = OrderedDict()
_CACHE_SIZE = 4


def key_of(tag_id: str) -> str:
    """The block property holding a block's state for one tag."""
    return "tag_" + tag_id.replace("-", "")


def tag_of(key: str) -> str | None:
    """The tag ID a ``tag_*`` property names (None for any other property)."""
    if not TAG_KEY.match(key):
        return None
    raw = key[4:]
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def states(properties: dict[str, Any]) -> dict[str, str]:
    """A block's tag states, by tag ID."""
    found = {}
    for key, value in properties.items():
        tag = tag_of(key)
        if tag is not None and isinstance(value, str):
            found[tag] = value
    return found


def _settings(conn: sqlite3.Connection) -> sqlite3.Row | None:
    try:
        return conn.execute("SELECT s.mode, s.vocabulary, h.device_id FROM tag_settings s, block_history h "
                            "WHERE s.id=1 AND h.id=1").fetchone()
    except sqlite3.OperationalError:  # before migration 037
        return None


def tag_record(row: sqlite3.Row) -> Tag:
    props = json.loads(row["properties"])
    strings = lambda value: tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()
    mode = props.get("mode")
    return Tag(row["id"], row["text"], strings(props.get("keywords")), strings(props.get("aliases")),
               mode if mode in MODES else None, props.get("short") is True)


def load_vocabulary(conn: sqlite3.Connection) -> Vocabulary:
    rows = conn.execute("SELECT id, text, properties FROM blocks WHERE type='tag' AND parent_id IS NULL AND deleted_at IS NULL")
    return Vocabulary.of(tag_record(row) for row in rows)


def vocabulary(conn: sqlite3.Connection, settings: sqlite3.Row | None = None) -> Vocabulary:
    """Every live tag's terms, cached until a tag record changes."""
    settings = settings or _settings(conn)
    if settings is None:
        return Vocabulary({})
    key = (settings["device_id"], settings["vocabulary"])
    found = _CACHE.get(key)
    if found is None:
        found = _CACHE[key] = load_vocabulary(conn)
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return found


def taggable(doc: OrderedPageDoc, block: Block) -> bool:
    """A live text-flow block of a note page, nested only in other text-flow blocks."""
    root = doc.root
    if not block.live or block.type not in TAGGABLE or root is None or root.type != "page" or block.parent_id is None:
        return False
    parent = doc.blocks.get(block.parent_id)
    while parent is not None and parent.parent_id is not None:
        if parent.type not in OUTLINE_TYPES:
            return False
        parent = doc.blocks.get(parent.parent_id)
    return True


def scan(vocab: Vocabulary, block: Block) -> list[Match]:
    return vocab.scan(block.text, marks=block.properties.get("marks"))


def suggestion(vocab: Vocabulary, block: Block, matches: list[Match], mode: Callable[[str], str]) -> dict:
    """What the editor shows for one block: matches of *suggest* tags not attached or rejected (UTF-16 offsets)."""
    known = states(block.properties)
    inline = vocab.inline(block.text)
    items = [match for match in matches if mode(match.tag) == "suggest" and match.tag not in known and match.tag not in inline]
    offsets = utf16(block.text, [value for match in items for value in (match.start, match.end)])
    return {"text": block.text, "items": [{"tag": match.tag, "name": vocab.tags[match.tag].name, "start": offsets[2 * index],
                                           "end": offsets[2 * index + 1]} for index, match in enumerate(items)]}


def annotate(conn: sqlite3.Connection, docs: dict[str, OrderedPageDoc], record: Callable[[str, dict], None],
             *, suggest: bool = False) -> dict:
    """Attach *always* tags to the changed blocks and collect *suggest* matches.

    Returns ``{"tagged": {page_id: [ops]}, "suggestions": {block_id: {text, items}}, "tags": {id: name}}``
    (empty parts left out).
    """
    settings = _settings(conn)
    if settings is None:
        return {}
    vocab = vocabulary(conn, settings)
    default = settings["mode"]

    def mode(tag: str) -> str:
        return vocab.tags[tag].mode or default

    tagged: dict[str, list[dict]] = {}
    suggestions: dict[str, dict] = {}
    names: dict[str, str] = {}
    for page_id, doc in list(docs.items()):
        for bid in sorted(doc.changed):
            block = doc.blocks.get(bid)
            if block is None or not taggable(doc, block):
                continue
            known = states(block.properties)
            if vocab.empty and not any(value == "auto" for value in known.values()):
                if suggest:
                    suggestions[bid] = {"text": block.text, "items": []}
                continue
            matches = scan(vocab, block)
            matched = {match.tag for match in matches}
            inline = vocab.inline(block.text)
            changes: dict[str, Any] = {}
            for tag in sorted(matched):
                if tag not in known and tag not in inline and mode(tag) == "always":
                    changes[key_of(tag)] = "auto"
            for tag, value in known.items():
                if value == "auto" and tag in vocab.tags and tag not in matched:
                    changes[key_of(tag)] = None  # its keyword is gone: an automatic tag follows the text
            if changes:
                op = {"op": "set", "id": bid, "props": changes}
                record(page_id, op)
                tagged.setdefault(page_id, []).append(op)
                names.update((tag, vocab.tags[tag].name) for tag in matched if key_of(tag) in changes)
            if suggest:
                suggestions[bid] = suggestion(vocab, block, matches, mode)
                names.update((item["tag"], item["name"]) for item in suggestions[bid]["items"])
    result: dict[str, Any] = {}
    if tagged:
        result["tagged"] = tagged
    if suggest:
        result["suggestions"] = suggestions
    if names:
        result["tags"] = names
    return result


def page_suggestions(conn: sqlite3.Connection, state: dict) -> dict:
    """``suggestions`` and tag ``tags`` (names) for a page as ``store.page_state`` lists it."""
    settings = _settings(conn)
    if settings is None:
        return {"suggestions": {}, "tags": {}}
    vocab = vocabulary(conn, settings)
    default = settings["mode"]
    root = state["root"]
    by_id = {row["id"]: row for row in state["blocks"]}
    suggestions: dict[str, dict] = {}
    names: dict[str, str] = {}
    attached: set[str] = set()
    for row in state["blocks"]:
        attached.update(states(row["properties"]))
        if root["type"] != "page" or row["type"] not in TAGGABLE:
            continue
        parent = by_id.get(row["parent_id"])
        while parent is not None and parent["type"] in OUTLINE_TYPES:
            parent = by_id.get(parent["parent_id"])
        if parent is not None:
            continue  # nested in a non-text block
        block = Block(row["id"], row["type"], row["parent_id"], root["id"], row["order_key"], row["properties"], row["text"],
                      row["created_at"], row["updated_at"], None)
        found = suggestion(vocab, block, scan(vocab, block), lambda tag: vocab.tags[tag].mode or default)
        if found["items"]:
            suggestions[row["id"]] = found
            names.update((item["tag"], item["name"]) for item in found["items"])
    names.update((tag, vocab.tags[tag].name) for tag in attached if tag in vocab.tags)
    return {"suggestions": suggestions, "tags": names}


def project(conn: sqlite3.Connection, changed: list[Block], at: str) -> None:
    """Rewrite ``block_tags`` and ``tag_rejections`` for `changed` blocks (already in ``blocks``)."""
    settings = _settings(conn)
    if settings is None or not changed:
        return
    roots = [item for item in changed if item.type == "tag" and item.parent_id is None]
    if roots:
        conn.execute("UPDATE tag_settings SET vocabulary=vocabulary+1 WHERE id=1")
        # Built from this transaction's own (uncommitted) rows, so never cached.
        vocab = load_vocabulary(conn)
    else:
        vocab = vocabulary(conn, settings)
    wanted_tags = {tag for item in changed for tag in states(item.properties)}
    existing_tags: set[str] = set()
    wanted_list = sorted(wanted_tags)
    for start in range(0, len(wanted_list), 500):
        chunk = wanted_list[start:start + 500]
        existing_tags.update(row[0] for row in conn.execute(
            f"SELECT id FROM blocks WHERE type='tag' AND parent_id IS NULL AND id IN ({','.join('?' * len(chunk))})", chunk))
    ids = [item.id for item in changed]
    current: dict[str, dict[str, str]] = {}
    rejected_now: dict[str, set[str]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(f"SELECT block_id, tag_id, source FROM block_tags WHERE block_id IN ({marks})", chunk):
            current.setdefault(row[0], {})[row[1]] = row[2]
        for row in conn.execute(f"SELECT block_id, tag_id FROM tag_rejections WHERE block_id IN ({marks})", chunk):
            rejected_now.setdefault(row[0], set()).add(row[1])
    for item in changed:
        attached: dict[str, str] = {}
        rejected: set[str] = set()
        if item.live and not (item.type == "tag" and item.parent_id is None):
            for tag, value in states(item.properties).items():
                if tag not in existing_tags:
                    continue
                if value in ATTACHED:
                    attached[tag] = value
                elif value == "rejected":
                    rejected.add(tag)
            for tag in vocab.inline(item.text):
                attached.setdefault(tag, "manual")
                rejected.discard(tag)
        before = current.get(item.id, {})
        gone = [tag for tag in before if tag not in attached]
        if gone:
            conn.executemany("DELETE FROM block_tags WHERE block_id=? AND tag_id=?", [(item.id, tag) for tag in gone])
        fresh = [(item.id, tag, source, at) for tag, source in attached.items() if before.get(tag) != source]
        if fresh:
            conn.executemany("INSERT INTO block_tags(block_id,tag_id,source,created_at) VALUES (?,?,?,?) "
                             "ON CONFLICT(block_id,tag_id) DO UPDATE SET source=excluded.source", fresh)
        was = rejected_now.get(item.id, set())
        if was - rejected:
            conn.executemany("DELETE FROM tag_rejections WHERE block_id=? AND tag_id=?", [(item.id, tag) for tag in was - rejected])
        if rejected - was:
            conn.executemany("INSERT INTO tag_rejections(block_id,tag_id,created_at) VALUES (?,?,?)",
                             [(item.id, tag, at) for tag in rejected - was])
    for root in roots:
        _refresh_inline(conn, root, at)


def _refresh_inline(conn: sqlite3.Connection, tag: Block, at: str) -> None:
    """A tag record changed (created, renamed, aliased, trashed): re-derive its ``#name`` attachments."""
    key = key_of(tag.id)
    conn.execute(f"DELETE FROM block_tags WHERE tag_id=? AND source='manual' AND block_id NOT IN "
                 f"(SELECT id FROM blocks WHERE json_extract(properties, '$.{key}') = 'manual')", (tag.id,))
    if not tag.live:
        return
    props = tag.properties
    aliases = props.get("aliases") if isinstance(props.get("aliases"), list) else []
    names = sorted({normalize_term(value) for value in (tag.text, *aliases) if isinstance(value, str) and normalize_term(value)})
    if not names:
        return
    conn.execute(f"INSERT INTO block_tags(block_id,tag_id,source,created_at) "
                 f"SELECT DISTINCT t.block_id, ?, 'manual', ? FROM block_topics t JOIN blocks b ON b.id=t.block_id "
                 f"WHERE t.kind='tag' AND t.topic IN ({','.join('?' * len(names))}) AND b.deleted_at IS NULL "
                 f"AND NOT (b.type='tag' AND b.parent_id IS NULL) ON CONFLICT(block_id,tag_id) DO NOTHING",
                 (tag.id, at, *names))
    conn.execute("DELETE FROM tag_rejections WHERE tag_id=? AND block_id IN "
                 "(SELECT block_id FROM block_tags WHERE tag_id=? AND source='manual')", (tag.id, tag.id))

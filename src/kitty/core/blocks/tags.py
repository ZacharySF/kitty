"""Tags (platform design Q13): records, the global mode, tag pages and the backfill.

A tag is a root block of type ``tag`` whose text is its name. Its properties:
``keywords`` and ``aliases`` (lists of strings; the name and aliases match too, and a
``#alias`` token names the tag like ``#name``), ``mode`` (``suggest`` or ``always``
overriding the global setting, absent to follow it) and ``short`` (also match terms
shorter than three characters). Every write here is one block transaction, so it is
logged, pushed live and undoable from History like any edit.

Which blocks carry a tag is each block's ``tag_<id>`` property (kitty.core.blocks.tagging);
``block_tags`` and ``tag_rejections`` are its projection and what the readers here use.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import nullcontext
from typing import Any

from kitty.core.blocks.autotag import MAX_TERM, Tag, Vocabulary, normalize_term
from kitty.core.blocks.pagedoc import BlockError, block_id
from kitty.core.blocks.store import TAG, transact
from kitty.core.blocks.tagging import MODES, TAGGABLE, key_of, load_vocabulary, tag_record
from kitty.db.connection import transaction

MAX_NAME = 100
MAX_TERMS = 200
PAGE_SIZE = 100
#: Link all attaches at most this many blocks at once (one transaction each chunk).
MAX_LINK = 5000
_TAGGABLE_SQL = ",".join(f"'{kind}'" for kind in sorted(TAGGABLE))
_LIKE_ESCAPE = re.compile(r"([\\%_])")


def _read(conn: sqlite3.Connection):
    return nullcontext() if conn.in_transaction else transaction(conn)


# Settings --------------------------------------------------------------------------------

def settings(conn: sqlite3.Connection) -> dict:
    """The global Tags setting. It moves into the build file once builds exist (Q8)."""
    row = conn.execute("SELECT mode, revision FROM tag_settings WHERE id=1").fetchone()
    return {"mode": row["mode"], "revision": row["revision"]}


def set_mode(conn: sqlite3.Connection, mode: Any, revision: Any) -> dict:
    if mode not in MODES:
        raise BlockError("The tag mode is suggest or always")
    if type(revision) is not int:
        raise BlockError("Send the settings revision you last saw")
    with transaction(conn, immediate=True):
        if conn.execute("UPDATE tag_settings SET mode=?, revision=revision+1 WHERE id=1 AND revision=?",
                        (mode, revision)).rowcount != 1:
            raise BlockError("Tag settings changed in another window; reload them", 409)
    return settings(conn)


# Records ---------------------------------------------------------------------------------

def _name(value: Any) -> str:
    if not isinstance(value, str) or not normalize_term(value) or len(value.strip()) > MAX_NAME or "\n" in value:
        raise BlockError(f"A tag name is 1–{MAX_NAME} characters on one line")
    return " ".join(value.split())


def _terms(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_TERMS or not all(isinstance(item, str) for item in value):
        raise BlockError(f"Tag {what} are a list of at most {MAX_TERMS} strings")
    kept: dict[str, str] = {}
    for item in value:
        term = " ".join(item.split())
        if len(term) > MAX_TERM or "\n" in item:
            raise BlockError(f"Each tag {what[:-1]} is at most {MAX_TERM} characters on one line")
        if term:
            kept.setdefault(normalize_term(term), term)
    return list(kept.values())


def _fields(body: dict, *, creating: bool) -> tuple[str | None, dict[str, Any]]:
    """Validated (name, property changes) from a create/update request; None removes a property."""
    allowed = {"name", "keywords", "aliases", "mode", "short"}
    if set(body) - allowed:
        raise BlockError("Unexpected tag fields")
    name = _name(body["name"]) if "name" in body or creating else None
    props: dict[str, Any] = {}
    if "keywords" in body:
        props["keywords"] = _terms(body["keywords"], "keywords")
    if "aliases" in body:
        props["aliases"] = _terms(body["aliases"], "aliases")
    if "mode" in body:
        if body["mode"] is not None and body["mode"] not in MODES:
            raise BlockError("A tag's mode is suggest, always, or null to follow the setting")
        props["mode"] = body["mode"]
    if "short" in body:
        if type(body["short"]) is not bool:
            raise BlockError("short is true or false")
        props["short"] = body["short"] or None
    return name, props


def _taken(conn: sqlite3.Connection, names: list[str], other_than: str | None = None) -> str | None:
    """A name or alias already used by another live tag."""
    vocab = load_vocabulary(conn)
    for name in names:
        owners = [tag for tag in vocab.names.get(normalize_term(name), ()) if tag != other_than]
        if owners:
            return name
    return None


def _row(conn: sqlite3.Connection, tag_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, text, properties, deleted_at FROM blocks WHERE id=? AND type='tag' AND parent_id IS NULL",
                       (block_id(tag_id),)).fetchone()
    if row is None or row["deleted_at"] is not None:
        raise BlockError("Tag not found", 404)
    return row


def _json(tag: Tag, default: str, count: int = 0, revision: int = 1) -> dict:
    return {"id": tag.id, "name": tag.name, "keywords": list(tag.terms), "aliases": list(tag.aliases), "mode": tag.mode,
            "short": tag.short, "effective_mode": tag.mode or default, "count": count, "revision": revision}


def get(conn: sqlite3.Connection, tag_id: str) -> dict:
    with _read(conn):
        row = _row(conn, tag_id)
        count = conn.execute("SELECT COUNT(*) FROM block_tags t JOIN blocks b ON b.id=t.block_id AND b.deleted_at IS NULL "
                             "WHERE t.tag_id=?", (row["id"],)).fetchone()[0]
        return _json(tag_record(row), settings(conn)["mode"], count, json.loads(row["properties"]).get("revision", 1))


def list_tags(conn: sqlite3.Connection) -> dict:
    """The setting and every live tag with its number of tagged blocks, by name."""
    with _read(conn):
        current = settings(conn)
        counts = dict(conn.execute("SELECT t.tag_id, COUNT(*) FROM block_tags t JOIN blocks b ON b.id=t.block_id "
                                   "AND b.deleted_at IS NULL GROUP BY t.tag_id").fetchall())
        rows = conn.execute("SELECT id, text, properties FROM blocks WHERE type='tag' AND parent_id IS NULL AND deleted_at IS NULL")
        tags = [_json(tag_record(row), current["mode"], counts.get(row["id"], 0), json.loads(row["properties"]).get("revision", 1))
                for row in rows]
    tags.sort(key=lambda tag: (tag["name"].casefold(), tag["id"]))
    return {**current, "tags": tags}


def find(conn: sqlite3.Connection, name: Any) -> str | None:
    """The live tag a name or alias (as written after ``#``) names."""
    if not isinstance(name, str) or len(name) > MAX_NAME:
        raise BlockError("Name a tag")
    owners = load_vocabulary(conn).names.get(normalize_term(name), ())
    return owners[0] if owners else None


def create(conn: sqlite3.Connection, body: dict) -> dict:
    name, props = _fields(body, creating=True)
    assert name is not None
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        clash = _taken(conn, [name, *props.get("aliases", [])])
        if clash:
            raise BlockError(f"A tag already uses the name {clash!r}", 409)
        tag_id = str(uuid.uuid4())
        clean = {key: value for key, value in props.items() if value is not None}
        transact(conn, [{"op": "insert", "id": tag_id, "type": "tag", "parent": None, "text": name,
                         "props": {"revision": 1, "keywords": [], "aliases": [], **clean}}])
        return get(conn, tag_id)


def update(conn: sqlite3.Connection, tag_id: Any, body: dict) -> dict:
    """Change a tag's name, keywords, aliases, mode or short-term opt-in (one transaction)."""
    if not isinstance(body, dict):
        raise BlockError("Expected tag fields")
    name, props = _fields(body, creating=False)
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        row = _row(conn, tag_id)
        current = tag_record(row)
        clash = _taken(conn, [name or current.name, *props.get("aliases", current.aliases)], other_than=row["id"])
        if clash:
            raise BlockError(f"A tag already uses the name {clash!r}", 409)
        ops: list[dict] = []
        if props:
            ops.append({"op": "set", "id": row["id"], "props": props})
        if name is not None and name != row["text"]:
            ops.append({"op": "text", "id": row["id"], "text": name})
        if ops:
            transact(conn, ops)
        return get(conn, row["id"])


def delete(conn: sqlite3.Connection, tag_id: Any) -> dict:
    """Move a tag to Trash (History and Trash can bring it back with its attachments)."""
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        row = _row(conn, tag_id)
        transact(conn, [{"op": "delete", "id": row["id"]}])
    return {"deleted": row["id"]}


# Attachments -----------------------------------------------------------------------------

def _set_state(conn: sqlite3.Connection, block: Any, tag: Any, state: str) -> dict:
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        row = _row(conn, tag)
        target = conn.execute("SELECT id, type, parent_id, deleted_at FROM blocks WHERE id=?", (block_id(block),)).fetchone()
        if target is None or target["deleted_at"] is not None:
            raise BlockError("Block not found", 404)
        if target["parent_id"] is None:
            raise BlockError("Tag blocks within a page, not the page itself")
        result = transact(conn, [{"op": "set", "id": target["id"], "props": {key_of(row["id"]): state}}])
    return {"block": target["id"], "tag": row["id"], "state": state, "seq": result["seq"]}


def attach(conn: sqlite3.Connection, block: Any, tag: Any) -> dict:
    """Attach a tag to one block by hand (``manual``); it also lifts an earlier rejection."""
    return _set_state(conn, block, tag, "manual")


def remove(conn: sqlite3.Connection, block: Any, tag: Any) -> dict:
    """Detach a tag from one block and remember it (``rejected``): no edit attaches or suggests it again.

    A ``#name`` token in the block's text still tags it; remove the token to untag it.
    """
    return _set_state(conn, block, tag, "rejected")


# Tag pages ------------------------------------------------------------------------------

def _mentions(conn: sqlite3.Connection, tag: Tag, limit: int) -> tuple[list[dict], int]:
    """Live text blocks of note pages whose words match `tag` but that neither carry nor rejected it."""
    vocab = Vocabulary.of([tag])
    if vocab.empty:
        return [], 0
    terms = sorted({normalize_term(value) for value in (tag.name, *tag.aliases, *tag.terms) if normalize_term(value)})
    clauses, params = [], []
    for term in terms:
        if term.isascii():
            clauses.append("b.text LIKE ? ESCAPE '\\'")
            params.append("%" + _LIKE_ESCAPE.sub(r"\\\1", term) + "%")
        else:
            clauses.append("instr(kitty_casefold(b.text), ?) > 0")
            params.append(term)
    conn.create_function("kitty_casefold", 1, lambda text: text.casefold() if isinstance(text, str) else text, deterministic=True)
    rows = conn.execute(
        f"SELECT b.id, b.text, b.properties, b.page_id, p.text AS title, CAST(json_extract(p.properties,'$.number') AS INTEGER) AS number "
        f"FROM blocks b JOIN blocks p ON p.id=b.page_id AND p.type='page' AND p.deleted_at IS NULL "
        f"WHERE b.deleted_at IS NULL AND b.parent_id IS NOT NULL AND b.type IN ({_TAGGABLE_SQL}) AND ({' OR '.join(clauses)}) "
        f"AND NOT EXISTS (SELECT 1 FROM block_tags t WHERE t.block_id=b.id AND t.tag_id=?) "
        f"AND NOT EXISTS (SELECT 1 FROM tag_rejections r WHERE r.block_id=b.id AND r.tag_id=?) "
        f"ORDER BY b.updated_at DESC, b.id", (*params, tag.id, tag.id))
    found: list[dict] = []
    total = 0
    for row in rows:
        if not vocab.scan(row["text"], marks=json.loads(row["properties"]).get("marks")):
            continue
        total += 1
        if len(found) < limit:
            found.append({"id": row["id"], "page": row["page_id"], "number": row["number"], "title": row["title"],
                          "text": row["text"][:2000]})
    return found, total


def page(conn: sqlite3.Connection, tag_id: Any, offset: int = 0) -> dict:
    """A tag page: the tag, the blocks it is attached to (backlinks, newest first) and its unlinked mentions."""
    with _read(conn):
        tag = get(conn, tag_id)
        rows = conn.execute(
            f"SELECT b.id, substr(b.text,1,2000) AS text, t.source, t.created_at, b.page_id, p.type AS page_type, p.text AS title, "
            f"CAST(json_extract(p.properties,'$.number') AS INTEGER) AS number, "
            f"json_extract(b.properties, '$.{key_of(tag['id'])}') IS NOT t.source AS inline "
            "FROM block_tags t JOIN blocks b ON b.id=t.block_id AND b.deleted_at IS NULL "
            "JOIN blocks p ON p.id=b.page_id AND p.deleted_at IS NULL "
            "WHERE t.tag_id=? ORDER BY b.updated_at DESC, b.id LIMIT ? OFFSET ?", (tag["id"], PAGE_SIZE, offset)).fetchall()
        record = tag_record(_row(conn, tag["id"]))
        unlinked, unlinked_count = _mentions(conn, record, PAGE_SIZE)
    count = tag["count"]
    return {"tag": tag, "blocks": [{"id": row["id"], "text": row["text"], "source": row["source"], "page": row["page_id"],
                                    "page_type": row["page_type"], "number": row["number"], "title": row["title"],
                                    "inline": bool(row["inline"])} for row in rows],
            "count": count, "next_offset": offset + len(rows) if offset + len(rows) < count else None,
            "unlinked": unlinked, "unlinked_count": unlinked_count}


def link_all(conn: sqlite3.Connection, tag_id: Any) -> dict:
    """Attach the tag (``accepted``) to every unlinked mention, in as few transactions as it takes."""
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        row = _row(conn, tag_id)
        mentions, _ = _mentions(conn, tag_record(row), MAX_LINK)
        key = key_of(row["id"])
        ops = [{"op": "set", "id": item["id"], "props": {key: "accepted"}} for item in mentions]
        for start in range(0, len(ops), 2000):
            transact(conn, ops[start:start + 2000])
    return {"linked": len(ops), "tag": row["id"]}


# Migration --------------------------------------------------------------------------------

def backfill(conn: sqlite3.Connection) -> None:
    """Migration 037: every ``#name`` already written becomes a tag record (spelled as first written)."""
    existing = load_vocabulary(conn)
    ops: list[dict] = []
    for (topic,) in conn.execute("SELECT DISTINCT topic FROM block_topics WHERE kind='tag' ORDER BY topic").fetchall():
        if normalize_term(topic) in existing.names:
            continue
        sample = conn.execute("SELECT b.text FROM block_topics t JOIN blocks b ON b.id=t.block_id WHERE t.kind='tag' AND t.topic=? "
                              "ORDER BY b.created_at, b.id LIMIT 1", (topic,)).fetchone()
        spelled = next((match[1] for match in TAG.finditer(sample[0] if sample else "") if match[1].casefold() == topic), topic)
        ops.append({"op": "insert", "id": str(uuid.uuid4()), "type": "tag", "parent": None, "text": spelled[:MAX_NAME],
                    "props": {"revision": 1, "keywords": [], "aliases": []}})
    for start in range(0, len(ops), 5000):
        transact(conn, ops[start:start + 5000])

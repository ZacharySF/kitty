"""Notes' concept maps, folded into the mind-map editor; the page ↔ map link.

A page's old concept map was the text and sticky elements joined by connectors on
its canvas document. `migrate` moves every such map once into a schema map, after
writing an owner-only JSON export of the affected pages; `revert` removes the maps
it created so the move can run again. Page documents are never modified: the cards
stay on the Canvas. `note_page_maps` links a page, by its Notes number, to its one
map either way (migration 042: pages live in the block projection, note_pages is the
frozen pre-block copy the one-time move reads).
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kitty.core import notes, schema_maps
from kitty.db.connection import transaction

EXPORT_FORMAT = "kitty-concept-maps"
CONCEPT_KINDS = frozenset({"text", "sticky"})
_COORDINATE = 100_000
_VIEWPORT = {"x": 40, "y": 40, "zoom": 1}


def _kind(element: Any) -> str | None:
    kind = element.get("kind") if isinstance(element, dict) else None
    return kind if isinstance(kind, str) else None


def _coordinate(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 0
    return max(-_COORDINATE, min(_COORDINATE, value))


def _valid_key(value: Any) -> bool:
    try:
        return schema_maps._key(value) == value
    except ValueError:
        return False


def _keys(values: list) -> list[str]:
    """Schema graph IDs for `values` in order; invalid or repeated ones get a fresh `concept-N`."""
    taken = {value for value in values if _valid_key(value)}
    keys, used, fresh = [], set(), 0
    for value in values:
        if _valid_key(value) and value not in used:
            key = value
        else:
            fresh += 1
            while f"concept-{fresh}" in taken or f"concept-{fresh}" in used:
                fresh += 1
            key = f"concept-{fresh}"
        used.add(key)
        keys.append(key)
    return keys


def _title(value: Any) -> str:
    return (value.strip() if isinstance(value, str) else "")[:200].strip() or "Untitled page"


def concept_graph(document: Any) -> dict | None:
    """The schema graph of a canvas document's concept map, or None when it has none.

    Concepts are text/sticky elements joined to another one by a connector. A
    breadth-first spanning forest over the connectors (from → to, document order,
    roots without incoming connectors first) gives the tree edges; every other
    connector, including those closing cycles, becomes a cross connection.
    """
    elements = document.get("elements") if isinstance(document, dict) else None
    if not isinstance(elements, list):
        return None
    cards: dict[str, dict] = {}
    for element in elements:
        if _kind(element) in CONCEPT_KINDS and isinstance(element.get("id"), str):
            cards.setdefault(element["id"], element)
    links = [element for element in elements if _kind(element) == "connector"
             and isinstance(element.get("from"), str) and isinstance(element.get("to"), str)
             and element["from"] != element["to"] and element["from"] in cards and element["to"] in cards]
    if not links:
        return None
    ends = {end for link in links for end in (link["from"], link["to"])}
    concepts = [eid for eid in cards if eid in ends]
    node_keys = dict(zip(concepts, _keys(concepts)))
    edge_keys = _keys([link.get("id") for link in links])

    children: dict[str, list[tuple[int, str]]] = {eid: [] for eid in concepts}
    incoming = set()
    for index, link in enumerate(links):
        children[link["from"]].append((index, link["to"]))
        incoming.add(link["to"])
    tree, seen = set(), set()
    for root in [eid for eid in concepts if eid not in incoming] + concepts:
        if root in seen:
            continue
        seen.add(root)
        queue = deque([root])
        while queue:
            for index, child in children[queue.popleft()]:
                if child not in seen:
                    seen.add(child)
                    tree.add(index)
                    queue.append(child)

    nodes = []
    for eid in concepts:
        element = cards[eid]
        body = element.get("text") if isinstance(element.get("text"), str) else ""
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        label = first[:200].strip() or "Concept"
        full = body.strip()
        nodes.append({"id": node_keys[eid], "kind": "topic", "label": label,
                      "notes": full[:10000] if full and full != label else "",
                      "x": _coordinate(element.get("x")), "y": _coordinate(element.get("y")),
                      "url": "", "source": None, "free": True})
    edges = []
    for index, link in enumerate(links):
        label = link.get("text") if isinstance(link.get("text"), str) else ""
        edges.append({"id": edge_keys[index], "from": node_keys[link["from"]], "to": node_keys[link["to"]],
                      "label": label.strip()[:200].strip(), **({} if index in tree else {"kind": "cross"})})
    return {"nodes": nodes, "edges": edges}


def _done(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM concept_map_migration WHERE id=1").fetchone() is not None


def _exports_folder(exports_dir: Path) -> Path:
    if exports_dir.is_symlink():
        raise ValueError("The exports folder cannot be a symbolic link")
    exports_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(exports_dir, 0o700)
    return exports_dir


def _write_export(exports_dir: Path, payload: dict, stamp: datetime) -> Path:
    """Write the owner-only export; refuses to overwrite an existing file."""
    path = _exports_folder(exports_dir) / f"concept-maps-{stamp:%Y%m%dT%H%M%S%fZ}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2).encode())
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _insert_map(conn: sqlite3.Connection, title: str, document: dict, stamp: str) -> int:
    return conn.execute("INSERT INTO schema_maps(title,mode,graph_json,viewport_json,updated_at) VALUES(?,?,?,?,?)",
                        (title, "study", json.dumps(document), json.dumps(_VIEWPORT), stamp)).lastrowid


def migrate(conn: sqlite3.Connection, exports_dir: Path) -> dict:
    """Move every unlinked page's concept map into a schema map, once.

    The export of the affected pages is written before any map is created. Pages
    whose graph exceeds the map limits, or that arrive after MAX_MAPS is reached,
    stay unmigrated (their cards remain on the Canvas) and are reported as skipped.
    """
    result: dict[str, Any] = {"migrated": [], "skipped": [], "export": None}
    if _done(conn):
        return result
    created: Path | None = None
    try:
        with transaction(conn, immediate=True):
            if _done(conn):
                return result
            stamp = datetime.now(timezone.utc)
            candidates = []
            # Canvas documents are read from note_pages, frozen by the block migration (032/033) at
            # the content it converted; this one-time move runs on the first start after both.
            # Only pages still live in the block projection are moved, under their current title.
            for row in conn.execute("""SELECT p.id,p.notebook_id,i.title,p.revision,p.document FROM note_pages p
                    JOIN note_page_index i ON i.id = p.id
                    WHERE NOT EXISTS (SELECT 1 FROM note_page_maps m WHERE m.page_id=p.id) ORDER BY p.id""").fetchall():
                try:
                    document = json.loads(row["document"])
                except ValueError:
                    continue
                concept = concept_graph(document)
                if concept is not None:
                    candidates.append(({**dict(row), "document": document}, concept))
            if candidates:
                created = _write_export(exports_dir, {
                    "format": EXPORT_FORMAT, "version": 1, "exported_at": stamp.isoformat(),
                    "pages": [{key: page[key] for key in ("id", "notebook_id", "title", "revision", "document")}
                              for page, _ in candidates]}, stamp)
                result["export"] = str(created)
                count = conn.execute("SELECT count(*) FROM schema_maps").fetchone()[0]
                for page, concept in candidates:
                    try:
                        document = schema_maps.graph(conn, concept)
                    except ValueError:
                        document = None
                    if document is None or count >= schema_maps.MAX_MAPS:
                        result["skipped"].append(page["id"])
                        continue
                    map_id = _insert_map(conn, _title(page["title"]), document, stamp.isoformat())
                    conn.execute("INSERT INTO note_page_maps(page_id,map_id,migrated) VALUES(?,?,1)", (page["id"], map_id))
                    count += 1
                    result["migrated"].append(page["id"])
            conn.execute("INSERT INTO concept_map_migration(id,done_at,export_path) VALUES(1,?,?)",
                         (stamp.isoformat(), result["export"]))
    except BaseException:
        if created is not None:
            created.unlink(missing_ok=True)
        raise
    return result


def revert(conn: sqlite3.Connection, export_path: Path | str) -> int:
    """Delete the migrated maps of the pages in `export_path` and clear the marker; returns maps removed."""
    payload = json.loads(Path(export_path).read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT
            or type(payload.get("version")) is not int or payload["version"] != 1
            or not isinstance(payload.get("pages"), list)):
        raise ValueError("Not a kitty concept-map export")
    page_ids = [page["id"] for page in payload["pages"] if isinstance(page, dict) and type(page.get("id")) is int]
    removed = 0
    with transaction(conn, immediate=True):
        for page_id in page_ids:
            row = conn.execute("SELECT map_id FROM note_page_maps WHERE page_id=? AND migrated=1", (page_id,)).fetchone()
            if row is not None:
                removed += conn.execute("DELETE FROM schema_maps WHERE id=?", (row["map_id"],)).rowcount
        conn.execute("DELETE FROM concept_map_migration")
    return removed


def _page(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row:
    """A live note page by its Notes number, from the block projection (note_pages is frozen)."""
    row = conn.execute("SELECT id,title FROM note_page_index WHERE id=?", (notes.integer(page_id),)).fetchone()
    if row is None:
        raise notes.NotesError("Page not found", 404)
    return row


def _linked(conn: sqlite3.Connection, page_id: int) -> int | None:
    row = conn.execute("SELECT map_id FROM note_page_maps WHERE page_id=?", (page_id,)).fetchone()
    return row["map_id"] if row else None


def page_map(conn: sqlite3.Connection, page_id: int) -> dict:
    """`{"map_id": id | None}` of the page's mind map."""
    page = _page(conn, page_id)
    return {"map_id": _linked(conn, page["id"])}


def create_page_map(conn: sqlite3.Connection, page_id: int) -> dict:
    """The page's mind map, created with the page as its root topic when it has none."""
    with transaction(conn, immediate=True):
        page = _page(conn, page_id)
        existing = _linked(conn, page["id"])
        if existing is not None:
            return {"map_id": existing}
        if conn.execute("SELECT count(*) FROM schema_maps").fetchone()[0] >= schema_maps.MAX_MAPS:
            raise ValueError(f"At most {schema_maps.MAX_MAPS} maps are supported; export and delete an old map first")
        title = _title(page["title"])
        document = schema_maps.graph(conn, {"nodes": [{
            "id": "page", "kind": "topic", "label": title, "notes": "", "x": 40, "y": 40, "url": "",
            "source": {"kind": "note_page", "id": page["id"]}}], "edges": []})
        map_id = _insert_map(conn, title, document, datetime.now(timezone.utc).isoformat())
        conn.execute("INSERT INTO note_page_maps(page_id,map_id,migrated) VALUES(?,?,0)", (page["id"], map_id))
    return {"map_id": map_id}

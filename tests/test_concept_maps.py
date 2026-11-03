"""One-time move of Notes' concept maps into schema maps, its export/revert, and the page ↔ map link."""
from __future__ import annotations

import dataclasses
import json
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitty import web_notes
from kitty.core import blocks, concept_maps, notes, schema_maps
from kitty.core.workspace import open_workspace
from kitty.db import connect, migrate
from kitty.db.migrations import available_migrations
from kitty.web_server import APIError

LONG = "Energy " + "x" * 250


def element(kind, eid, **changes):
    value = {"id": eid, "kind": kind, "layer": "default", "x": 0, "y": 0, "w": 160, "h": 90,
             "color": "#344054", "fill": "none", "width": 2}
    if kind in {"pen", "connector"}:
        value["points"] = [[0, 0, .5], [10, 10, .5]]
    if kind in {"text", "sticky"}:
        value.update(text="", fontSize=20)
    value.update(changes)
    return value


def link(eid, start, end, label=None):
    return element("connector", eid, **{"from": start, "to": end}, **({"text": label} if label is not None else {}))


def concept_document():
    """Three stickies and a text element joined by labelled connectors with a cycle, plus noise."""
    document = notes.empty_document()
    document["elements"] = [
        element("sticky", "s1", x=10, y=20, text="Photosynthesis\nConverts light to sugar"),
        element("pen", "ink"),
        element("sticky", "s2", x=200.5, y=-20, text="  Chlorophyll  "),
        element("sticky", "s3", x=250_000, y=-300_000, text="Glucose"),
        element("text", "text.1", x=5, y=6, text=f"\n  {LONG}"),
        element("sticky", "lonely", x=900, y=900, text="Unconnected"),
        element("image", "img", attachment="a" * 32),
        link("c1", "s1", "s2", " absorbs light via "),
        link("c2", "s2", "s3", "produces"),
        link("c3", "s3", "s1", "fuels"),
        link("c4", "s1", "text.1"),
        link("c5", "s1", "img", "ignored"),
        link("link one", "s2", "text.1", "  spaced  "),
    ]
    return document


#: Main's own migrations before the block backend (034-036 included): the schema a released
#: rika! had when its pages still lived in note_pages with their canvas documents.
MAIN_SCHEMA = {"record_events", "calendar_palette_subjects", "note_page_maps"}


@pytest.fixture
def legacy(workspace, tmp_path):
    """A database at main's pre-block schema; `upgrade()` then runs every migration since."""
    early = tmp_path / "main-schema"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 32 or migration.name in MAIN_SCHEMA:
            shutil.copy(migration.path, early / migration.path.name)
    settings = dataclasses.replace(workspace.settings, db_path=tmp_path / "data" / "legacy.db")
    conn = connect(settings.db_path)
    migrate(conn, early)
    space = SimpleNamespace(settings=settings, conn=conn, upgrade=lambda: migrate(conn))
    yield space
    conn.close()


def legacy_page(conn, title="Cell energy", document=None, notebook=None):
    """A note page as main stored it: a note_pages row with its canvas document."""
    if notebook is None:
        notebook = conn.execute("INSERT INTO note_notebooks(title, created_at, updated_at) VALUES ('Biology', 't', 't')").lastrowid
    page_id = conn.execute("INSERT INTO note_pages(notebook_id, title, document, created_at, updated_at) VALUES (?, ?, ?, 't', 't')",
                           (notebook, title, json.dumps(document or notes.empty_document()))).lastrowid
    return {"id": page_id, "notebook_id": notebook}


def make_page(conn, title="Cell energy", notebook=None):
    """A note page as the block backend stores it."""
    notebook = notebook or notes.create_notebook(conn, "Biology")
    return notes.create_page(conn, notebook["id"], title)


def root_block(conn, page_id):
    return conn.execute("SELECT id FROM blocks WHERE type='page' AND parent_id IS NULL AND "
                        "json_extract(properties, '$.number')=?", (page_id,)).fetchone()[0]


def raw_document(conn, page_id):
    return conn.execute("SELECT document FROM note_pages WHERE id=?", (page_id,)).fetchone()[0]


def link_row(conn, page_id):
    row = conn.execute("SELECT map_id, migrated FROM note_page_maps WHERE page_id=?", (page_id,)).fetchone()
    return dict(row) if row else None


def exports(workspace):
    return workspace.settings.data_dir / "exports"


def concept_exports(workspace):
    """The concept-map exports (the block migration's own notes export sits beside them)."""
    return sorted(exports(workspace).glob("concept-maps-*.json"))


def small_concepts(*labels):
    document = notes.empty_document()
    document["elements"] = [element("sticky", f"n{index}", text=label) for index, label in enumerate(labels)]
    document["elements"] += [link(f"e{index}", f"n{index}", f"n{index + 1}") for index in range(len(labels) - 1)]
    return document


def test_concept_graph_tree_cross_labels_notes_and_free_positions():
    graph = concept_maps.concept_graph(concept_document())
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert list(nodes) == ["s1", "s2", "s3", "concept-1"]
    assert nodes["s1"] == {"id": "s1", "kind": "topic", "label": "Photosynthesis",
                           "notes": "Photosynthesis\nConverts light to sugar", "x": 10, "y": 20,
                           "url": "", "source": None, "free": True}
    assert nodes["s2"]["label"] == "Chlorophyll" and nodes["s2"]["notes"] == ""
    assert (nodes["s2"]["x"], nodes["s2"]["y"]) == (200.5, -20)
    assert (nodes["s3"]["x"], nodes["s3"]["y"]) == (100_000, -100_000)
    assert nodes["concept-1"]["label"] == LONG[:200]
    assert nodes["concept-1"]["notes"] == LONG
    assert graph["edges"] == [
        {"id": "c1", "from": "s1", "to": "s2", "label": "absorbs light via"},
        {"id": "c2", "from": "s2", "to": "s3", "label": "produces"},
        {"id": "c3", "from": "s3", "to": "s1", "label": "fuels", "kind": "cross"},
        {"id": "c4", "from": "s1", "to": "concept-1", "label": ""},
        {"id": "concept-1", "from": "s2", "to": "concept-1", "label": "spaced", "kind": "cross"},
    ]
    assert concept_maps.concept_graph(notes.empty_document()) is None
    only_image = notes.empty_document()
    only_image["elements"] = [element("sticky", "a"), element("image", "img", attachment="a" * 32), link("c", "a", "img")]
    assert concept_maps.concept_graph(only_image) is None


def test_roots_without_incoming_connectors_lead_the_forest():
    document = small_concepts("Leaf", "Middle", "Root")
    document["elements"] = document["elements"][:3] + [link("up", "n2", "n1"), link("down", "n1", "n0"), link("back", "n0", "n1")]
    edges = {edge["id"]: edge.get("kind", "tree") for edge in concept_maps.concept_graph(document)["edges"]}
    assert edges == {"up": "tree", "down": "tree", "back": "cross"}
    blank = {"elements": [element("sticky", "s"), element("text", "t", text="   "), link("c", "s", "t")]}
    assert [node["label"] for node in concept_maps.concept_graph(blank)["nodes"]] == ["Concept", "Concept"]


def test_migration_exports_first_then_creates_maps_without_touching_pages(legacy, monkeypatch):
    conn = legacy.conn
    workspace = legacy
    page = legacy_page(conn, document=concept_document())
    plain = legacy_page(conn, "Plain ink", document=small_concepts("Alone"))
    legacy.upgrade()
    before = raw_document(conn, page["id"])
    page_row = dict(conn.execute("SELECT * FROM note_pages WHERE id=?", (page["id"],)).fetchone())

    seen = {}
    original = concept_maps._write_export

    def spy(folder, payload, stamp):
        seen["maps"] = conn.execute("SELECT count(*) FROM schema_maps").fetchone()[0]
        seen["links"] = conn.execute("SELECT count(*) FROM note_page_maps").fetchone()[0]
        return original(folder, payload, stamp)

    monkeypatch.setattr(concept_maps, "_write_export", spy)
    result = concept_maps.migrate(conn, exports(workspace))
    assert seen == {"maps": 0, "links": 0}
    assert result["migrated"] == [page["id"]] and result["skipped"] == []

    path = Path(result["export"])
    assert path.parent == exports(workspace) and path.name.startswith("concept-maps-") and path.suffix == ".json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(exports(workspace).stat().st_mode) == 0o700
    exported = json.loads(path.read_text(encoding="utf-8"))
    assert exported["format"] == "kitty-concept-maps" and exported["version"] == 1 and exported["exported_at"]
    assert exported["pages"] == [{"id": page["id"], "notebook_id": page["notebook_id"], "title": "Cell energy",
                                  "revision": page_row["revision"], "document": json.loads(before)}]

    assert raw_document(conn, page["id"]) == before
    assert dict(conn.execute("SELECT * FROM note_pages WHERE id=?", (page["id"],)).fetchone()) == page_row
    assert link_row(conn, plain["id"]) is None
    linked = link_row(conn, page["id"])
    assert linked["migrated"] == 1
    item = schema_maps.load(conn, linked["map_id"])
    assert (item["title"], item["mode"], item["viewport"]) == ("Cell energy", "study", {"x": 40, "y": 40, "zoom": 1})
    assert item["graph"] == schema_maps.graph(conn, concept_maps.concept_graph(json.loads(before)))
    assert [edge.get("kind", "tree") for edge in item["graph"]["edges"]] == ["tree", "tree", "cross", "tree", "cross"]
    assert all(node["free"] for node in item["graph"]["nodes"])
    assert conn.execute("SELECT export_path FROM concept_map_migration").fetchone()[0] == result["export"]

    again = concept_maps.migrate(conn, exports(workspace))
    assert again == {"migrated": [], "skipped": [], "export": None}
    assert len(concept_exports(workspace)) == 1
    assert conn.execute("SELECT count(*) FROM schema_maps").fetchone()[0] == 1


def test_revert_removes_migrated_maps_and_allows_rerunning(legacy):
    conn = legacy.conn
    workspace = legacy
    page = legacy_page(conn, document=concept_document())
    own = legacy_page(conn, "Hand made")
    legacy.upgrade()
    own_map = concept_maps.create_page_map(conn, own["id"])["map_id"]
    first = concept_maps.migrate(conn, exports(workspace))
    migrated_map = link_row(conn, page["id"])["map_id"]

    assert concept_maps.revert(conn, first["export"]) == 1
    assert conn.execute("SELECT 1 FROM schema_maps WHERE id=?", (migrated_map,)).fetchone() is None
    assert link_row(conn, page["id"]) is None
    assert link_row(conn, own["id"]) == {"map_id": own_map, "migrated": 0}
    assert conn.execute("SELECT count(*) FROM concept_map_migration").fetchone()[0] == 0

    second = concept_maps.migrate(conn, exports(workspace))
    assert second["migrated"] == [page["id"]] and second["export"] != first["export"]
    assert len(concept_exports(workspace)) == 2
    assert link_row(conn, page["id"])["migrated"] == 1

    bogus = exports(workspace) / "bogus.json"
    bogus.write_text(json.dumps({"format": "other", "version": 1, "pages": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="concept-map export"):
        concept_maps.revert(conn, bogus)


def test_nothing_to_move_sets_the_marker_without_an_export(legacy):
    conn = legacy.conn
    legacy_page(conn, document=small_concepts("Alone"))
    later = legacy_page(conn, "Later", document=concept_document())
    legacy.upgrade()
    # A page already in Trash is not moved.
    blocks.transact(conn, [{"op": "delete", "id": root_block(conn, later["id"])}])
    assert concept_maps.migrate(conn, exports(legacy)) == {"migrated": [], "skipped": [], "export": None}
    assert conn.execute("SELECT export_path FROM concept_map_migration").fetchone()[0] is None
    assert concept_exports(legacy) == []
    blocks.transact(conn, [{"op": "restore", "id": root_block(conn, later["id"])}])
    assert concept_maps.migrate(conn, exports(legacy))["migrated"] == [], "the move runs once"


def test_oversized_graphs_and_map_limit_leave_pages_unmigrated(legacy, monkeypatch):
    conn = legacy.conn
    workspace = legacy
    big = legacy_page(conn, "Big", small_concepts("a", "b", "c", "d"))
    fits = legacy_page(conn, "Fits", small_concepts("a", "b"), big["notebook_id"])
    late = legacy_page(conn, "Late", small_concepts("a", "b"), big["notebook_id"])
    legacy.upgrade()
    monkeypatch.setattr(schema_maps, "MAX_MAPS", 2)
    monkeypatch.setattr(schema_maps, "MAX_NODES", 3)
    schema_maps.create(conn, {"title": "Existing", "mode": "general"})
    before = raw_document(conn, late["id"])

    result = concept_maps.migrate(conn, exports(workspace))
    assert result["migrated"] == [fits["id"]]
    assert result["skipped"] == [big["id"], late["id"]]
    exported = json.loads(open(result["export"], encoding="utf-8").read())
    assert [page["id"] for page in exported["pages"]] == [big["id"], fits["id"], late["id"]]
    assert link_row(conn, big["id"]) is None and link_row(conn, late["id"]) is None
    assert raw_document(conn, late["id"]) == before
    assert conn.execute("SELECT count(*) FROM schema_maps").fetchone()[0] == 2
    with pytest.raises(ValueError, match="At most 2 maps"):
        concept_maps.create_page_map(conn, late["id"])


def test_page_map_is_created_once_follows_trash_and_goes_with_a_purge(workspace):
    conn = workspace.conn
    page = make_page(conn, "Krebs cycle")
    assert concept_maps.page_map(conn, page["id"]) == {"map_id": None}
    created = concept_maps.create_page_map(conn, page["id"])
    assert concept_maps.create_page_map(conn, page["id"]) == created
    assert concept_maps.page_map(conn, page["id"]) == created
    item = schema_maps.load(conn, created["map_id"])
    assert (item["title"], item["mode"]) == ("Krebs cycle", "study")
    assert item["graph"] == {"nodes": [{"id": "page", "kind": "topic", "label": "Krebs cycle", "notes": "",
                                        "x": 40, "y": 40, "url": "",
                                        "source": {"kind": "note_page", "id": page["id"]}}], "edges": []}
    assert link_row(conn, page["id"]) == {"map_id": created["map_id"], "migrated": 0}
    with pytest.raises(notes.NotesError) as missing:
        concept_maps.page_map(conn, 999_999)
    assert missing.value.status == 404

    schema_maps.delete(conn, {"id": item["id"], "revision": item["revision"], "confirm": True})
    assert concept_maps.page_map(conn, page["id"]) == {"map_id": None}
    replacement = concept_maps.create_page_map(conn, page["id"])
    assert link_row(conn, page["id"]) == {"map_id": replacement["map_id"], "migrated": 0}

    # Deleting the page moves it to Trash: the link stays, so a restored page keeps its map.
    root = root_block(conn, page["id"])
    notes.delete_page(conn, page["id"], notes.page(conn, page["id"])["revision"])
    assert link_row(conn, page["id"]) == {"map_id": replacement["map_id"], "migrated": 0}
    with pytest.raises(notes.NotesError):
        concept_maps.page_map(conn, page["id"])
    blocks.transact(conn, [{"op": "restore", "id": root}])
    assert concept_maps.page_map(conn, page["id"]) == replacement
    # Purging the page (Trash expiry) removes the link; the map itself stays.
    blocks.transact(conn, [{"op": "delete", "id": root}])
    blocks.transact(conn, [{"op": "purge", "id": root}])
    assert conn.execute("SELECT count(*) FROM note_page_maps").fetchone()[0] == 0
    assert schema_maps.load(conn, replacement["map_id"])["title"] == "Krebs cycle"

    blank = make_page(conn, "Temp")
    blocks.transact(conn, [{"op": "text", "id": root_block(conn, blank["id"]), "text": ""}])
    untitled = schema_maps.load(conn, concept_maps.create_page_map(conn, blank["id"])["map_id"])
    assert untitled["title"] == "Untitled page" and untitled["graph"]["nodes"][0]["label"] == "Untitled page"


def test_page_map_routes(workspace, monkeypatch):
    conn = workspace.conn
    app = SimpleNamespace(settings=workspace.settings)
    page = make_page(conn, "Routes")
    path = f"/api/notes/pages/{page['id']}/map"
    assert web_notes.get(app, conn, path, {}) == {"map_id": None}
    created = web_notes.post(app, conn, path, {})
    assert isinstance(created["map_id"], int)
    assert web_notes.post(app, conn, path, {}) == created
    assert web_notes.get(app, conn, path, {}) == created
    for call in (lambda: web_notes.get(app, conn, path, {"x": "1"}), lambda: web_notes.post(app, conn, path, {"x": 1})):
        with pytest.raises(APIError) as invalid:
            call()
        assert invalid.value.status == 400
    for call in (web_notes.get, web_notes.post):
        with pytest.raises(APIError) as missing:
            call(app, conn, "/api/notes/pages/999999/map", {})
        assert missing.value.status == 404
    monkeypatch.setattr(schema_maps, "MAX_MAPS", 1)
    other = make_page(conn, "Full")
    with pytest.raises(APIError) as full:
        web_notes.post(app, conn, f"/api/notes/pages/{other['id']}/map", {})
    assert full.value.status == 400 and "At most 1 maps" in str(full.value)


def test_opening_a_workspace_at_mains_schema_converts_pages_then_moves_their_maps_once(legacy):
    page = legacy_page(legacy.conn, document=concept_document())
    legacy.conn.commit()
    with open_workspace(legacy.settings) as opened:
        assert link_row(opened.conn, page["id"])["migrated"] == 1
        assert concept_maps.page_map(opened.conn, page["id"]) == {"map_id": link_row(opened.conn, page["id"])["map_id"]}
    with open_workspace(legacy.settings):
        pass
    assert len(concept_exports(legacy)) == 1

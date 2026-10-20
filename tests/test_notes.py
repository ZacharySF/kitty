"""Consumer regressions for local canvas persistence, references, and conflicts."""
from __future__ import annotations

import base64
import copy
import io
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest
from PIL import Image, PngImagePlugin
from pypdf import PdfWriter

from kitty import web_notes
from kitty.core import notes
from kitty.web_server import APIError
from kitty.web_tasks import TaskRunner


def element(kind="pen", eid="stroke", **changes):
    value = {"id": eid, "kind": kind, "layer": "default", "x": -150, "y": 40, "w": 100, "h": 50, "color": "#344054", "fill": "none", "width": 4}
    if kind in {"pen", "highlighter", "line", "arrow", "connector"}:
        value["points"] = [[0, 0, .15], [100, 50, .9]]
    if kind in {"text", "sticky"}:
        value.update(text="Remember mitochondria & ATP", fontSize=20)
    value.update(changes)
    return value


def document(*elements):
    value = notes.empty_document()
    value["elements"] = list(elements)
    return value


def image_data():
    stream = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "Should not be retained")
    Image.new("RGB", (40, 20), "#abc123").save(stream, format="PNG", pnginfo=metadata)
    return base64.b64encode(stream.getvalue()).decode()


@pytest.fixture
def page(workspace):
    notebook = notes.create_notebook(workspace.conn, "Biology", "Life sciences", "Research")
    return notes.create_page(workspace.conn, notebook["id"], "Cell energy")


def test_reload_preserves_pressure_layers_groups_text_and_geometry(workspace, page):
    conn = workspace.conn
    doc = document(element(group="group1"), element("sticky", "sticky", group="group1"), element("arrow", "arrow"), element("rect", "rect"), element("ellipse", "ellipse"), element("connector", "connection", **{"from": "sticky", "to": "rect"}))
    doc["layers"].append({"id": "ref", "name": "References", "visible": False, "locked": True})
    doc["grid"], doc["snap"] = False, True
    assert notes.save_page(conn, page["id"], 1, doc, "Energy flow") == {"revision": 2}
    loaded = notes.page(conn, page["id"])
    assert loaded["document"] == notes.normalize(doc)
    assert loaded["title"] == "Energy flow"
    assert loaded["document"]["elements"][0]["points"][1][2] == .9
    assert datetime.fromisoformat(loaded["updated_at"]).tzinfo is not None
    assert loaded["search_text"] == "\nRemember mitochondria & ATP\n\n\n\n"


def test_stale_tab_cannot_overwrite_or_delete_page(workspace, page):
    conn = workspace.conn
    first = document(element("text", "first", text="First tab"))
    notes.save_page(conn, page["id"], 1, first, "First")
    with pytest.raises(notes.NotesError) as conflict:
        notes.save_page(conn, page["id"], 1, document(element("text", "stale", text="Second tab")), "Stale")
    assert conflict.value.status == 409
    with pytest.raises(notes.NotesError) as deletion:
        notes.delete_page(conn, page["id"], 1)
    assert deletion.value.status == 409
    assert notes.page(conn, page["id"])["document"] == notes.normalize(first)


def test_viewport_and_content_versions_do_not_invalidate_each_other(workspace, page):
    conn = workspace.conn
    viewport = {"x": -3000, "y": 650, "zoom": .25}
    notes.save_viewport(conn, page["id"], 1, viewport)
    assert notes.save_page(conn, page["id"], 1, document(element()), "Drawn") == {"revision": 2}
    loaded = notes.page(conn, page["id"])
    assert loaded["viewport"] == viewport
    assert loaded["viewport_revision"] == 2
    notes.save_viewport(conn, page["id"], 2, {"x": 20, "y": 30, "zoom": 4})
    assert notes.page(conn, page["id"])["revision"] == 2
    with pytest.raises(notes.NotesError) as conflict:
        notes.save_viewport(conn, page["id"], 1, viewport)
    assert conflict.value.status == 409


@pytest.mark.parametrize("changes", [{"x": float("nan")}, {"x": float("inf")}, {"width": 0}, {"w": -1}, {"color": "url(javascript:alert(1))"}, {"kind": "script"}, {"layer": []}, {"id": "../../file"}, {"points": [[0, 0, 2]]}, {"points": [[1, 2]]}, {"onclick": "alert(1)"}])
def test_invalid_canvas_values_are_rejected_atomically(workspace, page, changes):
    with pytest.raises(notes.NotesError):
        notes.save_page(workspace.conn, page["id"], 1, document(element(**changes)), "Invalid")
    loaded = notes.page(workspace.conn, page["id"])
    assert loaded["revision"] == 1
    assert loaded["document"]["elements"] == []


def test_page_limits_and_missing_reference_are_rejected(workspace, page, monkeypatch):
    with pytest.raises(notes.NotesError, match="2000"):
        notes.normalize(document(*(element(eid=f"e{i}") for i in range(2001))))
    with pytest.raises(notes.NotesError, match="100000"):
        notes.normalize(document(*(element(eid=f"e{i}", points=[[0, 0, .5]] * 20000) for i in range(6))))
    with pytest.raises(notes.NotesError, match="unregistered"):
        notes.save_page(workspace.conn, page["id"], 1, document(element("image", attachment="unregistered")), "Image")
    with pytest.raises(notes.NotesError, match="endpoints"):
        notes.normalize(document(element("connector", **{"from": "gone", "to": "missing"})))
    monkeypatch.setattr(notes, "MAX_DOCUMENT_BYTES", 50)
    with pytest.raises(notes.NotesError, match="2 MiB"):
        notes.normalize(notes.empty_document())


def test_plain_text_search_and_literal_wildcards(workspace, page):
    notes.save_page(workspace.conn, page["id"], 1, document(element("text", text="ATP 100% concentration")), "Cells")
    assert [p["id"] for p in notes.listing(workspace.conn, "ATP")["pages"]] == [page["id"]]
    assert notes.listing(workspace.conn, "Life sciences")["pages"]
    assert notes.listing(workspace.conn, "Research")["pages"]
    assert notes.listing(workspace.conn, "100%")["pages"]
    assert notes.listing(workspace.conn, "_")["pages"] == []
    assert notes.listing(workspace.conn)["preferences"]["allow_ai"] is False


def test_registered_rasters_strip_metadata_and_never_expose_paths(workspace):
    registered = notes.register(workspace.conn, workspace.settings.data_dir, image_data(), "Reference.png")
    assert len(registered) == 1
    assert "filepath" not in registered[0]
    data = notes.attachment_bytes(workspace.conn, workspace.settings.data_dir, registered[0]["id"])
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (40, 20)
        assert "private" not in image.info
    app = SimpleNamespace(settings=workspace.settings)
    result = web_notes.download(app, workspace.conn, registered[0]["url"], {})
    assert result == (data, "image/png", None)
    assert web_notes.download(app, workspace.conn, "/api/notes/attachments/../../secret", {}) is None


def test_pdf_is_rasterized_without_serving_scripts_or_original_bytes(workspace):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=100)
    writer.add_js("app.alert('must never execute')")
    stream = io.BytesIO()
    writer.write(stream)
    registered = notes.register(workspace.conn, workspace.settings.data_dir, base64.b64encode(stream.getvalue()).decode(), "Lecture.pdf")
    data = notes.attachment_bytes(workspace.conn, workspace.settings.data_dir, registered[0]["id"])
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"app.alert" not in data
    assert not data.startswith(b"%PDF-")


def test_script_svg_and_oversize_storage_cannot_register(workspace, monkeypatch):
    with pytest.raises(notes.NotesError):
        notes.register(workspace.conn, workspace.settings.data_dir, base64.b64encode(b"<svg onload='alert(1)'/>").decode(), "bad.svg")
    monkeypatch.setattr(notes, "MAX_STORAGE_BYTES", 1)
    with pytest.raises(notes.NotesError, match="128 MiB"):
        notes.register(workspace.conn, workspace.settings.data_dir, image_data(), "too-big.png")
    assert workspace.conn.execute("SELECT COUNT(*) FROM note_attachments").fetchone()[0] == 0
    assert list((workspace.settings.data_dir / "notes-attachments").iterdir()) == []


def test_portable_restore_retains_all_content_viewport_and_notebook_links(workspace, page):
    conn, root = workspace.conn, workspace.settings.data_dir
    reference = notes.register(conn, root, image_data(), "Diagram")[0]
    doc = document(element("image", "image", attachment=reference["id"]), element("sticky", "sticky"), element("pen", "pen", group="g"), element("arrow", "arrow", group="g"))
    doc["snap"] = True
    notes.save_page(conn, page["id"], 1, doc, "Portable page")
    notes.save_viewport(conn, page["id"], 1, {"x": -920, "y": 311, "zoom": 2.25})
    exported = notes.export_page(conn, root, page["id"])
    restored = notes.restore(conn, root, 0, exported)
    assert restored["id"] != page["id"]
    assert restored["title"] == "Portable page"
    assert restored["viewport"] == exported["viewport"]
    new_reference = restored["document"]["elements"][0]["attachment"]
    assert new_reference != reference["id"]
    expected = copy.deepcopy(notes.normalize(doc))
    expected["elements"][0]["attachment"] = new_reference
    assert restored["document"] == expected
    notebook = conn.execute("SELECT title,subject,project FROM note_notebooks WHERE id=?", (restored["notebook_id"],)).fetchone()
    assert dict(notebook) == exported["notebook"]
    assert restored["notebook_id"] != page["notebook_id"]
    with Image.open(io.BytesIO(notes.attachment_bytes(conn, root, new_reference))) as image:
        assert image.size == (40, 20)


def test_restore_missing_reference_is_atomic(workspace, page):
    exported = {"format": "kitty-notes", "version": 1, "title": "Bad backup", "notebook": {"title": "Should not exist", "subject": "", "project": ""}, "document": document(element("image", attachment="missing")), "viewport": {"x": 0, "y": 0, "zoom": 1}, "attachments": []}
    before = workspace.conn.execute("SELECT COUNT(*) FROM note_notebooks").fetchone()[0]
    with pytest.raises(notes.NotesError, match="exactly"):
        notes.restore(workspace.conn, workspace.settings.data_dir, 0, exported)
    assert workspace.conn.execute("SELECT COUNT(*) FROM note_notebooks").fetchone()[0] == before
    assert workspace.conn.execute("SELECT COUNT(*) FROM note_pages").fetchone()[0] == 1


def test_retention_only_removes_unused_references_and_rejects_stale_privacy(workspace, page):
    conn, root = workspace.conn, workspace.settings.data_dir
    keep = notes.register(conn, root, image_data(), "Keep")[0]
    discard = notes.register(conn, root, image_data(), "Discard")[0]
    notes.save_page(conn, page["id"], 1, document(element("image", attachment=keep["id"])), "Referenced")
    assert notes.preferences(conn, 1, False, 0)["revision"] == 2
    with pytest.raises(notes.NotesError) as conflict:
        notes.preferences(conn, 1, True, 30)
    assert conflict.value.status == 409
    assert notes.cleanup(conn, root) == {"removed": 1}
    assert notes.attachment_bytes(conn, root, keep["id"])
    with pytest.raises(notes.NotesError):
        notes.attachment_bytes(conn, root, discard["id"])
    assert notes.page(conn, page["id"])["document"]["elements"]


def test_registered_attachment_rejects_path_escape_and_symlink(workspace, tmp_path):
    ref = notes.register(workspace.conn, workspace.settings.data_dir, image_data(), "Reference")[0]
    row = workspace.conn.execute("SELECT filepath FROM note_attachments WHERE id=?", (ref["id"],)).fetchone()
    from pathlib import Path
    path = Path(row["filepath"])
    target = tmp_path / "outside.png"
    path.replace(target)
    path.symlink_to(target)
    with pytest.raises(notes.NotesError) as failure:
        notes.attachment_bytes(workspace.conn, workspace.settings.data_dir, ref["id"])
    assert failure.value.status == 404


def test_api_errors_and_background_import_are_real(workspace, page):
    runner = TaskRunner()
    app = SimpleNamespace(settings=workspace.settings, tasks=runner, mutation_lock=threading.Lock())
    try:
        assert web_notes.get(app, workspace.conn, "/api/other", {}) is None
        assert web_notes.post(app, workspace.conn, "/api/other", {}) is None
        with pytest.raises(APIError) as invalid:
            web_notes.post(app, workspace.conn, f"/api/notes/pages/{page['id']}", {"revision": 1, "title": "Bad", "document": document(element(x=float("nan")))})
        assert invalid.value.status == 400
        started = web_notes.post(app, workspace.conn, "/api/notes/attachments", {"name": "Safe local reference", "data": image_data()})
        assert "task" in started
        runner.close()
        task = runner.snapshot(started["task"]["id"])
        assert task["status"] == "completed"
        assert len(task["result"]["attachments"]) == 1
        assert notes.attachment_bytes(workspace.conn, workspace.settings.data_dir, task["result"]["attachments"][0]["id"])
    finally:
        runner.close()


def test_folder_api_hierarchy_moves_survive_reload_without_changing_pages(workspace, page):
    from kitty.db import connect
    from contextlib import closing

    conn = workspace.conn
    app = SimpleNamespace(settings=workspace.settings)
    post = lambda path, body: web_notes.post(app, conn, path, body)
    root = post("/api/notes/folders", {"title": "Studies"})
    child = post("/api/notes/folders", {"title": "Biology", "parent_id": root["id"]})
    notebook = notes.listing(conn)["notebooks"][0]
    assert notebook["folder_id"] is None
    before = notes.page(conn, page["id"])
    body = {"revision": notebook["revision"], "title": notebook["title"], "subject": notebook["subject"], "project": notebook["project"]}
    moved = post(f"/api/notes/notebooks/{notebook['id']}", {**body, "folder_id": child["id"]})
    assert moved["folder_id"] == child["id"]
    # Legacy metadata-only callers must not silently move a notebook to root.
    saved = post(f"/api/notes/notebooks/{notebook['id']}", {**body, "revision": moved["revision"]})
    assert saved["folder_id"] == child["id"]
    renamed = post(f"/api/notes/folders/{child['id']}", {"revision": child["revision"], "title": "Life sciences", "parent_id": None})
    assert renamed["revision"] == child["revision"] + 1
    with closing(connect(workspace.settings.db_path)) as reloaded:
        listing = web_notes.get(app, reloaded, "/api/notes", {})
        folders = {folder["id"]: folder for folder in listing["folders"]}
        assert folders[child["id"]]["parent_id"] is None
        assert folders[child["id"]]["title"] == "Life sciences"
        assert listing["notebooks"][0]["folder_id"] == child["id"]
        assert notes.page(reloaded, page["id"]) == before
    returned = post(f"/api/notes/notebooks/{notebook['id']}", {**body, "revision": saved["revision"], "folder_id": None})
    assert returned["folder_id"] is None
    assert notes.page(conn, page["id"]) == before
    assert post(f"/api/notes/folders/{child['id']}/delete", {"revision": renamed["revision"], "confirm": True}) == {"ok": True}


def test_folder_api_rejects_cycles_missing_parents_nonempty_and_stale_edits(workspace):
    app = SimpleNamespace(settings=workspace.settings)
    conn = workspace.conn
    post = lambda path, body: web_notes.post(app, conn, path, body)
    root = post("/api/notes/folders", {"title": "Root"})
    child = post("/api/notes/folders", {"title": "Child", "parent_id": root["id"]})
    for parent in (root["id"], child["id"]):
        with pytest.raises(APIError) as cycle:
            post(f"/api/notes/folders/{root['id']}", {"revision": 1, "title": "Invalid", "parent_id": parent})
        assert cycle.value.status == 400
    for path, body in [
        ("/api/notes/folders", {"title": "Missing", "parent_id": 999999}),
        (f"/api/notes/folders/{child['id']}", {"revision": 1, "title": "Missing", "parent_id": 999999}),
        ("/api/notes/notebooks", {"title": "Missing", "folder_id": 999999}),
    ]:
        with pytest.raises(APIError) as missing:
            post(path, body)
        assert missing.value.status == 404
    with pytest.raises(APIError) as nonempty:
        post(f"/api/notes/folders/{root['id']}/delete", {"revision": 1, "confirm": True})
    assert nonempty.value.status == 409
    notebook = post("/api/notes/notebooks", {"title": "Preserved", "folder_id": child["id"]})
    with pytest.raises(APIError) as occupied:
        post(f"/api/notes/folders/{child['id']}/delete", {"revision": 1, "confirm": True})
    assert occupied.value.status == 409
    renamed = post(f"/api/notes/folders/{child['id']}", {"revision": 1, "title": "Renamed", "parent_id": root["id"]})
    for path, body in [
        (f"/api/notes/folders/{child['id']}", {"revision": 1, "title": "Stale", "parent_id": None}),
        (f"/api/notes/folders/{child['id']}/delete", {"revision": 1, "confirm": True}),
    ]:
        with pytest.raises(APIError) as stale:
            post(path, body)
        assert stale.value.status == 409
    metadata = {"title": notebook["title"], "subject": "", "project": ""}
    moved = post(f"/api/notes/notebooks/{notebook['id']}", {**metadata, "revision": 1, "folder_id": None})
    with pytest.raises(APIError) as stale_notebook:
        post(f"/api/notes/notebooks/{notebook['id']}", {**metadata, "revision": 1, "folder_id": root["id"]})
    assert stale_notebook.value.status == 409
    with pytest.raises(APIError) as missing_move:
        post(f"/api/notes/notebooks/{notebook['id']}", {**metadata, "revision": moved["revision"], "folder_id": 999999})
    assert missing_move.value.status == 404
    with pytest.raises(APIError) as unconfirmed:
        post(f"/api/notes/folders/{child['id']}/delete", {"revision": renamed["revision"], "confirm": False})
    assert unconfirmed.value.status == 400
    listing = notes.listing(conn)
    assert next(f for f in listing["folders"] if f["id"] == child["id"]) == renamed
    assert listing["notebooks"][0]["folder_id"] is None
    assert post(f"/api/notes/folders/{child['id']}/delete", {"revision": renamed["revision"], "confirm": True}) == {"ok": True}
    replacement = post("/api/notes/folders", {"title": "Replacement"})
    assert replacement["id"] != child["id"]


def test_additive_folder_migration_preserves_existing_notebook_and_ink():
    import sqlite3
    from kitty.db.migrations import available_migrations

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrations = {migration.version: migration for migration in available_migrations()}
    try:
        conn.executescript(migrations[14].sql)
        conn.execute("INSERT INTO note_notebooks(id,title,created_at,updated_at) VALUES (1,'Existing','before','before')")
        original = document(element())
        import json
        conn.execute("INSERT INTO note_pages(id,notebook_id,title,document,created_at,updated_at) VALUES (1,1,'Ink',?,'before','before')", (json.dumps(original),))
        conn.commit()
        before = notes.page(conn, 1)
        conn.executescript(migrations[19].sql)
        conn.executescript(migrations[22].sql)
        listing = notes.listing(conn)
        assert listing["folders"] == []
        assert listing["notebooks"][0]["folder_id"] is None
        assert listing["notebooks"][0]["title"] == "Existing"
        assert notes.page(conn, 1)["document"] == before["document"]
        assert notes.page(conn, 1)["revision"] == before["revision"]
        folder = notes.create_folder(conn, "Archive")
        moved = notes.save_notebook(conn, 1, 1, "Existing", "", "", folder["id"])
        assert moved["folder_id"] == folder["id"]
        assert notes.page(conn, 1)["document"] == before["document"]
    finally:
        conn.close()

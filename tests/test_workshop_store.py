"""Workshop draft isolation and optimistic concurrency: source revisions are immutable."""
import pytest
from kitty.core.workshop import store

def test_save_conflict_preserves_user_source(workspace):
    c = workspace.conn
    d = store.create_doc(c, title="Synthetic resume", format="html", source="<p>Draft one</p>")
    saved = store.save_source(c, d['id'], source="<p>Draft two</p>", base_revision=1)
    assert saved['revision'] == 2
    with pytest.raises(store.WorkshopError) as caught:
        store.save_source(c, d['id'], source="<p>Stale edit</p>", base_revision=1)
    assert caught.value.status == 409
    assert store.get_doc(c, d['id'])['source'] == "<p>Draft two</p>"
    restored = store.restore_revision(c, d['id'], revision=1, base_revision=2)
    assert restored['source'] == "<p>Draft one</p>"
    assert restored['revision'] == 3

def test_copy_retains_master_snapshot(workspace):
    c = workspace.conn
    master = store.create_doc(c, title="Systems", format="typst", source="= Synthetic candidate")
    variant = store.copy_doc(c, master['id'], title="Systems at Example", company="Example", role="Engineer")
    store.save_source(c, master['id'], source="= Updated master", base_revision=1)
    assert store.get_doc(c, variant['id'])['source'] == "= Synthetic candidate"
    assert variant['base_revision'] == 1
    assert variant['kind'] == 'tailored'

def test_snapshot_assets_title_and_noop(workspace):
    import base64
    c = workspace.conn
    assets = {"styles/base.css": base64.b64encode(b"p{color:black}").decode()}
    doc = store.create_doc(c, title="Original", format="html", source="<p>One</p>", assets=assets)
    assert store.save_source(c, doc["id"], source=doc["source"], base_revision=1)["revision"] == 1
    changed = store.save_source(c, doc["id"], source="<p>Two</p>", base_revision=1,
                                title="Changed", assets={})
    assert changed["revision"] == 2
    old = store.get_revision(c, doc["id"], 1)
    assert old["assets"] == assets and old["title"] == "Original"
    restored = store.restore_revision(c, doc["id"], revision=1, base_revision=2)
    assert restored["assets"] == assets and restored["title"] == "Original"
    assert [r["revision"] for r in store.revisions(c, doc["id"])] == [3, 2, 1]

@pytest.mark.parametrize("path", ["../escape", "/absolute", "dir/../../escape", "C:/windows",
                                   "back\\slash", "a//b", "a/./b"])
def test_asset_paths_cannot_escape(workspace, path):
    with pytest.raises(store.WorkshopError):
        store.create_doc(workspace.conn, title="Unsafe", format="typst", source="Hi",
                         assets={path: "eA=="})

def test_boolean_revision_rejected(workspace):
    d = store.create_doc(workspace.conn, title="Test", format="html", source="Hi")
    with pytest.raises(store.WorkshopError):
        store.save_source(workspace.conn, d["id"], source="Oops", base_revision=True)

def test_archive_keeps_history(workspace):
    d = store.create_doc(workspace.conn, title="Test", format="html", source="Hi")
    assert store.archive_doc(workspace.conn, d["id"])["archived"]
    assert store.get_revision(workspace.conn, d["id"], 1)["source"] == "Hi"
    with pytest.raises(store.WorkshopError):
        store.save_source(workspace.conn, d["id"], source="Oops", base_revision=1)

def test_tailored_snapshot_pins_old_master(workspace):
    c = workspace.conn
    master = store.create_doc(c, title="Master", format="html", source="Old")
    store.save_source(c, master["id"], source="New", base_revision=1)
    child = store.create_doc(c, title="Child", format="html", source="Old", kind="tailored",
                             base_doc_id=master["id"], base_revision=1)
    assert child["base_revision"] == 1
    with pytest.raises(store.WorkshopError):
        store.create_doc(c, title="Mismatch", format="html", source="Wrong", kind="tailored",
                         base_doc_id=master["id"], base_revision=1)

def test_source_import_retains_assets_and_original_zip(workspace):
    import base64
    import io
    import zipfile
    from kitty.core.workshop.imports import import_files
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("resume.typ", "= Synthetic Person")
        archive.writestr("styles/theme.typ", "#let accent = blue")
    result = import_files(workspace.conn, workspace.settings,
                          [{"name": "project.zip", "data": base64.b64encode(stream.getvalue()).decode()}])
    doc = result["documents"][0]
    assert doc["format"] == "typst"
    assert doc["source"] == "= Synthetic Person"
    assert "styles/theme.typ" in doc["assets"]
    assert base64.b64decode(doc["assets"]["originals/project.zip"]) == stream.getvalue()

def test_archive_traversal_and_bombs_rejected(workspace):
    import base64
    import io
    import zipfile
    from kitty.core.workshop.imports import import_files
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../resume.typ", "Danger")
    with pytest.raises(store.WorkshopError):
        import_files(workspace.conn, workspace.settings,
                     [{"name": "bad.zip", "data": base64.b64encode(stream.getvalue()).decode()}])

def test_markdown_import_is_escaped_and_original_retained(workspace):
    import base64
    from kitty.core.workshop.imports import import_files
    raw = b"# Synthetic Person\n\n<script>alert(1)</script>"
    result = import_files(workspace.conn, workspace.settings,
                          [{"name": "resume.md", "data": base64.b64encode(raw).decode()}])
    doc = result["documents"][0]
    assert "<script>" not in doc["source"]
    assert "&lt;script&gt;" in doc["source"]
    assert base64.b64decode(doc["assets"]["originals/resume.md"]) == raw


def test_json_resume_import_preserves_all_sections(workspace):
    import base64
    import json
    from kitty.core.workshop.imports import import_files
    raw = json.dumps({"basics": {"name": "Synthetic"}, "projects": [
        {"name": "Parser", "highlights": ["Reduced manual work"]}],
        "volunteer": [{"organization": "Example"}]}).encode()
    result = import_files(workspace.conn, workspace.settings,
                          [{"name": "resume.json", "data": base64.b64encode(raw).decode()}])
    source = result["documents"][0]["source"]
    assert all(value in source for value in ("Synthetic", "Parser", "Reduced manual work", "Example"))


def test_zip_symlink_and_duplicate_members_rejected(workspace):
    import base64
    import io
    import zipfile
    from kitty.core.workshop.imports import import_files
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        link = zipfile.ZipInfo("resume.typ")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(store.WorkshopError):
        import_files(workspace.conn, workspace.settings,
                     [{"name": "bad.zip", "data": base64.b64encode(stream.getvalue()).decode()}])


def test_multi_import_is_atomic(workspace):
    import base64
    from kitty.core.workshop.imports import import_files
    with pytest.raises(store.WorkshopError):
        import_files(workspace.conn, workspace.settings, [
            {"name": "one.html", "data": base64.b64encode(b"One").decode()},
            {"name": "two.json", "data": base64.b64encode(b"not json").decode()}])
    assert store.list_docs(workspace.conn) == []

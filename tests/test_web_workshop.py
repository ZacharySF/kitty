"""Workshop handlers preserve strict consent, revisions, and source downloads."""
from types import SimpleNamespace
import pytest
from kitty import web_workshop
from kitty.web_server import APIError


def app_for(workspace):
    return SimpleNamespace(settings=workspace.settings)


def test_create_save_restore_and_download(workspace):
    app, conn = app_for(workspace), workspace.conn
    doc = web_workshop.post(app, conn, "/api/workshop", {
        "title": "Synthetic", "format": "html", "source": "<p>Original</p>"})["document"]
    path = "/api/workshop/" + doc["id"]
    changed = web_workshop.post(app, conn, path, {"source": "New", "base_revision": 1})["document"]
    assert changed["revision"] == 2
    with pytest.raises(APIError) as error:
        web_workshop.post(app, conn, path, {"source": "Conflict", "base_revision": 1})
    assert error.value.status == 409
    restored = web_workshop.post(app, conn, path + "/restore", {"revision": 1, "base_revision": 2})["document"]
    assert restored["source"] == "<p>Original</p>"
    payload, mime, filename = web_workshop.download(app, conn, path + "/source", {"revision": "1"})
    assert payload.startswith(b"PK") and mime == "application/zip" and filename.endswith(".zip")


def test_strict_body_and_archive_confirmation(workspace):
    app, conn = app_for(workspace), workspace.conn
    with pytest.raises(APIError):
        web_workshop.post(app, conn, "/api/workshop", {"title": "Test", "format": "html", "source": "Hi", "path": "/etc/passwd"})
    doc = web_workshop.post(app, conn, "/api/workshop", {"title": "Test", "format": "html", "source": "Hi"})["document"]
    with pytest.raises(APIError):
        web_workshop.post(app, conn, "/api/workshop/" + doc["id"] + "/archive", {"confirm": 1})


def test_template_creation(workspace):
    result = web_workshop.post(app_for(workspace), workspace.conn, "/api/workshop", {
        "title": "Test", "format": "typst", "template_id": "typst-clean"})
    assert result["document"]["source"].strip()
    assert result["document"]["format"] == "typst"

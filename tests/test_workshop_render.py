"""Offline compile boundaries and immutable export regression tests.

Compiles, the sandbox and PDF/DOCX reading run in the Rust ``kitty-workshop`` engine
(workshop-engine/, whose own unit tests cover the sandbox rules). These tests drive the real
binary through the Python boundary; build it first with
``cargo build --release --manifest-path workshop-engine/Cargo.toml``.
"""
import base64
import os
import sys
from pathlib import Path

import pytest
from kitty.core.workshop import render, store

ENGINE = Path(__file__).resolve().parents[1] / "workshop-engine" / "target" / "release" / "kitty-workshop"
STAND_IN = os.path.realpath(sys.executable)  # any absolute executable file


@pytest.fixture(autouse=True)
def engine(monkeypatch):
    assert ENGINE.is_file(), "Build the workshop engine: cargo build --release --manifest-path workshop-engine/Cargo.toml"
    monkeypatch.setenv("KITTY_WORKSHOP", str(ENGINE))
    for name in ("BWRAP", "TYPST", "TECTONIC", "PDFLATEX", "LUALATEX", "CHROMIUM", "TESSERACT", "TECTONIC_BUNDLE"):
        monkeypatch.delenv("KITTY_" + name, raising=False)


def test_compile_fails_closed_without_sandbox(workspace, tmp_path, monkeypatch):
    # A configured but unusable bwrap never falls back to one found on PATH.
    monkeypatch.setenv("KITTY_BWRAP", str(tmp_path / "missing-bwrap"))
    monkeypatch.setenv("KITTY_TYPST", STAND_IN)
    doc = store.create_doc(workspace.conn, title="Synthetic", format="typst", source="Hello")
    result = render.compile_doc(workspace.settings, doc)
    assert result["ok"] is False
    assert result["revision"] == 1 and result["pdf_hash"] is None
    assert any("sandbox" in message.lower() for message in result["diagnostics"])


def test_missing_engine_makes_every_compiler_unavailable(workspace, tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_WORKSHOP", str(tmp_path / "kitty-workshop"))
    capabilities = render.capabilities(workspace.settings)
    assert capabilities["engines"] and not any(item["available"] for item in capabilities["engines"])
    assert all("workshop engine" in item["reason"].lower() for item in capabilities["engines"])
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="Hi")
    result = render.compile_doc(workspace.settings, doc)
    assert result["ok"] is False and "workshop engine" in result["diagnostics"][0].lower()


def test_source_archive_is_exact_snapshot(workspace):
    import io
    import zipfile
    from kitty.core.workshop.imports import source_zip
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="<p>Original</p>",
                           assets={"base.css": base64.b64encode(b"p {color:black}").decode()})
    store.save_source(workspace.conn, doc["id"], source="Changed", base_revision=1)
    with zipfile.ZipFile(io.BytesIO(source_zip(store.get_revision(workspace.conn, doc["id"], 1)))) as zipped:
        assert zipped.read("resume.html") == b"<p>Original</p>"
        assert zipped.read("base.css") == b"p {color:black}"


def test_export_rejects_boolean_revision(workspace):
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="Hi")
    with pytest.raises(store.WorkshopError):
        render.export_resume(workspace.conn, workspace.settings, doc["id"], revision=True, label="test")


def test_compile_key_accounts_for_revision_assets_engine_and_runtime(workspace, monkeypatch):
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="Hi")
    key = render.cache_key(doc)
    assert key == render.cache_key(dict(doc))
    assert key != render.cache_key({**doc, "revision": 2})
    assert key != render.cache_key({**doc, "assets": {"x.css": "eA=="}})
    # A different Chromium install changes what a preview looks like.
    monkeypatch.setenv("KITTY_CHROMIUM", STAND_IN)
    assert key != render.cache_key(doc)


def test_requested_revision_must_match_snapshot(workspace):
    doc = store.create_doc(workspace.conn, title="Test", format="html", source="One")
    with pytest.raises(store.WorkshopError):
        render.compiled_pdf(workspace.settings, doc, revision=2)


def test_capabilities_explain_missing_offline_bundle(workspace, monkeypatch):
    monkeypatch.setenv("KITTY_BWRAP", STAND_IN)
    monkeypatch.setenv("KITTY_TECTONIC", STAND_IN)
    engines = {item["id"]: item for item in render.capabilities(workspace.settings)["engines"]}
    assert not engines["tectonic"]["available"]
    assert "bundle" in engines["tectonic"]["reason"].lower()


def test_export_requires_successful_compile(workspace):
    doc = store.create_doc(workspace.conn, title="Test", format="html", source="One")
    with pytest.raises(store.WorkshopError):
        render.export_resume(workspace.conn, workspace.settings, doc["id"], revision=1, label="One")
    assert workspace.conn.execute("SELECT count(*) FROM workshop_exports").fetchone()[0] == 0

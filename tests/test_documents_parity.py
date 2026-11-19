"""The Documents routes answered by rika-server (server/src/documents) against the retained
`kitty.core.documents` on two copies of one seeded workspace.

The reference owns the deleted handlers' glue (`web_documents.get/post/download`). A file
clock and deterministic UUIDs make the answers, the chunk blocks, `op_log` (Loro bytes
included) and `record_events` comparable without masking anything. Imports, extraction
previews, document-to-note and the PDF export stay on the worker (pymupdf, tasks).
"""
from __future__ import annotations

import base64
import http.client
import json
import re
import shutil
from contextlib import closing
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from terminal_support import RIKA_SERVER, front_site
from test_blocks_parity import Clock, assert_stored_equal, base_time, deterministic_uuids, stored_rows

from kitty import web_auth
from kitty.core import block_api, documents, notes, record_events
from kitty.core.blocks import crdt, store
from kitty.db import connect
from kitty.modules import MODULE_IDS, ModuleDisabledError
from kitty.web_server import APIError, WebApplication

ID = r"[1-9][0-9]{0,15}"
MISSING_ORIGINAL = "0" * 32


def upload(name, data, path=None):
    return {"name": name, "data": base64.b64encode(data).decode(), **({"path": path} if path else {})}


class Reference:
    """`web_documents` as it was, over the retained core."""

    def __init__(self, db_path, data_dir, modules=MODULE_IDS):
        self.conn, self.data_dir, self.modules = connect(db_path), data_dir, modules

    def get(self, path, query):
        conn = self.conn
        if path == "/api/documents":
            notes.keys(query, set())
            return documents.listing(conn)
        if path == "/api/documents/originals":
            notes.keys(query, {"page_id", "document_id"})
            if len(query) != 1:
                raise notes.NotesError("Choose one page_id or document_id")
            key, value = next(iter(query.items()))
            if not re.fullmatch(ID, value):
                raise notes.NotesError("Invalid original destination id")
            return {"originals": documents.originals(conn, **{key: int(value)})}
        match = re.fullmatch(rf"/api/documents/({ID})", path)
        if match:
            notes.keys(query, set())
            return documents.document(conn, int(match[1]))
        raise APIError(404, "API route not found")

    def download(self, path, query):
        match = re.fullmatch(r"/api/documents/originals/([a-f0-9]{32})", path)
        assert match, path
        notes.keys(query, set())
        return documents.original(self.conn, self.data_dir, match[1])

    def post(self, path, body):
        conn = self.conn
        if path == "/api/documents":
            return documents.save(conn, body)
        if path == "/api/documents/topics":
            return documents.topics(conn, body)
        match = re.fullmatch(rf"/api/documents/({ID})", path)
        if match:
            return documents.save(conn, body, int(match[1]))
        raise APIError(404, "API route not found")

    def __call__(self, path, body, now, query, download=False):
        Clock.value = now
        try:
            if body is not None and query:
                raise APIError(400, "This route does not accept query parameters")
            if "workspace" not in self.modules:
                raise APIError(403, str(ModuleDisabledError("workspace")))
            if download:
                data, kind, name = self.download(path, query)
                return 200, {"data": data.hex(), "type": kind, "disposition": f'attachment; filename="{name}"'}
            if body is None:
                answer = self.get(path, query)
            else:
                before = record_events.op_log_mark(self.conn)
                answer = self.post(path, body)
                record_events.capture(self.conn, path, body, answer, op_log_before=before)
        except (notes.NotesError, APIError) as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            return exc.status, {"error": str(exc)}
        return 200, json.loads(json.dumps(answer))


class Through:
    def __init__(self, site, clock):
        self.site, self.clock = site, clock

    def __call__(self, path, body, now, query, download=False):
        self.clock.write_text(now.isoformat(), encoding="utf-8")
        target = path + ("?" + urlencode(query) if query else "")
        if not download:
            return self.site.request(target, body)
        client = http.client.HTTPConnection("127.0.0.1", self.site.port, timeout=10)
        try:
            client.request("GET", target, headers={"Origin": self.site.server.origin, web_auth.HEADER: self.site.secret})
            response = client.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                return response.status, json.loads(data)
            return response.status, {"data": data.hex(), "type": response.getheader("Content-Type"),
                                     "disposition": response.getheader("Content-Disposition")}
        finally:
            client.close()


def seed(workspace, base, monkeypatch):
    conn = workspace.conn
    with monkeypatch.context() as patch:
        patch.setattr(store, "now", Clock.now)
        patch.setattr(notes, "now", Clock.now)
        Clock.value = base - timedelta(hours=2)
        folder = notes.create_folder(conn, "Biology")
        child = notes.create_folder(conn, "Cells", folder["id"])
        book = notes.create_notebook(conn, "Mitochondria", folder_id=child["id"])
        other = notes.create_notebook(conn, "Chemistry")
        pages = []
        for index, (notebook, title, lines) in enumerate([
            (book["id"], "Energy", ["Mitochondria produce ATP through oxidative phosphorylation.", "Cristae fold the inner membrane."]),
            (book["id"], "Membranes", ["Membrane proteins and lipid bilayers; see https://example.com/membranes for proteins."]),
            (other["id"], "Catalysts", ["Enzymes are biological catalysts lowering activation energy.", "ATP synthase is an enzyme."]),
            (other["id"], "Unrelated", ["Quasar spectra and galaxies."]),
        ]):
            Clock.value = base - timedelta(hours=1, minutes=index)
            pages.append(block_api.create_page(conn, title, notebook=notebook, blocks=lines))
        # A canvas page: element texts (ink strokes count as empty lines) before the outline.
        Clock.value = base - timedelta(minutes=30)
        drawn = notes.create_page(conn, other["id"], "Drawn enzymes")
        canvas = notes.empty_document()
        canvas["elements"] = [
            {"id": "ink1", "kind": "pen", "layer": "default", "x": 0, "y": 0, "w": 1, "h": 1, "color": "#000000", "width": 1,
             "points": [[0, 0, 0.5], [1, 1, 0.5]]},
            {"id": "box1", "kind": "text", "layer": "default", "x": 0, "y": 0, "w": 10, "h": 10, "color": "#000000", "width": 1,
             "text": "Enzymes bind substrates at the active site."},
        ]
        notes.save_page(conn, drawn["id"], drawn["revision"], canvas, "Drawn enzymes")
        Clock.value = base - timedelta(minutes=20)
        saved = documents.save(conn, {"title": "Lab report", "content": "# Results\n\nEnzymes and ATP.\n", "format": "markdown",
                                      "notebook_id": book["id"]})
        plain = documents.save(conn, {"title": "Plain notes", "content": "line one\r\nline two\n\n\n", "format": "text", "notebook_id": None})
        imported = documents.import_notes(conn, workspace.settings.data_dir, {
            "document_id": saved["id"], "revision": saved["revision"],
            "files": [upload("source.md", b"# Imported source\n\nCristae and ATP.\n")]})
        paged = documents.import_notes(conn, workspace.settings.data_dir, {
            "notebook_id": other["id"], "files": [upload("loose.txt", b"Loose enzyme facts")]})
        # A registered original whose file no longer matches its row.
        broken = documents.import_notes(conn, workspace.settings.data_dir, {
            "notebook_id": other["id"], "files": [upload("broken.md", b"Broken original")]})
        broken_id = broken["pages"][0]["original_id"]
        (documents.original_root(workspace.settings.data_dir) / broken_id).write_bytes(b"tampered, longer bytes")
        conn.commit()
    return {"folder": folder["id"], "child": child["id"], "book": book["id"], "other": other["id"],
            "pages": [page["number"] for page in pages], "drawn": drawn["id"],
            "doc": imported["document"]["id"], "plain": plain["id"], "original": imported["originals"][0]["id"],
            "paged": paged["pages"][0]["page_id"], "page_original": paged["pages"][0]["original_id"], "broken": broken_id}


MARKDOWN = ("# Title 😀\n\nFirst paragraph\nwith two lines.\n\n```python\nprint(1)\n\n\nstill code\n```\n\n"
            "> quoted\n\n- item one\n- item two\n\n---\n\n1. numbered\n\nTrailing \u2028separator\x0bvertical\r\n\r\nCRLF end\r\n")


def scenario(call, seeded, base, modules=MODULE_IDS):
    answers = []
    allowed = "workspace" in modules
    now = base

    def do(path, body=None, expect=200, download=False, **query):
        nonlocal now
        now += timedelta(seconds=1)
        status, answer = call(path, body, now, query, download)
        if not allowed and not (body is not None and query):
            expect = 403
        assert status == expect, (len(answers), path, body, query, status, answer)
        answers.append((path, status, answer))
        return answer

    doc, plain, book, other = seeded["doc"], seeded["plain"], seeded["book"], seeded["other"]
    # Reads.
    do("/api/documents")
    do("/api/documents", expect=400, x="1")
    do(f"/api/documents/{doc}")
    do(f"/api/documents/{plain}")
    do("/api/documents/999", expect=404)
    do(f"/api/documents/{doc}", expect=400, revision="1")
    do("/api/documents/originals", expect=400)
    do("/api/documents/originals", expect=400, page_id="1", document_id="1")
    do("/api/documents/originals", expect=400, other="1")
    for value in ("abc", "0", "01", "1" * 17, ""):
        do("/api/documents/originals", expect=400, document_id=value)
    do("/api/documents/originals", document_id=str(doc))
    do("/api/documents/originals", page_id=str(seeded["paged"]))
    do("/api/documents/originals", page_id="999")
    # Original downloads.
    do(f"/api/documents/originals/{seeded['original']}", download=True)
    do(f"/api/documents/originals/{seeded['page_original']}", download=True)
    do(f"/api/documents/originals/{seeded['original']}", expect=400, download=True, x="1")
    do(f"/api/documents/originals/{MISSING_ORIGINAL}", expect=404, download=True)
    do(f"/api/documents/originals/{seeded['broken']}", expect=404, download=True)

    # Creation and its refusals.
    good = {"title": "Fresh", "content": "Body", "format": "text", "notebook_id": None}
    do("/api/documents", {}, 400)
    do("/api/documents", {**good, "extra": 1}, 400)
    do("/api/documents", {**good, "revision": 1}, 200)
    do("/api/documents", {**good, "title": 5}, 400)
    do("/api/documents", {**good, "title": "x\x00"}, 400)
    do("/api/documents", {**good, "title": "é" * 101}, 400)
    do("/api/documents", {**good, "title": "  \t"}, 400)
    do("/api/documents", {**good, "format": "html"}, 400)
    do("/api/documents", {**good, "format": ["text"]}, 400)
    do("/api/documents", {**good, "content": None}, 400)
    do("/api/documents", {**good, "content": "x" * (512 * 1024 + 1)}, 400)
    do("/api/documents", {**good, "notebook_id": 999}, 404)
    do("/api/documents", {**good, "notebook_id": "1"}, 400)
    do("/api/documents", {**good, "notebook_id": True}, 400)
    do("/api/documents", {**good, "notebook_id": 0}, 400)
    do("/api/documents", good, 400, x="1")
    created = do("/api/documents", {"title": "  Markdown 😀  ", "content": MARKDOWN, "format": "markdown", "notebook_id": book})
    empty = do("/api/documents", {**good, "title": "Empty", "content": ""})
    blank = do("/api/documents", {**good, "title": "Blank", "content": "\n\n  \n"})
    if allowed:
        number = created["id"]
        do(f"/api/documents/{number}")
        # Saves: stale, missing, bad revisions, then edits that keep, move, retype and drop chunks.
        edit = {"title": "Markdown 😀", "content": MARKDOWN, "format": "markdown", "notebook_id": book, "revision": 1}
        do(f"/api/documents/{number}", {k: v for k, v in edit.items() if k != "revision"}, 400)
        do(f"/api/documents/{number}", {**edit, "revision": 2}, 409)
        do(f"/api/documents/{number}", {**edit, "revision": "1"}, 400)
        do(f"/api/documents/{number}", {**edit, "revision": True}, 400)
        do("/api/documents/999", edit, 404)
        do("/api/documents/999", {**edit, "notebook_id": 999}, 404)
        do(f"/api/documents/{number}", {**edit, "title": ""}, 400)
        current = do(f"/api/documents/{number}", edit)
        reordered = "\n\n".join(reversed(MARKDOWN.split("\n\n")))
        current = do(f"/api/documents/{number}", {**edit, "content": reordered, "revision": current["revision"]})
        retyped = "# First paragraph\nwith two lines.\n\nNew middle\n\n- item one\n- item two\n\nTail added"
        current = do(f"/api/documents/{number}", {**edit, "title": "Renamed", "content": retyped, "revision": current["revision"]})
        current = do(f"/api/documents/{number}", {**edit, "content": retyped, "format": "text", "notebook_id": None,
                                                  "revision": current["revision"]})
        current = do(f"/api/documents/{number}", {**edit, "content": "", "notebook_id": other, "revision": current["revision"]})
        current = do(f"/api/documents/{number}", {**edit, "content": "Back again\n\n# Heading", "revision": current["revision"]})
        do(f"/api/documents/{number}", {**edit, "revision": current["revision"]}, 400, x="1")
        do(f"/api/documents/{empty['id']}", {**good, "content": "Now filled", "revision": empty["revision"]})
        do(f"/api/documents/{blank['id']}", {**good, "content": "", "revision": blank["revision"]})
        saved = do(f"/api/documents/{doc}")
        do(f"/api/documents/{doc}", {"title": saved["title"], "content": saved["content"] + "\n\nAppended", "format": "markdown",
                                     "notebook_id": saved["notebook_id"], "revision": saved["revision"]})
    do("/api/documents")

    # Topics.
    text = "Mitochondria make ATP. ATP synthase is an enzyme; enzymes catalyse. Cristae, cristae! the the and https://secret.example/path"
    do("/api/documents/topics", {}, 400)
    do("/api/documents/topics", {"text": 3}, 400)
    do("/api/documents/topics", {"text": text, "extra": 1}, 400)
    do("/api/documents/topics", {"text": text})
    do("/api/documents/topics", {"text": text, "scope": "document"})
    do("/api/documents/topics", {"text": text, "scope": "document", "notebook_id": 999})
    do("/api/documents/topics", {"text": text, "notebook_id": book})
    do("/api/documents/topics", {"text": text, "notebook_id": other, "folder_id": seeded["folder"]})
    do("/api/documents/topics", {"text": text, "folder_id": seeded["folder"]})
    do("/api/documents/topics", {"text": text, "folder_id": seeded["child"], "exclude_page_id": seeded["pages"][0]})
    do("/api/documents/topics", {"text": text, "exclude_page_id": seeded["drawn"]})
    do("/api/documents/topics", {"text": text, "notebook_id": 999}, 404)
    do("/api/documents/topics", {"text": text, "folder_id": 999}, 404)
    do("/api/documents/topics", {"text": text, "folder_id": "1"}, 400)
    do("/api/documents/topics", {"text": ""})
    do("/api/documents/topics", {"text": "Ünïcödé ÇAFÉ café Straße STRASSE 𐐀𐐀𐐀 İstanbul " * 3})
    do("/api/documents/topics", {"text": " ".join(f"word{i} " * (i % 5 + 1) for i in range(40))})
    do("/api/documents/topics", {"text": text}, 400, x="1")
    return answers


def prepare(workspace, tmp_path, monkeypatch, modules):
    base = base_time()
    seeded = seed(workspace, base, monkeypatch)
    if modules != MODULE_IDS:
        (workspace.settings.root / "config" / "build.json").write_text(
            json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    reference_path = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, reference_path)
    return base, seeded, reference_path


def reference_run(workspace, reference_path, seeded, base, monkeypatch, modules):
    with monkeypatch.context() as patch:
        deterministic_uuids(patch)
        reference = Reference(reference_path, workspace.settings.data_dir, modules)
        try:
            answers = scenario(reference, seeded, base, modules)
        finally:
            reference.conn.close()
            crdt._CACHE.clear()
    return answers


@pytest.mark.parametrize("modules", [MODULE_IDS, ("applyai",)])
def test_reference_scenario(workspace, tmp_path, monkeypatch, modules, record_property):
    """Executable oracle sanity check, usable before the Rust implementation exists."""
    base, seeded, path = prepare(workspace, tmp_path, monkeypatch, modules)
    answers = reference_run(workspace, path, seeded, base, monkeypatch, modules)
    record_property("requests", len(answers))
    if "workspace" in modules:
        assert len(answers) > 70
        assert stored_rows(path)["record_events"] == []
    else:
        assert {status for _, status, _ in answers} == {400, 403}


@pytest.mark.parametrize("modules", [MODULE_IDS, ("applyai",)])
def test_the_front_answers_documents_as_the_python_core_does(workspace, tmp_path, monkeypatch, modules):
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER})")
    base, seeded, reference_path = prepare(workspace, tmp_path, monkeypatch, modules)
    expected = reference_run(workspace, reference_path, seeded, base, monkeypatch, modules)
    clock, counter = tmp_path / "clock", tmp_path / "uuids"
    clock.write_text(base.isoformat(), encoding="utf-8")
    counter.write_text("0", encoding="utf-8")
    with front_site(workspace.settings, env={"KITTY_TEST_CLOCK": str(clock), "KITTY_TEST_UUIDS": str(counter)}) as site:
        actual = scenario(Through(site, clock), seeded, base, modules)
    assert len(actual) == len(expected)
    for index, (answer, wanted) in enumerate(zip(actual, expected, strict=True)):
        assert answer == wanted, (index, answer[0])
    assert_stored_equal(stored_rows(workspace.settings.db_path), stored_rows(reference_path))


def test_the_worker_answers_only_the_python_documents_routes(workspace, tmp_path):
    saved = documents.save(workspace.conn, {"title": "Forwarded", "content": "Still Python", "format": "text", "notebook_id": None})
    conn = workspace.conn
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for path in ("/api/documents", f"/api/documents/{saved['id']}", "/api/documents/originals"):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, path, {})
        for path in ("/api/documents", f"/api/documents/{saved['id']}", "/api/documents/topics"):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, {})
    finally:
        app.close()
    conn.close()
    with front_site(workspace.settings) as site:
        status, data = site.request(f"/api/documents/{saved['id']}/pdf")
        assert status == 200 and data.startswith(b"%PDF-")
        status, prepared = site.request("/api/documents/prepare", {"file": upload("a.md", b"# Prepared")})
        assert status == 200 and "task" in prepared

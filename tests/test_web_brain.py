"""GET /api/brain: every record and every edit as one node of a point cloud."""
from __future__ import annotations

import dataclasses
import json
import time
import uuid

import pytest

from kitty import web_brain
from kitty.core import blocks, flashcards
from kitty.web_server import APIError, WebApplication

NODE_KEYS = {"id", "kind", "section", "type", "label", "at", "parent"}


@pytest.fixture
def app(workspace, tmp_path):
    application = WebApplication(workspace.settings, workspace.config, tmp_path)
    yield application
    application.close()


def brain(app, conn, **query):
    return app.get_json(conn, "/api/brain", {key: str(value) for key, value in query.items()})


def by_id(result):
    return {node["id"]: node for node in result["nodes"]}


def assert_contract(result):
    assert set(result) == {"generated_at", "sections", "nodes", "total", "truncated"}
    assert result["generated_at"].endswith(("Z", "+00:00"))
    ids = [node["id"] for node in result["nodes"]]
    assert len(ids) == len(set(ids))
    known = set(ids)
    sections = {section["id"] for section in result["sections"]}
    for node in result["nodes"]:
        assert set(node) == NODE_KEYS
        assert node["kind"] in {"record", "edit"}
        assert node["section"] in sections
        assert isinstance(node["label"], str) and 0 < len(node["label"]) <= 80
        assert node["parent"] is None or node["parent"] in known, node
        if node["kind"] == "edit":
            assert node["at"], node
    for section in result["sections"]:
        assert set(section) == {"id", "label", "group", "records", "edits"}
        assert section["group"] in {"Plan", "Study", "Job search"}


def seed(app, conn):
    notebook = app.mutate(conn, "/api/notes/notebooks", {"title": "Physics"})
    page = app.mutate(conn, "/api/notes/pages", {"notebook_id": notebook["id"], "title": "Waves"})
    app.mutate(conn, f"/api/notes/pages/{page['id']}", {
        "revision": page["revision"], "title": "Standing waves", "document": page["document"]})
    note = app.mutate(conn, "/api/quick-notes/create", {"body": "Private scratch body"})
    event = app.mutate(conn, "/api/calendar/events", {
        "title": "Lab session", "start": "2030-01-02T09:00:00Z", "end": "2030-01-02T10:00:00Z",
        "timezone": "UTC", "all_day": False})
    subject = app.mutate(conn, "/api/study/subjects", {"name": "Chemistry"})["item"]
    job = app.mutate(conn, "/api/jobs/add", {"company": "Acme", "title": "Intern",
                                             "url": "https://example.invalid/acme"})
    return {"notebook": notebook, "page": page, "note": note, "event": event, "subject": subject, "job": job}


def test_records_and_captured_edits_form_a_valid_tree(app, workspace):
    conn = workspace.conn
    made = seed(app, conn)
    result = brain(app, conn)
    assert_contract(result)
    nodes = by_id(result)
    page = nodes[f"notes.page:{made['page']['id']}"]
    assert page["kind"] == "record" and page["label"] == "Standing waves"
    assert page["parent"] == f"notes.notebook:{made['notebook']['id']}"
    note_id = f"quick-notes.note:{made['note']['id']}"
    event_id = f"calendar.event:{made['event']['id']}"
    assert nodes[note_id]["kind"] == "record" and nodes[event_id]["label"] == "Lab session"
    edits = [node for node in result["nodes"] if node["kind"] == "edit"]
    # Block writes are counted once, from op_log, labelled by what they did to the page's root.
    assert {(edit["type"], edit["label"]) for edit in edits if edit["parent"] == note_id} == {("create", "Created quick note")}
    assert [edit["label"] for edit in edits if edit["parent"] == event_id] == ["Created event · Lab session"]
    assert [edit["type"] for edit in edits if edit["parent"] == page["id"]] == ["save", "create"]
    assert not conn.execute("SELECT COUNT(*) FROM record_events WHERE entity IN ('page', 'note')").fetchone()[0]
    assert nodes[f"study.subject:{made['subject']['id']}"]["section"] == "study"
    assert nodes[f"jobs.job:{made['job']['id']}"]["label"] == "Acme — Intern"
    assert "Private scratch body" not in json.dumps(result)
    assert "example.invalid" not in json.dumps(result)
    sections = {section["id"]: section for section in result["sections"]}
    # The notebook, the page and the empty first block a new page starts with.
    assert sections["notes"] == {"id": "notes", "label": "Notes", "group": "Study", "records": 3, "edits": 3}
    assert sections["calendar"]["group"] == "Plan" and sections["jobs"]["group"] == "Job search"
    assert result["total"] == {"records": sum(s["records"] for s in result["sections"]),
                               "edits": len(edits)}
    assert result["truncated"] is False


def test_edits_are_newest_first_and_deleted_records_read_as_deleted(app, workspace):
    conn = workspace.conn
    note = app.mutate(conn, "/api/quick-notes/create", {"body": "gone soon"})
    app.mutate(conn, "/api/quick-notes/delete", {"id": note["id"], "revision": note["revision"]})
    result = brain(app, conn)
    assert_contract(result)
    edits = [node for node in result["nodes"] if node["kind"] == "edit"]
    assert [(edit["label"], edit["parent"]) for edit in edits] == [
        ("Deleted quick note", None), ("Created quick note", None)]
    first = conn.execute("SELECT MIN(seq) FROM op_log WHERE seq > (SELECT last_seq FROM op_log_migrated)").fetchone()[0]
    assert [edit["id"] for edit in edits] == [f"edit:op_log:{first + 1}", f"edit:op_log:{first}"]


def test_historical_logs_become_edits(app, workspace):
    conn = workspace.conn
    state = flashcards.overview(conn)
    basic = next(item for item in state["types"] if item["kind"] == "basic")
    note = flashcards.save_note(conn, {"deck_id": state["decks"][0]["id"], "type_id": basic["id"],
                                       "fields": {"Front": "front-secret", "Back": "back-secret"}, "tags": []})
    card = flashcards.start_review(conn)["card"]
    flashcards.reveal(conn, card["token"])
    flashcards.grade(conn, card["token"], 3, 1000)
    job = app.mutate(conn, "/api/jobs/add", {"company": "Acme", "title": "Intern",
                                             "url": "https://example.invalid/acme"})
    app.mutate(conn, f"/api/jobs/{job['id']}", {"action": "applied", "value": True})
    application = conn.execute("SELECT id FROM applications").fetchone()[0]
    subject = app.mutate(conn, "/api/study/subjects", {"name": "Chemistry"})["item"]
    session = app.mutate(conn, "/api/study/sessions", {
        "subject_id": subject["id"], "started_at": "2026-01-01T09:00:00Z", "duration_seconds": 1500,
        "timezone": "UTC", "request_id": uuid.uuid4().hex})["item"]
    result = brain(app, conn)
    assert_contract(result)
    nodes = by_id(result)
    review = next(node for node in result["nodes"] if node["type"] == "review")
    assert review["parent"] == f"flashcards.note:{note['id']}" and review["label"] == "Reviewed card · Good"
    status = next(node for node in result["nodes"] if node["id"].startswith("edit:status_events:"))
    assert status["section"] == "applications"
    assert status["parent"] == f"applications.application:{application}"
    assert nodes[f"applications.application:{application}"]["parent"] == f"jobs.job:{job['id']}"
    session_id = f"study.session:{session['id']}"
    assert nodes[session_id]["parent"] == f"study.subject:{subject['id']}"
    studied = [node for node in result["nodes"] if node["kind"] == "edit" and node["parent"] == session_id]
    assert [node["label"] for node in studied] == ["Studied 25 min"]
    assert "front-secret" not in json.dumps(result)


def test_calendar_history_before_capture_began_is_kept_without_double_counting(app, workspace):
    conn = workspace.conn
    event = app.mutate(conn, "/api/calendar/events", {
        "title": "Lab", "start": "2030-01-02T09:00:00Z", "end": "2030-01-02T10:00:00Z",
        "timezone": "UTC", "all_day": False})
    conn.execute("INSERT INTO calendar_history(entity, entity_id, before_json, after_json, created_at)"
                 " VALUES ('event', ?, '{}', '{}', '2001-01-01T00:00:00+00:00')", (event["id"],))
    result = brain(app, conn)
    assert_contract(result)
    event_id = f"calendar.event:{event['id']}"
    edits = [node for node in result["nodes"] if node["kind"] == "edit" and node["parent"] == event_id]
    assert [(edit["type"], edit["at"]) for edit in edits][-1] == ("save", "2001-01-01T00:00:00+00:00")
    assert len(edits) == 2


def test_disabled_modules_are_neither_listed_nor_returned(workspace, tmp_path):
    conn = workspace.conn
    full = WebApplication(workspace.settings, workspace.config, tmp_path)
    try:
        seed(full, conn)
    finally:
        full.close()
    config = dataclasses.replace(workspace.config, modules=("workspace",))
    app = WebApplication(workspace.settings, config, tmp_path)
    try:
        result = brain(app, conn)
    finally:
        app.close()
    assert_contract(result)
    sections = {section["id"] for section in result["sections"]}
    assert sections == {"calendar", "opportunities", "study", "flashcards", "notes", "quick-notes", "schema", "documents"}
    assert not [node for node in result["nodes"] if node["section"] in {"jobs", "applications", "reminders"}]
    assert web_brain.brain(conn, modules=())["nodes"] == []


def test_truncation_keeps_records_before_edits(app, workspace):
    conn = workspace.conn
    seeded = brain(app, conn)["total"]["records"]  # e.g. the default calendar
    for index in range(4):
        app.mutate(conn, "/api/quick-notes/create", {"body": f"note {index}"})
    records = seeded + 4
    for limit in (2, records + 2):
        result = brain(app, conn, limit=limit)
        assert_contract(result)
        assert result["truncated"] is True
        assert result["total"] == {"records": records, "edits": 4}
        kinds = [node["kind"] for node in result["nodes"]]
        assert len(kinds) == limit
        assert kinds == ["record"] * min(limit, records) + ["edit"] * max(0, limit - records)
    edits = [node["id"] for node in brain(app, conn, limit=records + 2)["nodes"] if node["kind"] == "edit"]
    last = conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0]
    assert edits == [f"edit:op_log:{last}", f"edit:op_log:{last - 1}"]


@pytest.mark.parametrize("query", [{"limit": "0"}, {"limit": "50001"}, {"limit": "x"}, {"focus": "notes"}])
def test_rejects_invalid_queries(app, workspace, query):
    with pytest.raises(APIError) as error:
        app.get_json(workspace.conn, "/api/brain", query)
    assert error.value.status == 400


def test_block_backend_pages_blocks_op_log_and_compacted_days(app, workspace):
    conn = workspace.conn
    notebook = app.mutate(conn, "/api/notes/notebooks", {"title": "Physics"})
    page = app.mutate(conn, "/api/notes/pages", {"notebook_id": notebook["id"], "title": "Optics"})
    root = conn.execute("SELECT block_id FROM note_page_index WHERE id=?", (page["id"],)).fetchone()[0]
    child, nested, gone = (str(uuid.uuid4()) for _ in range(3))
    # The editor's live transactions never pass through WebApplication.mutate: op_log has them.
    blocks.transact(conn, [{"op": "insert", "id": child, "type": "text", "parent": root, "text": "a private outline line"},
                           {"op": "insert", "id": nested, "type": "text", "parent": child, "text": "nested"},
                           {"op": "insert", "id": gone, "type": "text", "parent": root, "text": "trashed"}])
    blocks.transact(conn, [{"op": "delete", "id": gone}])
    quick = app.mutate(conn, "/api/quick-notes/create", {"body": "quick private body"})
    # History older than the retention window survives as one snapshot per page per day.
    seq = conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0]
    conn.execute("INSERT INTO page_snapshots(page_id, day, seq, state) VALUES (?, '2001-01-01', ?, x'00')", (root, seq))
    result = brain(app, conn)
    assert_contract(result)
    nodes = by_id(result)
    number = page["id"]
    assert nodes[f"notes.page:{number}"]["label"] == "Optics"
    assert nodes[f"notes.page:{number}"]["parent"] == f"notes.notebook:{notebook['id']}"
    assert nodes[f"notes.block:{child}"]["parent"] == f"notes.page:{number}"
    assert nodes[f"notes.block:{nested}"]["parent"] == f"notes.block:{child}"
    assert f"notes.block:{gone}" not in nodes
    assert nodes[f"quick-notes.note:{quick['id']}"]["label"] == "Quick note"
    block_edits = [(node["section"], node["type"], node["label"], node["parent"]) for node in result["nodes"]
                   if node["id"].startswith(("edit:op_log:", "edit:page_snapshots:"))]
    assert block_edits == [
        ("quick-notes", "create", "Created quick note", f"quick-notes.note:{quick['id']}"),
        ("notes", "save", "Edited page · Optics", f"notes.page:{number}"),
        ("notes", "save", "Edited page · Optics", f"notes.page:{number}"),
        ("notes", "create", "Created page · Optics", f"notes.page:{number}"),
        ("notes", "save", "Edited page · Optics", f"notes.page:{number}"),
    ]
    assert block_edits[-1] == next((node["section"], node["type"], node["label"], node["parent"])
                                   for node in result["nodes"] if node["id"].startswith("edit:page_snapshots:"))
    sections = {section["id"]: section for section in result["sections"]}
    assert sections["notes"]["edits"] == 1 + 4  # the notebook capture, then op_log and the snapshot day
    text = json.dumps(result)
    assert "private outline" not in text and "quick private body" not in text


def test_the_block_migrations_own_rows_are_conversions_not_edits(tmp_path):
    from kitty.db import connect, migrate
    from kitty.db.migrations import available_migrations
    import shutil
    early = tmp_path / "main-schema"
    early.mkdir()
    for migration in available_migrations():
        if migration.version < 32 or migration.name in {"record_events", "calendar_palette_subjects", "note_page_maps"}:
            shutil.copy(migration.path, early / migration.path.name)
    conn = connect(tmp_path / "data" / "apply.db")
    migrate(conn, early)
    book = conn.execute("INSERT INTO note_notebooks(title, created_at, updated_at) VALUES ('Old', 't', 't')").lastrowid
    conn.execute("INSERT INTO note_pages(notebook_id, title, document, created_at, updated_at)"
                 " VALUES (?, 'Kept', '{\"version\": 1, \"elements\": []}', 't', 't')", (book,))
    conn.execute("INSERT INTO quick_notes(body, created_at, updated_at) VALUES ('old #idea', 't', 't')")
    conn.commit()
    migrate(conn)
    assert conn.execute("SELECT last_seq FROM op_log_migrated").fetchone()[0] == conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0] > 0
    result = web_brain.brain(conn)
    assert [node for node in result["nodes"] if node["kind"] == "edit"] == []
    assert {node["id"] for node in result["nodes"]} >= {"notes.page:1", "quick-notes.note:1"}
    conn.close()


def test_fifty_thousand_nodes_answer_quickly(app, workspace):
    conn = workspace.conn
    seeded = brain(app, conn)["total"]["records"]
    stamp = "2030-01-01T00:00:00Z"
    book = conn.execute("INSERT INTO note_notebooks(title, created_at, updated_at) VALUES ('Big', ?, ?)", (stamp, stamp)).lastrowid
    conn.executemany("INSERT INTO note_folders(title, created_at, updated_at) VALUES ('x', ?, ?)", [(stamp, stamp)] * 20000)
    conn.executemany("INSERT INTO record_events(at, section, entity, entity_id, action, route)"
                     " VALUES (?, 'notes', 'notebook', ?, 'save', '/api/notes/notebooks/1')",
                     [(stamp, str(book)) for _ in range(40000)])
    seeded += 1
    started = time.perf_counter()
    result = brain(app, conn, limit=50000)
    elapsed = time.perf_counter() - started
    assert len(result["nodes"]) == 50000 and result["truncated"] is True
    assert result["total"] == {"records": 20000 + seeded, "edits": 40000}
    assert elapsed < 3, elapsed


def test_records_created_after_the_block_migration_come_from_blocks(app, workspace):
    conn = workspace.conn
    legacy = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("note_pages", "note_blocks", "quick_notes", "documents")}
    notebook = app.mutate(conn, "/api/notes/notebooks", {"title": "Chemistry"})
    page = app.mutate(conn, "/api/notes/pages", {"notebook_id": notebook["id"], "title": "Acids"})
    root = conn.execute("SELECT block_id FROM note_page_index WHERE id=?", (page["id"],)).fetchone()[0]
    item = str(uuid.uuid4())
    blocks.transact(conn, [{"op": "insert", "id": item, "type": "text", "parent": root, "text": "pH"}])
    blocks.transact(conn, [{"op": "text", "id": item, "text": "pH scale"}])
    quick = app.mutate(conn, "/api/quick-notes/create", {"body": "later"})
    document = app.mutate(conn, "/api/documents", {"title": "Lab report", "content": "Body", "format": "text",
                                                   "notebook_id": notebook["id"]})
    # The frozen legacy tables never see them: only the block backend does.
    assert legacy == {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in legacy}
    result = brain(app, conn)
    assert_contract(result)
    nodes = by_id(result)
    assert nodes[f"notes.page:{page['id']}"]["label"] == "Acids"
    assert nodes[f"notes.block:{item}"]["parent"] == f"notes.page:{page['id']}"
    assert nodes[f"quick-notes.note:{quick['id']}"]["type"] == "note"
    assert nodes[f"documents.document:{document['id']}"]["label"] == "Lab report"
    item_edits = [node for node in result["nodes"] if node["kind"] == "edit"
                  and node["parent"] == f"notes.page:{page['id']}" and node["id"].startswith("edit:op_log:")]
    assert len(item_edits) == 3  # create, insert, update: each op_log row once

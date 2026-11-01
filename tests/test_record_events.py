"""record_events: the append-only history of browser mutations through WebApplication.mutate."""
from __future__ import annotations

import logging
import sqlite3

import pytest

from kitty.core import record_events
from kitty.web_server import ROUTE_MODULES, APIError, WebApplication


@pytest.fixture
def app(workspace, tmp_path):
    application = WebApplication(workspace.settings, workspace.config, tmp_path)
    yield application
    application.close()


def events(conn):
    return [tuple(row) for row in conn.execute(
        "SELECT section, entity, entity_id, action, route FROM record_events ORDER BY seq")]


def test_migration_creates_an_append_only_table(workspace):
    conn = workspace.conn
    columns = [row[1] for row in conn.execute("PRAGMA table_info(record_events)")]
    assert columns == ["seq", "at", "section", "entity", "entity_id", "action", "route"]
    indexes = {row[1]: [col[2] for col in conn.execute(f"PRAGMA index_info({row[1]})")]
               for row in conn.execute("PRAGMA index_list(record_events)")}
    assert ["section", "seq"] in indexes.values()
    conn.execute("INSERT INTO record_events(at, section, entity, entity_id, action, route)"
                 " VALUES ('2030-01-01T00:00:00Z', 'notes', 'page', '1', 'save', '/api/notes/pages/1')")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE record_events SET action = 'delete'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM record_events")
    assert events(conn) == [("notes", "page", "1", "save", "/api/notes/pages/1")]


def _notes(app, conn):
    notebook = app.mutate(conn, "/api/notes/notebooks", {"title": "Physics"})
    page = app.mutate(conn, "/api/notes/pages", {"notebook_id": notebook["id"], "title": "Waves"})
    path = f"/api/notes/pages/{page['id']}"
    app.mutate(conn, path, {"revision": page["revision"], "title": "Standing waves", "document": page["document"]})
    return ("notes", "page", str(page["id"]), "save", path)


def _documents(app, conn):
    notebook = app.mutate(conn, "/api/notes/notebooks", {"title": "Reading"})
    document = app.mutate(conn, "/api/documents", {"title": "Paper", "content": "Body", "format": "text",
                                                   "notebook_id": notebook["id"]})
    return ("documents", "document", str(document["id"]), "create", "/api/documents")


def _quick_notes(app, conn):
    note = app.mutate(conn, "/api/quick-notes/create", {"body": "Buy milk"})
    return ("quick-notes", "note", str(note["id"]), "create", "/api/quick-notes/create")


def _flashcards(app, conn):
    deck = app.mutate(conn, "/api/flashcards/deck", {"name": "Biology"})["deck"]
    return ("flashcards", "deck", str(deck["id"]), "create", "/api/flashcards/deck")


def _calendar(app, conn):
    event = app.mutate(conn, "/api/calendar/events", {
        "title": "Lab", "start": "2030-01-02T09:00:00Z", "end": "2030-01-02T10:00:00Z",
        "timezone": "UTC", "all_day": False})
    return ("calendar", "event", event["id"], "create", "/api/calendar/events")


def _study(app, conn):
    subject = app.mutate(conn, "/api/study/subjects", {"name": "Chemistry"})["item"]
    return ("study", "subject", str(subject["id"]), "create", "/api/study/subjects")


def _schema(app, conn):
    created = app.mutate(conn, "/api/schema/create", {"title": "Ideas"})
    return ("schema", "map", str(created["id"]), "create", "/api/schema/create")


def _opportunities(app, conn):
    item = app.mutate(conn, "/api/opportunities", {"title": "Hackathon"})
    return ("opportunities", "opportunity", str(item["id"]), "create", "/api/opportunities")


def _jobs(app, conn):
    job = app.mutate(conn, "/api/jobs/add", {"company": "Acme", "title": "Intern",
                                             "url": "https://example.invalid/acme"})
    path = f"/api/jobs/{job['id']}"
    app.mutate(conn, path, {"action": "favorite", "value": True})
    return ("jobs", "job", str(job["id"]), "favorite", path)


def _reminders(app, conn):
    reminder = app.mutate(conn, "/api/reminders", {"date": "2030-01-05", "note": "Call back"})
    return ("reminders", "reminder", str(reminder["id"]), "create", "/api/reminders")


@pytest.mark.parametrize("case", [_notes, _documents, _quick_notes, _flashcards, _calendar, _study,
                                  _schema, _opportunities, _jobs, _reminders], ids=lambda case: case.__name__[1:])
def test_one_real_route_per_section_is_captured(app, workspace, case):
    conn = workspace.conn
    expected = case(app, conn)
    captured = events(conn)
    assert captured[-1] == expected
    assert all(section in record_events.SECTIONS for section, *_ in captured)


def test_edits_and_deletes_resolve_the_entity_from_path_result_or_body(app, workspace):
    conn = workspace.conn
    note = app.mutate(conn, "/api/quick-notes/create", {"body": "draft"})
    saved = app.mutate(conn, "/api/quick-notes/save", {"id": note["id"], "revision": note["revision"], "body": "x"})
    app.mutate(conn, "/api/quick-notes/delete", {"id": note["id"], "revision": saved["revision"]})
    deck = app.mutate(conn, "/api/flashcards/deck", {"name": "Words"})["deck"]
    subject = app.mutate(conn, "/api/study/subjects", {"name": "Art"})["item"]
    app.mutate(conn, "/api/study/subjects", {"id": subject["id"], "revision": subject["revision"], "action": "delete"})
    ident = str(note["id"])
    assert events(conn) == [
        ("quick-notes", "note", ident, "create", "/api/quick-notes/create"),
        ("quick-notes", "note", ident, "save", "/api/quick-notes/save"),
        ("quick-notes", "note", ident, "delete", "/api/quick-notes/delete"),
        ("flashcards", "deck", str(deck["id"]), "create", "/api/flashcards/deck"),
        ("study", "subject", str(subject["id"]), "create", "/api/study/subjects"),
        ("study", "subject", str(subject["id"]), "delete", "/api/study/subjects"),
    ]


def test_calendar_undo_is_attributed_to_the_undone_event(app, workspace):
    conn = workspace.conn
    event = app.mutate(conn, "/api/calendar/events", {
        "title": "Seminar", "start": "2030-01-03T09:00:00Z", "end": "2030-01-03T10:00:00Z",
        "timezone": "UTC", "all_day": False})
    app.mutate(conn, "/api/calendar/undo", {"id": event["undo_id"]})
    assert events(conn)[-1] == ("calendar", "event", event["id"], "undo", "/api/calendar/undo")


def test_non_content_and_failed_mutations_are_not_captured(app, workspace):
    conn = workspace.conn
    app.mutate(conn, "/api/calendar/time", {"value": "2030-01-01T09:00", "timezone": "UTC"})
    revision = app.get_json(conn, "/api/study", {})["settings"]["revision"]
    app.mutate(conn, "/api/study/settings", {"revision": revision, "sound": False})
    with pytest.raises(APIError):
        app.mutate(conn, "/api/quick-notes/save", {"id": 999, "revision": 1, "body": "missing"})
    with pytest.raises(APIError):
        app.mutate(conn, "/api/notes/pages", {"notebook_id": 999})
    assert events(conn) == []


def test_logged_mutations_are_left_to_their_own_history(app, workspace):
    """Grading writes flash_reviews and job status writes status_events; neither is captured twice."""
    conn = workspace.conn
    job = app.mutate(conn, "/api/jobs/add", {"company": "Acme", "title": "Intern",
                                             "url": "https://example.invalid/acme"})
    app.mutate(conn, f"/api/jobs/{job['id']}", {"action": "applied", "value": True})
    assert [row[3] for row in events(conn)] == ["create"]
    assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == 1


def test_a_capture_failure_never_fails_the_request(app, workspace, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(record_events, "append", broken)
    with caplog.at_level(logging.WARNING, logger="kitty.core.record_events"):
        note = app.mutate(workspace.conn, "/api/quick-notes/create", {"body": "kept"})
    assert note["body"] == "kept"
    assert events(workspace.conn) == []
    assert any("not recorded" in record.message for record in caplog.records)


def test_unknown_routes_in_content_sections_fall_back_to_the_route_shape():
    event = record_events.classify("/api/notes/widgets/7/pin", {}, {"ok": True})
    assert event == record_events.Event("notes", "widget", "7", "pin")
    event = record_events.classify("/api/opportunities/gizmos", {}, {"item": {"id": 4}})
    assert event == record_events.Event("opportunities", "gizmo", "4", "create")


def test_every_route_prefix_is_classified():
    """A new feature prefix must be declared content (with a section) or non-content."""
    prefixes = {prefix.split("/")[2] for prefix, _ in ROUTE_MODULES}
    declared = set(record_events.SECTION_PREFIXES) | set(record_events.NON_CONTENT_PREFIXES)
    assert prefixes <= declared
    assert not set(record_events.SECTION_PREFIXES) & set(record_events.NON_CONTENT_PREFIXES)
    for path in ("/api/reload", "/api/modules", "/api/account/onboarding", "/api/ai/settings",
                 "/api/notes/preferences", "/api/notes/pages/3/viewport", "/api/flashcards/grade",
                 "/api/flashcards/review", "/api/study/timer", "/api/calendar/google/sync",
                 "/api/opportunities/settings", "/api/opportunities/discover", "/api/terminal/session"):
        assert record_events.classify(path, {}, {"id": 1}) is None, path
    assert record_events.classify("/api/documents/import", {}, {"task": "a" * 32}) is None

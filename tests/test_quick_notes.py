"""Quick notes: small local scratch notes with revision-checked autosave."""
from __future__ import annotations

import pytest

from kitty.core import quick_notes


def test_new_notes_list_pinned_first_then_most_recently_edited(workspace):
    conn = workspace.conn
    older = quick_notes.create(conn, "buy milk")
    newer = quick_notes.create(conn, "call back recruiter", "sky")
    pinned = quick_notes.create(conn, "exam friday")
    quick_notes.save(conn, pinned["id"], pinned["revision"], pinned=True)
    quick_notes.save(conn, older["id"], older["revision"], body="buy oat milk")
    listing = quick_notes.list_notes(conn)
    assert [note["id"] for note in listing] == [pinned["id"], older["id"], newer["id"]]
    assert listing[0]["pinned"] is True and listing[1]["body"] == "buy oat milk"
    assert listing[2]["tint"] == "sky" and listing[1]["tint"] == "rose"


def test_stale_revision_cannot_overwrite_or_delete_another_windows_edit(workspace):
    conn = workspace.conn
    note = quick_notes.create(conn, "draft")
    saved = quick_notes.save(conn, note["id"], note["revision"], body="edited in pop-out")
    assert saved["revision"] == note["revision"] + 1
    with pytest.raises(quick_notes.QuickNoteError) as conflict:
        quick_notes.save(conn, note["id"], note["revision"], body="stale section edit")
    assert conflict.value.status == 409
    with pytest.raises(quick_notes.QuickNoteError):
        quick_notes.delete(conn, note["id"], note["revision"])
    assert quick_notes.get(conn, note["id"])["body"] == "edited in pop-out"
    quick_notes.delete(conn, note["id"], saved["revision"])
    with pytest.raises(quick_notes.QuickNoteError) as missing:
        quick_notes.get(conn, note["id"])
    assert missing.value.status == 404


@pytest.mark.parametrize("changes", [
    {"tint": "neon"}, {"pinned": "yes"}, {"body": 5}, {"body": "x" * (quick_notes.MAX_BODY + 1)}, {"body": "a\x00b"},
])
def test_invalid_edits_are_rejected_without_changing_the_note(workspace, changes):
    conn = workspace.conn
    note = quick_notes.create(conn, "keep me")
    with pytest.raises(quick_notes.QuickNoteError):
        quick_notes.save(conn, note["id"], note["revision"], **changes)
    assert quick_notes.get(conn, note["id"]) == note


# The reads are answered by rika-server (server/src/quick_notes); the writes by the worker behind it.

def test_quick_notes_over_http_create_autosave_list_and_delete(workspace):
    from terminal_support import front_site

    with front_site(workspace.settings) as site:
        status, created = site.request("/api/quick-notes/create", {"body": "", "tint": "mint"})
        assert status == 200 and created["body"] == "" and created["tint"] == "mint"
        status, saved = site.request("/api/quick-notes/save", {"id": created["id"], "revision": created["revision"], "body": "idea: glass notes"})
        assert status == 200 and saved["revision"] == created["revision"] + 1
        status, stale = site.request("/api/quick-notes/save", {"id": created["id"], "revision": created["revision"], "body": "old"})
        assert status == 409 and "another window" in stale["error"]
        assert site.request(f"/api/quick-notes/{created['id']}")[1]["body"] == "idea: glass notes"
        assert [note["body"] for note in site.request("/api/quick-notes")[1]["notes"]] == ["idea: glass notes"]
        assert site.request("/api/quick-notes/save", {"id": created["id"], "revision": saved["revision"], "colour": "rose"})[0] == 400
        assert site.request("/api/quick-notes/delete", {"id": created["id"], "revision": saved["revision"]})[0] == 200
        assert site.request("/api/quick-notes")[1]["notes"] == []
        assert site.request(f"/api/quick-notes/{created['id']}")[0] == 404


def test_the_front_reads_quick_notes_as_the_python_core_does(workspace):
    """Same notes, same order (pinned first, then newest), same fields; and the same refusals."""
    from contextlib import closing

    from kitty.db import connect
    from terminal_support import front_site

    with closing(connect(workspace.settings.db_path)) as conn:
        first = quick_notes.create(conn, "first", "rose")
        second = quick_notes.create(conn, "second \u00e9\u4e16", "sky")
        third = quick_notes.create(conn, "", "mint")
        quick_notes.save(conn, first["id"], first["revision"], pinned=True)
        quick_notes.delete(conn, third["id"], third["revision"])
        expected = quick_notes.list_notes(conn)
        one = quick_notes.get(conn, second["id"])
    assert [note["body"] for note in expected] == ["first", "second \u00e9\u4e16"]
    with front_site(workspace.settings) as site:
        assert site.request("/api/quick-notes") == (200, {"notes": expected})
        assert site.request(f"/api/quick-notes/{second['id']}") == (200, one)
        assert site.request(f"/api/quick-notes/{third['id']}") == (404, {"error": "Quick note not found"})
        assert site.request("/api/quick-notes/9999999999999999") == (400, {"error": "Expected a quick note ID"})
        assert site.request("/api/quick-notes?x=1") == (400, {"error": "This route does not accept query parameters"})
        assert site.request("/api/quick-notes/0")[0] == 404 and site.request("/api/quick-notes/abc")[0] == 404

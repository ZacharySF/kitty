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

"""Version history (every transaction for 30 days, then one snapshot per page per day) and Trash."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core import blocks, notes, quick_notes
from kitty.core.blocks import history

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def stamp(days=0, minutes=0):
    return (NOW - timedelta(days=days, minutes=minutes)).isoformat()


def new_page(conn, at, title="History page"):
    page, block = str(uuid.uuid4()), str(uuid.uuid4())
    blocks.transact(conn, [{"op": "insert", "id": page, "type": "page", "parent": None, "text": title, "props": {"revision": 1}},
                           {"op": "insert", "id": block, "type": "text", "parent": page, "text": "v0"}], at=at)
    return page, block


def edit(conn, block, text, at):
    return blocks.transact(conn, [{"op": "text", "id": block, "text": text}], at=at)


def text_of(doc, block):
    return doc.blocks[block].text


def test_history_groups_transactions_into_editing_sessions(workspace):
    conn = workspace.conn
    page, block = new_page(conn, stamp(minutes=120))
    for minute in (118, 116, 115):
        edit(conn, block, f"morning {minute}", stamp(minutes=minute))
    for minute in (30, 29):
        edit(conn, block, f"later {minute}", stamp(minutes=minute))
    result = history.page_history(conn, page)
    assert [session["transactions"] for session in result["sessions"]] == [2, 4]
    newest, oldest = result["sessions"]
    assert newest["device_id"] == blocks.device_id(conn) and newest["start"] == stamp(minutes=30)
    assert oldest["ops"] == {"insert": 2, "text": 3, "set": 3}
    assert result["horizon_seq"] == 0 and result["snapshots"] == []


def test_preview_and_restore_append_a_new_transaction(workspace):
    conn = workspace.conn
    page, block = new_page(conn, stamp(minutes=10))
    first = edit(conn, block, "good draft", stamp(minutes=9))["seq"][page]
    edit(conn, block, "bad rewrite", stamp(minutes=8))
    extra = str(uuid.uuid4())
    blocks.transact(conn, [{"op": "insert", "id": extra, "type": "text", "parent": page, "text": "added later"}], at=stamp(minutes=7))
    rows_before = [tuple(row) for row in conn.execute("SELECT * FROM op_log")]
    preview = history.preview(conn, page, first)
    assert {item["id"]: item["text"] for item in preview["blocks"]}[block] == "good draft"
    assert extra not in {item["id"] for item in preview["blocks"]}
    result = history.restore(conn, page, first)
    doc = blocks.load_page(conn, page)
    assert text_of(doc, block) == "good draft" and not doc.blocks[extra].live
    assert doc.root.properties["revision"] == 5
    assert [tuple(row) for row in conn.execute("SELECT * FROM op_log")][:len(rows_before)] == rows_before
    assert result["seq"][page] > rows_before[-1][0]
    with pytest.raises(blocks.BlockError) as missing:
        history.preview(conn, str(uuid.uuid4()), first)
    assert missing.value.status == 404


def test_deleted_pages_and_quick_notes_go_to_trash_and_come_back(workspace):
    conn = workspace.conn
    book = notes.create_notebook(conn, "Trash tests")
    page = notes.create_page(conn, book["id"], "Recoverable")
    note = quick_notes.create(conn, "recover me", "mint")
    notes.delete_page(conn, page["id"], page["revision"])
    quick_notes.delete(conn, note["id"], note["revision"])
    items = history.trash(conn)["items"]
    assert [(item["root"]["type"], item["root"]["number"], item["title"]) for item in items] == [
        ("quick_note", note["id"], "recover me"), ("page", page["id"], "Recoverable")]
    for item in items:
        history.restore_from_trash(conn, item["id"])
    assert history.trash(conn)["items"] == []
    assert notes.page(conn, page["id"])["title"] == "Recoverable"
    assert quick_notes.get(conn, note["id"])["body"] == "recover me"
    with pytest.raises(blocks.BlockError) as live:
        history.restore_from_trash(conn, items[0]["id"])
    assert live.value.status == 409


def test_compaction_keeps_30_days_exactly_and_folds_older_days_into_snapshots(workspace):
    conn = workspace.conn
    page, block = new_page(conn, stamp(days=45, minutes=600))
    edit(conn, block, "45 days ago, morning", stamp(days=45, minutes=300))
    end_of_old_day = edit(conn, block, "45 days ago, evening", stamp(days=45, minutes=10))["seq"][page]
    edit(conn, block, "40 days ago", stamp(days=40))
    recent = edit(conn, block, "yesterday", stamp(days=1))["seq"][page]
    edit(conn, block, "today", stamp(minutes=5))
    before = blocks.load_page(conn, page).rows()
    result = history.compact(conn, now=NOW)
    assert result["folded"] == 4 and result["snapshots"] == 2
    assert [row["at"] for row in conn.execute("SELECT at FROM op_log ORDER BY seq")] == [stamp(days=1), stamp(minutes=5)]
    assert [row["day"] for row in conn.execute("SELECT day FROM page_snapshots ORDER BY day")] == [
        stamp(days=45)[:10], stamp(days=40)[:10]]
    assert blocks.replay(conn, page).rows() == before
    assert text_of(blocks.replay(conn, page, recent), block) == "yesterday"
    assert {item["id"]: item["text"] for item in history.preview(conn, page, end_of_old_day)["blocks"]}[block] == "45 days ago, evening"
    with pytest.raises(blocks.BlockError) as compacted:
        history.preview(conn, page, end_of_old_day - 1)
    assert compacted.value.status == 410
    assert history.page_history(conn, page)["snapshots"] == [{"day": stamp(days=40)[:10], "seq": end_of_old_day + 1},
                                                             {"day": stamp(days=45)[:10], "seq": end_of_old_day}]
    assert history.compact(conn, now=NOW)["folded"] == 0
    with pytest.raises(sqlite3.DatabaseError, match="compaction horizon"):
        conn.execute("DELETE FROM op_log")


def test_trash_follows_the_same_window(workspace):
    conn = workspace.conn
    old, _ = new_page(conn, stamp(days=50), "Deleted long ago")
    blocks.transact(conn, [{"op": "delete", "id": old}], at=stamp(days=35))
    fresh, _ = new_page(conn, stamp(days=50), "Deleted last week")
    blocks.transact(conn, [{"op": "delete", "id": fresh}], at=stamp(days=7))
    result = history.compact(conn, now=NOW)
    assert result["purged"] == 1
    assert conn.execute("SELECT COUNT(*) FROM blocks WHERE page_id=?", (old,)).fetchone()[0] == 0
    assert [item["id"] for item in history.trash(conn)["items"]] == [fresh]

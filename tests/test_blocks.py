"""Block backend: transactions, the append-only operation log and the projection."""
from __future__ import annotations

import json
import random
import sqlite3
import uuid

import pytest

from kitty.core import blocks
from kitty.core.blocks import order


def new_id():
    return str(uuid.uuid4())


def page_ops(page, *children, title="Page"):
    ops = [{"op": "insert", "id": page, "type": "page", "parent": None, "text": title, "props": {"revision": 1}}]
    for child, text in children:
        ops.append({"op": "insert", "id": child, "type": "text", "parent": page, "text": text})
    return ops


def projection(conn, page):
    return {row["id"]: (row["type"], row["parent_id"], row["order_key"], json.loads(row["properties"]), row["text"], row["deleted_at"])
            for row in conn.execute("SELECT * FROM blocks WHERE page_id=?", (page,))}


def test_order_keys_sort_between_their_neighbours():
    first = order.key_between(None, None)
    after = order.key_between(first, None)
    before = order.key_between(None, first)
    middle = order.key_between(first, after)
    assert before < first < middle < after
    keys = order.keys_between(first, after, 50)
    assert keys == sorted(keys) and len(set(keys)) == 50 and first < keys[0] and keys[-1] < after
    run = [None]
    for _ in range(300):
        run.append(order.key_between(run[-1], None))
    assert run[1:] == sorted(run[1:])
    with pytest.raises(ValueError):
        order.key_between(after, first)


def test_transaction_appends_one_log_row_per_page_and_updates_the_projection(workspace):
    conn = workspace.conn
    page, a, b = new_id(), new_id(), new_id()
    result = blocks.transact(conn, page_ops(page, (a, "first #tag [[Other page]]"), (b, f"cites (({a}))")))
    assert set(result["seq"]) == {page}
    rows = conn.execute("SELECT * FROM op_log").fetchall()
    assert len(rows) == 1 and rows[0]["page_id"] == page and rows[0]["txn_id"] == result["txn_id"]
    assert rows[0]["device_id"] == blocks.device_id(conn)
    logged = json.loads(rows[0]["ops"])
    assert [op["op"] for op in logged["ops"]] == ["insert", "insert", "insert"]
    state = projection(conn, page)
    assert state[page][1] is None and state[a][1] == page and state[b][1] == page
    assert state[a][2] < state[b][2]
    assert {tuple(row) for row in conn.execute("SELECT block_id,kind,topic FROM block_topics")} == {(a, "tag", "tag"), (a, "page", "other page")}
    assert [tuple(row) for row in conn.execute("SELECT source_id,target_id,kind FROM block_refs")] == [(b, a, "block")]
    assert result["revisions"] == {page: 1}


def test_content_edits_bump_the_page_revision_and_view_state_does_not(workspace):
    conn = workspace.conn
    page, a = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (a, "draft")))
    assert blocks.transact(conn, [{"op": "text", "id": a, "text": "final"}])["revisions"] == {page: 2}
    assert blocks.transact(conn, [{"op": "text", "id": a, "at": 0, "del": 1, "ins": "F"}])["revisions"] == {page: 3}
    assert blocks.load_page(conn, page).blocks[a].text == "Final"
    view = blocks.transact(conn, [{"op": "set", "id": page, "props": {"favorite": 1, "viewport": {"x": 1, "y": 2, "zoom": 1}}}])
    assert view["revisions"] == {page: 3}


def test_stale_base_revision_is_rejected_without_writing(workspace):
    conn = workspace.conn
    page, a = new_id(), new_id()
    blocks.transact(conn, page_ops(page, (a, "one")))
    blocks.transact(conn, [{"op": "text", "id": a, "text": "two"}], base={page: 1})
    before = projection(conn, page)
    count = conn.execute("SELECT COUNT(*) FROM op_log").fetchone()[0]
    with pytest.raises(blocks.BlockError) as stale:
        blocks.transact(conn, [{"op": "text", "id": a, "text": "stale"}], base={page: 1})
    assert stale.value.status == 409
    assert projection(conn, page) == before
    assert conn.execute("SELECT COUNT(*) FROM op_log").fetchone()[0] == count


def test_moves_that_would_create_a_cycle_are_refused_atomically(workspace):
    conn = workspace.conn
    page, parent, child, other = new_id(), new_id(), new_id(), new_id()
    blocks.transact(conn, page_ops(page, (parent, "parent"), (other, "other"))
                    + [{"op": "insert", "id": child, "type": "text", "parent": parent, "text": "child"}])
    before = projection(conn, page)
    for target in (child, parent):
        with pytest.raises(blocks.BlockError, match="cycle"):
            # The first op is valid; the whole transaction must still roll back.
            blocks.transact(conn, [{"op": "move", "id": other, "parent": parent}, {"op": "move", "id": parent, "parent": target}])
    assert projection(conn, page) == before
    blocks.transact(conn, [{"op": "move", "id": child, "parent": page, "after": None}])
    assert blocks.load_page(conn, page).children(page)[0].id == child


def test_unknown_blocks_and_bad_ops_are_rejected(workspace):
    conn = workspace.conn
    page = new_id()
    blocks.transact(conn, page_ops(page))
    for op in ({"op": "text", "id": new_id(), "text": "x"}, {"op": "explode", "id": page},
               {"op": "insert", "id": page, "type": "text", "parent": page}, {"op": "insert", "id": "not-a-uuid", "type": "text", "parent": page},
               {"op": "set", "id": page, "props": []}):
        with pytest.raises(blocks.BlockError):
            blocks.transact(conn, [op])


def test_delete_goes_to_trash_with_its_subtree_and_restore_brings_it_back(workspace):
    conn = workspace.conn
    page, a, b = new_id(), new_id(), new_id()
    blocks.transact(conn, page_ops(page, (a, "parent #kept")) + [{"op": "insert", "id": b, "type": "text", "parent": a, "text": "child"}])
    blocks.transact(conn, [{"op": "delete", "id": a}])
    state = projection(conn, page)
    assert state[a][5] and state[b][5] == state[a][5]
    assert conn.execute("SELECT COUNT(*) FROM block_topics").fetchone()[0] == 0
    blocks.transact(conn, [{"op": "restore", "id": a}])
    state = projection(conn, page)
    assert state[a][5] is None and state[b][5] is None
    assert conn.execute("SELECT COUNT(*) FROM block_topics").fetchone()[0] == 1
    with pytest.raises(blocks.BlockError) as orphan:
        blocks.transact(conn, [{"op": "delete", "id": a}, {"op": "restore", "id": b}])
    assert orphan.value.status == 409


def test_cross_page_move_logs_on_both_pages_and_moves_the_subtree(workspace):
    conn = workspace.conn
    one, two, a, b = new_id(), new_id(), new_id(), new_id()
    blocks.transact(conn, page_ops(one, (a, "moving")) + [{"op": "insert", "id": b, "type": "text", "parent": a, "text": "with child"}])
    blocks.transact(conn, page_ops(two, title="Two"))
    result = blocks.transact(conn, [{"op": "move", "id": a, "parent": two}])
    assert set(result["seq"]) == {one, two}
    assert {row["page_id"] for row in conn.execute("SELECT page_id FROM blocks WHERE id IN (?,?)", (a, b))} == {two}
    assert blocks.replay(conn, one).blocks.keys() == {one}
    assert set(blocks.replay(conn, two).blocks) == {two, a, b}


def test_operation_log_is_append_only(workspace):
    conn = workspace.conn
    page = new_id()
    blocks.transact(conn, page_ops(page))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE op_log SET ops=?", (b"{}",))
    with pytest.raises(sqlite3.DatabaseError, match="compaction horizon"):
        conn.execute("DELETE FROM op_log")


def test_replaying_the_log_rebuilds_the_projection(workspace):
    conn = workspace.conn
    rng = random.Random(7)
    page = new_id()
    blocks.transact(conn, page_ops(page))
    live = [page]
    for step in range(120):
        choice = rng.random()
        if choice < .45 or len(live) < 3:
            bid = new_id()
            placement = {"after": None} if rng.random() < .3 else {}
            blocks.transact(conn, [{"op": "insert", "id": bid, "type": "text", "parent": rng.choice(live), "text": f"b{step}", **placement}])
            live.append(bid)
        elif choice < .65:
            blocks.transact(conn, [{"op": "text", "id": rng.choice(live[1:]), "text": f"edit {step} #t{step % 3}"}])
        elif choice < .8:
            target = rng.choice(live[1:])
            try:
                blocks.transact(conn, [{"op": "move", "id": target, "parent": rng.choice(live)}])
            except blocks.BlockError:
                pass
        elif choice < .9:
            blocks.transact(conn, [{"op": "set", "id": rng.choice(live[1:]), "props": {"collapsed": rng.random() < .5}}])
        else:
            victim = rng.choice(live[1:])
            blocks.transact(conn, [{"op": "delete", "id": victim}])
            gone = {victim} | {b.id for b in blocks.load_page(conn, page).descendants(victim)}
            live = [bid for bid in live if bid not in gone]
    assert blocks.replay(conn, page).rows() == blocks.load_page(conn, page).rows()


def test_block_types_may_carry_digits_for_editor_types_like_heading_levels(workspace):
    conn = workspace.conn
    page, heading = new_id(), new_id()
    blocks.transact(conn, [*page_ops(page), {"op": "insert", "id": heading, "type": "heading_2", "parent": page, "text": "Level two"}])
    blocks.transact(conn, [{"op": "set", "id": heading, "type": "heading_3"}])
    assert projection(conn, page)[heading][0] == "heading_3"
    for bad in ("2heading", "Heading_1", "heading-1", "h" * 33):
        with pytest.raises(blocks.BlockError):
            blocks.transact(conn, [{"op": "set", "id": heading, "type": bad}])

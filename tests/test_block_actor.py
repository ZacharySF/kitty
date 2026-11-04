"""Who made each block transaction (op_log actor), History by actor, per-session undo and pinned citations."""
from __future__ import annotations

import sqlite3

import pytest

from kitty.core import block_api, blocks, quick_notes
from kitty.core.blocks import history, sessions
from kitty.core.blocks.actor import (
    COMPACTION,
    USER,
    Actor,
    acting,
    cli_agent,
    from_row,
    merged_via,
    parse,
)

AGENT = Actor("agent", "claude-code", "11111111-1111-4111-8111-111111111111")
OTHER = Actor("agent", "codex", "22222222-2222-4222-8222-222222222222")


def _actors(conn, page_id=None):
    sql = "SELECT actor, actor_name, actor_session FROM op_log" + (" WHERE page_id=?" if page_id else "") + " ORDER BY seq"
    return [tuple(row) for row in conn.execute(sql, (page_id,) if page_id else ())]


def _texts(conn, page_id):
    return [block["text"] for block in block_api.get_page(conn, page_id)["blocks"]]


def _page(conn):
    """A page the person wrote, with three blocks."""
    made = block_api.create_page(conn, "Plan", blocks=["alpha", "beta", "gamma"])
    return made["page_id"], [item["id"] for item in made["blocks"]]


# The actor columns ----------------------------------------------------------------------

def test_transactions_record_the_person_by_default_and_the_actor_set_around_them(workspace):
    conn = workspace.conn
    page_id, (alpha, *_rest) = _page(conn)
    with acting(AGENT):
        block_api.update_block(conn, alpha, text="alpha by agent")
    blocks.transact(conn, [{"op": "text", "id": alpha, "text": "alpha by hand"}])
    blocks.transact(conn, [{"op": "text", "id": alpha, "text": "alpha by a client"}], actor=Actor("client", "Tern", "c1"))
    assert _actors(conn, page_id) == [("user", None, None), ("agent", "claude-code", AGENT.session),
                                      ("user", None, None), ("client", "Tern", "c1")]


def test_op_log_stays_append_only_with_the_actor_columns(workspace):
    conn = workspace.conn
    _page(conn)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE op_log SET actor='agent'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO op_log(device_id,txn_id,page_id,at,ops,actor) VALUES ('d','t','p','a','{}','robot')")


def test_actor_values_are_validated_and_carried_actors_are_flattened():
    with pytest.raises(ValueError):
        Actor("robot")
    with pytest.raises(ValueError):
        Actor("agent", "x", "bad session!")
    assert Actor("agent", "  a\nname\x00 ").name == "a name"
    assert parse({"kind": "peer", "name": "x"}) is None and parse({"kind": "agent", "session": "no way"}) is None
    assert parse({"kind": "agent", "name": "claude", "session": "s-1"}) == Actor("agent", "claude", "s-1")
    peer = Actor("peer", "Laptop", None, (AGENT, OTHER))
    assert merged_via([peer, AGENT, USER]) == (AGENT, OTHER, USER)
    agent = cli_agent({"KITTY_AGENT_SESSION": "run-7", "KITTY_AGENT_NAME": "nightly"})
    assert agent == Actor("agent", "nightly", "run-7")
    assert cli_agent({}).name == "kitty notes" and cli_agent({}).session != cli_agent({}).session
    with pytest.raises(ValueError):
        cli_agent({"KITTY_AGENT_SESSION": "has space"})


def test_compaction_purges_are_the_systems(workspace):
    conn = workspace.conn
    note = quick_notes.create(conn, "Old", "rose")
    root = conn.execute("SELECT id FROM blocks WHERE type='quick_note' AND json_extract(properties,'$.number')=?",
                        (note["id"],)).fetchone()[0]
    blocks.transact(conn, [{"op": "delete", "id": root}], at="2020-01-01T00:00:00+00:00")
    history.compact(conn)
    purge = conn.execute("SELECT actor, actor_name FROM op_log WHERE page_id=? ORDER BY seq DESC LIMIT 1", (root,)).fetchone()
    assert tuple(purge) == COMPACTION.columns()[:2]


# History by actor -----------------------------------------------------------------------

def test_history_splits_sessions_by_actor_and_filters_by_kind_and_session(workspace):
    conn = workspace.conn
    page_id, (alpha, beta, _gamma) = _page(conn)
    with acting(AGENT):
        block_api.update_block(conn, alpha, text="a1")
        block_api.update_block(conn, beta, text="b1")
    block_api.update_block(conn, alpha, text="a2")
    with acting(OTHER):
        block_api.update_block(conn, beta, text="b2")
    every = history.page_history(conn, page_id)
    assert [(item["actor"]["kind"], item["actor"]["name"], item["transactions"]) for item in every["sessions"]] == [
        ("agent", "codex", 1), ("user", None, 1), ("agent", "claude-code", 2), ("user", None, 1)]
    agents = history.page_history(conn, page_id, actor="agent")
    assert [item["actor"]["session"] for item in agents["sessions"]] == [OTHER.session, AGENT.session]
    one = history.page_history(conn, page_id, session=AGENT.session)
    assert [len(item["moments"]) for item in one["sessions"]] == [2]
    with pytest.raises(blocks.BlockError):
        history.page_history(conn, page_id, actor="robot")


def test_sessions_are_listed_most_recent_first_with_their_pages(workspace):
    conn = workspace.conn
    page_id, (alpha, *_rest) = _page(conn)
    with acting(AGENT):
        block_api.update_block(conn, alpha, text="a1")
        other = block_api.create_page(conn, "Agent notes", blocks=["x"])
    with acting(OTHER):
        block_api.update_block(conn, alpha, text="a2")
    listed = sessions.list_sessions(conn)["sessions"]
    assert [item["session"] for item in listed] == [OTHER.session, AGENT.session]
    mine = listed[1]
    assert mine["actor"] == {"kind": "agent", "name": "claude-code", "session": AGENT.session}
    assert mine["transactions"] == 2 and mine["page_count"] == 2
    assert {item["page_id"] for item in mine["pages"]} == {page_id, other["page_id"]}
    assert sessions.list_sessions(conn, kind="user")["sessions"] == []


# Undo one agent session -----------------------------------------------------------------

def test_undo_reverts_the_agents_untouched_edits_and_keeps_and_reports_the_persons(workspace):
    conn = workspace.conn
    page_id, (alpha, beta, gamma) = _page(conn)
    with acting(AGENT):
        block_api.update_block(conn, alpha, text="alpha (agent)")
        block_api.update_block(conn, beta, text="beta (agent)")
        added = block_api.insert_blocks(conn, ["agent's new block"], after=gamma)["blocks"][0]["id"]
    block_api.update_block(conn, beta, text="beta (agent), fixed by hand")
    before = conn.execute("SELECT COUNT(*) FROM op_log").fetchone()[0]

    plan = sessions.undo_session(conn, AGENT.session, dry_run=True)
    assert not plan["applied"] and conn.execute("SELECT COUNT(*) FROM op_log").fetchone()[0] == before
    assert {item["id"]: item["fields"] for item in plan["reverted"]} == {alpha: ["text"], added: ["created"]}
    assert [(item["id"], item["fields"]) for item in plan["conflicts"]] == [(beta, ["text"])]
    # Each reverted block reports its text now, the text it goes back to and whether it stays
    # live afterwards (false: to Trash), for a before/after.
    texts = {item["id"]: (item["current"], item["text"], item["live"]) for item in plan["reverted"]}
    assert texts == {alpha: ("alpha (agent)", "alpha", True), added: ("agent's new block", "agent's new block", False)}

    result = sessions.undo_session(conn, AGENT.session)
    assert result["applied"] and result["transactions"] == 3 and result["reverted_count"] == 2
    assert result["conflict_count"] == 1 and "someone else" in result["conflicts"][0]["reasons"][0]
    assert _texts(conn, page_id) == ["alpha", "beta (agent), fixed by hand", "gamma"]
    assert conn.execute("SELECT deleted_at IS NOT NULL FROM blocks WHERE id=?", (added,)).fetchone()[0]
    # One new forward transaction, recorded under whoever asked (here the person).
    rows = conn.execute("SELECT DISTINCT txn_id, actor FROM op_log WHERE seq>?", (before,)).fetchall()
    assert [tuple(row) for row in rows] == [(result["txn_id"], "user")]
    # Undoing again finds nothing left and writes nothing.
    again = sessions.undo_session(conn, AGENT.session)
    assert not again["applied"] and again["reverted_count"] == 0
    assert conn.execute("SELECT COUNT(*) FROM op_log").fetchone()[0] == before + 1


def test_undo_stops_at_a_field_someone_else_changed_between_two_agent_edits(workspace):
    conn = workspace.conn
    page_id, (alpha, beta, _gamma) = _page(conn)
    with acting(AGENT):
        block_api.update_block(conn, alpha, text="agent 1")
    block_api.update_block(conn, alpha, text="person in between")
    with acting(AGENT):
        block_api.update_block(conn, alpha, text="agent 2")
        block_api.update_block(conn, beta, props=None, kind="to_do")
    result = sessions.undo_session(conn, AGENT.session)
    # The newest agent edit goes back to the person's text; the older one is not replayed over it.
    assert _texts(conn, page_id)[0] == "person in between"
    assert block_api.get_page(conn, page_id)["blocks"][1]["type"] == "text"
    assert [item["id"] for item in result["conflicts"]] == [alpha]


def test_undo_keeps_a_field_level_change_and_reverts_the_rest_of_the_block(workspace):
    conn = workspace.conn
    made = block_api.create_page(conn, "Tasks", blocks=[{"type": "to_do", "text": "call", "props": {"checked": False}}])
    task = made["blocks"][0]["id"]
    with acting(AGENT):
        block_api.update_block(conn, task, text="call the dentist", props={"checked": True})
    block_api.update_block(conn, task, props={"checked": False})
    result = sessions.undo_session(conn, AGENT.session)
    block = block_api.get_page(conn, made["page_id"])["blocks"][0]
    assert block["text"] == "call" and block["props"] == {"checked": False}
    assert result["applied"] and result["conflict_count"] == 0


def test_undo_brings_back_deleted_and_moved_blocks_and_trashes_created_pages(workspace):
    conn = workspace.conn
    page_id, (alpha, beta, gamma) = _page(conn)
    target = block_api.create_page(conn, "Elsewhere", blocks=["anchor"])
    with acting(AGENT):
        block_api.delete_block(conn, alpha)
        block_api.move_block(conn, gamma, page=target["page_id"])
        block_api.move_block(conn, beta, after=gamma)
        created = block_api.create_page(conn, "Agent page", blocks=["x", {"text": "y", "children": ["z"]}])
    assert _texts(conn, page_id) == []
    sessions.undo_session(conn, AGENT.session)
    assert _texts(conn, page_id) == ["alpha", "beta", "gamma"]
    assert _texts(conn, target["page_id"]) == ["anchor"]
    assert conn.execute("SELECT deleted_at IS NOT NULL FROM blocks WHERE id=?", (created["page_id"],)).fetchone()[0]


def test_undo_keeps_an_agent_block_the_person_wrote_inside(workspace):
    conn = workspace.conn
    page_id, _ids = _page(conn)
    with acting(AGENT):
        parent = block_api.append_blocks(conn, page_id, ["agent heading"])["blocks"][0]["id"]
    block_api.append_blocks(conn, page_id, ["my child"], parent=parent)
    result = sessions.undo_session(conn, AGENT.session)
    assert not result["applied"] and [item["id"] for item in result["conflicts"]] == [parent]
    assert "agent heading" in _texts(conn, page_id)


def test_an_agent_undoing_its_own_session_records_the_undo_beside_it(workspace):
    conn = workspace.conn
    _page_id, (alpha, *_rest) = _page(conn)
    with acting(AGENT):
        block_api.update_block(conn, alpha, text="oops")
        sessions.undo_session(conn, AGENT.session)
    assert _actors(conn)[-1] == ("agent", "claude-code", f"{AGENT.session}:undo")
    with pytest.raises(blocks.BlockError) as missing:
        sessions.undo_session(conn, "33333333-3333-4333-8333-333333333333")
    assert missing.value.status == 404
    with pytest.raises(blocks.BlockError):
        sessions.undo_session(conn, "not a session!")


# Pinned citations -----------------------------------------------------------------------

def test_a_pinned_citation_says_whether_its_block_changed_since_with_a_word_diff(workspace):
    conn = workspace.conn
    page_id, (alpha, beta, gamma) = _page(conn)
    seq = block_api.get_page(conn, page_id)["seq"]
    block_api.update_block(conn, alpha, text="alpha particle")
    block_api.delete_block(conn, gamma)
    tags = block_api.tag_block(conn, beta, "biology", create=True)  # tagging is not a content change
    assert tags
    found = {item["id"]: item for item in history.citations(conn, [f"(({alpha}@{seq}))", f"{beta}@{seq}", f"{gamma}@{seq}"])["citations"]}
    assert found[alpha]["status"] == "changed" and found[alpha]["seq"] > seq
    assert found[alpha]["then"]["text"] == "alpha" and found[alpha]["now"]["text"] == "alpha particle"
    assert found[alpha]["diff"] == [{"op": "equal", "text": "alpha"}, {"op": "insert", "text": " particle"}]
    assert found[beta]["status"] == "unchanged" and not found[beta]["changed"] and found[beta]["seq"] == seq
    assert found[gamma]["status"] == "deleted" and found[gamma]["changed"]
    early = history.citations(conn, [f"{alpha}@0"])["citations"][0]
    assert early["status"] == "unknown"
    with pytest.raises(blocks.BlockError):
        history.citations(conn, ["nonsense"])


def test_from_row_reads_a_peer_rows_carried_actors(workspace):
    row = {"actor": "peer", "actor_name": "Laptop", "actor_session": None,
           "actor_via": '[{"kind":"agent","name":"claude","session":"s1"},{"kind":"robot"}]'}
    assert from_row(row) == Actor("peer", "Laptop", None, (Actor("agent", "claude", "s1"),))


# Migration 051 --------------------------------------------------------------------------

def test_migration_051_classifies_the_rows_already_written_and_keeps_the_log_append_only(tmp_path):
    import shutil

    from kitty.db import connect, migrate
    from kitty.db.migrations import available_migrations

    before = tmp_path / "before"
    before.mkdir()
    for migration in available_migrations():
        if migration.version < 51:
            shutil.copy(migration.path, before / migration.path.name)
    conn = connect(tmp_path / "data" / "kitty.db")
    migrate(conn, before)
    assert not conn.execute("SELECT 1 FROM pragma_table_info('op_log') WHERE name='actor'").fetchone()
    page, block = "00000000-0000-4000-8000-0000000000a1", "00000000-0000-4000-8000-0000000000a2"
    # Written before the actor columns exist (as the block migrations do), then a conversion row,
    # an edit, a Trash purge and a merged row from a paired device.
    blocks.transact(conn, [{"op": "insert", "id": page, "type": "page", "parent": None, "text": "Old", "props": {"revision": 1}},
                           {"op": "insert", "id": block, "type": "text", "parent": page, "text": "x"}])
    conn.execute("UPDATE op_log_migrated SET last_seq=1")
    blocks.transact(conn, [{"op": "text", "id": block, "text": "y"}])
    blocks.transact(conn, [{"op": "delete", "id": block}])
    blocks.transact(conn, [{"op": "purge", "id": block}])
    conn.execute("INSERT INTO peers(device_id,name,public_key,paired_at) VALUES ('ffffffffffffffffffffffffffffffff','Laptop',x'00','t')")
    conn.execute("INSERT INTO op_log(device_id,txn_id,page_id,at,ops) VALUES "
                 "('ffffffffffffffffffffffffffffffff','t1',?,'t','{\"v\":1,\"ops\":[{\"op\":\"purge\",\"id\":\"x\"}]}')", (page,))
    conn.commit()
    migrate(conn)
    assert _actors(conn) == [("system", "migration", None), ("user", None, None), ("user", None, None),
                             ("system", "compaction", None), ("peer", "Laptop", None)]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE op_log SET actor='user'")
    conn.close()

"""The block API over HTTP (/api/blocks/*, kitty.web_blocks): behind the UI secret, pushed live."""
from __future__ import annotations

from contextlib import closing

import pytest

from kitty.core import block_api
from kitty.db import connect
from terminal_support import make_root, site
from test_web_blocks import Client


@pytest.fixture
def running(tmp_path):
    with site(make_root(tmp_path, ("workspace",))) as server:
        yield server


def test_reads_and_writes_round_trip_and_reach_open_windows(running):
    window = Client(running)
    try:
        status, made = running.request("/api/blocks/pages/create", {"title": "HTTP page", "blocks": ["alpha"]})
        assert status == 200 and made["revision"] == 1
        assert window.expect("change")["seq"] == made["seq"]
        status, added = running.request("/api/blocks/insert", {"page": made["number"], "blocks": [
            {"type": "to_do", "text": "beta", "props": {"checked": True}}], "revision": 1})
        assert status == 200 and added["revision"] == 2
        assert window.expect("change")["seq"] == added["seq"]
        status, updated = running.request("/api/blocks/update", {"id": made["blocks"][0]["id"], "text": "alpha!"})
        assert status == 200 and updated["revision"] == 3
        status, moved = running.request("/api/blocks/move", {"id": added["blocks"][0]["id"], "before": made["blocks"][0]["id"]})
        assert status == 200
        status, page = running.request(f"/api/blocks/page?page={made['page_id']}")
        assert status == 200 and [block["text"] for block in page["blocks"] if block["type"] != "canvas"] == ["beta", "alpha!"]
        status, found = running.request("/api/blocks/search?q=beta")
        assert status == 200 and [hit["block_id"] for hit in found["hits"]] == [added["blocks"][0]["id"]]
        assert found["hits"][0]["cite"] == f"(({added['blocks'][0]['id']}@{found['seq']}))"
        status, listed = running.request("/api/blocks/pages?q=http")
        assert status == 200 and [item["id"] for item in listed["pages"]] == [made["page_id"]]
        status, inbox = running.request("/api/blocks/inbox", {"blocks": [f"see {found['hits'][0]['cite']}"]})
        assert status == 200
        status, links = running.request(f"/api/blocks/backlinks?id={added['blocks'][0]['id']}")
        assert status == 200 and [item["block_id"] for item in links["references"]["blocks"]] == [inbox["blocks"][0]["id"]]
        status, deleted = running.request("/api/blocks/delete", {"id": made["blocks"][0]["id"]})
        assert status == 200 and deleted["seq"] > moved["seq"]
        status, stale = running.request("/api/blocks/update", {"id": added["blocks"][0]["id"], "text": "x", "revision": 1})
        assert status == 409
        status, refused = running.request("/api/blocks/insert", {"page": made["page_id"], "blocks": ["x"], "extra": 1})
        assert status == 400
    finally:
        window.close()


def test_block_routes_need_the_ui_secret(running):
    status, _ = running.request("/api/blocks/pages", headers={"X-Kitty-Secret": None})
    assert status == 401
    status, _ = running.request("/api/blocks/inbox", {"blocks": ["x"]}, headers={"X-Kitty-Secret": None})
    assert status == 401
    with closing(connect(running.app.settings.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM blocks WHERE json_extract(properties,'$.inbox')=1").fetchone()[0] == 0


def test_block_routes_are_absent_without_the_workspace_module(tmp_path):
    with site(make_root(tmp_path, ())) as server:
        status, _ = server.request("/api/blocks/pages")
        assert status == 403
        status, _ = server.request("/api/blocks/inbox", {"blocks": ["x"]})
        assert status == 403


def test_a_write_from_another_process_reaches_an_open_window(running):
    """What the MCP server and the CLI do: write the database directly; the live channel follows the log."""
    window = Client(running)
    try:
        with closing(connect(running.app.settings.db_path)) as conn:
            made = block_api.create_page(conn, "Written elsewhere", blocks=["from an agent"])
        change = window.expect("change")
        assert change["seq"] == made["seq"] and change["page_id"] == made["page_id"]
        assert any(op.get("text") == "from an agent" for op in change["ops"])
    finally:
        window.close()


def test_agent_writes_show_their_actor_live_in_history_and_one_session_undoes_over_http(running):
    from kitty.core.blocks.actor import Actor, acting

    agent = Actor("agent", "claude-code", "http-agent-session")
    window = Client(running)
    try:
        status, made = running.request("/api/blocks/pages/create", {"title": "Shared", "blocks": ["one", "two"]})
        assert status == 200 and window.expect("change")["actor"] == {"kind": "user", "name": None, "session": None}
        one, two = (item["id"] for item in made["blocks"])
        with closing(connect(running.app.settings.db_path)) as conn, acting(agent):
            block_api.update_block(conn, one, text="one (agent)")
            block_api.update_block(conn, two, text="two (agent)")
        assert window.expect("change")["actor"] == agent.as_dict()
        status, _ = running.request("/api/blocks/update", {"id": two, "text": "two (mine)"})
        assert status == 200
        status, seen = running.request(f"/api/blocks/history?page={made['page_id']}&actor=agent")
        assert status == 200 and [item["actor"]["session"] for item in seen["sessions"]] == [agent.session]
        status, every = running.request(f"/api/blocks/history?page={made['page_id']}")
        assert [item["actor"]["kind"] for item in every["sessions"]] == ["user", "agent", "user"]
        status, listed = running.request("/api/blocks/sessions")
        assert status == 200 and [item["session"] for item in listed["sessions"]] == [agent.session]
        status, plan = running.request("/api/blocks/sessions/undo", {"session": agent.session, "dry_run": True})
        assert status == 200 and not plan["applied"] and plan["reverted_count"] == 1 and plan["conflict_count"] == 1
        status, done = running.request("/api/blocks/sessions/undo", {"session": agent.session})
        assert status == 200 and done["applied"]
        undo = window.expect("change")
        while undo["txn_id"] != done["txn_id"]:
            undo = window.expect("change")
        assert undo["actor"]["kind"] == "user"
        status, page = running.request(f"/api/blocks/page?page={made['page_id']}")
        assert [block["text"] for block in page["blocks"] if block["type"] == "text"] == ["one", "two (mine)"]
        status, cited = running.request(f"/api/blocks/citations?cites={one}@{made['seq']},{two}@{made['seq']}")
        assert status == 200 and [item["status"] for item in cited["citations"]] == ["unchanged", "changed"]
        status, _ = running.request("/api/blocks/sessions/undo", {"session": agent.session, "extra": 1})
        assert status == 400
        status, _ = running.request("/api/blocks/history?page=" + made["page_id"] + "&actor=robot")
        assert status == 400
    finally:
        window.close()

"""The MCP notes tools (workspace module) through the protocol: reads cite, writes return seq and revision."""
from __future__ import annotations

import asyncio
import json
import typing

import pytest

from mcp import Client

from kitty.core import block_api
from kitty.mcp_server import BlockType, build_server


def _session(settings, script):
    async def run():
        async with Client(build_server(settings, ("workspace",))) as client:
            async def call(name, **arguments):
                result = await client.call_tool(name, arguments)
                text = result.content[0].text if result.content else ""
                return (json.loads(text) if not result.is_error else None), result.is_error, text

            return await script(client, call)

    return asyncio.run(run())


def test_block_types_match_the_core():
    assert set(typing.get_args(BlockType)) == block_api.WRITE_TYPES


def test_tools_have_schemas_agents_can_follow(workspace):
    async def script(client, call):
        return {tool.name: tool for tool in (await client.list_tools()).tools}

    tools = _session(workspace.settings, script)
    append = tools["append_blocks"].input_schema
    assert set(append["required"]) == {"page", "blocks"}
    schema = json.dumps(append)
    assert '"bulleted_list"' in schema and '"children"' in schema and '"checked"' in schema
    assert tools["get_page"].annotations.read_only_hint is True
    assert tools["delete_block"].annotations.destructive_hint is True
    assert tools["append_blocks"].annotations.destructive_hint is False


def test_a_read_search_write_round_trip(workspace):
    async def script(client, call):
        made, error, _ = await call("create_page", title="Agent page", blocks=[
            {"type": "heading_2", "text": "Findings"}, {"type": "bulleted_list", "text": "Photosynthesis uses light",
                                                      "children": ["chlorophyll absorbs red and blue"]}])
        assert not error and made["revision"] == 1 and isinstance(made["seq"], int)
        page, _, _ = await call("get_page", page=made["page_id"])
        by_number, _, _ = await call("get_page", page=made["number"])
        found, _, _ = await call("search_blocks", query="chlorophyll")
        cited = found["hits"][0]["cite"]
        appended, _, _ = await call("append_blocks", page=made["page_id"], blocks=[f"Source: {cited}"],
                                    revision=page["revision"])
        _, stale_error, stale_text = await call("append_blocks", page=made["page_id"], blocks=["late"], revision=1)
        links, _, _ = await call("get_backlinks", target=found["hits"][0]["block_id"])
        inbox, _, _ = await call("add_to_inbox", blocks=["remember this"])
        listed, _, _ = await call("list_pages", query="agent")
        _, bad_error, bad_text = await call("append_blocks", page=made["page_id"], blocks=[{"type": "canvas", "text": "x"}])
        return made, page, by_number, found, appended, stale_error, stale_text, links, inbox, listed, bad_error

    made, page, by_number, found, appended, stale_error, stale_text, links, inbox, listed, bad_error = _session(workspace.settings, script)
    assert by_number["page"]["id"] == page["page"]["id"] == made["page_id"]
    assert page["blocks"][1]["children"][0]["cite"] == f"(({page['blocks'][1]['children'][0]['id']}@{page['seq']}))"
    assert found["hits"][0]["page_title"] == "Agent page" and "**chlorophyll**" in found["hits"][0]["snippet"]
    assert appended["revision"] == 2 and appended["seq"] > made["seq"]
    assert stale_error and "409" in stale_text
    assert [item["block_id"] for item in links["references"]["blocks"]] == [appended["blocks"][0]["id"]]
    assert inbox["number"] and inbox["blocks"][0]["cite"].startswith("((")
    assert [item["title"] for item in listed["pages"]] == ["Agent page"]
    assert bad_error
    row = workspace.conn.execute("SELECT text FROM blocks WHERE id=?", (appended["blocks"][0]["id"],)).fetchone()
    assert row["text"] == f"Source: {found['hits'][0]['cite']}"


@pytest.mark.parametrize("modules", [(), ("applyai",)])
def test_notes_tools_need_the_workspace_module(workspace, modules):
    async def run():
        async with Client(build_server(workspace.settings, modules)) as client:
            names = [tool.name for tool in (await client.list_tools()).tools]
            try:
                outcome = await client.call_tool("get_page", {"page": 1})
            except Exception as exc:  # an unknown tool may surface as a protocol error
                outcome = exc
            return names, outcome

    names, outcome = asyncio.run(run())
    assert "get_page" not in names and "append_blocks" not in names
    assert isinstance(outcome, Exception) or outcome.is_error


def test_writes_are_one_agent_session_named_by_the_client_and_undo_agent_session_reverts_it(workspace):
    from mcp_types import Implementation

    from kitty.core.blocks.actor import USER, acting

    async def run():
        server = build_server(workspace.settings, ("workspace",))
        async with Client(server, client_info=Implementation(name="claude-code", version="9.9")) as client:
            async def call(name, **arguments):
                result = await client.call_tool(name, arguments)
                return json.loads(result.content[0].text), result.is_error

            made, _ = await call("create_page", title="Agent page", blocks=["first", "second"])
            first, second = (item["id"] for item in made["blocks"])
            await call("update_block", block=first, text="first, rewritten")
            await call("update_block", block=second, text="second, rewritten")
            with acting(USER):  # the person fixes one of them in a window
                block_api.update_block(workspace.conn, second, text="second, fixed by hand")
            listed, _ = await call("list_agent_sessions")
            cited, _ = await call("check_citations", cites=[f"(({first}@{made['seq']}))"])
            plan, _ = await call("undo_agent_session", dry_run=True)
            done, error = await call("undo_agent_session")
            return made, second, listed, cited, plan, done, error

    made, second, listed, cited, plan, done, error = asyncio.run(run())
    rows = workspace.conn.execute("SELECT DISTINCT actor, actor_name, actor_session FROM op_log WHERE page_id=? ORDER BY seq",
                                  (made["page_id"],)).fetchall()
    session = listed["current"]
    assert [tuple(row) for row in rows] == [("agent", "claude-code", session), ("user", None, None),
                                            ("agent", "claude-code", f"{session}:undo")]
    assert [item["session"] for item in listed["sessions"]] == [session]
    assert cited["citations"][0]["status"] == "changed"
    assert not plan["applied"] and plan["transactions"] == 3
    assert not error and done["applied"]
    assert {item["id"]: item["fields"] for item in done["conflicts"]} == {made["page_id"]: [], second: ["text"]}
    texts = [row[0] for row in workspace.conn.execute(
        "SELECT text FROM blocks WHERE page_id=? AND parent_id IS NOT NULL AND deleted_at IS NULL ORDER BY order_key", (made["page_id"],))]
    # The agent's untouched block goes to Trash; the page it created stays, holding the person's edit.
    assert texts == ["second, fixed by hand"]

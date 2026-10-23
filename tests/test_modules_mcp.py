"""The MCP server only offers, and only runs, the tools of the modules it started with."""

from __future__ import annotations

import asyncio
import inspect

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from kitty import mcp_server
from kitty.mcp_server import KittyTools, build_server
from kitty.modules import save_modules


def _tool_names(server) -> list[str]:
    async def listing():
        async with Client(server) as client:
            return [tool.name for tool in (await client.list_tools()).tools], client.instructions

    return asyncio.run(listing())


def _public_tools() -> list[str]:
    return [
        name for name, _member in inspect.getmembers(KittyTools, inspect.isfunction)
        if not name.startswith("_")
    ]



@pytest.mark.parametrize("modules", [(), ("workspace",)])
def test_no_tool_is_advertised_without_applyai(workspace, modules):
    names, instructions = _tool_names(build_server(workspace.settings, modules))
    assert names == []
    assert "applyai" in (instructions or "")


@pytest.mark.parametrize("modules", [(), ("workspace",)])
def test_disabled_tools_cannot_be_called_through_the_protocol(workspace, modules):
    async def call():
        async with Client(build_server(workspace.settings, modules)) as client:
            try:
                return await client.call_tool("today", {})
            except Exception as exc:                  # an unknown tool may surface as a protocol error
                return exc

    outcome = asyncio.run(call())
    assert isinstance(outcome, Exception) or outcome.is_error


def test_direct_calls_are_refused_before_any_feature_work(workspace, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("a disabled tool opened the workspace")

    monkeypatch.setattr(mcp_server, "open_workspace", forbidden)
    tools = KittyTools(workspace.settings, ())
    for name in _public_tools():
        method = getattr(tools, name)
        kwargs = {
            parameter: 1 if parameter.endswith("_id") else "x"
            for parameter, spec in inspect.signature(method).parameters.items()
            if spec.default is inspect.Parameter.empty
        }
        with pytest.raises(ToolError, match="applyai.*disabled|disabled.*applyai"):
            method(**kwargs)


def test_the_selection_is_frozen_for_the_server_lifetime(workspace):
    config_dir = workspace.settings.config_dir
    enabled = KittyTools(workspace.settings)
    disabled = KittyTools(workspace.settings, ())

    save_modules(config_dir, [])                       # pending edit: applyai off
    assert enabled.list_jobs()["total"] == 0           # the running server keeps its tools
    assert KittyTools(workspace.settings).modules == ()
    with pytest.raises(ToolError, match="disabled"):
        KittyTools(workspace.settings).list_jobs()

    save_modules(config_dir, ["applyai"])               # pending edit: applyai back on
    with pytest.raises(ToolError, match="disabled"):
        disabled.list_jobs()                            # ... but a server that started without it stays off


def test_other_config_still_reloads_while_modules_stay_frozen(workspace):
    tools = KittyTools(workspace.settings)
    save_modules(workspace.settings.config_dir, ["workspace"])
    (workspace.settings.config_dir / "apply.yaml").write_text("autofill_cap: 3\n", encoding="utf-8")
    plan = tools.preparation_plan()
    assert plan["cap"] == 3

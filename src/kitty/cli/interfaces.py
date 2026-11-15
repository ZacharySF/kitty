"""`kitty tui` and `kitty mcp`."""

from __future__ import annotations

from kitty.cli.common import _open, _settings, _state
from kitty.cli.mediated import _agent_client
from kitty.cli.root import app

# --------------------------------------------------------------------- tui


@app.command()
def tui() -> None:
    """Open the terminal UI (job screens need the applyai module; otherwise a core Modules screen)."""
    workspace = _open()
    modules = workspace.config.modules     # frozen for this process; the app reuses it
    workspace.close()          # the app opens its own connection
    from kitty.tui.shell import create_app

    create_app(demo=bool(_state["demo"]), modules=modules).run()


# ---- mcp


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio for an agent. Never submits; stdout is the protocol.

    In a rika! Agent tab (KITTY_AGENT_TOKEN) it serves the tab's mediated notes tools
    through rika!'s API and never opens the workspace."""
    remote = _agent_client()
    if remote is not None:
        from kitty.mcp_server import run_agent_stdio

        run_agent_stdio(remote)
        return
    workspace = _open()        # config problems go to stderr and exit before serving
    modules = workspace.config.modules     # frozen for this process; later edits wait for a restart
    workspace.close()          # each tool call opens its own connection
    from kitty.mcp_server import run_stdio

    run_stdio(_settings(), modules)

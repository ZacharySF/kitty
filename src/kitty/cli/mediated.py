"""Commands that work both locally and inside a rika! Agent tab.

With KITTY_AGENT_TOKEN set, a call goes through rika!'s mediated API (kitty.agent_client);
otherwise it runs on the workspace as this command's agent session.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import typer
from rich.markup import escape

from kitty.cli.common import _open, console, error_console


@contextmanager
def _blocks() -> Iterator:
    """A connection for one notes command, writing as its agent session; refusals print and exit 1."""
    from kitty.core.blocks import BlockError
    from kitty.core.blocks.actor import acting, cli_agent

    try:
        agent = cli_agent()
    except ValueError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    workspace = _open()
    try:
        with acting(agent):
            yield workspace.conn
    except BlockError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    finally:
        workspace.close()


def _agent_client():
    """The Agent tab's client (kitty.agent_client) when KITTY_AGENT_TOKEN is set, else None."""
    from kitty.agent_client import AgentClient

    try:
        return AgentClient.from_env()
    except ValueError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None


def _note_call(tool: str, local, **arguments) -> dict:
    """One notes command: tool `tool` through rika!'s API in an Agent tab, else `local` on the workspace."""
    from kitty.agent_client import AgentCallError

    remote = _agent_client()
    if remote is None:
        with _blocks() as conn:
            return local(conn)
    try:
        return remote.call(tool, arguments)
    except AgentCallError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red] (status {exc.status})")
        raise typer.Exit(code=1) from None


def _emit(result: dict, as_json: bool, summary: str) -> None:
    import json

    if as_json:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(summary)

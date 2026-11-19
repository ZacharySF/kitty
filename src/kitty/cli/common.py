"""State and output shared by every command: consoles, --demo, opening the workspace."""

from __future__ import annotations

import os

import typer
from rich.console import Console

from kitty.config import ConfigError
from kitty.core.workspace import Workspace, open_workspace
from kitty.settings import Settings

console = Console()
error_console = Console(stderr=True)

#: Set by the top-level callback, read by each command.
_state: dict[str, object] = {"demo": False}


def _settings() -> Settings:
    """Settings for this invocation, honouring --demo."""
    return Settings.load(demo=bool(_state["demo"]))


def _config_exit(exc: ConfigError) -> typer.Exit:
    """Print a config error's problems to stderr; return the exit to raise."""
    error_console.print("[bold red]Config problems:[/bold red]")
    for problem in exc.problems:
        error_console.print(f"  {problem}")
    error_console.print("\nRun [bold]kitty doctor[/bold] after fixing them.")
    return typer.Exit(code=1)


def _open() -> Workspace:
    """Open the workspace or exit with a readable config error."""
    try:
        return open_workspace(_settings())
    except ConfigError as exc:
        raise _config_exit(exc) from exc

#: The module whose commands, screens and tools need the careers config.
APPLYAI = "applyai"

def _agent_tab() -> bool:
    return bool(os.environ.get("KITTY_AGENT_TOKEN", "").strip())


def _module_error(lines: list[str]) -> typer.Exit:
    """Print an error to stderr and return the exit to raise."""
    for line in lines:
        error_console.print(line)
    return typer.Exit(code=1)


_JSON_OPTION = typer.Option("--json", help="Machine-readable output (with ((id@seq)) citations).")
#: How `notes show` marks each block type.

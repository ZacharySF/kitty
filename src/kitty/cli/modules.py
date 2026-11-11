"""`kitty modules`: choose which modules this workspace uses."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from kitty.cli.common import _config_exit, _settings, console, error_console
from kitty.cli.root import app
from kitty.config import ConfigError
from kitty.core import bootstrap
from kitty.modules import MODULE_IDS, available_modules, load_modules

modules_app = typer.Typer(no_args_is_help=True, help="Choose which modules this workspace uses.")
app.add_typer(modules_app, name="modules")


# ------------------------------------------------------------------ modules

_RESTART_NOTE = (
    "Changes apply the next time rika! starts: a running web server, TUI or MCP server "
    "keeps the modules it started with. Your data is never removed."
)


def _configured_modules() -> tuple[Path, tuple[str, ...]]:
    """The config directory and the build's module selection, or exit with a readable error."""
    config_dir = _settings().config_dir
    try:
        return config_dir, load_modules(config_dir)
    except ConfigError as exc:
        raise _config_exit(exc) from exc


def _change_modules(ids: list[str], *, enable: bool) -> None:
    """Persist `current + ids` (enable) or `current - ids` (disable) and say what happens next."""
    unknown = [item for item in ids if item not in MODULE_IDS]
    if unknown:
        error_console.print(
            f"[bold red]Unknown module {escape(', '.join(unknown))}.[/bold red] "
            f"Available: {', '.join(MODULE_IDS)}."
        )
        raise typer.Exit(code=2)
    config_dir, current = _configured_modules()
    wanted = {*current, *ids} if enable else set(current) - set(ids)
    selection = tuple(item for item in MODULE_IDS if item in wanted)
    if selection == current:
        console.print(f"Nothing to change: enabled modules are {', '.join(current) or 'none (core only)'}.")
        return
    try:
        saved = bootstrap.configure_modules(config_dir, selection)
    except (ValueError, OSError) as exc:
        error_console.print(f"[bold red]Could not save the modules in config/build.json:[/bold red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc
    console.print(f"Saved the build's modules in config/build.json: {', '.join(saved) or 'none (core only)'}.")
    console.print(f"[dim]{_RESTART_NOTE}[/dim]")


@modules_app.command("list")
def modules_list() -> None:
    """Show each module and whether the build (config/build.json) enables it."""
    _config_dir, enabled = _configured_modules()
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("module")
    table.add_column("state")
    table.add_column("what it adds", overflow="fold")
    for item in available_modules():
        on = item["id"] in enabled
        table.add_row(item["id"], "[green]enabled[/green]" if on else "[dim]disabled[/dim]", item["description"])
    console.print(table)
    console.print(f"\n[dim]{_RESTART_NOTE}[/dim]")


@modules_app.command("enable")
def modules_enable(
    ids: Annotated[list[str], typer.Argument(metavar="MODULE...", help=f"One or more of: {', '.join(MODULE_IDS)}.")],
) -> None:
    """Enable modules (takes effect when rika! next starts)."""
    _change_modules(ids, enable=True)


@modules_app.command("disable")
def modules_disable(
    ids: Annotated[list[str], typer.Argument(metavar="MODULE...", help=f"One or more of: {', '.join(MODULE_IDS)}.")],
) -> None:
    """Disable modules (takes effect when rika! next starts; data stays)."""
    _change_modules(ids, enable=False)

"""The top-level `kitty` group: module gating, the Agent-tab allowlist and global options."""

from __future__ import annotations

from typing import Annotated

import typer
from typer.core import TyperGroup

from kitty import __version__
from kitty.cli.common import _agent_tab, _module_error, _state, console
from kitty.config import ConfigError
from kitty.modules import load_modules
from kitty.settings import Settings

#: Top-level commands that belong to a module. Every other command is core and is
#: always listed and runnable: init, doctor, modules, tui and mcp (the last two show
#: or serve only what the enabled modules provide).
COMMAND_MODULES: dict[str, str] = dict.fromkeys(
    (
        "add", "status", "list", "fav", "unfav", "favorites", "discover", "refresh", "apply",
        "autofill", "triage", "coach", "today", "report", "export", "finder", "schedule",
        "resumes", "import", "demo", "gmail", "proposals", "questions",
    ),
    "applyai",
) | {"notes": "workspace"}
#: What `kitty` runs inside a rika! Agent tab (KITTY_AGENT_TOKEN set): the commands that
#: are clients of rika!'s mediated API. Everything else needs the database, which the
#: tab cannot open.
AGENT_TAB_COMMANDS = ("notes", "mcp", "effects")


class _ModuleGroup(TyperGroup):
    """The top-level group: lists and runs only the commands of enabled modules.

    The selection is read from config/build.json when the command line is resolved,
    which happens before any feature code, so a disabled command cannot reach it. A
    build file that cannot be read leaves only the core commands (doctor and
    modules show what is wrong).
    """

    @staticmethod
    def _enabled() -> tuple[str, ...]:
        """The enabled modules for this process; empty when the build file is unreadable."""
        try:
            return load_modules(Settings.load().config_dir)
        except ConfigError:
            return ()

    def list_commands(self, ctx):
        if _agent_tab():
            return [name for name in super().list_commands(ctx) if name in AGENT_TAB_COMMANDS]
        enabled = self._enabled()
        return [
            name for name in super().list_commands(ctx)
            if COMMAND_MODULES.get(name) in (None, *enabled)
        ]

    def resolve_command(self, ctx, args):
        if _agent_tab():
            # rika! checks the modules itself; the tab cannot read the workspace.
            if args and args[0] not in AGENT_TAB_COMMANDS:
                raise _module_error([
                    f"[bold red]In a rika! Agent tab, kitty runs only {', '.join(AGENT_TAB_COMMANDS)}[/bold red]",
                    "They reach rika!'s notes and effects through its API with this tab's KITTY_AGENT_TOKEN.",
                ])
            return super().resolve_command(ctx, args)
        module = COMMAND_MODULES.get(args[0]) if args else None
        if module is not None:
            try:
                enabled = load_modules(Settings.load().config_dir)
            except ConfigError as exc:
                raise _module_error(
                    ["[bold red]Cannot read config/build.json:[/bold red]",
                     *[f"  {problem}" for problem in exc.problems],
                     "\nFix it, or run [bold]kitty doctor[/bold]."],
                ) from exc
            if module not in enabled:
                raise _module_error([
                    f"[bold red]`kitty {args[0]}` belongs to the {module} module, which is not enabled.[/bold red]",
                    f"Enable it with [bold]kitty modules enable {module}[/bold] and start rika! again; "
                    "your data is kept either way.",
                ])
        return super().resolve_command(ctx, args)


app = typer.Typer(
    cls=_ModuleGroup,
    add_completion=False,
    no_args_is_help=True,
    help="Personal job-search tracker and workspace: modules you enable add the job tracker "
         "(applyai) and notes, study and planning tools (workspace).",
)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    demo: Annotated[bool, typer.Option("--demo", help="Use data/demo.db instead of the real database.")] = False,
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    """Global options."""
    _state["demo"] = demo
    if version:
        console.print(f"kitty {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()

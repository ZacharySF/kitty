"""`kitty init` and `kitty doctor`."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.table import Table

from kitty.cli.common import (
    APPLYAI,
    _config_exit,
    _open,
    _settings,
    console,
    error_console,
)
from kitty.cli.root import app
from kitty.config import ConfigError
from kitty.core import bootstrap
from kitty.core import doctor as doctor_core
from kitty.modules import BUILD_FILE, LEGACY_MODULES_FILE, MODULE_IDS

# ------------------------------------------------------------------- init


@app.command()
def init(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Take defaults, ask nothing.")] = False,
    module: Annotated[Optional[list[str]], typer.Option(
        "--module", "-m",
        help=f"Select this module ({', '.join(MODULE_IDS)}) when the workspace has no "
             "config/build.json yet; repeat for several. Default for a new workspace: none, core only.",
    )] = None,
) -> None:
    """Set up the workspace: modules, config files, the database, and (applyai) your profile."""
    settings = _settings()
    console.print(f"[bold]Workspace:[/bold] {settings.root}")

    try:
        before = any(settings.config_path(name).exists() for name in (BUILD_FILE, LEGACY_MODULES_FILE))
        enabled = bootstrap.initialize_modules(settings, list(module) if module else None)
        results = bootstrap.copy_examples(settings, enabled)
    except ConfigError as exc:
        raise _config_exit(exc) from exc
    except ValueError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=2) from exc
    if module and before:
        console.print("  kept     config/build.json (change it with `kitty modules enable|disable`)")
    console.print(f"  modules  {', '.join(enabled) if enabled else 'none (core only)'}")
    for result in results:
        if result.action == "created":
            console.print(f"  created  config/{result.filename}")
        elif result.action == "kept":
            console.print(f"  kept     config/{result.filename} (unchanged)")
    env_action = bootstrap.ensure_env_file(settings)
    console.print(f"  {'created ' if env_action == 'created' else 'kept    '} .env")

    applyai = APPLYAI in enabled
    if applyai and not yes:
        console.print("\n[bold]A few questions for config/profile.yaml[/bold] (Enter keeps the current value)")
        answers: dict[str, str] = {}
        for key, question in bootstrap.PROFILE_QUESTIONS:
            answers[key] = typer.prompt(f"  {question}", default="", show_default=False)
        bootstrap.write_profile(settings, answers)
        console.print("  wrote    config/profile.yaml")

    workspace = _open()
    try:
        if applyai:
            from kitty.core.workspace import sync_config_to_db

            counts = sync_config_to_db(workspace.conn, workspace.config)
            console.print(
                f"\n[bold]Database:[/bold] {workspace.settings.db_path} "
                f"({counts['tracks']} tracks, {counts['sources']} sources, "
                f"{counts['watchlist']} watchlist companies)"
            )
        else:
            console.print(f"\n[bold]Database:[/bold] {workspace.settings.db_path}")
    finally:
        workspace.close()

    console.print("\n[bold]Try these next:[/bold]")
    console.print("  kitty doctor                     # check everything is wired up")
    console.print("  kitty modules list               # see which modules are on")
    if applyai:
        console.print("  kitty --demo demo reset          # build a throwaway database")
        console.print("  kitty --demo tui                 # learn the TUI on fake data")
    else:
        console.print("  kitty modules enable applyai     # add the job tracker (takes effect on restart)")


# ----------------------------------------------------------------- doctor


@app.command()
def doctor() -> None:
    """Check config, database, keys, resumes and source health."""
    settings = _settings()
    checks = doctor_core.run_all(settings)

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("")
    table.add_column("check")
    table.add_column("detail", overflow="fold")
    for check in checks:
        colour = "green" if check.ok and not check.warning else ("yellow" if check.ok else "red")
        table.add_row(f"[{colour}]{check.symbol}[/{colour}]", check.name, check.detail)
        if check.fix and (check.warning or not check.ok):
            table.add_row("", "", f"[dim]fix: {check.fix}[/dim]")
    console.print(table)

    failed = doctor_core.failures(checks)
    warnings = [c for c in checks if c.ok and c.warning]
    console.print(
        f"\n{len(checks) - len(failed) - len(warnings)} ok, {len(warnings)} warning(s), "
        f"{len(failed)} failure(s)"
    )
    if failed:
        raise typer.Exit(code=1)

"""`kitty demo`: the throwaway demo database."""

from __future__ import annotations

import typer

from kitty.cli.common import console, error_console
from kitty.cli.root import app
from kitty.config import ConfigError, load_config
from kitty.core import demo as demo_core
from kitty.settings import Settings

demo_app = typer.Typer(no_args_is_help=True, help="The throwaway demo database.")
app.add_typer(demo_app, name="demo")


# -------------------------------------------------------------------- demo


@demo_app.command("reset")
def demo_reset() -> None:
    """Rebuild data/demo.db with fake jobs and applications."""
    settings = Settings.load(demo=True)
    try:
        config = load_config(settings.config_dir)
    except ConfigError as exc:
        error_console.print("[bold red]Config problems:[/bold red]")
        for problem in exc.problems:
            error_console.print(f"  {problem}")
        raise typer.Exit(code=1) from exc

    stats = demo_core.reset_demo(settings, config)
    console.print(
        f"[green]Rebuilt[/green] {settings.db_path} with {stats['jobs']} jobs, "
        f"{stats['applications']} applications, {stats['resumes']} resumes, "
        f"{stats['questions']} screening questions."
    )
    console.print("Open it with: [bold]kitty --demo tui[/bold]")

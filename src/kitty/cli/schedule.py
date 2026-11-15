"""`kitty schedule`: run discovery on a timer."""

from __future__ import annotations

from typing import Annotated

import typer

from kitty.cli.common import _open, _settings, error_console
from kitty.cli.root import app

schedule_app = typer.Typer(no_args_is_help=True, help="Run discovery on a timer.")
app.add_typer(schedule_app, name="schedule")


# --------------------------------------------------------------- schedule


@schedule_app.command("print")
def schedule_print(
    style: Annotated[str, typer.Option("--format", help="home-manager | systemd | cron")] = "home-manager",
    with_triage: Annotated[bool, typer.Option(
        "--with-triage", help="Also run paid triage using the configured provider (spends up to the cap).",
    )] = False,
) -> None:
    """Print the config that runs `kitty discover` twice a day. Nothing is installed."""
    from kitty.core import schedule

    builders = {"home-manager": schedule.home_manager, "systemd": schedule.systemd_units, "cron": schedule.crontab}
    if style not in builders:
        error_console.print(f"Unknown format '{style}'. Use one of: {', '.join(builders)}")
        raise typer.Exit(code=1)
    if with_triage:
        workspace = _open()
        try:
            text = builders[style](workspace.settings.root, with_triage=True,
                                   provider=workspace.config.triage.provider)
        finally:
            workspace.close()
    else:
        text = builders[style](_settings().root)
    print(text, end="")

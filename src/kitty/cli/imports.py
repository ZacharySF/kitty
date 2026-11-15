"""`kitty import`: import past applications."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from kitty.cli.common import _open, console, error_console
from kitty.cli.root import app
from kitty.core import importer as importer_core

import_app = typer.Typer(no_args_is_help=True, help="Import past applications.")
app.add_typer(import_app, name="import")


# ------------------------------------------------------------------ import


@import_app.command("csv")
def import_csv(
    file: Annotated[Path, typer.Argument(help="CSV of past applications.")],
    map_option: Annotated[Optional[list[str]], typer.Option("--map", help="field=column, repeatable.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")] = False,
) -> None:
    """Import past applications from a CSV. Shows a preview before writing."""
    if not file.exists():
        error_console.print(f"[red]No such file: {file}[/red]")
        raise typer.Exit(code=1)

    overrides: dict[str, str] = {}
    for pair in map_option or []:
        if "=" not in pair:
            error_console.print(f"[red]--map expects field=column, got '{pair}'[/red]")
            raise typer.Exit(code=1)
        field_name, column = pair.split("=", 1)
        overrides[field_name.strip()] = column.strip()

    workspace = _open()
    try:
        plan = importer_core.plan_import(
            workspace.conn, file, overrides=overrides,
            valid_tracks=workspace.config.track_names,
        )
        console.print(f"[bold]Mapping[/bold] (field -> column): {plan.mapping}")
        if plan.unmapped_columns:
            console.print(f"[dim]Ignored columns: {', '.join(plan.unmapped_columns)}[/dim]")

        table = Table(box=None, header_style="bold", title="First 10 rows")
        for column in ("line", "action", "company", "title", "status", "applied", "track", "problem"):
            table.add_column(column, overflow="fold")
        for row in plan.rows[:10]:
            table.add_row(
                str(row.line),
                row.action if row.ok else "[red]error[/red]",
                row.company, row.title, row.status,
                (row.applied_at or "now")[:10], row.track or "-",
                row.problem or "",
            )
        console.print(table)
        console.print(
            f"\nWould create [bold]{len(plan.creatable)}[/bold] job/application pair(s), "
            f"skip {len(plan.already_present)} already tracked, "
            f"and could not read {len(plan.problems)} row(s)."
        )
        if not plan.creatable:
            console.print("Nothing to import.")
            raise typer.Exit()
        if not yes and not typer.confirm("Import these rows?", default=True):
            console.print("Cancelled; nothing was written.")
            raise typer.Exit()

        stats = importer_core.apply_import(workspace.conn, plan)
        console.print(
            f"[green]Imported[/green] {stats['applications_created']} application(s), "
            f"{stats['jobs_created']} new job(s), {stats['events']} status event(s), "
            f"{stats['skipped']} row(s) skipped."
        )
    finally:
        workspace.close()

"""`kitty backup`: one archive of the whole workspace, previewed and restored into a new folder."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from kitty.cli.common import _settings, console, error_console
from kitty.cli.root import app

backup_app = typer.Typer(no_args_is_help=True, help=(
    "Whole-workspace backup: one archive file (notes, files, tasks, study, Career; no keys or sign-ins), "
    "restored into a new folder."))
app.add_typer(backup_app, name="backup")


def _path(value: str) -> Path:
    return Path(value).expanduser().absolute()


def _refused(error) -> typer.Exit:
    error_console.print(f"[bold red]{escape(str(error))}[/bold red]")
    return typer.Exit(code=1)


def _counts(counts: dict, files: dict) -> None:
    for label, number in counts.items():
        console.print(f"  {label:<20} {number}")
    console.print(f"  {'Files':<20} {files.get('count', 0)} ({_size(files.get('bytes', 0))})")


def _size(number: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if number < 1024 or unit == "GB":
            return f"{number:.0f} {unit}" if unit == "bytes" else f"{number:.1f} {unit}"
        number /= 1024
    return f"{number} bytes"


def _excluded(items: list[dict]) -> None:
    console.print("Excluded:")
    for item in items:
        console.print(f"  {escape(item['category'])}: {escape(item['reason'])}")


@backup_app.command("create")
def backup_create(
    out: Annotated[str | None, typer.Option("--out", help="A folder (default ~/rika-backups) or a new .zip path.")] = None,
) -> None:
    """Write one archive: a database snapshot, the files rows point at and the settings files."""
    from kitty.core import backup

    try:
        made = backup.create(_settings(), _path(out) if out else backup.default_folder())
    except backup.BackupError as error:
        raise _refused(error) from None
    console.print(f"[green]Backed up to[/green] {escape(made['path'])} ({_size(made['bytes'])})")
    _counts(made["counts"], made["files"])
    _excluded(made["excluded"])
    for item in made["missing"]:
        console.print(f"[yellow]Missing when backed up: {escape(item)}[/yellow]")


@backup_app.command("preview")
def backup_preview(
    archive: Annotated[str, typer.Argument(help="The backup file.")],
    into: Annotated[str | None, typer.Option("--into", help="The folder a restore would write (new or empty).")] = None,
) -> None:
    """Show what a restore would bring back and what would stop it; writes nothing. Exit 1 if it cannot restore."""
    from kitty.core import backup

    settings = _settings()
    try:
        seen = backup.preview(_path(archive), _path(into) if into else None, live_root=settings.root)
    except backup.BackupError as error:
        raise _refused(error) from None
    console.print(f"Backup from {escape(str(seen['created_at']))}, rika! {escape(str(seen['app_version']))}, "
                  f"format {seen['format']}")
    _counts(seen["counts"], seen["files"])
    _excluded(seen["excluded"])
    for warning in seen["warnings"]:
        console.print(f"[yellow]{escape(warning)}[/yellow]")
    for problem in seen["problems"]:
        error_console.print(f"[bold red]{escape(problem)}[/bold red]")
    if not seen["can_restore"]:
        raise typer.Exit(code=1)


@backup_app.command("restore")
def backup_restore(
    archive: Annotated[str, typer.Argument(help="The backup file.")],
    into: Annotated[str, typer.Option("--into", help="A new or empty folder; never the open workspace.")],
) -> None:
    """Restore into a separate folder. The open workspace is not touched; switching to the copy is up to you."""
    from kitty.core import backup

    settings = _settings()
    try:
        done = backup.restore(_path(archive), _path(into), live_root=settings.root)
    except backup.BackupError as error:
        raise _refused(error) from None
    console.print(f"[green]Restored to[/green] {escape(done['path'])}")
    _counts(done["counts"], done["files"])
    for warning in done["warnings"]:
        console.print(f"[yellow]{escape(warning)}[/yellow]")
    console.print("Not restored: keys, passwords and sign-ins (add them again), paired devices and programs (pair again).")
    console.print("Open it (this workspace stays as it is):")
    console.print(f"  desktop app: {escape(done['open'])}")
    console.print(f"  website and CLI: {escape(done['open_cli'])}")

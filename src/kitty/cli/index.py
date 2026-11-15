"""`kitty index`: check or rebuild the full-text search index."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape

from kitty.cli.common import _open, console
from kitty.cli.root import app

# ------------------------------------------------------------------ index

index_app = typer.Typer(no_args_is_help=True, help="The full-text search index: check it against a rebuild, or rebuild it.")
app.add_typer(index_app, name="index")


@index_app.command("check")
def index_check(
    repair: Annotated[bool, typer.Option("--repair/--no-repair", help="Rewrite documents the check finds wrong.")] = True,
    source: Annotated[str, typer.Option("--from", help="Rebuild blocks from the op log and snapshots (log) or the projection.")] = "log",
) -> None:
    """Rebuild the index from scratch, compare it with the incremental one; exit 1 on any difference."""
    from kitty.core import search

    if source not in {"log", "projection"}:
        raise typer.BadParameter("--from is log or projection")
    workspace = _open()
    try:
        report = search.check(workspace.conn, repair=repair, blocks=source)
    finally:
        workspace.close()
    console.print(f"{report['docs']} documents rebuilt from the {source} in {report['seconds']} s; "
                  f"{len(report['mismatches'])} mismatched, {report['repaired']} repaired, {len(report['drift'])} log drift; "
                  f"FTS5 integrity: " + ", ".join(f"{name} {'ok' if ok else 'FAILED'}" for name, ok in report["fts"].items()))
    for item in report["mismatches"][:20]:
        console.print(f"  {item['problem']}: {item['kind']} {escape(item['ref'])}")
    for item in report["unreplayable"][:20]:
        console.print(f"  [red]page {escape(item['page_id'])} cannot be replayed: {escape(item['error'])}[/red]")
    if report["missing_triggers"]:
        console.print(f"  [red]missing triggers: {', '.join(report['missing_triggers'])}[/red]")
    if not report["ok"]:
        raise typer.Exit(code=1)
    console.print("[green]The incremental index equals the rebuild.[/green]")


@index_app.command("rebuild")
def index_rebuild() -> None:
    """Replace every document with one made from the current data."""
    import time

    from kitty.core import search

    workspace = _open()
    try:
        started = time.perf_counter()
        count = search.build(workspace.conn)
        stats = search.stats(workspace.conn)
    finally:
        workspace.close()
    console.print(f"Indexed {count} documents in {time.perf_counter() - started:.2f} s: "
                  + ", ".join(f"{kind} {number}" for kind, number in stats["documents"].items()))

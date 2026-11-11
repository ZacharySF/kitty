"""`kitty today`, `report`, `export` and `finder`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.table import Table

from kitty.cli.common import _open, console, error_console
from kitty.cli.root import app
from kitty.dates import now_iso

# ------------------------------------------------------------------ today


@app.command()
def today(
    notify: Annotated[bool, typer.Option(
        "--notify", help="Also push the digest via ntfy or Telegram, if .env sets one up.",
    )] = False,
) -> None:
    """The daily digest: due reminders, deadlines, new A/B jobs, weekly progress, source errors."""
    from kitty.core import notify as notify_core
    from kitty.core import sources as sources_core
    from kitty.core import today as today_core

    workspace = _open()
    try:
        digest = today_core.build_digest(workspace.conn, workspace.config)
        text = today_core.digest_text(digest)
        console.print(escape(text), highlight=False)
        if not notify:
            return
        run_id = sources_core.start_run(workspace.conn, "digest")
        result = notify_core.send_digest(text)
        if result is None:
            console.print("\n[dim]Not sent: no notifier set up (NTFY_TOPIC or TELEGRAM_* in .env).[/dim]")
        elif " failed: " in result:
            error_console.print(f"\n[yellow]{escape(result)}[/yellow]")
        else:
            console.print(f"\n[green]{escape(result)}[/green]")
        sources_core.finish_run(workspace.conn, run_id, stats={
            "reminders": len(digest.reminders), "deadlines": len(digest.deadlines),
            "source_problems": len(digest.source_problems), "notify": result or "not configured",
        })
    finally:
        workspace.close()


# ---------------------------------------------------------- report / export


@app.command()
def report() -> None:
    """Response rates by resume, track, source and tier."""
    from kitty.core import stats as stats_core

    workspace = _open()
    try:
        sections = stats_core.response_report(workspace.conn)
    finally:
        workspace.close()
    if not any(sections.values()):
        console.print("No applications yet. Mark jobs applied (a on the Apply tab) and this fills in.")
        return
    for name, rates in sections.items():
        table = Table(title=f"By {name}", title_justify="left")
        for column in (name, "applied", "heard back", "rate", "OA+", "OA+ rate", "offers", "open"):
            table.add_column(column, justify="left" if column == name else "right")
        for rate in rates:
            table.add_row(escape(rate.key), str(rate.applications), str(rate.responses),
                          f"{rate.response_rate:.0%}", str(rate.advanced), f"{rate.advance_rate:.0%}",
                          str(rate.offers), str(rate.open_count))
        console.print(table)


@app.command()
def export(
    fmt: Annotated[str, typer.Option(
        "--format", "-f", help="csv (three files) or json (one file).",
    )] = "csv",
    out: Annotated[Optional[Path], typer.Option(
        "--out", "-o", help="Folder for csv, file for json. Default: data/export/.",
    )] = None,
) -> None:
    """Export every job, application and status change (CSV cells are formula-safe)."""
    from kitty.core import export as export_core

    if fmt not in ("csv", "json"):
        error_console.print("--format must be csv or json")
        raise typer.Exit(2)
    workspace = _open()
    try:
        stamp = now_iso()[:10]
        base = workspace.settings.data_dir / "export"
        if fmt == "csv":
            result = export_core.export_csv(workspace.conn, out or base / stamp)
        else:
            result = export_core.export_json(workspace.conn, out or base / f"kitty-{stamp}.json")
    finally:
        workspace.close()
    for path in result.paths:
        console.print(f"wrote {path}")
    console.print(", ".join(f"{name}: {count}" for name, count in result.counts.items()))



# ----------------------------------------------------------------- finder


@app.command()
def finder(
    cap: Annotated[Optional[int], typer.Option(
        "--cap", min=0, help="Most firms to propose this run (default 15).",
    )] = None,
    list_only: Annotated[bool, typer.Option(
        "--list", help="Only show the pending proposals; fetch nothing.",
    )] = False,
) -> None:
    """Find niche companies matching config/skills.yaml and propose them for the watchlist."""
    from kitty.core import finder as finder_core

    workspace = _open()
    try:
        if not list_only:
            try:
                result = finder_core.run_finder(
                    workspace.settings, workspace.config, cap=cap,
                    progress=lambda message: console.print(f"[dim]{escape(message)}[/dim]"),
                )
            except finder_core.FinderError as exc:
                error_console.print(f"[red]{escape(str(exc))}[/red]")
                raise typer.Exit(code=1) from exc
            _print_finder_sources(result)
            _print_candidates(f"Proposed this run ({len(result.proposed)} of cap {result.cap})", result.proposed)
            for why, count in result.skipped.items():
                console.print(f"  [dim]skipped, {escape(why)}: {count}[/dim]")
        else:
            _print_candidates("Pending proposals", finder_core.list_candidates(workspace.conn))
        counts = finder_core.decision_counts(workspace.conn)
        console.print(f"\n{counts['pending']} pending, {counts['approved']} approved, "
                      f"{counts['rejected']} rejected. Approve or reject on the Finder screen (F9) "
                      "in [bold]kitty tui[/bold].")
    finally:
        workspace.close()


def _print_finder_sources(result) -> None:
    """Each candidate source's honest status: a failed source never reads as '0 found'."""
    table = Table(title="Candidate sources", box=None, header_style="bold")
    for column in ("source", "status", "firms", "problem / note"):
        table.add_column(column, overflow="fold")
    for name, report in result.adapters.items():
        colour = {"ok": "green", "partial": "yellow"}.get(report.status, "red")
        found = str(report.found) if report.status in ("ok", "partial") else "-"
        table.add_row(name, f"[{colour}]{report.status}[/]", found,
                      escape((report.error or report.stats.get("note") or "")[:160]))
    console.print(table)


def _print_candidates(title: str, candidates: list) -> None:
    """Proposed firms, one block each: id, name, area, score, board, then the reason line."""
    console.print(f"\n[bold]{title}[/bold]" + ("" if candidates else ": none"))
    for candidate in candidates:
        board = (f"{candidate.ats_kind}:{candidate.ats_slug}  {candidate.board_url}"
                 if candidate.ats_kind else "[dim]no board found[/dim]")
        score = f"{candidate.score:g}" if candidate.score is not None else "-"
        console.print(f"  [bold]{candidate.id:>3}  {escape(candidate.display_name)}[/bold]  "
                      f"{candidate.area or '-'}  score {score}  {board}")
        console.print(f"       {escape(candidate.reason or '')}", soft_wrap=True)

"""`kitty triage` and `kitty coach`."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.markup import escape

from kitty.cli.common import _open, console, error_console
from kitty.cli.root import app

# ----------------------------------------------------------------- triage


@app.command()
def triage(
    now: Annotated[bool, typer.Option("--now", help="Send jobs one at a time now (small runs).")] = False,
    batch: Annotated[bool, typer.Option("--batch", help="Send one Message Batch (half price, results within 24 h).")] = False,
    collect: Annotated[bool, typer.Option("--collect", help="Apply batches that have finished.")] = False,
    rules: Annotated[bool, typer.Option("--rules", help="Only set the free rules tiers (no key, no cost).")] = False,
    track: Annotated[Optional[str], typer.Option("--track", help="Only jobs on this track.")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", help="Send at most this many jobs.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation (for scheduled runs).")] = False,
) -> None:
    """Tier jobs with the LLM, pick a resume, and note what to check. Shows the cost first.

    With no flag it only shows what a run would do and cost.
    """
    from kitty.core.triage import run as triage_core

    if sum((now, batch, collect, rules)) > 1:
        error_console.print("Choose one of --now, --batch, --collect, --rules.")
        raise typer.Exit(code=1)
    workspace = _open()
    try:
        config, settings = workspace.config, workspace.settings
        if track and config.track(track) is None:
            error_console.print(f"[red]Unknown track '{track}'. Known: {', '.join(config.track_names)}[/red]")
            raise typer.Exit(code=1)
        if rules:
            from kitty.core.triage.rules import apply_rules_tiers

            console.print(f"Rules tiers set or updated on {apply_rules_tiers(workspace.conn, config)} job(s).")
            return
        provider = config.triage.provider
        if provider == "openrouter" and (batch or collect):
            error_console.print("OpenRouter triage supports --now only; --batch and --collect require provider: anthropic.")
            raise typer.Exit(code=1)
        if collect:
            if not triage_core.pending_batches(workspace.conn):
                console.print("No batches waiting.")
                return
            results = triage_core.collect_batches(workspace.conn, config, settings,
                                                  triage_core.make_client(settings))
            for result in results:
                _print_triage_result(result)
            return

        plan = triage_core.plan_triage(workspace.conn, config, settings, track=track, limit=limit)
        cap = config.triage.max_cost_usd_per_run
        console.print(f"[bold]{len(plan.to_send)}[/bold] job(s) to send via {provider} to {escape(config.triage.model)}, "
                      f"{len(plan.cached)} with a saved answer (free), {len(plan.rules)} tiered by rules (free).")
        if provider == "openrouter":
            from kitty.core.triage import openrouter

            estimates = [("now", openrouter.plan_estimate(plan, config))]
        else:
            estimates = [(label, triage_core.plan_estimate(plan, config, batch=is_batch))
                         for label, is_batch in (("now", False), ("batch", True))]
        for label, estimate in estimates:
            console.print(f"  {label:<6} about {estimate.input_tokens:,} input + {estimate.output_tokens:,} output "
                          f"tokens, at most [bold]${estimate.usd:.2f}[/bold]")
        console.print(f"  [dim]Conservative token and price bounds; "
                      f"a run stops at the ${cap:.2f} cap in config/triage.yaml.[/dim]")
        for label, pending in triage_core.pending_batches(workspace.conn):
            action = ("switch provider to anthropic, then kitty triage --collect"
                      if provider == "openrouter" else "kitty triage --collect")
            console.print(f"  [yellow]batch {pending['batch_id']} is still waiting: {action}[/yellow]")
        if not (now or batch):
            console.print("Run it with --now." if provider == "openrouter" else "Run it with --now or --batch.")
            return
        if not plan.to_send and not plan.cached and not plan.rules:
            console.print("Nothing to triage.")
            return
        client = triage_core.make_client(settings) if plan.to_send and provider == "anthropic" else None
        if plan.to_send and not yes and not typer.confirm(f"Spend up to ${cap:.2f}?"):
            raise typer.Exit()
        if provider == "openrouter":
            result = openrouter.run_now(
                workspace.conn, config, plan, settings,
                progress=lambda message: console.print(f"[dim]{escape(message)}[/dim]"),
            )
        elif now:
            result = triage_core.run_now(workspace.conn, config, plan, client,
                                         progress=lambda message: console.print(f"[dim]{escape(message)}[/dim]"))
        else:
            result = triage_core.submit_batch(workspace.conn, config, plan, client)
        _print_triage_result(result)
    except triage_core.TriageError as exc:
        error_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    finally:
        workspace.close()


def _print_triage_result(result) -> None:
    """What a triage run did, including what it cost and whether the cap stopped it."""
    if result.pending and result.mode == "collect":
        console.print(f"Batch {result.batch_id} is still processing; try --collect later.")
        return
    console.print(f"\n[bold]{result.mode}[/bold]: {result.applied} triaged by the model, "
                  f"{result.cached_applied} from saved answers, {result.rules_set} by rules; "
                  f"cost ${result.cost_usd:.4f}")
    if result.batch_id and result.mode == "batch":
        console.print(f"Batch {result.batch_id} sent with {result.sent} job(s). "
                      "Results take up to 24 h: run [bold]kitty triage --collect[/bold].")
    if result.cap_hit:
        console.print(f"[yellow]Stopped at the cost cap; {result.skipped_for_cap} job(s) left for the next run.[/yellow]")
    if result.notes_dropped:
        console.print(f"[dim]{result.notes_dropped} edit note(s) dropped for not quoting the posting "
                      f"or resume exactly.[/dim]")
    for error in result.errors[:10]:
        console.print(f"  [red]{escape(error)}[/red]")


# ------------------------------------------------------------------ coach


@app.command()
def coach(
    track: Annotated[Optional[str], typer.Option("--track", help="Only this track.")] = None,
    top: Annotated[int, typer.Option("--top", help="Terms to show per list.")] = 12,
) -> None:
    """What each track's postings ask for that its resume does / doesn't mention. Statistics only."""
    from kitty.core.resume_coach import coach as run_coach

    workspace = _open()
    try:
        if track and workspace.config.track(track) is None:
            error_console.print(f"[red]Unknown track '{track}'. Known: {', '.join(workspace.config.track_names)}[/red]")
            raise typer.Exit(code=1)
        for report in run_coach(workspace.conn, workspace.config, workspace.settings, track=track, top=top):
            console.print(f"\n[bold]{report.track}[/bold]  resume [bold]{escape(report.resume_label or '-')}[/bold]"
                          f"  [dim]{report.postings} postings[/dim]")
            if report.error:
                console.print(f"  [red]{escape(report.error)}[/red]")
                continue
            for stat in report.missing:
                console.print(f"  [yellow]{escape(stat.term)}[/yellow] {stat.share:.0%} of postings, not on your resume"
                              f"  [dim]e.g. {escape(stat.example_company or '')}: \"{escape(stat.example_snippet or '')}\"[/dim]")
            for stat in report.covered:
                console.print(f"  [green]{escape(stat.term)}[/green] {stat.share:.0%}, on your resume:"
                              f" [dim]{escape(stat.resume_line or '')}[/dim]")
        console.print("\n[dim]Add a term only if it is true. Coach is read-only; "
                      "`kitty resumes improve` creates a local candidate for explicit review.[/dim]")
    finally:
        workspace.close()

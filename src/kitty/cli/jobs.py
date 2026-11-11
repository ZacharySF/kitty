"""Adding, listing, starring and discovering jobs."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.table import Table

from kitty.cli.common import _open, console, error_console
from kitty.cli.root import app
from kitty.core import applications as applications_core
from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.core import urlmeta
from kitty.core.workspace import Workspace
from kitty.dates import now_iso, parse_user_date
from kitty.models import PIPELINE_STATUSES

# -------------------------------------------------------------- add a job


@app.command()
def add(
    url: Annotated[str, typer.Argument(help="The job posting URL.")],
    company: Annotated[Optional[str], typer.Option("--company", "-c")] = None,
    title: Annotated[Optional[str], typer.Option("--title", "-t")] = None,
    location: Annotated[Optional[str], typer.Option("--location", "-l")] = None,
    track: Annotated[Optional[str], typer.Option("--track", help="Track name from tracks.yaml.")] = None,
    tier: Annotated[Optional[str], typer.Option("--tier", help="A, B or C.")] = None,
    deadline: Annotated[Optional[str], typer.Option("--deadline", help="YYYY-MM-DD, 'today', '+7'.")] = None,
    fetch: Annotated[bool, typer.Option("--fetch/--no-fetch", help="Fetch the page title to fill gaps.")] = False,
    applied: Annotated[bool, typer.Option("--applied", help="Also record that you applied.")] = False,
    resume: Annotated[Optional[str], typer.Option("--resume", help="Resume label used.")] = None,
) -> None:
    """Add a job by URL. Missing fields are asked for, never guessed silently."""
    workspace = _open()
    try:
        conn = workspace.conn
        existing = jobs_core.find_by_url(conn, url)
        if existing:
            console.print(f"Already tracked as job {existing.id}: {existing.company_name} - {existing.title}")
            raise typer.Exit()

        if fetch and (not company or not title):
            meta = urlmeta.fetch_page_meta(url)
            if meta.error:
                console.print(f"[yellow]Could not read the page:[/yellow] {meta.error}")
            else:
                company = company or meta.company_guess
                title = title or meta.title_guess
                console.print(f"[dim]page title:[/dim] {meta.page_title}")

        company = company or typer.prompt("Company")
        title = title or typer.prompt("Title")
        if location is None:
            location = typer.prompt("Location", default="", show_default=False) or None

        if track and track not in workspace.config.track_names:
            error_console.print(
                f"[red]Unknown track '{track}'.[/red] tracks.yaml has: "
                f"{', '.join(workspace.config.track_names)}"
            )
            raise typer.Exit(code=1)

        job, created = jobs_core.upsert_job(
            conn, company=company, title=title, url=url, location=location,
            source="manual", tracks=[track] if track else (), match_reason="added by hand",
            deadline_at=parse_user_date(deadline) if deadline else None,
        )
        if tier:
            jobs_core.set_tier(conn, job.id, tier.upper(), reason="set by hand", tier_source="manual")

        console.print(
            f"[green]{'Added' if created else 'Updated'}[/green] job {job.id}: "
            f"{job.company_name} - {job.title}"
        )

        if applied:
            resume_row = resumes_core.get_resume_by_label(conn, resume) if resume else None
            if resume and resume_row is None:
                error_console.print(f"[yellow]No resume labelled '{resume}'; recording without one.[/yellow]")
            application = applications_core.record_application(
                conn, job.id, resume_id=resume_row.id if resume_row else None, track=track,
            )
            console.print(f"[green]Applied[/green] recorded as application {application.id}")
    finally:
        workspace.close()


# ------------------------------------------------------------------ status


@app.command()
def status(
    job_id: Annotated[int, typer.Argument(help="Job id (shown in the TUI and by `kitty list`).")],
    new_status: Annotated[str, typer.Argument(help=f"One of: {', '.join(PIPELINE_STATUSES)}, withdrawn.")],
    when: Annotated[Optional[str], typer.Option("--when", help="YYYY-MM-DD, 'today', '+3'. Default now.")] = None,
    note: Annotated[Optional[str], typer.Option("--note")] = None,
) -> None:
    """Update a job's application status (creates the application if needed)."""
    workspace = _open()
    try:
        conn = workspace.conn
        job = jobs_core.get_job(conn, job_id)
        if job is None:
            error_console.print(f"[red]No job with id {job_id}.[/red]")
            raise typer.Exit(code=1)

        occurred = parse_user_date(when) if when else now_iso()
        if when and not occurred:
            error_console.print(f"[red]Could not read the date '{when}'.[/red]")
            raise typer.Exit(code=1)

        application = applications_core.get_application_for_job(conn, job_id)
        track = job.tracks[0] if job.tracks else None
        if new_status == "applied":
            # Stage 4: "applied" also schedules the follow-ups from config/apply.yaml.
            from kitty.core import followups as followups_core

            application = followups_core.mark_applied(
                conn, workspace.config, job_id, track=track, resume_id=job.resume_id,
                applied_at=occurred,
            )
            if application.status == "applied":
                days = ", ".join(f"+{day}" for day in workspace.config.apply.followup_days)
                console.print(f"[dim]Follow-up reminders: {days} days (config/apply.yaml).[/dim]")
        else:
            if application is None:
                application = applications_core.record_application(
                    conn, job_id, track=track, resume_id=job.resume_id, applied_at=occurred,
                )
                console.print("[dim]No application yet; recorded one first.[/dim]")
            applications_core.set_status(
                conn, application.id, new_status, occurred_at=occurred, evidence=note
            )
        console.print(
            f"[green]{job.company_name} - {job.title}[/green] is now "
            f"[bold]{new_status}[/bold]"
        )
    finally:
        workspace.close()


@app.command(name="list")
def list_jobs_command(
    track: Annotated[Optional[str], typer.Option("--track")] = None,
    tier: Annotated[Optional[str], typer.Option("--tier")] = None,
    applied: Annotated[bool, typer.Option("--applied", help="Only jobs you applied to.")] = False,
    favorites: Annotated[bool, typer.Option("--favorites", help="Only your favorites (★).")] = False,
    best: Annotated[bool, typer.Option("--best", help="Best first, like the Inbox and Jobs tabs.")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 30,
) -> None:
    """List tracked jobs, newest first (or best first with --best)."""
    workspace = _open()
    try:
        config = workspace.config
        found = jobs_core.list_jobs(
            workspace.conn,
            jobs_core.JobFilter(
                tracks=[track] if track else None,
                tiers=[tier.upper()] if tier else None,
                applied_only=applied or None,
                favorites_only=favorites or None,
                limit=limit,
                order="best" if best else "newest",
                prefer_cities=config.prefer_cities,
                prefer_remote=config.profile.open_to_remote,
            ),
        )
        _print_jobs(found)
    finally:
        workspace.close()


def _print_jobs(found: list) -> None:
    """A job table: ★ marks favorites; 'like' is the like-your-favorites score."""
    table = Table(box=None, header_style="bold")
    for column in ("", "id", "company", "title", "location", "tracks", "tier", "state", "like"):
        table.add_column(column, overflow="fold")
    for job in found:
        table.add_row(
            "[yellow]★[/yellow]" if job.is_favorite else "", str(job.id), escape(job.company_name),
            escape(job.title), escape(job.location or "-"), ",".join(job.tracks) or "-",
            job.tier or "-", job.status or job.queue_state,
            "-" if job.like_score is None else f"{job.like_score:+.2f}",
        )
    console.print(table)
    console.print(f"\n{len(found)} job(s)")


# -------------------------------------------------------------- favorites


@app.command()
def fav(job_ids: Annotated[list[int], typer.Argument(help="Job ids to star.")]) -> None:
    """Star jobs you like. Autofill fills favorites; discovery ranks look-alikes higher."""
    from kitty.core import favorites as favorites_core

    workspace = _open()
    try:
        _report_missing(workspace, job_ids)
        changed = favorites_core.favorite(workspace.conn, job_ids)
        for job_id in changed:
            job = jobs_core.get_job(workspace.conn, job_id)
            console.print(f"[yellow]★[/yellow] {job_id}  {escape(job.company_name)} - {escape(job.title)}")
        console.print(f"{len(changed)} starred; like scores updated.")
    finally:
        workspace.close()


@app.command()
def unfav(job_ids: Annotated[list[int], typer.Argument(help="Job ids to unstar.")]) -> None:
    """Unstar jobs."""
    from kitty.core import favorites as favorites_core

    workspace = _open()
    try:
        _report_missing(workspace, job_ids)
        changed = favorites_core.unfavorite(workspace.conn, job_ids)
        console.print(f"{len(changed)} unstarred: {' '.join(map(str, changed)) or 'none were favorites'}.")
    finally:
        workspace.close()


@app.command(name="favorites")
def favorites_command(
    closed: Annotated[bool, typer.Option("--closed", help="Also closed postings.")] = False,
) -> None:
    """List your favorites, best first."""
    from kitty.core import favorites as favorites_core

    workspace = _open()
    try:
        _print_jobs(favorites_core.list_favorites(workspace.conn, workspace.config, include_closed=closed))
    finally:
        workspace.close()


def _report_missing(workspace: Workspace, job_ids: list[int]) -> None:
    """Warn about ids that are not jobs (the rest still go through)."""
    for job_id in job_ids:
        if jobs_core.get_job(workspace.conn, job_id) is None:
            error_console.print(f"[red]No job with id {job_id}.[/red]")


# --------------------------------------------------------------- discover


@app.command()
def discover(
    source: Annotated[Optional[list[str]], typer.Option(
        "--source", "-s", help="Only run these sources (repeatable). Default: every enabled one.",
    )] = None,
) -> None:
    """Fetch new postings from the enabled sources, route and filter them, and store them."""
    from kitty.core.discovery import pipeline
    from kitty.core.discovery.report import build_report

    workspace = _open()
    unknown = [name for name in source or [] if name not in pipeline.ADAPTERS]
    if unknown:
        error_console.print(f"Unknown source(s): {', '.join(unknown)}. "
                            f"Known: {', '.join(pipeline.ADAPTERS)}")
        workspace.close()
        raise typer.Exit(code=1)
    try:
        result = pipeline.run_discover(
            workspace.settings, workspace.config, only=source or None,
            progress=lambda message: console.print(f"[dim]{message}[/dim]"),
        )
        _print_discover_report(build_report(workspace.conn, result.run_id, workspace.config.watchlist))
    finally:
        workspace.close()


@app.command()
def refresh(
    limit: Annotated[int, typer.Option("--limit", min=1, max=100,
        help="At most N stored postings, favorites first (maximum 100).")] = 10,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List the selection; make no public requests or fact updates.")] = False,
    track: Annotated[Optional[str], typer.Option("--track", help="Only stored jobs in this track.")] = None,
    job: Annotated[Optional[list[int]], typer.Option("--job", help="Check these exact stored job IDs (repeatable), including closed jobs.")] = None,
) -> None:
    """Refresh public evidence for favorites, then best-ranked jobs. Never discovers whole boards."""
    from kitty.core import refresh as refresh_core
    from kitty.core.evidence import chosen_facts

    workspace = _open()
    try:
        if track and track not in workspace.config.track_names:
            raise ValueError(f"Unknown track: {track}")
        selected = refresh_core.select_jobs(workspace.conn, limit=limit, track=track, job_ids=job)
        if dry_run:
            console.print("Would refresh (no requests or evidence changes):")
            for candidate in selected:
                console.print(f"  {candidate.id}  {escape(candidate.company_name)} - "
                              f"{escape(candidate.title)}  [{candidate.freshness}]"
                              f"{'  favorite' if candidate.favorited_at else ''}")
            return
        results = refresh_core.refresh_jobs(workspace.conn, workspace.config, workspace.settings,
                                           limit=limit, track=track, job_ids=job)
        for result in results:
            current = jobs_core.get_job(workspace.conn, result.job_id)
            console.print(f"{result.job_id}  {escape(current.company_name)} - {escape(current.title)}: "
                          f"{current.freshness} ({result.check.status}) — {escape(result.check.reason)}")
            description = chosen_facts(workspace.conn, result.job_id).get("jd_text")
            if description:
                console.print(f"  JD: {escape(description['source'])} at {description['observed_at']} "
                              f"{escape(description['source_url'] or '')}")
        console.print(f"Checked {len(results)} stored posting(s); no paid calls, no submissions.")
    except ValueError as exc:
        error_console.print(escape(str(exc)))
        raise typer.Exit(code=2) from exc
    finally:
        workspace.close()


def _print_discover_report(report) -> None:
    """The first-sync summary: counts by source, track and eligibility, then why jobs were filtered."""
    table = Table(title="New jobs by source", box=None, header_style="bold")
    for column in ("source", "status", "fetched", "new", "merged", "seen again", "closed", "unrouted", "problem"):
        table.add_column(column, overflow="fold")
    for name, source in report.sources.items():
        colour = {"ok": "green", "partial": "yellow"}.get(source["status"], "red")
        table.add_row(
            name, f"[{colour}]{source['status']}[/]", str(source["fetched"]), str(source["new"]),
            str(source["merged"]), str(source["seen_again"]), str(source["closed"]),
            str(source["unrouted"]), (source.get("error") or "")[:120],
        )
    console.print(table)
    console.print(f"\nRe-judged stored jobs with the current rules: {report.rejudged} changed eligibility")

    for title, counts in (("New jobs by track", report.new_by_track),
                          ("New jobs by eligibility", report.new_by_eligibility)):
        console.print(f"\n[bold]{title}[/bold]")
        for name, count in counts.most_common():
            console.print(f"  {name:<12} {count}")

    console.print("\n[bold]Top filtered-out reasons (new jobs)[/bold]")
    for index, reason in enumerate(report.top_filtered, start=1):
        console.print(f"  {index:>2}. {escape(reason.rule)}  [bold]{reason.count}[/bold]")
        console.print(f"      e.g. {escape(reason.example_company)} - {escape(reason.example_title)}")
        console.print(f"           {escape(reason.example_reason)}")
        console.print(f"           [dim]{escape(reason.example_url)}[/dim]")

    console.print("\n[bold]\\[F] companies with an open posting[/bold]")
    if not report.freshman_open:
        console.print("  none right now")
    for company in report.freshman_open:
        console.print(f"  {escape(company.company)}: {company.open_postings} open, "
                      f"{company.not_filtered} not filtered out")
        for example in company.examples:
            console.print(f"      {escape(example)}")

"""The command line. A thin layer over `kitty.core`.

Printing and prompting live here; every decision and every write lives in the
core package, so the TUI and a future MCP server behave identically.
"""

from __future__ import annotations

import signal
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from typer.core import TyperGroup

from kitty import __version__
from kitty.config import ConfigError, load_config
from kitty.core import applications as applications_core
from kitty.core import bootstrap, demo as demo_core, doctor as doctor_core
from kitty.core import importer as importer_core
from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.core import urlmeta
from kitty.core.workspace import Workspace, open_workspace
from kitty.dates import now_iso, parse_user_date
from kitty.models import PIPELINE_STATUSES
from kitty.modules import BUILD_FILE, LEGACY_MODULES_FILE, MODULE_IDS, available_modules, load_modules
from kitty.settings import Settings

#: The module whose commands, screens and tools need the careers config.
APPLYAI = "applyai"

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


def _module_error(lines: list[str]) -> typer.Exit:
    """Print an error to stderr and return the exit to raise."""
    for line in lines:
        error_console.print(line)
    return typer.Exit(code=1)


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
        enabled = self._enabled()
        return [
            name for name in super().list_commands(ctx)
            if COMMAND_MODULES.get(name) in (None, *enabled)
        ]

    def resolve_command(self, ctx, args):
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
resumes_app = typer.Typer(no_args_is_help=True, help="Register, improve locally, review and version resumes.")
import_app = typer.Typer(no_args_is_help=True, help="Import past applications.")
demo_app = typer.Typer(no_args_is_help=True, help="The throwaway demo database.")
app.add_typer(resumes_app, name="resumes")
app.add_typer(import_app, name="import")
app.add_typer(demo_app, name="demo")
schedule_app = typer.Typer(no_args_is_help=True, help="Run discovery on a timer.")
app.add_typer(schedule_app, name="schedule")
modules_app = typer.Typer(no_args_is_help=True, help="Choose which modules this workspace uses.")
app.add_typer(modules_app, name="modules")

console = Console()
error_console = Console(stderr=True)

#: Set by the top-level callback, read by each command.
_state: dict[str, object] = {"demo": False}


def _settings() -> Settings:
    """Settings for this invocation, honouring --demo."""
    return Settings.load(demo=bool(_state["demo"]))


def _config_exit(exc: ConfigError) -> typer.Exit:
    """Print a config error's problems to stderr; return the exit to raise."""
    error_console.print("[bold red]Config problems:[/bold red]")
    for problem in exc.problems:
        error_console.print(f"  {problem}")
    error_console.print("\nRun [bold]kitty doctor[/bold] after fixing them.")
    return typer.Exit(code=1)


def _open() -> Workspace:
    """Open the workspace or exit with a readable config error."""
    try:
        return open_workspace(_settings())
    except ConfigError as exc:
        raise _config_exit(exc) from exc


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


# ------------------------------------------------------------------ apply


@app.command()
def apply(
    job_ids: Annotated[Optional[list[int]], typer.Argument(help="Job ids to open and fill.")] = None,
    queued: Annotated[bool, typer.Option("--queued", help="Every queued job you have not applied to.")] = False,
    top: Annotated[Optional[int], typer.Option("--top", help="The N best new/queued jobs you have not applied to.")] = None,
    track: Annotated[Optional[str], typer.Option("--track", help="Only jobs in this track (with --queued / --top).")] = None,
    headless: Annotated[bool, typer.Option("--headless", help="Use an invisible browser that closes afterwards.")] = False,
    no_watch: Annotated[bool, typer.Option("--no-watch", help="Do not watch the tabs for your submit afterwards.")] = False,
    include_ineligible: Annotated[bool, typer.Option(
        "--include-ineligible",
        help="Explicit compatibility research on ineligible jobs only; never ready, authenticated or watched.",
    )] = False,
) -> None:
    """Open each job's application form in Firefox and fill it. You review and submit.

    Afterwards it watches the tabs (config/apply.yaml watch_hours) and records each
    application when its tab shows the board's confirmation page. Ctrl-C stops watching.
    """
    from kitty.core.apply.filler import EligibilityOverride, fill_jobs
    from kitty.core.apply.watch import Watch

    workspace = _open()
    try:
        config = workspace.config
        if track and config.track(track) is None:
            error_console.print(f"[red]Unknown track '{track}'. Known: {', '.join(config.track_names)}[/red]")
            raise typer.Exit(code=1)
        chosen = list(job_ids or [])
        if queued or top:
            picked = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(
                order="best", prefer_cities=config.prefer_cities,
                prefer_remote=config.profile.open_to_remote,
                queue_states=(["queued", "not_eligible"] if queued and not top else ["new", "queued", "not_eligible"])
                if include_ineligible else (["queued"] if queued and not top else ["new", "queued"]),
                eligibility=None if include_ineligible else ["boosted", "eligible", "ambiguous", "unknown"],
                not_applied=True, tracks=[track] if track else None, limit=top,
            ))
            chosen += [job.id for job in picked if job.id not in chosen]
        if not chosen:
            error_console.print("Nothing to fill: give job ids, --queued or --top N.")
            raise typer.Exit(code=1)

        if include_ineligible:
            console.print("[bold yellow]Compatibility research explicitly enabled for ineligible jobs.[/bold yellow] "
                          "They cannot become ready, use account automation, or enter submission watching.")

        def filled(reports) -> None:
            for report in reports:
                _print_fill_report(report)
            _print_fill_causes(reports)
            console.print("\n[bold yellow]Nothing was submitted.[/bold yellow] Review eligible applications "
                          "before submitting yourself. Ineligible tabs, if explicitly requested, are research only.")

        def applied(tab) -> None:
            days = ", ".join(f"+{day}" for day in config.apply.followup_days)
            console.print(f"[bold green]{tab.job_id}  {escape(tab.label)}: recorded as applied[/bold green]"
                          f" [dim](follow-ups at {days} days)[/dim]")

        watching = not (no_watch or headless) and config.apply.watch_hours > 0
        watch = Watch(
            hours=config.apply.watch_hours, on_applied=applied,
            on_start=lambda count: console.print(
                f"\n[bold]Watching {count} tab{'s' if count != 1 else ''} for submissions; "
                "Ctrl-C to stop[/bold]"),
        ) if watching else None
        try:
            with _interrupt_on_hangup():
                fill_jobs(
                    workspace.settings, config, chosen, headless=headless, on_filled=filled,
                    watch=watch, progress=lambda message: console.print(f"[dim]{escape(message)}[/dim]"),
                    eligibility_override=EligibilityOverride.COMPATIBILITY_RESEARCH if include_ineligible else None,
                )
        except KeyboardInterrupt:
            console.print("\nStopped.")
        if watch is not None and watch.tabs:
            ids = " ".join(str(tab.job_id) for tab in watch.tabs)
            console.print(f"Not seen submitted: {ids}. After you submit, mark it: "
                          "[bold]kitty status <id> applied[/bold]")
        elif watch is None and not include_ineligible:
            console.print("After you submit an eligible application, mark it: [bold]kitty status <id> applied[/bold]")
    finally:
        workspace.close()


@contextmanager
def _interrupt_on_hangup() -> Iterator[None]:
    """Turn SIGHUP (terminal window closed) and SIGTERM into KeyboardInterrupt meanwhile.

    By default Python dies on both without running `finally` blocks, so the
    Firefox remote-control session would never be ended, and Firefox keeps a
    session until it restarts (core.apply.bidi.SessionBusy).
    """
    def interrupt(signum, frame) -> None:
        raise KeyboardInterrupt

    caught = [sig for sig in (getattr(signal, "SIGHUP", None), signal.SIGTERM) if sig is not None]
    previous = {sig: signal.signal(sig, interrupt) for sig in caught}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _print_fill_report(report) -> None:
    """One job's fill result: what was filled, what is left for me."""
    colour = {"ready": "green", "partial": "yellow", "attempted": "cyan", "blocked": "yellow"}.get(report.status, "red")
    console.print(f"\n[bold]{report.job_id}  {escape(report.company)} - {escape(report.title)}[/bold]"
                  f"  [{colour}]{report.status}[/]")
    console.print(f"  cause: {escape((report.cause or 'unsupported_controls').replace('_', ' '))}")
    console.print(f"  {report.ats}  [dim]{escape(report.url)}[/dim]")
    console.print(f"  resume (uploaded or retained): {escape(report.resume_label or 'not verified')}")
    if report.resumed:
        console.print("  resumed the existing application tab")
    console.print(f"  pages inspected: {report.pages}")
    console.print(f"  filled: {escape(', '.join(report.filled) or 'nothing')}")
    if report.unanswered:
        console.print(f"  left for you ({len(report.unanswered)}):")
        for question in report.unanswered:
            console.print(f"    - {escape(question)}")
    if report.note:
        console.print(f"  note: {escape(report.note)}")
    if report.error:
        console.print(f"  [red]error: {escape(report.error)}[/red]")


def _print_fill_causes(reports) -> None:
    counts = Counter(report.cause for report in reports)
    if counts:
        console.print("\n[bold]Preparation outcomes[/bold]: " + "; ".join(
            f"{escape((cause or 'unsupported_controls').replace('_', ' '))}: {count}"
            for cause, count in sorted(counts.items(), key=lambda item: item[0] or "")
        ))


@app.command()
def autofill(
    limit: Annotated[Optional[int], typer.Option(
        "--limit", help="Fill at most N favorites (never more than autofill_cap in config/apply.yaml).",
    )] = None,
    track: Annotated[Optional[str], typer.Option(
        "--track", help="Only favorites in this track (e.g. parttime).",
    )] = None,
    no_watch: Annotated[bool, typer.Option("--no-watch", help="Do not watch the tabs for your submit afterwards.")] = False,
    detach: Annotated[bool, typer.Option(
        "--detach", help="Run on its own (closing this terminal does not stop it); output in data/autofill.log.",
    )] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only list what it would fill; open nothing.")] = False,
) -> None:
    """Fill your favorites' application forms in background tabs, best first. You submit.

    Skips closed, applied and skipped / not eligible jobs. Existing tabs are resumed;
    account, verification and unsupported steps stay blocked with reasons.
    Pauses autofill_pause_seconds between jobs; pushes a summary when .env sets
    up ntfy or Telegram. Then watches the tabs like `kitty apply` (Ctrl-C stops).
    """
    from kitty.core.apply import autofill as autofill_core
    from kitty.core.apply.watch import Watch

    workspace = _open()
    try:
        config = workspace.config
        if track and track not in config.track_names:
            error_console.print(f"[red]Unknown track {escape(track)}[/red]; tracks: {', '.join(config.track_names)}")
            raise typer.Exit(code=2)
        if dry_run:
            plan = autofill_core.plan_run(workspace.conn, config, limit=limit, track=track)
            console.print("[bold]Would fill, in this order[/bold]" if plan.job_ids else "Nothing to fill.")
            for job_id in plan.job_ids:
                job = jobs_core.get_job(workspace.conn, job_id)
                console.print(f"  {job_id}  {escape(job.company_name)} - {escape(job.title)}")
            _print_left_out(plan)
            return
        if detach:
            arguments = [*(["--demo"] if workspace.settings.demo else []), "autofill",
                         *(["--limit", str(limit)] if limit else []), *(["--track", track] if track else []),
                         *(["--no-watch"] if no_watch else [])]
            pid, log_path = autofill_core.start_detached(workspace.settings, arguments)
            console.print(f"Autofill runs on its own (pid {pid}). Output: [bold]{log_path}[/bold]")
            return

        def filled(result) -> None:
            if result.failure:
                error_console.print(f"[red]{escape(result.failure)}[/red]")
                return
            for report in result.reports:
                _print_fill_report(report)
            _print_fill_causes(result.reports)
            _print_left_out(result.plan)
            if not result.plan.job_ids:
                console.print("\nNothing to fill.")
            else:
                console.print("\n[bold yellow]Nothing was submitted.[/bold yellow] Review each tab in the "
                              "rika! Firefox and click submit yourself.")
            if result.pushed:
                console.print(f"[dim]summary pushed: {escape(result.pushed)}[/dim]")

        watching = not no_watch and config.apply.watch_hours > 0
        watch = Watch(
            hours=config.apply.watch_hours,
            on_applied=lambda tab: console.print(
                f"[bold green]{tab.job_id}  {escape(tab.label)}: recorded as applied[/bold green]"),
            on_start=lambda count: console.print(
                f"\n[bold]Watching {count} tab{'s' if count != 1 else ''} for submissions; Ctrl-C to stop[/bold]"),
        ) if watching else None
        try:
            with _interrupt_on_hangup():
                result = autofill_core.run_autofill(
                    workspace.settings, config, limit=limit, track=track, watch=watch, on_filled=filled,
                    progress=lambda message: console.print(f"[dim]{escape(message)}[/dim]"),
                )
        except KeyboardInterrupt:
            console.print("\nStopped.")
            return
        if result.failure:
            raise typer.Exit(code=1)
        if watch is not None and watch.tabs:
            ids = " ".join(str(tab.job_id) for tab in watch.tabs)
            console.print(f"Not seen submitted: {ids}. After you submit, mark it: "
                          "[bold]kitty status <id> applied[/bold]")
    finally:
        workspace.close()


def _print_left_out(plan) -> None:
    """Favorites an autofill run does not fill, each with the reason."""
    if not plan.left:
        return
    console.print("\n[bold]Not filled[/bold]")
    for left in plan.left:
        console.print(f"  {left.job_id}  {escape(left.company)} - {escape(left.title)}: {escape(left.reason)}")


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


# ----------------------------------------------------------------- resumes


@resumes_app.command("add")
def resumes_add(
    file: Annotated[Path, typer.Argument(help="Path to the resume file.")],
    label: Annotated[Optional[str], typer.Option("--label", help="Short name, e.g. quant.")] = None,
    description: Annotated[Optional[str], typer.Option("--description", help="What this version emphasises.")] = None,
    tracks: Annotated[Optional[str], typer.Option("--tracks", help="Comma-separated default tracks.")] = None,
) -> None:
    """Register a resume file: its path, its hash, and a description you write."""
    if not file.exists():
        error_console.print(f"[red]No such file: {file}[/red]")
        raise typer.Exit(code=1)

    workspace = _open()
    try:
        resolved_label = label or typer.prompt("Label (short, e.g. quant)", default=file.stem)
        resolved_description = description or typer.prompt(
            "One line: what does this version emphasise?", default="", show_default=False
        )
        track_names = (
            [t.strip() for t in tracks.split(",") if t.strip()]
            if tracks
            else [
                t.strip()
                for t in typer.prompt(
                    f"Default tracks ({', '.join(workspace.config.track_names)})",
                    default="", show_default=False,
                ).split(",")
                if t.strip()
            ]
        )
        unknown = [t for t in track_names if t not in workspace.config.track_names]
        if unknown:
            error_console.print(f"[red]Unknown track(s): {', '.join(unknown)}[/red]")
            raise typer.Exit(code=1)

        resume = resumes_core.add_resume(
            workspace.conn, resolved_label, file,
            description=resolved_description or None, default_tracks=track_names,
        )
        console.print(
            f"[green]Registered[/green] {resume.label} -> {resume.file_path} "
            f"(sha256 {resume.file_hash[:12]})"
        )
    finally:
        workspace.close()


@resumes_app.command("list")
def resumes_list() -> None:
    """List registered resumes and whether their files still match."""
    workspace = _open()
    try:
        problems = {
            resume.label: problem
            for resume, problem in resumes_core.check_files(workspace.conn)
        }
        table = Table(box=None, header_style="bold")
        for column in ("label", "file", "tracks", "description", "state"):
            table.add_column(column, overflow="fold")
        for resume in resumes_core.list_resumes(workspace.conn):
            table.add_row(
                resume.label, resume.file_path, ",".join(resume.default_tracks) or "-",
                resume.description or "-",
                f"[yellow]{problems[resume.label]}[/yellow]" if resume.label in problems else "ok",
            )
        console.print(table)
    finally:
        workspace.close()


@contextmanager
def _resume_workspace() -> Iterator[Workspace]:
    """Translate local artifact/validation errors without concealing a failed action."""
    workspace = _open()
    try:
        yield workspace
    except (ValueError, OSError, RuntimeError) as exc:
        error_console.print(str(exc), markup=False)
        raise typer.Exit(code=1) from exc
    finally:
        workspace.close()


@resumes_app.command("improve")
def resumes_improve(
    track: Annotated[str, typer.Option("--track", help="Track whose posting evidence to use.")],
    source: Annotated[Optional[Path], typer.Option("--source", help="Local HTML resume source; otherwise infer only an unambiguous match.")] = None,
    min_postings: Annotated[int, typer.Option("--min-postings", min=1, help="Minimum eligible posting sample.")] = 5,
) -> None:
    """Create an evidence-backed HTML/PDF candidate locally. Never promotes it."""
    from kitty.core import resume_versions

    console.print("Local processing only: no upload, no paid AI calls ($0). "
                  "Only supported facts may be edited; missing skills stay review items.")
    with _resume_workspace() as workspace:
        candidate = resume_versions.create_candidate(
            workspace.conn, workspace.config, workspace.settings, track,
            source.expanduser() if source else None, min_postings=min_postings,
        )
        console.print(f"Created candidate {candidate['id']} for {candidate['track']}.", markup=False)
        console.print(f"PDF: {candidate.get('pdf_path') or '(render failed)'}", markup=False)
        validation = candidate.get("validation", {})
        console.print("Validation: " + ("passed" if validation.get("valid") else "FAILED; promotion blocked"))
        for error in validation.get("errors", []):
            console.print(f"  {error}", markup=False)
        for warning in candidate.get("warnings", []):
            console.print(f"Warning: {warning}", markup=False)
        review_items = candidate.get("review_items", [])
        for item in review_items[:10]:
            note = f"{item.get('term', 'Review')}: {item.get('reason', '')}" if isinstance(item, dict) else str(item)
            console.print(f"Factual review (not inserted): {note}", markup=False)
        if len(review_items) > 10:
            console.print(f"{len(review_items) - 10} more factual review items retained with full evidence in the review.")
        console.print(f"Default unchanged. Review exact edits and evidence:\n"
                      f"  kitty resumes review {candidate['id']} --open", markup=False)


@resumes_app.command("review")
def resumes_review(
    version: Annotated[str, typer.Argument(help="Immutable version id from resumes versions.")],
    open_preview: Annotated[bool, typer.Option("--open", help="Open the local candidate PDF after printing the full review.")] = False,
) -> None:
    """Print the exact diff, anchored facts, posting evidence, measurements and validation."""
    from kitty.core import resume_versions
    from kitty.tui.screens.resume_review import open_pdf, review_text

    with _resume_workspace() as workspace:
        candidate = resume_versions.get_version(workspace.conn, workspace.settings, version)
        console.print(review_text(candidate), markup=False, highlight=False, emoji=False, soft_wrap=True)
        if open_preview:
            open_pdf(candidate)


@resumes_app.command("versions")
def resumes_versions(
    track: Annotated[Optional[str], typer.Option("--track", help="Only this track's version history.")] = None,
) -> None:
    """List retained candidates, decisions and promotion/rollback history."""
    import json

    from kitty.core import resume_versions

    with _resume_workspace() as workspace:
        versions = resume_versions.list_versions(workspace.conn, workspace.settings, track)
        if not versions:
            console.print("No resume versions yet. Use kitty resumes improve --track TRACK.")
        for version in versions:
            state = "current default" if version.get("is_default") else "not default"
            console.print(f"{version['id']}  {version['track']}  {version['status']}  {state}  "
                          f"{version.get('created_at', '')}", markup=False)
            console.print(f"  PDF: {version.get('pdf_path') or '(unavailable)'}", markup=False)
            console.print(f"  Validation: {version.get('validation', {})}", markup=False)
            if version.get("history"):
                console.print(json.dumps(version["history"], ensure_ascii=False, indent=2), markup=False)


@resumes_app.command("promote")
def resumes_promote(
    version: Annotated[str, typer.Argument(help="Candidate id to explicitly approve.")],
) -> None:
    """Review and confirm a new default for future unchosen jobs only."""
    from kitty.core import resume_versions
    from kitty.tui.screens.resume_review import review_text

    with _resume_workspace() as workspace:
        candidate = resume_versions.get_version(workspace.conn, workspace.settings, version)
        console.print(review_text(candidate), markup=False, highlight=False, emoji=False, soft_wrap=True)
        if candidate["status"] != "candidate" or not candidate.get("validation", {}).get("valid"):
            raise ValueError("Only an undecided candidate with passing validation can be promoted.")
        if not typer.confirm("Have you reviewed the exact edits, evidence and PDF, and approve this "
                             "candidate as the default for future unchosen jobs?", default=False):
            console.print("Cancelled; default unchanged.")
            return
        result = resume_versions.promote(workspace.conn, workspace.config, workspace.settings, version)
        console.print(f"Promoted {result.get('id', version)}. Existing jobs, applications and "
                      "original source files are unchanged.", markup=False)


@resumes_app.command("reject")
def resumes_reject(
    version: Annotated[str, typer.Argument(help="Candidate id to reject; artifacts are retained.")],
) -> None:
    """Reject a candidate without deleting its audit artifacts or changing the default."""
    from kitty.core import resume_versions

    with _resume_workspace() as workspace:
        resume_versions.reject(workspace.conn, workspace.settings, version)
        console.print(f"Rejected {version}. Audit artifacts retained; default unchanged.", markup=False)


@resumes_app.command("rollback")
def resumes_rollback(
    track: Annotated[str, typer.Option("--track", help="Track whose default should return to its prior version.")],
) -> None:
    """Confirm restoring the prior default without rewriting any application or artifact."""
    from kitty.core import resume_versions

    with _resume_workspace() as workspace:
        current = resume_versions.default_resume(workspace.conn, workspace.config, track)
        console.print(f"Current default for {track}: {current.label if current else '(none)'}", markup=False)
        if not typer.confirm("Restore the prior default? Existing choices and applications stay unchanged.",
                             default=False):
            console.print("Cancelled; default unchanged.")
            return
        result = resume_versions.rollback(workspace.conn, workspace.config, workspace.settings, track)
        console.print(f"Restored default: {result.get('label')} (resume {result.get('id')}). "
                      "History and artifacts retained.", markup=False)


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


# --------------------------------------------------------------------- tui


@app.command()
def tui() -> None:
    """Open the terminal UI (job screens need the applyai module; otherwise a core Modules screen)."""
    workspace = _open()
    modules = workspace.config.modules     # frozen for this process; the app reuses it
    workspace.close()          # the app opens its own connection
    from kitty.tui.shell import create_app

    create_app(demo=bool(_state["demo"]), modules=modules).run()


# ---- mcp


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio for an agent. Never submits; stdout is the protocol."""
    workspace = _open()        # config problems go to stderr and exit before serving
    modules = workspace.config.modules     # frozen for this process; later edits wait for a restart
    workspace.close()          # each tool call opens its own connection
    from kitty.mcp_server import run_stdio

    run_stdio(_settings(), modules)


# ------------------------------------------------------------------ modules

_RESTART_NOTE = (
    "Changes apply the next time rika! starts: a running web server, TUI or MCP server "
    "keeps the modules it started with. Your data is never removed."
)


def _configured_modules() -> tuple[Path, tuple[str, ...]]:
    """The config directory and the build's module selection, or exit with a readable error."""
    config_dir = _settings().config_dir
    try:
        return config_dir, load_modules(config_dir)
    except ConfigError as exc:
        raise _config_exit(exc) from exc


def _change_modules(ids: list[str], *, enable: bool) -> None:
    """Persist `current + ids` (enable) or `current - ids` (disable) and say what happens next."""
    unknown = [item for item in ids if item not in MODULE_IDS]
    if unknown:
        error_console.print(
            f"[bold red]Unknown module {escape(', '.join(unknown))}.[/bold red] "
            f"Available: {', '.join(MODULE_IDS)}."
        )
        raise typer.Exit(code=2)
    config_dir, current = _configured_modules()
    wanted = {*current, *ids} if enable else set(current) - set(ids)
    selection = tuple(item for item in MODULE_IDS if item in wanted)
    if selection == current:
        console.print(f"Nothing to change: enabled modules are {', '.join(current) or 'none (core only)'}.")
        return
    try:
        saved = bootstrap.configure_modules(config_dir, selection)
    except (ValueError, OSError) as exc:
        error_console.print(f"[bold red]Could not save the modules in config/build.json:[/bold red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc
    console.print(f"Saved the build's modules in config/build.json: {', '.join(saved) or 'none (core only)'}.")
    console.print(f"[dim]{_RESTART_NOTE}[/dim]")


@modules_app.command("list")
def modules_list() -> None:
    """Show each module and whether the build (config/build.json) enables it."""
    _config_dir, enabled = _configured_modules()
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("module")
    table.add_column("state")
    table.add_column("what it adds", overflow="fold")
    for item in available_modules():
        on = item["id"] in enabled
        table.add_row(item["id"], "[green]enabled[/green]" if on else "[dim]disabled[/dim]", item["description"])
    console.print(table)
    console.print(f"\n[dim]{_RESTART_NOTE}[/dim]")


@modules_app.command("enable")
def modules_enable(
    ids: Annotated[list[str], typer.Argument(metavar="MODULE...", help=f"One or more of: {', '.join(MODULE_IDS)}.")],
) -> None:
    """Enable modules (takes effect when rika! next starts)."""
    _change_modules(ids, enable=True)


@modules_app.command("disable")
def modules_disable(
    ids: Annotated[list[str], typer.Argument(metavar="MODULE...", help=f"One or more of: {', '.join(MODULE_IDS)}.")],
) -> None:
    """Disable modules (takes effect when rika! next starts; data stays)."""
    _change_modules(ids, enable=False)


# ---- keys (platform design Q10: one Keys service; values are never printed)

keys_app = typer.Typer(no_args_is_help=True, help="API keys: list, set, remove, and inject into one command.")
app.add_typer(keys_app, name="keys")


def _keystore():
    from kitty.core import keys as keys_core

    return keys_core.for_settings(_settings())


def _keys_exit(exc: Exception) -> typer.Exit:
    error_console.print(f"[bold red]{escape(str(exc))}[/bold red]", highlight=False)
    return typer.Exit(code=1)


@keys_app.command("list")
def keys_list() -> None:
    """Each key: set or unset and where it is stored (never its value)."""
    status = _keystore().status()
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("name", "state", "stored in", "variable"):
        table.add_column(column)
    for row in status["keys"]:
        state = "[red]error[/red]" if row["error"] else "[green]set[/green]" if row["set"] else "[dim]unset[/dim]"
        table.add_row(row["name"], state, row["backend"] or "-", row["env"])
    console.print(table)
    for row in status["keys"]:
        if row["error"]:
            console.print(f"[red]{row['name']}: {escape(row['error'])}[/red]")
    if not status["keyring"]:
        console.print("[yellow]No OS keyring is available: new keys are stored in an owner-only file "
                      "under .private/.[/yellow]")


@keys_app.command("set")
def keys_set(
    name: Annotated[str, typer.Argument(help="OPENROUTER, ANTHROPIC, USAJOBS, BRAVE or your own NAME (A-Z, 0-9, _).")],
) -> None:
    """Store a key: typed without echo, or read from standard input (one line)."""
    import getpass
    import sys

    from kitty.core import keys as keys_core

    store = _keystore()
    try:
        name = keys_core.canonical(name)
        value = getpass.getpass(f"{name}: ") if sys.stdin.isatty() else sys.stdin.readline()
        where = store.set(name, value)
    except (keys_core.KeysError, EOFError) as exc:
        raise _keys_exit(exc) from None
    console.print(f"Stored {name} in " + ("the OS keyring." if where == "keyring"
                  else "an owner-only file under .private/ (no OS keyring available)."))


@keys_app.command("remove")
def keys_remove(name: Annotated[str, typer.Argument(help="The key's NAME.")]) -> None:
    """Remove a key from the keyring and the owner-only file (.env is yours to edit)."""
    from kitty.core import keys as keys_core

    store = _keystore()
    try:
        name = keys_core.canonical(name)
        removed = store.remove(name)
        left = store.lookup(name)
    except keys_core.KeysError as exc:
        raise _keys_exit(exc) from None
    console.print(f"Removed {name}." if removed else f"{name} was not stored by kitty.")
    if left:
        console.print(f"[yellow]{keys_core.env_name(name)} is still set in the environment (.env).[/yellow]")


@keys_app.command("move")
def keys_move(name: Annotated[str, typer.Argument(help="The key's NAME.")]) -> None:
    """Move a key from its owner-only file into the OS keyring."""
    from kitty.core import keys as keys_core

    try:
        _keystore().move_to_keyring(name)
    except keys_core.KeysError as exc:
        raise _keys_exit(exc) from None
    console.print(f"Moved {keys_core.canonical(name)} to the OS keyring.")


@keys_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def keys_run(
    args: Annotated[list[str], typer.Argument(metavar="+NAME... -- COMMAND [ARGS]...",
                                              help="Keys to inject, then the command.")],
) -> None:
    """Run COMMAND with the named keys as environment variables, in that process only.

    Example: kitty keys run +OPENROUTER -- apply-ai. OPENROUTER, ANTHROPIC, USAJOBS and
    BRAVE arrive as OPENROUTER_API_KEY, ANTHROPIC_API_KEY, USAJOBS_API_KEY and
    BRAVE_SEARCH_API_KEY; your own keys under their NAME. Other keys kitty knows are
    removed from the command's environment. kitty is replaced by the command, so its
    exit code is the command's.
    """
    import os

    from kitty.core import keys as keys_core

    names = []
    while args and args[0].startswith("+"):
        names.append(args.pop(0)[1:])
    if args and args[0] == "--":
        args.pop(0)
    if not names or not args:
        raise _keys_exit(ValueError("Usage: kitty keys run +NAME [+NAME...] -- COMMAND [ARGS]..."))
    store = _keystore()
    try:
        injected = store.environment(names)
        known = {keys_core.env_name(name) for name in store.names()}
    except keys_core.KeysError as exc:
        raise _keys_exit(exc) from None
    env = {key: value for key, value in os.environ.items() if key not in known}
    env.update(injected)
    try:
        os.execvpe(args[0], args, env)
    except OSError as exc:
        error_console.print(f"[bold red]Cannot run {escape(args[0])}: {escape(exc.strerror or 'error')}[/bold red]",
                            highlight=False)
        raise typer.Exit(code=127) from None


# ---- gmail (Stage 6: read-only Gmail proposes status changes; I confirm them)

gmail_app = typer.Typer(no_args_is_help=True, help="Read-only Gmail: propose status changes from recruiting mail.")
app.add_typer(gmail_app, name="gmail")
proposals_app = typer.Typer(
    invoke_without_command=True, help="Status changes waiting for your OK (list, confirm, reject).",
)
app.add_typer(proposals_app, name="proposals")


@gmail_app.command("auth")
def gmail_auth(
    compose: Annotated[bool, typer.Option(
        "--compose", help="Authorise Rika's separate drafting token (read + drafts and sending) instead.")] = False,
) -> None:
    """Open the browser consent page (read-only scope) and save the token to data/.

    With --compose, save Rika's separate opt-in token (gmail.readonly + gmail.compose) to
    data/gmail_compose_token.json; the job-status check keeps using only the read-only token.
    """
    from kitty.core.gmail import auth as gmail_auth_core

    try:
        path = gmail_auth_core.run_consent_flow(_settings(), compose=compose)
    except gmail_auth_core.GmailSetupError as exc:
        error_console.print(escape(str(exc)), highlight=False, soft_wrap=True)
        raise typer.Exit(code=1) from exc
    if compose:
        console.print(f"[green]Gmail connected for Rika's drafts (read + compose).[/green] Token saved to {path}.")
        console.print("Rika saves replies as Gmail drafts; auto-send stays off per kind until you turn it on.")
        return
    console.print(f"[green]Gmail authorised (read-only).[/green] Token saved to {path}.")
    console.print("Next: [bold]kitty gmail check[/bold]")


@gmail_app.command("status")
def gmail_status() -> None:
    """Say whether Gmail is set up, and what the last check found."""
    import json

    from kitty.core.gmail import auth as gmail_auth_core
    from kitty.core.gmail.proposals import pending_proposals

    state = gmail_auth_core.setup_state(_settings())
    if not state.client_present:
        error_console.print(escape(f"{gmail_auth_core.SETUP_STEPS}\n\nMissing: {state.client_path}"),
                            highlight=False, soft_wrap=True)
        raise typer.Exit(code=1)
    if not state.token_present:
        error_console.print(f"Client file found ({state.client_path}), but not authorised yet: "
                            "run [bold]kitty gmail auth[/bold].")
        raise typer.Exit(code=1)
    console.print(f"[green]Gmail set up[/green] (read-only). Token: {state.token_path}")
    workspace = _open()
    try:
        last = workspace.conn.execute(
            "SELECT started_at, stats_json, error FROM runs WHERE kind = 'mail' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is None:
            console.print("Never checked: run [bold]kitty gmail check[/bold].")
        else:
            stats = json.loads(last["stats_json"] or "{}")
            console.print(f"Last check {last['started_at']}: {stats.get('fetched', 0)} new messages, "
                          f"{stats.get('proposed', 0)} proposed, status {stats.get('status', '?')}.")
            if last["error"]:
                error_console.print(f"[yellow]{escape(last['error'])}[/yellow]")
        console.print(f"{len(pending_proposals(workspace.conn))} proposal(s) waiting: "
                      "[bold]kitty proposals[/bold]")
    finally:
        workspace.close()


@gmail_app.command("check")
def gmail_check(
    days: Annotated[int, typer.Option("--days", min=1, help="How far back to look.")] = 30,
) -> None:
    """Read new recruiting mail and propose status changes (nothing moves until you confirm)."""
    from kitty.core.gmail import auth as gmail_auth_core
    from kitty.core.gmail.check import check_gmail

    workspace = _open()
    try:
        try:
            result = check_gmail(workspace.conn, workspace.settings, days=days)
        except gmail_auth_core.GmailSetupError as exc:
            error_console.print(escape(str(exc)), highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc
        for proposal in result.proposals:
            console.print(
                f"  #{proposal.event_id}  {escape(proposal.company)} - {escape(proposal.title)}: "
                f"{proposal.from_status} -> [bold]{proposal.to_status}[/bold]  "
                f"[dim]{escape(proposal.subject)} | \"{escape(proposal.phrase)}\"[/dim]"
            )
        skipped = ", ".join(f"{reason} {count}" for reason, count in sorted(result.skipped_by_reason.items()))
        console.print(
            f"{result.listed} listed, {result.already_seen} seen before, {result.fetched} new, "
            f"{result.classified} status mail, {result.matched} matched, "
            f"[bold]{result.proposed} proposed[/bold]" + (f"  [dim]skipped: {skipped}[/dim]" if skipped else "")
        )
        if result.error:
            error_console.print(f"[red]{result.status}: {escape(result.error)}[/red]")
            raise typer.Exit(code=1)
        if result.proposed:
            console.print("Review with [bold]kitty proposals[/bold] or [bold]v[/bold] on the Pipeline tab.")
    finally:
        workspace.close()


@proposals_app.callback()
def proposals_list(ctx: typer.Context) -> None:
    """List status changes waiting for your OK."""
    if ctx.invoked_subcommand is not None:
        return
    from kitty.core.gmail.proposals import pending_proposals

    workspace = _open()
    try:
        pending = pending_proposals(workspace.conn)
        table = Table(box=None, header_style="bold")
        for column in ("event", "app", "company", "title", "change", "when", "evidence"):
            table.add_column(column, overflow="fold")
        for proposal in pending:
            table.add_row(
                str(proposal.event_id), str(proposal.application_id), escape(proposal.company),
                escape(proposal.title), f"{proposal.current_status} -> {proposal.to_status}",
                proposal.occurred_at[:10], escape(proposal.evidence or ""),
            )
        console.print(table)
        console.print(f"\n{len(pending)} waiting. [bold]kitty proposals confirm <event>[/bold] "
                      "or [bold]kitty proposals reject <event>[/bold].")
    finally:
        workspace.close()


@proposals_app.command("confirm")
def proposals_confirm(
    event_id: Annotated[int, typer.Argument(help="Event id from `kitty proposals`.")],
) -> None:
    """Accept a proposal: the application moves to the proposed status."""
    from kitty.core.gmail.proposals import get_pending

    workspace = _open()
    try:
        proposal = get_pending(workspace.conn, event_id)
        if proposal is None:
            error_console.print(f"[red]No pending proposal with id {event_id}.[/red]")
            raise typer.Exit(code=1)
        applications_core.confirm_proposal(workspace.conn, event_id)
        console.print(f"[green]Confirmed[/green]: {escape(proposal.company)} - {escape(proposal.title)} "
                      f"is now [bold]{proposal.to_status}[/bold].")
    finally:
        workspace.close()


@proposals_app.command("reject")
def proposals_reject(
    event_id: Annotated[int, typer.Argument(help="Event id from `kitty proposals`.")],
) -> None:
    """Decline a proposal: nothing changes; it stays in the history as declined."""
    from kitty.core.gmail.proposals import get_pending

    workspace = _open()
    try:
        proposal = get_pending(workspace.conn, event_id)
        if proposal is None:
            error_console.print(f"[red]No pending proposal with id {event_id}.[/red]")
            raise typer.Exit(code=1)
        applications_core.reject_proposal(workspace.conn, event_id)
        console.print(f"Declined: {escape(proposal.company)} - {escape(proposal.title)} "
                      f"stays [bold]{proposal.current_status}[/bold].")
    finally:
        workspace.close()


# ---- questions: every form question in the queue, answered once in answers.yaml

questions_app = typer.Typer(no_args_is_help=True,
                            help="Form questions across the queue (read without a browser).")
app.add_typer(questions_app, name="questions")

#: How `questions list` titles each kind of question.
_QUESTION_KIND_TITLES = {
    "choice": "Choice questions", "factual": "Factual questions (typed)", "essay": "Essays",
    "demographic": "Demographic (EEO, voluntary)", "answered": "Already in answers.yaml",
    "profile": "Covered by profile.yaml",
}


def _csv_option(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


@questions_app.command("collect")
def questions_collect(
    states: Annotated[str, typer.Option("--states", help="Queue states, comma-separated.")] = "new,queued",
    tiers: Annotated[str, typer.Option("--tiers", help="Tiers, comma-separated.")] = "A,B",
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="At most N jobs.")] = None,
) -> None:
    """Read the application-form questions of the queue's Greenhouse and Lever jobs."""
    from kitty.core import form_questions

    workspace = _open()
    try:
        result = form_questions.collect(
            workspace.conn, workspace.settings, workspace.config,
            states=_csv_option(states), tiers=[t.upper() for t in _csv_option(tiers)], limit=limit,
            progress=lambda message: console.print(f"[dim]{escape(message)}[/dim]"),
        )
        console.print(f"\n{result.considered} job(s) considered, [bold]{result.fetched} form(s) read[/bold] "
                      f"({result.from_cache} from cache), {sum(result.failed.values())} failed, "
                      f"{sum(result.skipped.values())} skipped.")
        for title, counts in (("Failed", result.failed), ("Skipped", result.skipped)):
            for reason, count in counts.most_common():
                console.print(f"  {title.lower()}: {escape(reason)}  {count}")
        console.print(f"{result.questions_seen} questions kept (profile fields and uploads left out), "
                      f"[bold]{result.distinct_questions} distinct[/bold]; "
                      f"{result.new_questions} new question(s), {result.new_links} new job link(s).")
        console.print("Next: [bold]kitty questions list[/bold]")
    finally:
        workspace.close()


def _question_json(summary) -> dict:
    """One question for `questions list --json` (long choice lists capped at 100)."""
    return {
        "id": summary.id, "text": summary.text, "kind": summary.kind, "field_kind": summary.field_kind,
        "job_count": summary.job_count, "required_jobs": summary.required_jobs,
        "companies": list(summary.companies), "job_ids": list(summary.job_ids),
        "choices": list(summary.choices[:100]), "choice_count": len(summary.choices),
        "other_choice_sets": [
            {"jobs": count, "choices": list(choices[:100])}
            for choices, count in summary.choice_sets[1:]
        ],
    }


def _print_question(summary) -> None:
    """One question: job count, text, example companies, choices."""
    required = f"  [dim]required on {summary.required_jobs}[/dim]" if summary.required_jobs else ""
    console.print(f"  [bold]{summary.job_count:>3}[/bold]  {escape(summary.text)}{required}")
    if summary.companies:
        more = len(summary.companies) - 4
        console.print(f"       [dim]{escape(', '.join(summary.companies[:4]))}"
                      + (f" (+{more} more)" if more > 0 else "") + "[/dim]")
    if summary.choices:
        shown = " | ".join(summary.choices[:12])
        more = len(summary.choices) - 12
        console.print(f"       choices: {escape(shown)}" + (f" [dim](+{more} more)[/dim]" if more > 0 else ""))
    if len(summary.choice_sets) > 1:
        console.print(f"       [dim](+{len(summary.choice_sets) - 1} other choice set(s) on other forms)[/dim]")


@questions_app.command("list")
def questions_list(
    show_all: Annotated[bool, typer.Option("--all", help="Everything, including answered and profile questions.")] = False,
    min_jobs: Annotated[int, typer.Option("--min-jobs", min=0, help="Only questions asked by at least N jobs.")] = 1,
    include_essays: Annotated[bool, typer.Option("--include-essays")] = False,
    include_demographic: Annotated[bool, typer.Option("--include-demographic")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Questions still to answer in answers.yaml, most-shared first, grouped by kind."""
    import json

    from kitty.core import form_questions

    wanted = {"choice", "factual"}
    wanted |= {"essay"} if include_essays or show_all else set()
    wanted |= {"demographic"} if include_demographic or show_all else set()
    wanted |= {"answered", "profile"} if show_all else set()
    workspace = _open()
    try:
        found = [s for s in form_questions.summarize(workspace.conn, workspace.config)
                 if s.kind in wanted and s.job_count >= min_jobs]
    finally:
        workspace.close()
    if as_json:
        typer.echo(json.dumps({"questions": [_question_json(s) for s in found]}, indent=2, ensure_ascii=False))
        return
    for kind in form_questions.KINDS:
        group = [s for s in found if s.kind == kind]
        if group:
            console.print(f"\n[bold]{_QUESTION_KIND_TITLES[kind]} ({len(group)})[/bold]  [dim]jobs  question[/dim]")
            for summary in group:
                _print_question(summary)
    console.print(f"\n{len(found)} question(s). Answer them in config/answers.yaml; a `match:` phrase "
                  f"covers differently worded ones ({form_questions.COMPANY} stands for the company's name).")


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


# ------------------------------------------------------------------ notes (blocks)

notes_app = typer.Typer(no_args_is_help=True, help=(
    "Read, search and write notes as blocks (kitty.core.block_api). Writes are recorded as an agent "
    "session in History: one per command, or KITTY_AGENT_SESSION (named by KITTY_AGENT_NAME)."))
app.add_typer(notes_app, name="notes")

_JSON_OPTION = typer.Option("--json", help="Machine-readable output (with ((id@seq)) citations).")
#: How `notes show` marks each block type.
_MARKERS = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list": "• ", "numbered_list": "1. ",
            "quote": "> ", "callout": "! ", "toggle": "▸ ", "code": "``` ", "divider": "---"}


@contextmanager
def _blocks() -> Iterator:
    """A connection for one notes command, writing as its agent session; refusals print and exit 1."""
    from kitty.core.blocks import BlockError
    from kitty.core.blocks.actor import acting, cli_agent

    try:
        agent = cli_agent()
    except ValueError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    workspace = _open()
    try:
        with acting(agent):
            yield workspace.conn
    except BlockError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    finally:
        workspace.close()


def _emit(result: dict, as_json: bool, summary: str) -> None:
    import json

    if as_json:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        console.print(summary)


def _texts(values: list[str]) -> list[str]:
    """Block texts from arguments; ``-`` reads one block from stdin."""
    import sys

    return [sys.stdin.read().rstrip("\n") if value == "-" else value for value in values]


def _written(result: dict) -> str:
    return (f"seq {result['seq']} · revision {result['revision']} · "
            + ", ".join(escape(item["cite"]) for item in result["blocks"]))


@notes_app.command("ls")
def notes_ls(
    query: Annotated[str, typer.Argument(help="Only titles containing this.")] = "",
    kind: Annotated[Optional[str], typer.Option("--kind", help="page, document or quick_note.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """List note pages, documents and quick notes, most recently edited first."""
    from kitty.core import block_api

    with _blocks() as conn:
        result = block_api.list_pages(conn, query, kind=kind, limit=limit)
    lines = [f"{page['type']:<10} {page['number'] or '':>4}  {escape(page['title'])}  [dim]{page['id']}[/dim]"
             for page in result["pages"]]
    more = f"\n[dim]{result['total'] - len(result['pages'])} more; use --limit or a title filter.[/dim]" if result["truncated"] else ""
    _emit(result, as_json, "\n".join(lines) + more if lines else "No pages.")


def _outline(blocks: list[dict], depth: int = 0) -> Iterator[str]:
    for block in blocks:
        marker = ("[x] " if block["props"].get("checked") else "[ ] ") if block["type"] == "to_do" else _MARKERS.get(block["type"], "")
        tags = "".join(f" #{name}" for name in block["tags"])
        yield f"{'  ' * depth}{escape(marker + block['text'])}[cyan]{escape(tags)}[/cyan]  [dim]{block['id']}[/dim]"
        yield from _outline(block["children"], depth + 1)


@notes_app.command("show")
def notes_show(
    page: Annotated[str, typer.Argument(help="A page's block ID or a note page number.")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Print one page's blocks as an outline (block IDs dimmed)."""
    from kitty.core import block_api

    with _blocks() as conn:
        result = block_api.get_page(conn, page)
    head = result["page"]
    lines = [f"[bold]{escape(head['title'])}[/bold]  [dim]{head['type']} {head['number'] or ''} · revision "
             f"{result['revision']} · seq {result['seq']} · {head['id']}[/dim]", *_outline(result["blocks"])]
    if head["canvas_elements"]:
        lines.append(f"[dim](and {head['canvas_elements']} canvas element(s), not listed)[/dim]")
    _emit(result, as_json, "\n".join(lines))


@notes_app.command("search")
def notes_search(
    query: Annotated[str, typer.Argument(help="Words (prefixes) or, from 3 characters, any substring.")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 20,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Search the blocks of note pages, documents and quick notes (the full-text index)."""
    from kitty.core import block_api

    with _blocks() as conn:
        result = block_api.search_blocks(conn, query, limit=limit)
    lines = [f"{escape(hit['page_title'])}: {escape(hit['snippet']).replace('**', '')}  [dim]{escape(hit['cite'])}[/dim]"
             for hit in result["hits"]]
    more = f"\n[dim]{result['total'] - len(result['hits'])} more; use --limit.[/dim]" if result["truncated"] else ""
    _emit(result, as_json, "\n".join(lines) + more if lines else "No matching blocks.")


@notes_app.command("new")
def notes_new(
    title: Annotated[str, typer.Argument(help="The page title.")],
    texts: Annotated[Optional[list[str]], typer.Argument(metavar="[TEXT]...", help="Text blocks to start with ('-' reads stdin).")] = None,
    notebook: Annotated[Optional[str], typer.Option("--notebook", help="Notebook title (created when missing).")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Create a note page, optionally with text blocks."""
    from kitty.core import block_api

    with _blocks() as conn:
        result = block_api.create_page(conn, title, notebook=notebook, blocks=_texts(texts or []) or None)
    _emit(result, as_json, f"Created page {result['number']} {escape(result['cite'])} (seq {result['seq']}).")


@notes_app.command("append")
def notes_append(
    page: Annotated[str, typer.Argument(help="A page's block ID or a note page number.")],
    texts: Annotated[list[str], typer.Argument(metavar="TEXT...", help="One block each ('-' reads stdin).")],
    kind: Annotated[str, typer.Option("--type", help="Block type for every block (text, to_do, bulleted_list, ...).")] = "text",
    parent: Annotated[Optional[str], typer.Option("--parent", help="Append under this block instead.")] = None,
    revision: Annotated[Optional[int], typer.Option("--revision", help="Refuse if the page changed since this revision.")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Append blocks at the end of a note page."""
    from kitty.core import block_api

    with _blocks() as conn:
        result = block_api.append_blocks(conn, page, [{"type": kind, "text": text} for text in _texts(texts)],
                                         parent=parent, revision=revision)
    _emit(result, as_json, f"Appended: {_written(result)}")


@notes_app.command("inbox")
def notes_inbox(
    texts: Annotated[list[str], typer.Argument(metavar="TEXT...", help="One block each ('-' reads stdin).")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Add text blocks to the end of the Inbox page."""
    from kitty.core import block_api

    with _blocks() as conn:
        result = block_api.add_to_inbox(conn, _texts(texts))
    _emit(result, as_json, f"Added to the Inbox (page {result['number']}): {_written(result)}")


@notes_app.command("quick")
def notes_quick(
    text: Annotated[str, typer.Argument(help="The quick note ('-' reads stdin).")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Create a quick note."""
    from kitty.core import block_api

    with _blocks() as conn:
        result = block_api.quick_note(conn, _texts([text])[0])
    _emit(result, as_json, f"Quick note {result['number']}: {_written(result)}")


@notes_app.command("sessions")
def notes_sessions(
    kind: Annotated[str, typer.Option("--kind", help="agent (default), client, user, system, peer or all.")] = "agent",
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 20,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Agent sessions in the kept history, most recent first (undo one with undo-session)."""
    from kitty.core.blocks import sessions

    with _blocks() as conn:
        result = sessions.list_sessions(conn, kind=None if kind == "all" else kind, limit=limit)
    lines = [f"{escape(item['session'])}  {escape(item['actor']['name'] or item['actor']['kind'])}"
             f"{' via ' + escape(item['device']) if item['device'] else ''}  {item['start'][:16]} – {item['end'][:16]}  "
             f"{item['transactions']} transaction(s) on {item['page_count']} page(s)" for item in result["sessions"]]
    _emit(result, as_json, "\n".join(lines) or "No sessions in the kept history.")


@notes_app.command("undo-session")
def notes_undo_session(
    session: Annotated[str, typer.Argument(help="The session ID (from `kitty notes sessions` or History).")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only report what would be reverted.")] = False,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Revert everything one agent session wrote, as one new transaction; later edits by others are kept and listed."""
    from kitty.core.blocks import sessions

    with _blocks() as conn:
        result = sessions.undo_session(conn, session, dry_run=dry_run)
    verb = "Would revert" if dry_run else "Reverted" if result["applied"] else "Nothing to revert:"
    lines = [f"{verb} {result['reverted_count']} block(s) from {result['transactions']} transaction(s)"
             + (f" (seq {', '.join(str(value) for value in result['seq'].values())})" if result.get("seq") else "")]
    lines += [f"  reverted {escape(item['text'][:60]) or item['id']}: {', '.join(item['fields'])}" for item in result["reverted"]]
    lines += [f"  kept {escape((item.get('text') or '')[:60]) or item.get('id') or 'seq ' + str(item.get('seq'))}: "
              f"{escape('; '.join(item['reasons']))}" for item in result["conflicts"]]
    _emit(result, as_json, "\n".join(lines))


@notes_app.command("citations")
def notes_citations(
    cites: Annotated[list[str], typer.Argument(help="Pinned citations, ((id@seq)) or id@seq.")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Whether each cited block changed since the cited seq, and how (word diff: {-old-}{+new+})."""
    from kitty.core.blocks import history

    with _blocks() as conn:
        result = history.citations(conn, cites)
    lines = []
    for item in result["citations"]:
        lines.append(f"{escape(item['cite'])}  {item['status']}" + (f" (changed at seq {item['seq']})" if item["changed"] else ""))
        if item["diff"]:
            lines.append("  " + "".join(escape(part["text"]) if part["op"] == "equal" else
                                       f"[red]{{-{escape(part['text'])}-}}[/red]" if part["op"] == "delete" else
                                       f"[green]{{+{escape(part['text'])}+}}[/green]" for part in item["diff"]))
    _emit(result, as_json, "\n".join(lines))


# ------------------------------------------------------------------ effects (proposals)

effects_app = typer.Typer(no_args_is_help=True, help=(
    "Propose effects for the owner to approve in rika!'s pending approvals (kitty.core.effects). "
    "Proposals are made as an agent session (one per command, or KITTY_AGENT_SESSION); "
    "nothing here approves or executes one, and submit_application never executes."))
app.add_typer(effects_app, name="effects")


@contextmanager
def _effects() -> Iterator:
    """The workspace and this command's agent actor; refusals print and exit 1."""
    from kitty.core import effects
    from kitty.core.blocks.actor import cli_agent

    try:
        agent = cli_agent()
    except ValueError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    workspace = _open()
    try:
        yield workspace, agent
    except effects.EffectError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    finally:
        workspace.close()


def _effect_line(effect: dict) -> str:
    actor = effect["actor"]
    who = actor["name"] or actor["kind"]
    return f"{effect['state']:<9} {escape(effect['title'])}  by {escape(who)}  [dim]{effect['id']}[/dim]"


def _propose(kind: str, payload: dict, as_json: bool) -> None:
    from kitty.core import effect_kinds, effects

    with _effects() as (workspace, agent):
        effect = effects.propose(workspace.conn, kind, payload, actor=agent,
                                 ctx=effect_kinds.Context(workspace.conn, workspace.config))
        result = {**effect.as_dict(), "events": effects.events(workspace.conn, effect.id)}
    _emit(result, as_json, _effect_line(result) + "\nWaiting for approval in rika!'s pending approvals.")


@effects_app.command("propose-delete")
def effects_propose_delete(
    block: Annotated[str, typer.Argument(help="The block's ID (a text block of a note page).")],
    reason: Annotated[Optional[str], typer.Option("--reason", help="Why, shown to the owner.")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Propose moving a block and its children to Trash; it runs only once the owner approves it."""
    _propose("delete_block", {"block": block, "reason": reason}, as_json)


@effects_app.command("propose-submit")
def effects_propose_submit(
    job_id: Annotated[int, typer.Argument(help="The job's number.")],
    reason: Annotated[Optional[str], typer.Option("--reason", help="Why, shown to the owner.")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Ask the owner to submit an application himself: it shows what would be submitted and never executes."""
    _propose("submit_application", {"job_id": job_id, "reason": reason}, as_json)


@effects_app.command("list")
def effects_list(as_json: Annotated[bool, _JSON_OPTION] = False) -> None:
    """Effects waiting in the pending approvals, oldest first, and the latest decided ones."""
    from kitty.core import effect_kinds, effects  # noqa: F401 (registers the kinds listed here)

    with _effects() as (workspace, _agent):
        pending = [effect.as_dict() for effect in effects.pending(workspace.conn)]
        recent = [effect.as_dict() for effect in effects.recent(workspace.conn)]
    lines = [_effect_line(effect) for effect in pending + recent]
    _emit({"pending": pending, "recent": recent}, as_json, "\n".join(lines) if lines else "No effects.")


@effects_app.command("show")
def effects_show(
    effect_id: Annotated[str, typer.Argument(help="The effect's ID.")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """One effect: what it does, its preconditions and every state it went through."""
    import json

    from kitty.core import effect_kinds, effects  # noqa: F401 (registers the kinds shown here)

    with _effects() as (workspace, _agent):
        effect = effects.get(workspace.conn, effect_id)
        result = {**effect.as_dict(), "events": effects.events(workspace.conn, effect.id)}
    lines = [_effect_line(result), escape(json.dumps(result["preview"], indent=2, ensure_ascii=False))]
    lines += [f"  {event['at']}  {event['state']}  {escape(event['actor']['name'] or event['actor']['kind'])}"
              for event in result["events"]]
    _emit(result, as_json, "\n".join(lines))


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()

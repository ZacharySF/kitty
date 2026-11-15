"""`kitty apply` and `kitty autofill`: preparing applications for review."""

from __future__ import annotations

import signal
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.table import Table

from kitty.cli.common import _open, console, error_console
from kitty.cli.root import app
from kitty.core import jobs as jobs_core

# ------------------------------------------------------------------ apply


@app.command()
def apply(
    job_ids: Annotated[Optional[list[str]], typer.Argument(
        help="Job ids to open and fill; `report` for the apply health report; `resolve [JOB_IDS]...` "
             "to check postings are open and resolve redirects; `accounts [grant|revoke FAMILY]` for "
             "the employer account registry and ATS-family grants; `restart-firefox` after a "
             "restart_firefox handoff.",
        metavar="[JOB_IDS]... | report | resolve [JOB_IDS]... | accounts [grant|revoke FAMILY] | restart-firefox",
    )] = None,
    queued: Annotated[bool, typer.Option("--queued", help="Every queued job you have not applied to.")] = False,
    top: Annotated[Optional[int], typer.Option("--top", help="The N best new/queued jobs you have not applied to.")] = None,
    track: Annotated[Optional[str], typer.Option("--track", help="Only jobs in this track (with --queued / --top).")] = None,
    headless: Annotated[bool, typer.Option("--headless", help="Use an invisible browser that closes afterwards.")] = False,
    no_watch: Annotated[bool, typer.Option("--no-watch", help="Do not watch the tabs for your submit afterwards.")] = False,
    include_ineligible: Annotated[bool, typer.Option(
        "--include-ineligible",
        help="Explicit compatibility research on ineligible jobs only; never ready, authenticated or watched.",
    )] = False,
    as_json: Annotated[bool, typer.Option("--json", help="With `report` or `resolve`: machine-readable output.")] = False,
    dry_run: Annotated[bool, typer.Option(
        "--dry-run", help="With `resolve`: report what would change; write nothing.")] = False,
) -> None:
    """Open each job's application form in Firefox and fill it. You review and submit.

    Afterwards it watches the tabs (config/apply.yaml watch_hours) and records each
    application when its tab shows the board's confirmation page. Ctrl-C stops watching.

    `kitty apply report` shows apply health instead: the unattended ready rate per ATS
    family (unit = job, closed postings removed, Wilson 95% intervals), top causes and
    per-field correctness by provenance class. It only reads the attempt records.

    `kitty apply resolve` checks the queue's postings before any attempt: closed postings
    (HTTP 404/410, a closed notice, Workday cxs 403 S22 with an empty page shell, Greenhouse
    ?questions=true 404) are marked closed; zapply/recruitics/appcast/Litos redirects are
    resolved to the employer's posting, stored as the apply URL; excluded flows (Indeed Apply,
    Tesla, employer-own portals, Paradox) become typed handoffs. Without job ids it checks
    every new/queued job (or --queued / --top N); public GETs only, robots.txt respected, at
    most one request per second per host. `--dry-run` reports the changes and writes nothing.

    `kitty apply restart-firefox` answers a `restart_firefox:` handoff (the connection to the
    rika! Firefox broke while tabs waited for you): finish or discard those tabs first. It kills
    that Firefox, so requests its lost session still held are dropped, never sent, and starts it
    again without restoring tabs.

    `kitty apply accounts` lists the employer account registry (state, login email, password
    profile and where it is stored, never a password) and the ATS-family grants;
    `kitty apply accounts grant workday` (or revoke) is your one-time approval for kitty to
    create, verify, sign in to and reset accounts on that family's tenants (web, CLI, autofill).
    """
    from kitty.core.apply.filler import EligibilityOverride, fill_jobs
    from kitty.core.apply.watch import Watch

    if job_ids and job_ids[0] == "report":
        if len(job_ids) > 1 or queued or top or track or headless or no_watch or include_ineligible or dry_run:
            error_console.print("`kitty apply report` takes no job ids or fill options (only --json).")
            raise typer.Exit(code=1)
        _apply_report(as_json)
        return
    if job_ids and job_ids[0] == "restart-firefox":
        if (len(job_ids) > 1 or queued or top or track or headless or no_watch or include_ineligible
                or dry_run or as_json):
            error_console.print("`kitty apply restart-firefox` takes no job ids or options.")
            raise typer.Exit(code=1)
        _apply_restart_firefox()
        return
    if job_ids and job_ids[0] == "accounts":
        if queued or top or track or headless or no_watch or include_ineligible or dry_run:
            error_console.print("`kitty apply accounts` takes only grant/revoke FAMILY and --json.")
            raise typer.Exit(code=1)
        _apply_accounts(job_ids[1:], as_json)
        return
    resolving = bool(job_ids) and job_ids[0] == "resolve"
    if resolving:
        job_ids = job_ids[1:]
        if headless or no_watch or include_ineligible:
            error_console.print("`kitty apply resolve` takes job ids, --queued, --top, --track, --dry-run and --json.")
            raise typer.Exit(code=1)
    elif as_json or dry_run:
        error_console.print("--json applies to `kitty apply report` / `resolve`; --dry-run to `resolve` only.")
        raise typer.Exit(code=1)
    try:
        ids = [int(value) for value in job_ids or []]
    except ValueError:
        error_console.print("Job ids must be whole numbers (or use `kitty apply report` / `resolve`).")
        raise typer.Exit(code=1) from None
    if resolving:
        _apply_resolve(ids, queued=queued, top=top, track=track, dry_run=dry_run, as_json=as_json)
        return

    workspace = _open()
    try:
        config = workspace.config
        if track and config.track(track) is None:
            error_console.print(f"[red]Unknown track '{track}'. Known: {', '.join(config.track_names)}[/red]")
            raise typer.Exit(code=1)
        chosen = ids
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


def _percent(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else ""


def _rate_text(rate: float | None, low: float | None, high: float | None, n: int) -> str:
    if rate is None:
        return f"insufficient data (n = {n})"
    return f"{_percent(rate)} ({_percent(low)}–{_percent(high)}), n = {n}"


def _apply_restart_firefox() -> None:
    """`kitty apply restart-firefox`: kill the rika! Firefox and start it again (browser.restart_own_firefox)."""
    from kitty.core.apply.browser import BrowserError, restart_own_firefox

    workspace = _open()
    try:
        settings = workspace.settings
    finally:
        workspace.close()
    try:
        port = restart_own_firefox(settings)
    except BrowserError as exc:
        error_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from None
    console.print(f"Restarted the rika! Firefox (remote control on port {port}). Requests its lost "
                  "session still held were dropped, not sent; no tabs were restored.")


def _apply_accounts(args: list[str], as_json: bool) -> None:
    """List the account registry and family grants, or grant/revoke one family."""
    import json

    from kitty.core.apply import accounts
    from kitty.core.apply.grants import FAMILIES

    if args and (len(args) != 2 or args[0] not in ("grant", "revoke") or args[1] not in FAMILIES):
        error_console.print(f"Use `kitty apply accounts [grant|revoke FAMILY]`; families: {', '.join(FAMILIES)}.")
        raise typer.Exit(code=1)
    workspace = _open()
    try:
        if args:
            accounts.set_grant(workspace.conn, args[1], args[0] == "grant")
        found = accounts.listing(workspace.conn, workspace.settings, workspace.config)
    finally:
        workspace.close()
    if as_json:
        typer.echo(json.dumps(found, indent=2))
        return
    console.print("[bold]ATS-family grants[/bold] (create, verify, sign in and reset on matching tenants)")
    for grant in found["grants"]:
        state = f"[green]granted {grant['granted_at']}[/green]" if grant["granted"] else "[dim]not granted[/dim]"
        console.print(f"  {grant['label']:<26} {state}  [dim]{', '.join(grant['patterns'])}[/dim]")
    caps = found["caps"]
    console.print(f"\nNew accounts in the last 24 h: {caps['new_accounts_today']}/{caps['new_accounts_per_day']}; "
                  f"sign-in tries per employer per day: at most {caps['sign_ins_per_tenant_per_day']}.")
    if found["keyring_locked"]:
        console.print("[yellow]The OS keyring is locked: runs hand off until you unlock it.[/yellow]")
    for warning in found["warnings"]:
        console.print(f"[yellow]{escape(warning)}[/yellow]")
    if not found["accounts"]:
        console.print("\nNo employer accounts recorded yet.")
        return
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("family", "employer", "login email", "state", "password", "stored", "last sign-in"):
        table.add_column(column)
    for row in found["accounts"]:
        table.add_row(row["family"], row["origin"], row["login_email"], row["state"], row["password_profile"],
                      row["password_stored"] or "-", row["last_sign_in_at"] or "-")
    console.print()
    console.print(table)


def _apply_report(as_json: bool) -> None:
    """Print the apply health report (kitty.core.apply.health)."""
    import json

    from kitty.core.apply import health

    workspace = _open()
    try:
        found = health.report(workspace.conn)
    finally:
        workspace.close()
    if as_json:
        typer.echo(json.dumps(found, indent=2))
        return
    if not found["attempts"]:
        console.print("No apply attempts recorded yet. Run [bold]kitty apply[/bold] first.")
        return
    overall = found["overall"]
    console.print(f"[bold]Apply health[/bold]  {found['attempts']} attempts, last {found['last_attempt_at']}")
    console.print("Unit = job; closed postings removed; Wilson 95% intervals; "
                  f"fewer than {found['min_n']} = insufficient data.\n")
    console.print("[bold]Unattended ready rate (URR)[/bold]")
    for family in [overall, *found["families"]]:
        console.print(f"  {family['family']:<16} {family['ready']:>4}/{family['jobs']:<4} "
                      f"{_rate_text(family['urr'], family['low'], family['high'], family['jobs'])}"
                      f"  [dim]no owner input: {family['ready_no_input']}; closed removed: {family['closed']}[/dim]")
    if found["causes"]:
        console.print("\n[bold]Top causes[/bold] (live jobs never ready, by their latest attempt)")
        for cause in found["causes"]:
            console.print(f"  {cause['jobs']:>4} jobs  {cause['attempts']:>4} attempts  "
                          f"{escape(cause['label'])} [dim]({cause['cause']}, {cause['outcome']})[/dim]")
    if found["correctness"]:
        console.print("\n[bold]Per-field correctness[/bold] (fields the owner kept unchanged before Submit)")
        for item in found["correctness"]:
            console.print(f"  {item['class']:<16} filled {item['filled']:>4}  edited {item['edited']:>3}/{item['compared']:<4} "
                          f"{_rate_text(item['rate'], item['low'], item['high'], item['compared'])}")


def _apply_resolve(ids: list[int], *, queued: bool, top: int | None, track: str | None,
                   dry_run: bool, as_json: bool) -> None:
    """`kitty apply resolve`: liveness, redirect resolution and handoffs (kitty.core.apply.resolve)."""
    import json
    from dataclasses import asdict

    from kitty.core.apply import resolve

    workspace = _open()
    try:
        config = workspace.config
        if track and config.track(track) is None:
            error_console.print(f"[red]Unknown track '{track}'. Known: {', '.join(config.track_names)}[/red]")
            raise typer.Exit(code=1)
        chosen = []
        for job_id in dict.fromkeys(ids):
            job = jobs_core.get_job(workspace.conn, job_id)
            if job is None:
                error_console.print(f"No job with id {job_id}.")
                raise typer.Exit(code=1)
            chosen.append(job)
        if not ids or queued or top:
            picked = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(
                order="best", prefer_cities=config.prefer_cities, prefer_remote=config.profile.open_to_remote,
                queue_states=["queued"] if queued and not top else ["new", "queued"],
                eligibility=["boosted", "eligible", "ambiguous", "unknown"],
                not_applied=True, tracks=[track] if track else None, limit=top,
            ))
            chosen += [job for job in picked if job.id not in ids]
        say = None if as_json else (lambda message: error_console.print(f"[dim]{escape(message)}[/dim]"))
        if not as_json:
            console.print(f"{'Dry run: nothing is written. ' if dry_run else ''}"
                          f"Checking {len(chosen)} posting(s); public pages only, robots.txt respected, "
                          f"at most one request per second per host.")
        results = resolve.resolve_jobs(workspace.conn, workspace.settings, chosen, dry_run=dry_run, say=say)
    finally:
        workspace.close()
    if as_json:
        typer.echo(json.dumps({"dry_run": dry_run, "results": [
            {"job_id": r.job_id, "company": r.company, "title": r.title, "changes": list(r.changes),
             **asdict(r.check)} for r in results]}, indent=2))
        return
    verb = {"close": "would close" if dry_run else "closed",
            "apply_url": "would set apply URL" if dry_run else "apply URL set",
            "handoff": "would file handoff" if dry_run else "handoff filed"}
    counts: dict[str, int] = {}
    for result in results:
        check = result.check
        counts[check.status] = counts.get(check.status, 0) + 1
        route = f"{check.source} → " if check.source else ""
        console.print(f"{result.job_id}  {escape(result.company)} - {escape(result.title)}: "
                      f"[bold]{check.status}[/bold] ({route}{check.family})")
        console.print(f"    {escape(check.reason)}")
        for change in result.changes:
            detail = f": {escape(check.resolved_url or '')}" if change == "apply_url" else ""
            console.print(f"    [yellow]{verb[change]}{detail}[/yellow]")
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items())) or "nothing"
    changed = sum(1 for result in results if result.changes)
    console.print(f"\nChecked {len(results)} posting(s): {summary}. "
                  f"{changed} job(s) {'would change' if dry_run else 'changed'}; nothing submitted.")



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

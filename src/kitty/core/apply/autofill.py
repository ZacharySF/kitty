"""Autofill: fill my favorited jobs' forms in the background while I do something else.

It picks the favorites that are still worth filling, best first, at most
config/apply.yaml `autofill_cap` per run, and hands them to the ordinary
filler (filler.fill_jobs) with background tabs and a polite pause between
jobs. The filler stops before submit as always: the tabs wait in the kitty
Firefox for me to review and click submit, and the submission watcher records
each one I submit. When filling is done the summary is pushed through
core/notify.py if .env sets a notifier up (off by default).

Left out, with the reason reported: closed postings, jobs I applied to,
skipped or marked not eligible, and forms verified ready for review.
Partial and blocked attempts remain eligible, including Workday guest/session forms.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kitty.config import Config
from kitty.core import favorites as favorites_core
from kitty.core import notify
from kitty.core.apply.browser import own_scope
from kitty.core.apply.filler import FillReport, fill_jobs
from kitty.core.apply.watch import Watch
from kitty.db import connect
from kitty.models import Job
from kitty.settings import Settings

#: Where a detached autofill run writes what it prints (data/ is gitignored).
LOG_NAME = "autofill.log"


@dataclass(frozen=True)
class Left:
    """A favorite this run does not fill, and why."""

    job_id: int
    company: str
    title: str
    reason: str


@dataclass
class Plan:
    """Which favorites one run fills (best first) and which it leaves out."""

    job_ids: list[int] = field(default_factory=list)
    left: list[Left] = field(default_factory=list)


@dataclass
class AutofillResult:
    """What one autofill run did."""

    plan: Plan
    reports: list[FillReport] = field(default_factory=list)
    failure: str | None = None    # the one error every job hit (Firefox busy or missing)
    pushed: str | None = None     # what notify said, None when no notifier is set up


def _why_not(job: Job) -> str | None:
    """Why autofill leaves this favorite alone, or None when it should fill it."""
    if not job.is_active:
        return "posting closed"
    if job.application_id is not None:
        return f"already applied ({job.status})"
    if job.queue_state in ("skipped", "not_eligible", "applied"):
        return f"queue state {job.queue_state}"
    if job.fill_state == "ready":
        return ("verified ready for review at the last attempt; nothing submitted "
                f"(kitty apply {job.id} rechecks or restarts its tab)")
    return None


def plan_run(
    conn: sqlite3.Connection, config: Config, *, limit: int | None = None, track: str | None = None,
) -> Plan:
    """The favorites to fill this run, best first, capped at `limit` and autofill_cap.

    With `track`, only favorites in that track (the track I am filtered on); the
    others are not part of this run at all, so they are not listed as left out.
    """
    cap = min(limit, config.apply.autofill_cap) if limit else config.apply.autofill_cap
    plan = Plan()
    for job in favorites_core.list_favorites(conn, config, include_closed=True):
        if track and track not in job.tracks:
            continue
        reason = _why_not(job)
        if reason is None and len(plan.job_ids) >= cap:
            reason = f"over this run's cap of {cap}; the next run takes it"
        if reason is None:
            plan.job_ids.append(job.id)
        else:
            plan.left.append(Left(job.id, job.company_name, job.title, reason))
    return plan


def _common_failure(reports: list[FillReport]) -> str | None:
    """The error every job hit, when the browser could not be used at all."""
    errors = {report.error for report in reports}
    if reports and all(report.status == "error" for report in reports) and len(errors) == 1:
        return errors.pop()
    return None


def summary(result: AutofillResult) -> str:
    """Plain-text digest of a run: per job filled / blanks left, then what was left out."""
    lines = []
    if result.failure:
        lines.append(f"Autofill could not run: {result.failure}")
    elif result.plan.job_ids:
        ready = sum(report.status == "ready" for report in result.reports)
        lines.append(f"Autofill: {ready} of {len(result.plan.job_ids)} favorite(s) verified ready for review; "
                     f"{len(result.reports)} attempted.")
        for report in result.reports:
            blanks = f", {len(report.unanswered)} blank(s) left for you" if report.unanswered else ""
            note = f" ({report.error or report.note})" if report.error or report.note else ""
            lines.append(f"{report.job_id} {report.company} - {report.title}: {report.status}, "
                         f"{len(report.filled)} field(s) filled{blanks}{note}")
        if result.reports:
            lines.append("Nothing was submitted: review each tab in the rika! Firefox and click submit.")
    for left in result.plan.left:
        lines.append(f"skipped {left.job_id} {left.company} - {left.title}: {left.reason}")
    if not result.plan.job_ids and not result.plan.left:
        lines.append("No favorites yet: star jobs with * in the TUI or `kitty fav <ids>`.")
    return "\n".join(lines)


def run_autofill(
    settings: Settings,
    config: Config,
    *,
    limit: int | None = None,
    track: str | None = None,
    watch: Watch | None = None,
    progress: Callable[[str], None] | None = None,
    on_filled: Callable[[AutofillResult], None] | None = None,
) -> AutofillResult:
    """Fill this run's favorites (only `track`'s, when given) in background tabs; never submits.

    Opens its own connection for planning (thread-safe, like fill_jobs).
    `on_filled` gets the result as soon as filling is done, after the digest
    is pushed; with a `watch` the call then keeps watching the tabs for my
    submits (see filler.fill_jobs) before it returns.
    """
    conn = connect(settings.db_path)
    try:
        result = AutofillResult(plan=plan_run(conn, config, limit=limit, track=track))
    finally:
        conn.close()

    def filled(reports: list[FillReport]) -> None:
        result.reports = reports
        result.failure = _common_failure(reports)
        if result.plan.job_ids:
            result.pushed = notify.send_digest(summary(result), title="kitty autofill")
        if on_filled is not None:
            on_filled(result)

    if not result.plan.job_ids:
        filled([])
        return result
    fill_jobs(settings, config, result.plan.job_ids, progress=progress, watch=watch,
              on_filled=filled, background=True, pause=config.apply.autofill_pause_seconds)
    return result


def start_detached(settings: Settings, arguments: list[str]) -> tuple[int, Path]:
    """Start `kitty <arguments>` on its own, so closing this terminal does not stop it.

    Same idea as the Firefox window (browser.own_scope): its own systemd user
    scope when systemd is there, a new session, no terminal. What it prints
    goes to data/autofill.log. Returns (pid, log path).
    """
    log_path = settings.data_dir / LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = own_scope([sys.executable, "-m", "kitty.cli", *arguments])
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=settings.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True,
            env={**os.environ, "KITTY_DB": str(settings.db_path), "KITTY_HOME": str(settings.root)},
        )
    return process.pid, log_path

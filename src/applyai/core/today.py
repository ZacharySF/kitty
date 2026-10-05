"""`applyai today`: the daily digest.

One read-only pass over the database answers "what needs me today": reminders
due or overdue, deadlines coming up, new A/B jobs per track, this week's
progress, sources that failed, applications gone quiet, and companies whose
postings should open soon. `digest_text` is the short plain-text form used by
the CLI and by push notifications, so both always say the same thing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from applyai.config import Config
from applyai.core import jobs as jobs_core
from applyai.core import pipeline as pipeline_core
from applyai.core import sources as sources_core
from applyai.core import stats as stats_core
from applyai.core import tracks as tracks_core
from applyai.core.discovery.openings import OpeningSoon, opening_soon
from applyai.dates import now, parse
from applyai.models import Job, PipelineCard, SourceHealth, TrackProgress

#: Each list in the text digest stops after this many lines (push messages stay short).
TEXT_LIMIT = 8


@dataclass(frozen=True)
class DueReminder:
    """An open reminder due today or earlier, with the job it is about."""

    id: int
    kind: str
    due_at: str
    note: str | None
    job_id: int | None
    label: str                  # "Company - Title", or the note when no job is attached


@dataclass(frozen=True)
class Digest:
    """Everything `applyai today` shows, computed once."""

    day: str                                   # YYYY-MM-DD
    reminders: list[DueReminder]               # due today or overdue, oldest first
    deadlines: list[Job]                       # not applied, deadline within the window
    new_by_track: dict[str, dict[str, int]]    # track -> {"A": n, "B": n}
    weekly: list[TrackProgress]
    weekly_total: int
    streak_days: int
    source_problems: list[SourceHealth]
    stale: list[PipelineCard]                  # quiet long enough to suggest archiving
    opens_soon: list[OpeningSoon]


def _end_of(day: date) -> str:
    """The last stored timestamp that still falls on `day` (UTC)."""
    return f"{day.isoformat()}T23:59:59Z"


def due_reminders(conn: sqlite3.Connection, day: date) -> list[DueReminder]:
    """Open reminders due on or before `day`, oldest first, labelled with their job.

    A follow-up stops showing once its application has moved past 'applied': an
    OA, interview or rejection is the response the follow-up was waiting for.
    """
    rows = conn.execute(
        """
        SELECT r.id, r.kind, r.due_at, r.note,
               COALESCE(r.job_id, a.job_id) AS job_id,
               c.display_name AS company_name, j.title AS title
          FROM reminders r
          LEFT JOIN applications a ON a.id = r.application_id
          LEFT JOIN jobs j         ON j.id = COALESCE(r.job_id, a.job_id)
          LEFT JOIN companies c    ON c.id = j.company_id
         WHERE r.done_at IS NULL AND r.due_at <= ?
           AND NOT (r.kind = 'followup' AND a.status IS NOT NULL AND a.status != 'applied')
         ORDER BY r.due_at, r.id
        """,
        (_end_of(day),),
    ).fetchall()
    return [
        DueReminder(
            id=row["id"], kind=row["kind"], due_at=row["due_at"], note=row["note"],
            job_id=row["job_id"],
            label=(f"{row['company_name']} - {row['title']}" if row["title"]
                   else row["note"] or row["kind"]),
        )
        for row in rows
    ]


def upcoming_deadlines(conn: sqlite3.Connection, day: date, window_days: int) -> list[Job]:
    """Jobs I have not applied to (or skipped) whose deadline is within `window_days` of `day`."""
    last = day + timedelta(days=window_days)
    found: list[Job] = []
    for job in jobs_core.list_jobs(
        conn, jobs_core.JobFilter(has_deadline=True, not_applied=True, order="deadline")
    ):
        deadline = parse(job.deadline_at)
        if deadline is None or job.queue_state in ("skipped", "not_eligible"):
            continue
        if day <= deadline.date() <= last:
            found.append(job)
    return found


def new_by_track(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """New (untouched, not applied) A and B jobs per active track, in tracks.yaml order."""
    counts: dict[str, dict[str, int]] = {
        name: {"A": 0, "B": 0} for name in tracks_core.list_track_names(conn)
    }
    rows = conn.execute(
        """
        SELECT jt.track AS track, j.tier AS tier, COUNT(*) AS n
          FROM jobs j
          JOIN job_tracks jt ON jt.job_id = j.id
          LEFT JOIN applications a ON a.job_id = j.id
         WHERE j.is_active = 1 AND j.queue_state = 'new' AND a.id IS NULL
           AND j.tier IN ('A', 'B')
         GROUP BY jt.track, j.tier
        """
    )
    for row in rows:
        if row["track"] in counts:
            counts[row["track"]][row["tier"]] = row["n"]
    return counts


def source_problems(conn: sqlite3.Connection) -> list[SourceHealth]:
    """Enabled sources whose last run failed or returned incomplete coverage."""
    return [
        source for source in sources_core.list_sources(conn)
        if source.enabled and source.last_status in sources_core.PROBLEM_STATUSES
    ]


def build_digest(conn: sqlite3.Connection, config: Config, *, today: date | None = None) -> Digest:
    """Compute today's digest. Read-only; `today` exists for tests (default: UTC today)."""
    day = today or now().date()
    return Digest(
        day=day.isoformat(),
        reminders=due_reminders(conn, day),
        deadlines=upcoming_deadlines(conn, day, config.apply.deadline_window_days),
        new_by_track=new_by_track(conn),
        weekly=stats_core.weekly_progress(conn),
        weekly_total=stats_core.weekly_total(conn),
        streak_days=stats_core.streak_days(conn, today=day),
        source_problems=source_problems(conn),
        stale=pipeline_core.stale_applications(conn, days=config.apply.archive_after_days),
        opens_soon=opening_soon(conn, day),
    )


def _section(title: str, lines: list[str]) -> list[str]:
    """A titled block, cut to TEXT_LIMIT lines; nothing at all when `lines` is empty."""
    if not lines:
        return []
    shown = [f"  {line}" for line in lines[:TEXT_LIMIT]]
    if len(lines) > TEXT_LIMIT:
        shown.append(f"  ... and {len(lines) - TEXT_LIMIT} more")
    return [f"{title} ({len(lines)})", *shown, ""]


def _short_date(stored: str | None) -> str:
    """'YYYY-MM-DD' from a stored timestamp (the raw text when it does not parse)."""
    parsed = parse(stored)
    return parsed.date().isoformat() if parsed else (stored or "?")


def digest_text(digest: Digest) -> str:
    """The digest as short plain text: non-empty sections, then one line per track."""
    lines = [f"applyai today - {digest.day}", ""]
    lines += _section("Due", [
        f"{_short_date(r.due_at)} {r.kind}: {r.label}"
        + (f" ({r.note})" if r.note and r.note != r.label else "")
        for r in digest.reminders
    ])
    lines += _section("Deadlines soon", [
        f"{_short_date(job.deadline_at)} {job.company_name} - {job.title}" for job in digest.deadlines
    ])
    lines += _section("Source problems", [
        sources_core.source_warning(s, detail_limit=160)
        for s in digest.source_problems
    ])
    lines += _section("Quiet, consider archiving", [
        f"{card.company_name} - {card.title} ({card.days_since_change} days)" for card in digest.stale
    ])
    lines += _section("Opens soon", [
        f"{o.opens_on.isoformat()} {o.company} ({o.basis})" for o in digest.opens_soon
    ])
    streak = f", streak {digest.streak_days} day{'s' if digest.streak_days != 1 else ''}"
    lines.append(f"This week: {digest.weekly_total} applied{streak if digest.streak_days else ''}")
    targets = {progress.track: progress for progress in digest.weekly}
    for track, tiers in digest.new_by_track.items():
        progress = targets.get(track)
        week = f"{progress.applied_this_week}/{progress.weekly_target} this week" if progress else "-"
        lines.append(f"  {track}: {week}, new A {tiers.get('A', 0)} B {tiers.get('B', 0)}")
    return "\n".join(lines)

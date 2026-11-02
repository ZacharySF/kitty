"""Weekly progress and simple reports.

Response-rate-by-resume lives here too; Stage 6 adds the screen that shows it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from kitty.core import tracks as tracks_core
from kitty.dates import now, parse, week_start_iso
from kitty.models import OPEN_STATUSES, TrackProgress


def applied_since(conn: sqlite3.Connection, since_iso: str) -> dict[str | None, int]:
    """Applications recorded since a timestamp, grouped by track."""
    return {
        row["track"]: row["n"]
        for row in conn.execute(
            "SELECT track, COUNT(*) AS n FROM applications WHERE applied_at >= ? GROUP BY track",
            (since_iso,),
        )
    }


def weekly_progress(conn: sqlite3.Connection) -> list[TrackProgress]:
    """Applications this week against each active track's target.

    The week starts Monday UTC. Applications with no track still count in the
    total shown on the dashboard, but they cannot count toward a target.
    """
    counts = applied_since(conn, week_start_iso())
    return [
        TrackProgress(
            track=name,
            applied_this_week=counts.get(name, 0),
            weekly_target=target,
        )
        for name, target in tracks_core.weekly_targets(conn).items()
    ]


def weekly_total(conn: sqlite3.Connection) -> int:
    """Every application recorded this week, track or not."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE applied_at >= ?", (week_start_iso(),)
    ).fetchone()
    return int(row["n"])


def streak_days(conn: sqlite3.Connection, *, today: date | None = None) -> int:
    """How many days in a row, up to today, I applied to at least one job.

    A today with no application yet does not break the streak - it counts back
    from yesterday until the day is over. Days are UTC dates, like every stored
    timestamp.
    """
    day = today or now().date()
    applied_days: set[date] = set()
    for row in conn.execute("SELECT applied_at FROM applications"):
        parsed = parse(row["applied_at"])
        if parsed is not None:
            applied_days.add(parsed.date())
    if day not in applied_days:
        day -= timedelta(days=1)
    streak = 0
    while day in applied_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


_ADVANCED = frozenset({"oa", "interview", "offer"})
_REPLIES = _ADVANCED | {"rejected"}


def report_summary(conn: sqlite3.Connection, *, today: date | None = None, weeks: int = 8) -> dict:
    """The figures behind the Reports narrative card: this week and this month so far.

    `weeks` counts applications per Monday-start week, oldest first, ending with this
    week. Replies, advances and offers count distinct applications with a confirmed,
    not-undone status change inside the period. Days are UTC dates, like streak_days.
    """
    day = today or now().date()
    monday = day - timedelta(days=day.weekday())
    first = monday - timedelta(weeks=weeks - 1)
    month = day.replace(day=1)
    previous = (month - timedelta(days=1)).replace(day=1)
    applied = [parsed.date() for row in conn.execute("SELECT applied_at FROM applications")
               if (parsed := parse(row["applied_at"])) is not None and parsed.date() <= day]
    per_week = [0] * weeks
    for when in applied:
        if when >= first:
            per_week[(when - first).days // 7] += 1
    events = [(row["application_id"], row["to_status"], parsed.date()) for row in conn.execute(
        """
        SELECT application_id, to_status, occurred_at FROM status_events
         WHERE state = 'confirmed' AND undoes_event_id IS NULL
           AND id NOT IN (SELECT undoes_event_id FROM status_events WHERE undoes_event_id IS NOT NULL)
        """
    ) if (parsed := parse(row["occurred_at"])) is not None]

    def period(start: date) -> dict:
        inside = [(app, status) for app, status, when in events if start <= when <= day]
        return {
            "start": start.isoformat(),
            "applied": sum(1 for when in applied if when >= start),
            "replies": len({app for app, status in inside if status in _REPLIES}),
            "advanced": len({app for app, status in inside if status in _ADVANCED}),
            "offers": len({app for app, status in inside if status == "offer"}),
        }

    return {
        "weeks": [{"start": (first + timedelta(weeks=index)).isoformat(), "applied": count}
                  for index, count in enumerate(per_week)],
        "weekly_total": per_week[-1],
        "weekly_target": sum(tracks_core.weekly_targets(conn).values()),
        "streak_days": streak_days(conn, today=day),
        "week": period(monday),
        "month": {**period(month), "previous_applied": sum(1 for when in applied if previous <= when < month)},
    }


def counts_by_queue_state(conn: sqlite3.Connection) -> dict[str, int]:
    """How many open jobs sit in each queue state (drives the Inbox badge)."""
    return {
        row["queue_state"]: row["n"]
        for row in conn.execute(
            "SELECT queue_state, COUNT(*) AS n FROM jobs WHERE is_active = 1 GROUP BY queue_state"
        )
    }


def counts_by_tier(conn: sqlite3.Connection) -> dict[str, int]:
    """Open, not-yet-applied jobs per tier."""
    return {
        (row["tier"] or "untriaged"): row["n"]
        for row in conn.execute(
            """
            SELECT j.tier AS tier, COUNT(*) AS n
              FROM jobs j
              LEFT JOIN applications a ON a.job_id = j.id
             WHERE j.is_active = 1 AND a.id IS NULL
             GROUP BY j.tier
            """
        )
    }


@dataclass(frozen=True)
class ResponseRate:
    """Outcome counts for one grouping key (a resume, a track, a source, a tier)."""

    key: str
    applications: int
    responses: int          # OA, interview, offer or rejection
    advanced: int           # OA or beyond
    offers: int
    open_count: int

    @property
    def response_rate(self) -> float:
        return self.responses / self.applications if self.applications else 0.0

    @property
    def advance_rate(self) -> float:
        return self.advanced / self.applications if self.applications else 0.0


def _response_rates(rows: list[sqlite3.Row]) -> list[ResponseRate]:
    """Fold status counts into ResponseRate rows."""
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = grouped.setdefault(row["key"] or "(none)", {})
        bucket[row["status"]] = bucket.get(row["status"], 0) + row["n"]

    results: list[ResponseRate] = []
    for key, counts in grouped.items():
        total = sum(counts.values())
        advanced = counts.get("oa", 0) + counts.get("interview", 0) + counts.get("offer", 0)
        results.append(
            ResponseRate(
                key=key,
                applications=total,
                responses=advanced + counts.get("rejected", 0),
                advanced=advanced,
                offers=counts.get("offer", 0),
                open_count=sum(n for status, n in counts.items() if status in OPEN_STATUSES),
            )
        )
    return sorted(results, key=lambda r: (-r.applications, r.key))


def response_rate_by_resume(conn: sqlite3.Connection) -> list[ResponseRate]:
    """Response rate grouped by the resume I sent."""
    rows = conn.execute(
        """
        SELECT COALESCE(r.label, '(none)') AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
          LEFT JOIN resumes r ON r.id = a.resume_id
         GROUP BY key, a.status
        """
    ).fetchall()
    return _response_rates(rows)


def response_rate_by_track(conn: sqlite3.Connection) -> list[ResponseRate]:
    """Response rate grouped by track."""
    rows = conn.execute(
        """
        SELECT COALESCE(a.track, '(none)') AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
         GROUP BY key, a.status
        """
    ).fetchall()
    return _response_rates(rows)


def response_rate_by_source(conn: sqlite3.Connection) -> list[ResponseRate]:
    """Response rate grouped by the source a job came from.

    A job found in two sources counts once per source, which is the honest way to
    answer "which source produces jobs I actually hear back from".
    """
    rows = conn.execute(
        """
        SELECT js.source AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
          JOIN job_sources js ON js.job_id = a.job_id
         GROUP BY key, a.status
        """
    ).fetchall()
    return _response_rates(rows)


def response_rate_by_tier(conn: sqlite3.Connection) -> list[ResponseRate]:
    """Response rate grouped by the job's tier (A / B / C, or untriaged).

    The tier is the job's current one; tiers are set before I apply and rarely
    change after, so this answers "do my A picks hear back more than my B ones".
    """
    rows = conn.execute(
        """
        SELECT COALESCE(j.tier, 'untriaged') AS key, a.status AS status, COUNT(*) AS n
          FROM applications a
          JOIN jobs j ON j.id = a.job_id
         GROUP BY key, a.status
        """
    ).fetchall()
    return _response_rates(rows)


#: The response-rate report's sections, in the order they are shown.
REPORT_GROUPS = {
    "resume": response_rate_by_resume,
    "track": response_rate_by_track,
    "source": response_rate_by_source,
    "tier": response_rate_by_tier,
}


def response_report(conn: sqlite3.Connection) -> dict[str, list[ResponseRate]]:
    """Every response-rate grouping at once: by resume, track, source and tier."""
    return {name: rates(conn) for name, rates in REPORT_GROUPS.items()}

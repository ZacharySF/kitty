"""The kanban board and the funnel."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from kitty.dates import days_since
from kitty.models import PIPELINE_STATUSES, PipelineCard, Status

_CARD_SELECT = """
SELECT a.id            AS application_id,
       a.job_id        AS job_id,
       a.status        AS status,
       a.track         AS track,
       a.applied_at    AS applied_at,
       a.last_activity_at AS last_activity_at,
       a.next_action_at   AS next_action_at,
       c.display_name  AS company_name,
       j.title         AS title,
       r.label         AS resume_label,
       EXISTS (SELECT 1 FROM status_events se
                WHERE se.application_id = a.id AND se.state = 'proposed') AS has_proposals
  FROM applications a
  JOIN jobs j      ON j.id = a.job_id
  JOIN companies c ON c.id = j.company_id
  LEFT JOIN resumes r ON r.id = a.resume_id
"""


def _card(row: sqlite3.Row) -> PipelineCard:
    """Build one board card, computing how stale it is."""
    return PipelineCard(
        application_id=row["application_id"],
        job_id=row["job_id"],
        company_name=row["company_name"],
        title=row["title"],
        status=row["status"],
        track=row["track"],
        resume_label=row["resume_label"],
        applied_at=row["applied_at"],
        last_activity_at=row["last_activity_at"],
        days_since_change=days_since(row["last_activity_at"]),
        next_action_at=row["next_action_at"],
        has_proposals=bool(row["has_proposals"]),
    )


def board(
    conn: sqlite3.Connection, *, track: str | None = None
) -> dict[Status, list[PipelineCard]]:
    """Applications grouped into the pipeline columns, stalest first.

    Withdrawn applications are left out of the board; they are still in the
    database and in exports.
    """
    sql = _CARD_SELECT
    params: list[object] = []
    clauses = ["a.status != 'withdrawn'"]
    if track:
        clauses.append("a.track = ?")
        params.append(track)
    sql += " WHERE " + " AND ".join(clauses) + " ORDER BY a.last_activity_at"

    columns: dict[Status, list[PipelineCard]] = {status: [] for status in PIPELINE_STATUSES}
    for row in conn.execute(sql, params):
        card = _card(row)
        columns.setdefault(card.status, []).append(card)
    return columns


def cards_for_status(
    conn: sqlite3.Connection, status: Status, *, track: str | None = None
) -> list[PipelineCard]:
    """One pipeline column."""
    return board(conn, track=track).get(status, [])


@dataclass(frozen=True)
class Funnel:
    """Counts down the pipeline, plus the rates I actually care about."""

    applied: int
    oa: int
    interview: int
    offer: int
    rejected: int
    ghosted: int

    @property
    def total(self) -> int:
        """Every application ever recorded (each one is counted once)."""
        return self.applied + self.oa + self.interview + self.offer + self.rejected + self.ghosted

    @property
    def response_rate(self) -> float:
        """Share of applications that got any response (OA, interview, offer, rejection)."""
        if not self.total:
            return 0.0
        return (self.oa + self.interview + self.offer + self.rejected) / self.total

    @property
    def advance_rate(self) -> float:
        """Share of applications that reached OA or beyond."""
        if not self.total:
            return 0.0
        return (self.oa + self.interview + self.offer) / self.total


def funnel(conn: sqlite3.Connection, *, track: str | None = None) -> Funnel:
    """Current status counts across the pipeline.

    These are *current* states, not cumulative history: an application that went
    applied -> OA -> rejected counts only in 'rejected'.
    """
    sql = "SELECT status, COUNT(*) AS n FROM applications WHERE status != 'withdrawn'"
    params: list[object] = []
    if track:
        sql += " AND track = ?"
        params.append(track)
    sql += " GROUP BY status"

    counts = {row["status"]: row["n"] for row in conn.execute(sql, params)}
    return Funnel(
        applied=counts.get("applied", 0),
        oa=counts.get("oa", 0),
        interview=counts.get("interview", 0),
        offer=counts.get("offer", 0),
        rejected=counts.get("rejected", 0),
        ghosted=counts.get("ghosted", 0),
    )


def stale_applications(
    conn: sqlite3.Connection, *, days: int = 21
) -> list[PipelineCard]:
    """Open applications with no activity for `days` - the archive-me candidates."""
    rows = conn.execute(
        _CARD_SELECT + " WHERE a.status IN ('applied', 'oa') ORDER BY a.last_activity_at"
    ).fetchall()
    return [card for card in map(_card, rows) if card.days_since_change >= days]

"""Pending status proposals, with the company and title needed to judge them.

Confirming and declining are applications.confirm_proposal / reject_proposal;
this module only reads.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

_SELECT = """
SELECT se.id, se.application_id, a.job_id, c.display_name, j.title, a.status,
       se.from_status, se.to_status, se.occurred_at, se.source, se.evidence
  FROM status_events se
  JOIN applications a ON a.id = se.application_id
  JOIN jobs j         ON j.id = a.job_id
  JOIN companies c    ON c.id = j.company_id
 WHERE se.state = 'proposed'
"""


@dataclass(frozen=True)
class PendingProposal:
    """One proposed status change waiting for my keypress."""

    event_id: int
    application_id: int
    job_id: int
    company: str
    title: str
    current_status: str       # the application's status now
    from_status: str | None   # its status when the proposal was made
    to_status: str
    occurred_at: str
    source: str
    evidence: str | None


def pending_proposals(conn: sqlite3.Connection) -> list[PendingProposal]:
    """Every proposal not yet confirmed or declined, newest first."""
    rows = conn.execute(_SELECT + " ORDER BY se.occurred_at DESC, se.id DESC")
    return [PendingProposal(*tuple(row)) for row in rows]


def get_pending(conn: sqlite3.Connection, event_id: int) -> PendingProposal | None:
    """One pending proposal by event id; None when it does not exist or was decided."""
    row = conn.execute(_SELECT + " AND se.id = ?", (event_id,)).fetchone()
    return PendingProposal(*tuple(row)) if row else None

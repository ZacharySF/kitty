"""Application preparation evidence, independent of actual submission history."""
from __future__ import annotations

import sqlite3

from applyai.db.connection import transaction
from applyai.dates import now_iso


def record_attempt(
    conn: sqlite3.Connection, job_id: int, state: str, *, blocker: str | None = None,
    context_id: str | None = None, url: str | None = None,
) -> None:
    """Replace preparation evidence; only verified review readiness gets a ready timestamp."""
    if state not in {"attempted", "partial", "blocked", "ready"}:
        raise ValueError(f"invalid application preparation state: {state}")
    timestamp = now_iso()
    with transaction(conn):
        conn.execute(
            """UPDATE jobs SET fill_state = ?, fill_attempted_at = ?,
               fill_ready_at = ?, fill_blocker = ?, fill_context_id = ?, fill_url = ?,
               updated_at = ? WHERE id = ?""",
            (state, timestamp, timestamp if state == "ready" else None,
             blocker, context_id, url, timestamp, job_id),
        )

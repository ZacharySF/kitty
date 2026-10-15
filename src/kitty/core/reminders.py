"""Reminders: follow-ups, deadlines and 'opens soon' hints.

Stage 1 stores and lists them (Job Detail sets the next action). Stage 4 creates
the +7/+14/+21 follow-up set automatically when I mark something applied.
"""

from __future__ import annotations

import sqlite3

from kitty.dates import now_iso
from kitty.models import Reminder


def create_reminder(
    conn: sqlite3.Connection,
    *,
    kind: str,
    due_at: str,
    note: str | None = None,
    job_id: int | None = None,
    application_id: int | None = None,
) -> Reminder:
    """Add a reminder. `kind` is followup | deadline | opens | custom."""
    cursor = conn.execute(
        """
        INSERT INTO reminders (job_id, application_id, kind, due_at, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, application_id, kind, due_at, note, now_iso()),
    )
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return Reminder.from_row(row)


def due_reminders(conn: sqlite3.Connection, *, through: str | None = None) -> list[Reminder]:
    """Open reminders due at or before `through` (default: now), soonest first."""
    return [
        Reminder.from_row(row)
        for row in conn.execute(
            "SELECT * FROM reminders WHERE done_at IS NULL AND due_at <= ? ORDER BY due_at",
            (through or now_iso(),),
        )
    ]


def upcoming_reminders(conn: sqlite3.Connection, *, limit: int = 20) -> list[Reminder]:
    """Open reminders in the future, soonest first."""
    return [
        Reminder.from_row(row)
        for row in conn.execute(
            "SELECT * FROM reminders WHERE done_at IS NULL AND due_at > ? "
            "ORDER BY due_at LIMIT ?",
            (now_iso(), limit),
        )
    ]


def followups_for(conn: sqlite3.Connection, application_id: int) -> list[Reminder]:
    """Every follow-up reminder of an application, done or not, soonest first."""
    return [
        Reminder.from_row(row)
        for row in conn.execute(
            "SELECT * FROM reminders WHERE application_id = ? AND kind = 'followup' "
            "ORDER BY due_at",
            (application_id,),
        )
    ]


def complete_reminder(conn: sqlite3.Connection, reminder_id: int) -> None:
    """Mark a reminder done."""
    conn.execute(
        "UPDATE reminders SET done_at = ? WHERE id = ? AND done_at IS NULL",
        (now_iso(), reminder_id),
    )

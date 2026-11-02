"""Reminders: follow-ups, deadlines and 'opens soon' hints.

Stage 1 stores and lists them (Job Detail sets the next action). Stage 4 creates
the +7/+14/+21 follow-up set automatically when I mark something applied.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from kitty.dates import now, now_iso, parse, to_iso
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


def update_reminder(
    conn: sqlite3.Connection, reminder_id: int, *, due_at: str | None = None, note: str | None = None,
) -> None:
    """Change a reminder's date and/or note; None leaves that field as it is."""
    if due_at is not None:
        conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (due_at, reminder_id))
    if note is not None:
        conn.execute("UPDATE reminders SET note = ? WHERE id = ?", (note or None, reminder_id))


def snooze_reminder(conn: sqlite3.Connection, reminder_id: int) -> tuple[str, str]:
    """Push a reminder one day past the later of its due date and now.

    Returns (previous due_at, new due_at) so the caller can offer undo.
    """
    previous = conn.execute("SELECT due_at FROM reminders WHERE id = ?", (reminder_id,)).fetchone()["due_at"]
    current = now()
    base = max(parse(previous) or current, current)
    due_at = to_iso(base + timedelta(days=1))
    conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (due_at, reminder_id))
    return previous, due_at


def delete_reminder(conn: sqlite3.Connection, reminder_id: int) -> dict:
    """Delete a reminder and return its full row, which `restore_reminder` puts back."""
    row = dict(conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())
    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    return row


def restore_reminder(conn: sqlite3.Connection, row: dict) -> None:
    """Re-insert a deleted reminder exactly as `delete_reminder` returned it."""
    conn.execute(
        "INSERT INTO reminders (id, job_id, application_id, kind, due_at, note, done_at, created_at) "
        "VALUES (:id, :job_id, :application_id, :kind, :due_at, :note, :done_at, :created_at)",
        row,
    )

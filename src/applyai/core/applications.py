"""Applications and their status history.

Two rules shape this module:
  * `status_events` is append-only. Corrections are new rows, including undo.
  * A 'proposed' event (Stage 6 Gmail) changes nothing until I confirm it.
"""

from __future__ import annotations

import sqlite3

from applyai.core import jobs as jobs_core
from applyai.dates import now_iso
from applyai.models import Application, Status, StatusEvent

_APP_SELECT = """
SELECT a.*, r.label AS resume_label
  FROM applications a
  LEFT JOIN resumes r ON r.id = a.resume_id
"""


def get_application(conn: sqlite3.Connection, application_id: int) -> Application | None:
    """One application by id."""
    row = conn.execute(_APP_SELECT + " WHERE a.id = ?", (application_id,)).fetchone()
    return Application.from_row(row) if row else None


def get_application_for_job(conn: sqlite3.Connection, job_id: int) -> Application | None:
    """The application for a job, if I have applied to it."""
    row = conn.execute(_APP_SELECT + " WHERE a.job_id = ?", (job_id,)).fetchone()
    return Application.from_row(row) if row else None


def record_application(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    resume_id: int | None = None,
    track: str | None = None,
    applied_at: str | None = None,
    notes: str | None = None,
    source: str = "manual",
    evidence: str | None = None,
) -> Application:
    """Record that I applied to a job.

    Idempotent: a job already applied to returns its existing application, so the
    CSV importer and a stray keypress cannot create duplicates. Also sets the
    job's queue_state to 'applied' and writes the first status_event.
    """
    existing = get_application_for_job(conn, job_id)
    if existing is not None:
        return existing

    timestamp = applied_at or now_iso()
    cursor = conn.execute(
        """
        INSERT INTO applications (job_id, track, resume_id, status, applied_at,
                                  last_activity_at, notes, created_at, updated_at)
        VALUES (?, ?, ?, 'applied', ?, ?, ?, ?, ?)
        """,
        (job_id, track, resume_id, timestamp, timestamp, notes, now_iso(), now_iso()),
    )
    application_id = int(cursor.lastrowid)

    conn.execute(
        """
        INSERT INTO status_events (application_id, from_status, to_status, occurred_at,
                                  source, evidence, state, created_at)
        VALUES (?, NULL, 'applied', ?, ?, ?, 'confirmed', ?)
        """,
        (application_id, timestamp, source, evidence, now_iso()),
    )

    jobs_core.set_queue_state(conn, job_id, "applied")
    if resume_id is not None:
        jobs_core.set_resume(conn, job_id, resume_id)

    application = get_application(conn, application_id)
    assert application is not None       # just inserted
    return application


def _append_event(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    from_status: str | None,
    to_status: str,
    occurred_at: str,
    source: str,
    evidence: str | None,
    state: str,
    undoes_event_id: int | None = None,
) -> StatusEvent:
    """Insert one status_events row and return it."""
    cursor = conn.execute(
        """
        INSERT INTO status_events (application_id, from_status, to_status, occurred_at,
                                  source, evidence, state, undoes_event_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application_id, from_status, to_status, occurred_at, source, evidence,
            state, undoes_event_id, now_iso(),
        ),
    )
    row = conn.execute(
        "SELECT * FROM status_events WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return StatusEvent.from_row(row)


def _apply_status(
    conn: sqlite3.Connection, application_id: int, status: str, occurred_at: str
) -> None:
    """Move an application's current status and bump last_activity_at."""
    conn.execute(
        "UPDATE applications SET status = ?, last_activity_at = ?, updated_at = ? WHERE id = ?",
        (status, occurred_at, now_iso(), application_id),
    )


def set_status(
    conn: sqlite3.Connection,
    application_id: int,
    status: Status,
    *,
    occurred_at: str | None = None,
    source: str = "manual",
    evidence: str | None = None,
    state: str = "confirmed",
) -> StatusEvent:
    """Append a status change.

    With state='proposed' (Gmail, Stage 6) the event is recorded but the
    application keeps its current status until I confirm the proposal.
    """
    application = get_application(conn, application_id)
    if application is None:
        raise ValueError(f"no application with id {application_id}")

    timestamp = occurred_at or now_iso()
    event = _append_event(
        conn, application_id,
        from_status=application.status, to_status=status, occurred_at=timestamp,
        source=source, evidence=evidence, state=state,
    )
    if state == "confirmed":
        _apply_status(conn, application_id, status, timestamp)
    return event


def history(conn: sqlite3.Connection, application_id: int) -> list[StatusEvent]:
    """Every status event for an application, oldest first."""
    return [
        StatusEvent.from_row(row)
        for row in conn.execute(
            "SELECT * FROM status_events WHERE application_id = ? ORDER BY id",
            (application_id,),
        )
    ]


def last_undoable_event(
    conn: sqlite3.Connection, application_id: int
) -> StatusEvent | None:
    """The most recent confirmed change that can still be undone.

    Skips events that are themselves undos, events already undone, and the
    initial 'applied' event - undoing that would mean deleting the application,
    which undo deliberately does not do.
    """
    row = conn.execute(
        """
        SELECT * FROM status_events
         WHERE application_id = ?
           AND state = 'confirmed'
           AND undoes_event_id IS NULL
           AND from_status IS NOT NULL
           AND id NOT IN (SELECT undoes_event_id FROM status_events
                           WHERE undoes_event_id IS NOT NULL)
         ORDER BY id DESC
         LIMIT 1
        """,
        (application_id,),
    ).fetchone()
    return StatusEvent.from_row(row) if row else None


def undo_last_status(conn: sqlite3.Connection, application_id: int) -> StatusEvent | None:
    """Revert the last confirmed status change by appending a reverting event.

    Returns the new event, or None when there is nothing to undo. Nothing is
    ever deleted, so the history shows both the change and the undo.
    """
    target = last_undoable_event(conn, application_id)
    if target is None or target.from_status is None:
        return None

    timestamp = now_iso()
    event = _append_event(
        conn, application_id,
        from_status=target.to_status, to_status=target.from_status,
        occurred_at=timestamp, source="manual",
        evidence=f"undo of event {target.id}", state="confirmed",
        undoes_event_id=target.id,
    )
    _apply_status(conn, application_id, target.from_status, timestamp)
    return event


def proposed_events(conn: sqlite3.Connection) -> list[StatusEvent]:
    """Status changes waiting for my confirmation (Stage 6 fills this)."""
    return [
        StatusEvent.from_row(row)
        for row in conn.execute(
            "SELECT * FROM status_events WHERE state = 'proposed' ORDER BY occurred_at DESC"
        )
    ]


def confirm_proposal(conn: sqlite3.Connection, event_id: int) -> StatusEvent | None:
    """Accept a proposed status change: apply it as a new confirmed event.

    The proposal row itself is marked 'confirmed' in place of being deleted, and
    the application status moves.
    """
    row = conn.execute(
        "SELECT * FROM status_events WHERE id = ? AND state = 'proposed'", (event_id,)
    ).fetchone()
    if row is None:
        return None
    event = StatusEvent.from_row(row)
    conn.execute("UPDATE status_events SET state = 'confirmed' WHERE id = ?", (event_id,))
    _apply_status(conn, event.application_id, event.to_status, event.occurred_at)
    return event


def reject_proposal(conn: sqlite3.Connection, event_id: int) -> None:
    """Decline a proposed status change; it stays visible in the history."""
    conn.execute(
        "UPDATE status_events SET state = 'rejected_by_me' WHERE id = ? AND state = 'proposed'",
        (event_id,),
    )


def set_next_action(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    due_at: str | None,
    note: str | None = None,
) -> None:
    """Set the 'what next, and when' field shown on the pipeline and Job Detail."""
    conn.execute(
        "UPDATE applications SET next_action_at = ?, next_action_note = ?, updated_at = ? "
        "WHERE id = ?",
        (due_at, note, now_iso(), application_id),
    )


def set_notes(conn: sqlite3.Connection, application_id: int, notes: str | None) -> None:
    """Replace my notes on an application."""
    conn.execute(
        "UPDATE applications SET notes = ?, updated_at = ? WHERE id = ?",
        (notes, now_iso(), application_id),
    )


def set_resume(conn: sqlite3.Connection, application_id: int, resume_id: int | None) -> None:
    """Record which resume I actually sent (the response-rate report needs this)."""
    conn.execute(
        "UPDATE applications SET resume_id = ?, updated_at = ? WHERE id = ?",
        (resume_id, now_iso(), application_id),
    )

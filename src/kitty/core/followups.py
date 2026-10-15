"""Marking a job applied, with the follow-up reminders that go with it.

Every "I applied" from the CLI, the TUI or the apply session goes through
`mark_applied`, so the +7 / +14 / +21 follow-ups (config/apply.yaml) are never
forgotten. The CSV importer calls `applications.record_application` directly on
purpose: months-old applications should not flood the digest with overdue
follow-ups.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from kitty.config import Config
from kitty.core import applications as applications_core
from kitty.core import reminders as reminders_core
from kitty.dates import now, parse, to_iso
from kitty.models import Application, Reminder


def mark_applied(
    conn: sqlite3.Connection,
    config: Config,
    job_id: int,
    *,
    resume_id: int | None = None,
    track: str | None = None,
    applied_at: str | None = None,
    source: str = "manual",
    evidence: str | None = None,
) -> Application:
    """Record that I applied to a job and schedule its follow-up reminders.

    Safe to call twice: the application is recorded once (record_application is
    idempotent) and a follow-up that already exists is not added again. Nothing
    is scheduled for an application that has moved past 'applied' - an OA or a
    rejection is already the response a follow-up would chase. `source` and
    `evidence` go on the first status event (the apply watcher passes 'rule' and
    the confirmation page it saw).
    """
    application = applications_core.record_application(
        conn, job_id, resume_id=resume_id, track=track, applied_at=applied_at,
        source=source, evidence=evidence,
    )
    if application.status == "applied":
        add_followups(conn, application, config.apply.followup_days)
    return application


def add_followups(
    conn: sqlite3.Connection, application: Application, days: list[int]
) -> list[Reminder]:
    """Create one 'followup' reminder per offset in `days` after the application date.

    An offset whose reminder already exists (open or done) is skipped, so a
    follow-up I ticked off never comes back. Returns the reminders created.
    """
    applied = parse(application.applied_at) or now()
    existing = {reminder.due_at for reminder in reminders_core.followups_for(conn, application.id)}
    created: list[Reminder] = []
    for offset in days:
        due_at = to_iso(applied + timedelta(days=offset))
        if due_at in existing:
            continue
        created.append(reminders_core.create_reminder(
            conn, kind="followup", due_at=due_at,
            note=f"{offset} days after applying",
            job_id=application.job_id, application_id=application.id,
        ))
    return created

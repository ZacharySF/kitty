"""One Gmail check: fetch new status mail and record proposals.

Each new message is classified, matched to one open application, and turned
into a status_events row with state='proposed'. A proposal changes nothing
until I confirm it (applications.confirm_proposal). Every message looked at is
remembered in gmail_messages, whatever the outcome, so it is never fetched or
proposed again. The run is recorded in `runs` with kind 'mail'.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass, field

from applyai.core import applications as applications_core
from applyai.core import sources as sources_core
from applyai.core.gmail import auth
from applyai.core.gmail.classify import classify
from applyai.core.gmail.fetch import (
    REQUEST_DELAY, GmailFetchError, GmailMessage, GmailSession, build_query, get_message,
    list_message_ids,
)
from applyai.core.gmail.match import OpenApplication, match_application, open_applications
from applyai.dates import now_iso
from applyai.db import transaction
from applyai.settings import Settings

#: Most messages one check lists (new ones are then fetched one by one).
DEFAULT_LIMIT = 200


@dataclass(frozen=True)
class Proposal:
    """A status change this check proposed."""

    event_id: int
    application_id: int
    company: str
    title: str
    from_status: str
    to_status: str
    phrase: str
    subject: str


@dataclass
class GmailCheckResult:
    """Counts for one check. `status` is ok | auth | rate_limited | error."""

    status: str = "ok"
    listed: int = 0          # ids Gmail returned for the query
    already_seen: int = 0    # of those, looked at in an earlier check
    fetched: int = 0         # new messages read this check
    classified: int = 0      # of those, recognised as status mail
    matched: int = 0         # of those, tied to one application
    proposed: int = 0
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    proposals: list[Proposal] = field(default_factory=list)
    error: str | None = None

    def skip(self, reason: str) -> None:
        self.skipped_by_reason[reason] = self.skipped_by_reason.get(reason, 0) + 1

    def stats(self) -> dict:
        """The counts stored in runs.stats_json."""
        counts = asdict(self)
        counts.pop("proposals")
        counts.pop("error")
        return counts


def evidence_line(message: GmailMessage, phrase: str) -> str:
    """What a proposal shows as its reason: message id, subject, matched phrase."""
    return f'gmail {message.id}: {message.subject} | "{phrase}"'


def _seen(conn: sqlite3.Connection, message_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM gmail_messages WHERE message_id = ?", (message_id,)).fetchone()
    return row is not None


def _remember(
    conn: sqlite3.Connection, message: GmailMessage, outcome: str, event_id: int | None = None
) -> None:
    """Mark a message as looked at, with what came of it."""
    conn.execute(
        """
        INSERT OR IGNORE INTO gmail_messages
               (message_id, received_at, subject, outcome, event_id, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (message.id, message.received_at, message.subject, outcome, event_id, now_iso()),
    )


def _pending_exists(conn: sqlite3.Connection, application_id: int, to_status: str) -> bool:
    """True when the same change is already waiting for me (two rejection mails, say)."""
    row = conn.execute(
        "SELECT 1 FROM status_events WHERE application_id = ? AND to_status = ? AND state = 'proposed'",
        (application_id, to_status),
    ).fetchone()
    return row is not None


def _process(
    conn: sqlite3.Connection,
    message: GmailMessage,
    applications: list[OpenApplication],
    result: GmailCheckResult,
) -> None:
    """Classify, match and maybe propose one new message; always remember it."""
    verdict = classify(message.subject, message.body)
    if verdict is None:
        _remember(conn, message, "not_status_mail")
        result.skip("not_status_mail")
        return
    result.classified += 1
    to_status, phrase = verdict

    match = match_application(message, applications)
    if match.application is None:
        _remember(conn, message, match.reason or "no_company")
        result.skip(match.reason or "no_company")
        return
    result.matched += 1

    application = applications_core.get_application(conn, match.application.id)
    if application is None or application.status == to_status:
        _remember(conn, message, "same_status")
        result.skip("same_status")
        return
    if _pending_exists(conn, application.id, to_status):
        _remember(conn, message, "already_proposed")
        result.skip("already_proposed")
        return

    with transaction(conn):
        event = applications_core.set_status(
            conn, application.id, to_status, occurred_at=message.received_at or None,
            source="gmail", evidence=evidence_line(message, phrase), state="proposed",
        )
        _remember(conn, message, "proposed", event.id)
    result.proposed += 1
    result.proposals.append(Proposal(
        event.id, application.id, match.application.company_name, match.application.title,
        application.status, to_status, phrase, message.subject,
    ))


def check_gmail(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    days: int = 30,
    session: GmailSession | None = None,
    limit: int = DEFAULT_LIMIT,
    delay: float = REQUEST_DELAY,
) -> GmailCheckResult:
    """Look at the last `days` of recruiting mail and propose status changes.

    Without `session`, the saved token is used; GmailSetupError (with the setup
    steps) is raised before anything is recorded when Gmail is not set up.
    A 401, a 429 or any other API failure stops the check and is reported in
    the result; messages handled before it stay handled.
    """
    if session is None:
        session = auth.authorized_session(settings)
    run_id = sources_core.start_run(conn, "mail")
    result = GmailCheckResult()
    try:
        ids = list_message_ids(session, build_query(days), limit=limit, delay=delay)
        result.listed = len(ids)
        new_ids = [message_id for message_id in ids if not _seen(conn, message_id)]
        result.already_seen = len(ids) - len(new_ids)
        applications = open_applications(conn)
        for message_id in reversed(new_ids):          # oldest first
            time.sleep(delay)
            message = get_message(session, message_id)
            result.fetched += 1
            _process(conn, message, applications, result)
    except GmailFetchError as exc:
        result.status, result.error = exc.kind, str(exc)
    sources_core.finish_run(conn, run_id, stats=result.stats(), error=result.error)
    return result

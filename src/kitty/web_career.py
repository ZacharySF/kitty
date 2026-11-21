"""The Career rail's counts: ``GET /api/career/summary`` -> ``{counts}``.

Each count is the size of the list its rail link opens: New jobs, Queue and Saved count
what the inbox, Ready to apply and Saved lists show (rika-server's job listing,
server/src/jobs/reads.rs); Applied counts open applications (not rejected, ghosted or
withdrawn); Interviews those at the interview stage; Reminders the ones due by today's
end (the Reminders page's "Due"); Email updates the status changes waiting for review.
Plain counts only: the rail asks once per Career page it draws.

The front (rika-server) does not claim ``/api/career``, so it forwards it here.
"""
from __future__ import annotations

from kitty.core import jobs, today
from kitty.dates import now

SUMMARY = "/api/career/summary"
#: Application statuses that end it; the rest are still open.
CLOSED = ("rejected", "ghosted", "withdrawn")
#: The inbox shows jobs first seen or posted in the last two weeks (JobBrowser's default age).
NEW_DAYS = 14


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def summary(conn) -> dict:
    eligible = jobs.NOT_FILTERED_OUT
    new = jobs.JobFilter(queue_states=["new"], not_applied=True, eligibility=eligible, posted_within_days=NEW_DAYS)
    queue = jobs.JobFilter(queue_states=["new", "queued"], tiers=["A", "B"], not_applied=True, eligibility=eligible)
    saved = jobs.JobFilter(favorites_only=True, include_closed=True)
    statuses = {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM applications GROUP BY status")}
    emails = conn.execute("SELECT COUNT(*) FROM status_events WHERE state = 'proposed'").fetchone()[0]
    return {"counts": {
        "new": jobs.count_jobs(conn, new),
        "queued": jobs.count_jobs(conn, queue),
        "saved": jobs.count_jobs(conn, saved),
        "active": sum(count for status, count in statuses.items() if status not in CLOSED),
        "interviews": statuses.get("interview", 0),
        "reminders": len(today.due_reminders(conn, now().date())),
        "emails": emails,
    }}


def get(app, conn, path, query):
    if path != SUMMARY:
        return None
    if query:
        raise _error(400, "This route does not accept query parameters")
    return summary(conn)


def post(app, conn, path, body):
    return None

"""The Career rail's counts over HTTP (kitty.web_career): synthetic jobs and applications only."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from kitty import web_career as routes
from kitty.core import applications, favorites, jobs, reminders
from kitty.dates import now, to_iso
from kitty.web_server import EXTENSIONS, ROUTE_MODULES, APIError


@pytest.fixture
def app(workspace):
    return SimpleNamespace(settings=workspace.settings, config=workspace.config)


def _job(conn, slug: str, *, tier: str | None = None, state: str = "new", posted_days: int = 1, **extra):
    job, _created = jobs.upsert_job(
        conn, company=f"Synthetic {slug}", title=f"Engineer {slug}", url=f"https://synthetic.example/{slug}",
        posted_at=to_iso(now() - timedelta(days=posted_days)), eligibility="eligible", **extra,
    )
    if tier:
        jobs.set_tier(conn, job.id, tier, reason="synthetic")
    if state != "new":
        jobs.set_queue_state(conn, job.id, state)
    return job.id


def test_the_route_runs_with_applyai():
    assert ("applyai", "web_career") in EXTENSIONS
    assert ("/api/career", "applyai") in ROUTE_MODULES


def test_an_empty_workspace_counts_nothing(app, workspace):
    body = routes.get(app, workspace.conn, "/api/career/summary", {})
    assert body == {"counts": {"new": 0, "queued": 0, "saved": 0, "active": 0, "interviews": 0,
                               "reminders": 0, "emails": 0}}


def test_counts_match_the_lists_each_rail_link_opens(app, workspace):
    c = workspace.conn
    _job(c, "fresh")                                          # New jobs
    _job(c, "old", posted_days=40)                            # too old for New jobs
    _job(c, "ready", tier="A", state="queued")                # Queue
    _job(c, "strong", tier="B")                               # New jobs and Queue (new, B)
    _job(c, "weak", tier="C", state="queued")                 # not Queue: C
    saved = _job(c, "saved", state="skipped")
    favorites.favorite(c, [saved])
    applied = [_job(c, f"applied-{n}") for n in range(4)]
    records = [applications.record_application(c, job) for job in applied]
    applications.set_status(c, records[1].id, "interview")
    applications.set_status(c, records[2].id, "rejected")
    applications.set_status(c, records[3].id, "oa", state="proposed")   # a status email to review
    reminders.create_reminder(c, job_id=applied[0], kind="custom", due_at=to_iso(now() - timedelta(hours=1)), note="Call")
    reminders.create_reminder(c, job_id=applied[0], kind="custom", due_at=to_iso(now() + timedelta(days=3)), note="Later")

    counts = routes.get(app, c, "/api/career/summary", {})["counts"]
    assert counts == {"new": 2, "queued": 2, "saved": 1, "active": 3, "interviews": 1, "reminders": 1, "emails": 1}


def test_the_summary_takes_no_query_and_other_paths_pass(app, workspace):
    with pytest.raises(APIError) as caught:
        routes.get(app, workspace.conn, "/api/career/summary", {"x": "1"})
    assert caught.value.status == 400
    assert routes.get(app, workspace.conn, "/api/career", {}) is None
    assert routes.get(app, workspace.conn, "/api/career/summary/1", {}) is None

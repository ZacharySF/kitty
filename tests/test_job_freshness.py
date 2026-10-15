"""Live verification freshness is not the same as a discovery sighting."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from kitty import models
from kitty.models import Job


@pytest.fixture
def job(monkeypatch):
    monkeypatch.setattr(models, "now", lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc))
    return Job(id=1, company_id=1, company_name="Sample", title="Intern",
               url="https://example.invalid/1", verification_status="open",
               verified_at="2026-10-01T12:00:00Z")


def test_verification_expires_without_being_renewed_by_discovery(job):
    assert job.freshness == "verified"
    assert replace(job, verified_at="2026-10-01T11:59:59Z",
                   last_seen_at="2026-10-08T12:00:00Z").freshness == "stale"


def test_failed_check_is_not_a_fresh_success_or_closure(job):
    failed = replace(job, verification_status="blocked", verified_at="2026-10-08T11:00:00Z",
                     verification_checked_at="2026-10-08T12:00:00Z")
    assert failed.freshness == "stale" and failed.is_active
    assert replace(failed, verified_at=None).freshness == "unverified"


def test_unverified_or_invalid_dates_never_imply_current_availability(job):
    assert replace(job, verified_at=None).freshness == "unverified"
    assert replace(job, verified_at="not a date").freshness == "unverified"
    assert replace(job, verified_at="2026-10-09T12:00:00Z").freshness == "stale"
    assert replace(job, is_active=False).freshness == "closed"
    assert replace(job, verification_status="closed").freshness == "closed"

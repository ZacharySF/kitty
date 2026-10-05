"""Program windows and YC current-detail requirements, independent of live networks."""
import json
from datetime import datetime, timezone
from html import escape

import pytest

from applyai.core.discovery.public_pages import PublicPage
from applyai.core.discovery.research import parse_doe, parse_tamu, window_state
from applyai.core.discovery.startups import parse_detail

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def page(text, url="https://example.edu/research"):
    return PublicPage(url, text, NOW.isoformat())


def test_tamu_one_program_with_exact_dates_and_unverified_qualifications():
    main = page("<main>Undergraduate Summer Research 2027. Application Opens: October 1, 2026 "
                "Application Deadline: January 3, 2027 at 5:00pm CST</main>")
    about = page("<main>Receive a $6,000 stipend paid out as a scholarship. "
                 "30 credit hours required by spring; not work experience.</main>")
    jobs, evidence = parse_tamu(main, about, NOW)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.deadline_at == "2027-01-03T23:00:00+00:00"
    assert job.opens_at == "2026-10-01T05:00:00+00:00"
    assert job.raw["record_kind"] == "research_program"
    assert "require verification" in job.raw["eligibility_note"]
    assert "30 credit hours" in job.description
    assert evidence[0]["state"] == "open"
    assert parse_tamu(main, about, datetime(2027, 1, 4, tzinfo=timezone.utc))[0] == []


def test_tamu_no_paid_or_unambiguous_deadline_means_no_vacancy():
    main = page("Application Opens: October 1, 2026 Application Deadline: January 3, 2027")
    assert parse_tamu(main, page("Scholarships may be available"), NOW)[0] == []


def test_doe_future_and_closed_cycles_are_evidence_only_then_open():
    dates = page("<table><tr><th>SULI Internship Term:</th><th>Spring 2027</th><th>Summer 2027</th></tr>"
                 "<tr><td>On-line Application Opens</td><td>July 8, 2026</td><td>October 14, 2026</td></tr>"
                 "<tr><td>Applications including recommendations Due</td>"
                 "<td>September 30, 2026 5:00 PM ET</td><td>January 6, 2027 5:00 PM ET</td></tr></table>")
    eligibility = page("Completed semester and GPA 3.0 required")
    benefits = page("Participants receive a weekly stipend")
    jobs, evidence = parse_doe(dates, eligibility, benefits, NOW)
    assert jobs == []
    assert [e["state"] for e in evidence] == ["closed", "not_open"]
    jobs, _ = parse_doe(dates, eligibility, benefits, datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert len(jobs) == 1
    assert jobs[0].source_job_id == "doe-suli-summer-2027"
    assert jobs[0].deadline_at == "2027-01-06T22:00:00+00:00"
    assert "GPA 3.0" in jobs[0].description


def test_window_boundary_does_not_keep_expired_program_open():
    assert window_state(NOW, NOW, NOW) == "closed"
    assert window_state(None, datetime(2027, 1, 1, tzinfo=timezone.utc), NOW) == "unknown"


def yc_page(**overrides):
    job = {"id": 1, "title": "Software Engineering Intern Summer 2027", "companyName": "Example",
           "type": "Internship", "location": "Austin, TX, US", "description": "Build firmware systems.",
           "applyUrl": "https://account.ycombinator.com/authenticate", "lastActive": "2 days"}
    job.update(overrides)
    return page('<div data-page="' + escape(json.dumps({"props": {"job": job, "showDirectApply": True}}), quote=True) + '"></div>',
                "https://www.ycombinator.com/companies/example/jobs/one")


def test_yc_minimum_standing_is_an_eligibility_requirement():
    from applyai.config import EligibilityRules
    from applyai.core.discovery.eligibility import evaluate

    job, _ = parse_detail(yc_page(minSchoolYear="Junior and above"), "1", now=NOW)
    verdict = evaluate(title=job.title, description=job.description, location=job.location,
                       country=job.country, degrees=None,
                       rules=EligibilityRules(drop_if_requires=["junior standing"]))
    assert verdict.eligibility == "ineligible"


def test_yc_missing_activity_date_is_uncertainty_not_evidence_of_closure():
    from applyai.config import EligibilityRules
    from applyai.core.discovery.eligibility import evaluate
    from applyai.core.discovery.pipeline import _with_eligibility_note

    job, _ = parse_detail(yc_page(lastActive=None, description="Open to undergraduates."), "1", now=NOW)
    verdict = evaluate(title=job.title, description=job.description, location=job.location,
                       country=job.country, degrees=None, rules=EligibilityRules())
    assert _with_eligibility_note(verdict, job.raw["eligibility_note"]).eligibility == "ambiguous"


@pytest.mark.parametrize(("changes", "reason"), [
    ({"location": "London, UK"}, "US_location_unconfirmed"),
    ({"location": "Remote"}, "US_location_unconfirmed"),
    ({"lastActive": "about 1 year"}, "stale_last_active"),
    ({"type": "Full-time"}, "not_internship"),
])
def test_yc_directory_snippets_cannot_override_invalid_current_details(changes, reason):
    job, state = parse_detail(yc_page(**changes), "1", now=NOW)
    assert job is None
    assert state == reason


@pytest.mark.parametrize("changes", [
    {"title": "Intern Summer 2026"},
    {"title": "2026 Summer Intern"},
    {"title": "Founder Intern (now and Summer 2026)"},
    {"closed": True, "applyUrl": None},
    {"description": "We are no longer accepting applications.", "applyUrl": None},
])
def test_yc_explicit_closure_is_retained_for_known_postings(changes):
    job, state = parse_detail(yc_page(**changes), "1", now=NOW)
    assert job.closed
    assert job.source_job_id == "1"
    assert state in {"closed", "past_cycle"}

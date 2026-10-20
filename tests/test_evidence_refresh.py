"""Evidence precedence, honest live availability, and preservation of hand choices."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from kitty.config import EligibilityRules, Track, WatchlistCompany
from kitty.core import jobs as jobs_core
from kitty.core.discovery.ats import GreenhouseAdapter
from kitty.core.discovery.http import PoliteHttp
from kitty.core.discovery.base import NormalizedJob
from kitty.core.discovery.pipeline import CompanyResolver, SourceTally, rejudge, store_posting
from kitty.core.discovery.public_pages import PageHTTPError, PublicPage, parse_job_postings
from kitty.core.evidence import chosen_facts
from kitty.core.refresh import PostingCheck, check_posting, persist_check, refresh_jobs, select_jobs
from kitty.dates import now_iso, parse
from kitty.normalize import sha256_text


def add(workspace, **kwargs):
    data = dict(company="Evidence Co", title="Systems Intern", location="Austin, TX",
                url="https://job-boards.greenhouse.io/evidence/jobs/42", source="greenhouse",
                source_job_id="evidence:42", jd_text="Pursuing a bachelor's degree.",
                observed_at="2026-01-01T00:00:00Z")
    data.update(kwargs)
    return jobs_core.upsert_job(workspace.conn, **data)[0]


class Pages:
    def __init__(self, text=None, error=None, redirected=None):
        self.text, self.error, self.redirected = text, error, redirected
        self.requests = []

    def fetch(self, url):
        self.requests.append(url)
        if self.error:
            raise self.error
        return PublicPage(self.redirected or url, self.text, now_iso())


def greenhouse_payload(**kwargs):
    return json.dumps({"id": 42, "title": "Systems Intern",
                       "absolute_url": "https://job-boards.greenhouse.io/evidence/jobs/42",
                       "content": "<p>Pursuing a bachelor's degree. Must have junior standing.</p>",
                       **kwargs})


def test_newer_direct_qualifications_replace_old_text_and_invalidate_model_tier(workspace):
    job = add(workspace)
    jobs_core.set_tier(workspace.conn, job.id, "A", reason="old description", tier_source="llm")
    jobs_core.mark_triaged(workspace.conn, job.id)
    update = "Must have junior standing. Required: embedded systems coursework."
    changed = jobs_core.merge_source_facts(workspace.conn, job.id, source="greenhouse",
                                           observed_at="2026-02-01T00:00:00Z", jd_text=update)
    current = jobs_core.get_job(workspace.conn, job.id)
    assert changed == {"jd_text"}
    assert current.jd_text == update and current.tier is None and current.triaged_at is None
    assert workspace.conn.execute("SELECT jd_hash FROM jobs WHERE id=?", (job.id,)).fetchone()[0] == sha256_text(update)
    provenance = chosen_facts(workspace.conn, job.id)["jd_text"]
    assert (provenance["source"], provenance["observed_at"]) == ("greenhouse", "2026-02-01T00:00:00Z")


@pytest.mark.parametrize("source,date,text", [
    ("jobspy", "2026-12-01T00:00:00Z", "Intern wanted"),
    ("greenhouse", "2025-12-01T00:00:00Z", "Old but much longer information without the current requirements"),
    ("greenhouse", "2026-12-01T00:00:00Z", "   "),
    ("greenhouse", "2026-12-01T00:00:00Z", None),
])
def test_snippets_old_and_empty_observations_cannot_erase_authoritative_facts(workspace, source, date, text):
    description = "Required: must have junior standing."
    job = add(workspace, jd_text=description)
    jobs_core.merge_source_facts(workspace.conn, job.id, source=source, observed_at=date, jd_text=text)
    assert jobs_core.get_job(workspace.conn, job.id).jd_text == description
    assert chosen_facts(workspace.conn, job.id)["jd_text"]["source"] == "greenhouse"


def test_source_dates_do_not_replace_personal_reminders_or_notes(workspace):
    job = add(workspace, deadline_at="2026-05-01T00:00:00Z")
    jobs_core.set_deadline(workspace.conn, job.id, "2026-04-02T00:00:00Z")
    jobs_core.set_notes(workspace.conn, job.id, "Ask mentor first")
    jobs_core.merge_source_facts(workspace.conn, job.id, source="greenhouse",
                                observed_at="2026-02-01T00:00:00Z", source_deadline_at="2026-06-01T00:00:00Z")
    jobs_core.close_job(workspace.conn, job.id, reason="Exact posting closed")
    current = jobs_core.get_job(workspace.conn, job.id)
    assert current.deadline_at == "2026-04-02T00:00:00Z"
    assert current.my_notes == "Ask mentor first"
    assert chosen_facts(workspace.conn, job.id)["source_deadline_at"]["value"] == "2026-06-01T00:00:00Z"


def test_known_and_url_matched_pipeline_paths_accept_updated_qualifications(workspace):
    track = Track(name=workspace.config.track_names[0], search_terms=["systems"],
                  sources=["greenhouse", "jobspy"],
                  eligibility_rules=EligibilityRules(drop_if_requires=["junior", "senior"]))
    job = add(workspace, source="jobspy", source_job_id="snippet", jd_text="Systems internship", tracks=[track.name])
    jobs_core.set_queue_state(workspace.conn, job.id, "queued")
    jobs_core.set_tier(workspace.conn, job.id, "A", reason="my choice", tier_source="manual")
    jobs_core.set_notes(workspace.conn, job.id, "My own note")
    resume_id = workspace.conn.execute(
        "INSERT INTO resumes(label,file_path,file_hash,created_at,updated_at) VALUES(?,?,?,?,?)",
        ("Chosen by hand", "/not-read.pdf", "hash", now_iso(), now_iso()),
    ).lastrowid
    jobs_core.set_resume(workspace.conn, job.id, resume_id)
    workspace.conn.execute("UPDATE jobs SET favorited_at=? WHERE id=?", ("2026-01-02T00:00:00Z", job.id))
    resolver = CompanyResolver(workspace.config.watchlist)
    for date, text in [("2026-02-01T00:00:00Z", "Must have junior standing."),
                       ("2026-03-01T00:00:00Z", "Required: junior or senior standing. Embedded controls coursework required.")]:
        posting = NormalizedJob(source="greenhouse", source_job_id="evidence:42", company="Evidence Co",
                                title=job.title, url=job.url, location=job.location, description=text,
                                raw={"provenance": {"retrieved_at": date}})
        store_posting(workspace.conn, posting, tracks=[track], resolver=resolver,
                      selective_companies=set(), tally=SourceTally())
        rejudge(workspace.conn, [track])
        current = jobs_core.get_job(workspace.conn, job.id)
        assert current.jd_text == text and current.eligibility == "ineligible"
        assert (current.queue_state, current.tier, current.tier_source) == ("queued", "A", "manual")
        assert current.my_notes == "My own note" and current.favorited_at == "2026-01-02T00:00:00Z"
        assert current.resume_id == resume_id
    jobs_core.mark_not_eligible(workspace.conn, job.id)
    jobs_core.merge_source_facts(workspace.conn, job.id, source="greenhouse",
                                observed_at="2026-04-01T00:00:00Z", jd_text="Open to freshmen.")
    rejudge(workspace.conn, [track])
    assert jobs_core.get_job(workspace.conn, job.id).eligibility_reason == jobs_core.HAND_NOT_ELIGIBLE


def test_exact_detail_refresh_updates_eligibility_and_free_tier_without_discovery(workspace):
    track = Track(name=workspace.config.track_names[0], search_terms=["systems"], sources=["greenhouse"],
                  triage_depth="rules", eligibility_rules=EligibilityRules(drop_if_requires=["junior", "senior"]))
    config = replace(workspace.config, tracks=workspace.config.tracks.model_copy(update={"tracks": [track]}))
    job = add(workspace, tracks=[track.name], eligibility="eligible")
    pages = Pages(greenhouse_payload())
    results = refresh_jobs(workspace.conn, config, workspace.settings, limit=1, pages=pages)
    current = jobs_core.get_job(workspace.conn, job.id)
    assert current.eligibility == "ineligible" and current.tier == "C"
    assert current.freshness == "verified" and current.verification_status == "open"
    assert results[0].changed_fields >= {"jd_text"}
    assert pages.requests == ["https://boards-api.greenhouse.io/v1/boards/evidence/jobs/42?content=true"]


@pytest.mark.parametrize(("url", "endpoint"), [
    ("https://job-boards.greenhouse.io/evidence/jobs/42",
     "https://boards-api.greenhouse.io/v1/boards/evidence/jobs/42?content=true"),
    ("https://jobs.lever.co/evidence/11111111-1111-1111-1111-111111111111",
     "https://api.lever.co/v0/postings/evidence/11111111-1111-1111-1111-111111111111?mode=json"),
    ("https://jobs.smartrecruiters.com/Evidence/123-systems-intern",
     "https://api.smartrecruiters.com/v1/companies/Evidence/postings/123"),
])
@pytest.mark.parametrize("status", [404, 410])
def test_removed_exact_ats_detail_leaves_active_results(workspace, url, endpoint, status):
    job = add(workspace, url=url)
    check = check_posting(job, Pages(error=PageHTTPError(status, endpoint)))
    persist_check(workspace.conn, job.id, check)
    current = jobs_core.get_job(workspace.conn, job.id)
    assert check.status == "closed" and not current.is_active and current.closed_at
    assert job.id not in {item.id for item in jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter())}
    assert job.id in {item.id for item in jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))}


def test_older_not_found_response_cannot_close_newer_verified_posting(workspace):
    job = add(workspace)
    persist_check(workspace.conn, job.id, PostingCheck("open", "Newer live detail", job.url, "2026-01-01T00:00:00Z"))
    check = check_posting(job, Pages(error=PageHTTPError(
        404, "https://boards-api.greenhouse.io/v1/boards/evidence/jobs/42?content=true",
        observed_at="2000-01-01T00:00:00Z",
    )))
    persist_check(workspace.conn, job.id, check)
    assert jobs_core.get_job(workspace.conn, job.id).is_active


@pytest.mark.parametrize("status", [401, 403, 404, 410, 429, 503])
def test_http_failures_never_close_or_upgrade_freshness(workspace, status):
    job = add(workspace)
    persist_check(workspace.conn, job.id, PostingCheck("open", "earlier exact detail", job.url,
                                                     "2026-01-01T00:00:00Z"))
    check = check_posting(job, Pages(error=PageHTTPError(status, job.url)))
    persist_check(workspace.conn, job.id, check)
    current = jobs_core.get_job(workspace.conn, job.id)
    assert current.is_active and current.verified_at == "2026-01-01T00:00:00Z"
    assert current.freshness == "stale" and current.verification_checked_at is not None
    assert current.jd_text == job.jd_text and current.verification_status != "open"


def test_exact_unavailable_status_closes_but_other_posting_and_redirect_do_not(workspace):
    job = add(workspace)
    unrelated = check_posting(job, Pages(greenhouse_payload(id=99, status="closed")))
    assert unrelated.status == "unverified"
    redirected = check_posting(job, Pages(greenhouse_payload(), redirected="https://boards-api.greenhouse.io/"))
    assert redirected.status == "unverified"
    closed = check_posting(job, Pages(greenhouse_payload(status="closed")))
    persist_check(workspace.conn, job.id, closed)
    assert jobs_core.get_job(workspace.conn, job.id).freshness == "closed"
    assert jobs_core.get_job(workspace.conn, job.id).my_notes is None


def test_jsonld_exact_expiry_is_closure_but_page_absence_is_not(workspace):
    job = add(workspace, source="manual", url="https://careers.example.org/postings/42")
    empty = check_posting(job, Pages("<h1>Search careers</h1>"))
    assert empty.status == "unverified"
    record = {"@type": "JobPosting", "title": job.title, "hiringOrganization": {"name": job.company_name},
              "url": job.url, "description": "Qualifications", "validThrough": "2000-01-01"}
    check = check_posting(job, Pages('<script type="application/ld+json">' + json.dumps(record) + '</script>'))
    assert check.status == "closed"


def test_refresh_selection_favors_favorites_then_missing_stale_then_rank(workspace):
    conn = workspace.conn
    fresh_favorite = add(workspace, company="Favorite Current", source_job_id="f1")
    missing_favorite = add(workspace, company="Favorite Missing", source_job_id="f2", jd_text=None)
    top = add(workspace, company="Top Ranked", source_job_id="top", eligibility="boosted")
    low = add(workspace, company="Lower Ranked", source_job_id="low", eligibility="unknown")
    for job in (fresh_favorite, missing_favorite):
        conn.execute("UPDATE jobs SET favorited_at=? WHERE id=?", (now_iso(), job.id))
    persist_check(conn, fresh_favorite.id, PostingCheck("open", "exact detail", fresh_favorite.url, now_iso()))
    assert [job.id for job in select_jobs(conn, limit=3)] == [missing_favorite.id, fresh_favorite.id, top.id]
    jobs_core.close_job(conn, low.id, reason="closed")
    assert [job.id for job in select_jobs(conn, limit=1, job_ids=[low.id])] == [low.id]


def test_invalid_observation_time_and_legacy_empty_values_do_not_corrupt_facts(workspace):
    job = add(workspace)
    with pytest.raises(ValueError, match="Invalid source observation timestamp"):
        jobs_core.merge_source_facts(workspace.conn, job.id, source="greenhouse",
                                    observed_at="not a date", jd_text="Do not accept")
    assert jobs_core.get_job(workspace.conn, job.id).jd_text == job.jd_text
    # Simulate a pre-provenance row: a blank authoritative field is not evidence.
    workspace.conn.execute("DELETE FROM source_facts WHERE job_id=? AND field='jd_text'", (job.id,))
    workspace.conn.execute("UPDATE jobs SET jd_text='   ' WHERE id=?", (job.id,))
    jobs_core.merge_source_facts(workspace.conn, job.id, source="jobspy", jd_text="Useful public snippet")
    assert jobs_core.get_job(workspace.conn, job.id).jd_text == "Useful public snippet"


def test_legacy_provenance_is_labeled_unknown_not_attributed_to_a_later_source(workspace):
    job = add(workspace, jd_text="Required: junior standing.")
    workspace.conn.execute("DELETE FROM source_facts WHERE job_id=? AND field='jd_text'", (job.id,))
    jobs_core.merge_source_facts(workspace.conn, job.id, source="jobspy", jd_text="Short snippet")
    selected = chosen_facts(workspace.conn, job.id)["jd_text"]
    assert selected["source"] == "legacy" and selected["source_url"] is None
    assert jobs_core.get_job(workspace.conn, job.id).jd_text == "Required: junior standing."


def test_cached_open_evidence_does_not_reopen_a_more_recent_closure(workspace):
    job = add(workspace)
    jobs_core.close_job(workspace.conn, job.id, reason="Newer exact closure")
    persist_check(workspace.conn, job.id, PostingCheck(
        "open", "cached direct posting", job.url, "2000-01-01T00:00:00Z",
    ))
    current = jobs_core.get_job(workspace.conn, job.id)
    assert current.freshness == "closed" and current.verified_at is None
    assert "Older open evidence ignored" in current.verification_reason


def test_cached_discovery_cannot_replace_a_newer_live_detail_or_reopen_a_closed_job(workspace, tmp_path):
    """Exercise a real byte cache and adapter, not a timestamp mocked on a job."""
    url = "https://boards-api.greenhouse.io/v1/boards/evidence/jobs?content=true"
    old = "Pursuing a bachelor's degree."
    payload = {"jobs": [{"id": 42, "title": "Systems Intern",
                        "absolute_url": "https://job-boards.greenhouse.io/evidence/jobs/42",
                        "location": {"name": "Austin, TX"}, "content": old}]}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    http = PoliteHttp(tmp_path / "cache", min_interval=0, cache_ttl_minutes=10_000_000, client=client)
    track = Track(name=workspace.config.track_names[0], search_terms=["systems"], sources=["greenhouse"])
    company = WatchlistCompany(name="Evidence Co", ats="greenhouse", slug="evidence")
    ctx = SimpleNamespace(http_for=lambda source: http, boards=lambda source: [company],
                          dropped_to_zero=lambda *args: False)
    adapter = GreenhouseAdapter(ctx)
    resolver = CompanyResolver(workspace.config.watchlist)
    try:
        http.get_json(url)
        original_time = parse("2026-01-01T00:00:00Z").timestamp()
        os.utime(http._cache_path("GET", url, None), (original_time, original_time))
        [cached] = adapter.fetch(None).jobs
        store_posting(workspace.conn, cached, tracks=[track], resolver=resolver,
                      selective_companies=set(), tally=SourceTally())
        [job] = jobs_core.list_jobs(workspace.conn)
        rich = "Required: junior standing. Required coursework: controls and embedded systems."
        persist_check(workspace.conn, job.id, PostingCheck(
            "open", "new live detail", job.url, "2026-02-01T00:00:00Z",
            replace(cached, description=rich),
        ))
        [again] = adapter.fetch(None).jobs
        store_posting(workspace.conn, again, tracks=[track], resolver=resolver,
                      selective_companies=set(), tally=SourceTally())
        current = jobs_core.get_job(workspace.conn, job.id)
        assert current.jd_text == rich and current.verified_at == "2026-02-01T00:00:00Z"
        jobs_core.close_job(workspace.conn, job.id, reason="new exact closure")
        [again] = adapter.fetch(None).jobs
        store_posting(workspace.conn, again, tracks=[track], resolver=resolver,
                      selective_companies=set(), tally=SourceTally())
        assert jobs_core.get_job(workspace.conn, job.id).freshness == "closed"
        assert http.requests_made == 1
    finally:
        http.close()


@pytest.mark.parametrize("source", ["employers", "research"])
def test_top_level_public_retrieval_timestamp_is_not_promoted_to_now(workspace, source):
    job = add(workspace, source=source, jd_text="New qualifications: junior standing required.",
              observed_at="2026-02-01T00:00:00Z")
    old = NormalizedJob(source=source, source_job_id="evidence:42", company=job.company_name,
                        title=job.title, url=job.url, location=job.location,
                        description="Old cached qualifications", raw={"retrieved_at": "2026-01-01T00:00:00Z"})
    store_posting(workspace.conn, old, tracks=[], resolver=CompanyResolver(workspace.config.watchlist),
                  selective_companies=set(), tally=SourceTally())
    current = jobs_core.get_job(workspace.conn, job.id)
    assert current.jd_text == job.jd_text
    assert current.verified_at == "2026-01-01T00:00:00Z"
    assert chosen_facts(workspace.conn, job.id)["jd_text"]["observed_at"] == "2026-02-01T00:00:00Z"


@pytest.mark.parametrize("other_status", ["open", "closed"])
def test_shared_listing_remaining_role_cannot_replace_or_close_another_role(workspace, other_status):
    url = "https://careers.example.org/current-openings"
    records = [
        {"@type": "JobPosting", "title": "Systems Intern", "identifier": "A",
         "hiringOrganization": {"name": "Evidence Co"}, "url": url, "description": "Original A qualifications"},
        {"@type": "JobPosting", "title": "Controls Intern", "identifier": "B",
         "hiringOrganization": {"name": "Evidence Co"}, "url": url, "description": "Original B qualifications"},
    ]

    def markup(items):
        return '<script type="application/ld+json">' + json.dumps(items) + '</script>'

    initial = parse_job_postings(PublicPage(url, markup(records), now_iso()), source="websearch")
    stored = []
    for posting in initial:
        stored.append(jobs_core.upsert_job(
            workspace.conn, company=posting.company, title=posting.title, url=posting.url,
            source=posting.source, source_job_id=posting.source_job_id, jd_text=posting.description,
            raw_json=json.dumps(posting.raw),
        )[0])
    a, b = stored
    remaining = records[1] | {"status": other_status}
    check = check_posting(a, Pages(markup([remaining])))
    assert check.status == "unverified"
    persist_check(workspace.conn, a.id, check)
    current = jobs_core.get_job(workspace.conn, a.id)
    assert (current.title, current.jd_text, current.is_active) == (a.title, a.jd_text, True)
    assert current.verified_at is None
    matching = check_posting(b, Pages(markup([remaining])))
    assert matching.status == other_status
    persist_check(workspace.conn, b.id, matching)
    assert all(source.url_is_listing for source in jobs_core.get_job(workspace.conn, b.id).sources)


def test_clearing_personal_deadline_does_not_resurrect_it_as_a_source_deadline(workspace):
    job = add(workspace, source="manual", deadline_at="2026-12-01T00:00:00Z",
              opens_at="2026-11-01T00:00:00Z")
    jobs_core.set_deadline(workspace.conn, job.id, None)
    current = jobs_core.get_job(workspace.conn, job.id)
    assert current.deadline_at is None
    assert jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(has_deadline=True)) == []
    facts = chosen_facts(workspace.conn, job.id)
    assert "source_deadline_at" not in facts and "source_opens_at" not in facts


def test_successful_verification_timestamp_uses_canonical_utc_storage(workspace):
    job = add(workspace)
    persist_check(workspace.conn, job.id, PostingCheck(
        "open", "exact active detail", job.url, "2026-10-04T16:46:04.665040-05:00",
    ))
    assert jobs_core.get_job(workspace.conn, job.id).verified_at == "2026-10-04T21:46:04Z"

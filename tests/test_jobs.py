"""Adding jobs, dedupe across sources, and filters."""

from applyai.core import jobs as jobs_core


def _add(workspace, **kwargs):
    defaults = dict(
        company="Test Co", title="Embedded Intern", url="https://example.invalid/1",
        location="Waco, TX",
    )
    return jobs_core.upsert_job(workspace.conn, **{**defaults, **kwargs})


def test_adding_a_job_records_company_source_and_track(workspace):
    job, created = _add(workspace, tracks=["embedded"], match_reason="test")
    assert created is True
    assert job.company_name == "Test Co"
    assert job.tracks == ["embedded"]
    assert [source.source for source in job.sources] == ["manual"]
    assert job.queue_state == "new"
    assert job.eligibility == "unknown"


def test_the_same_posting_from_two_sources_is_one_job_with_two_sources(workspace):
    _add(workspace, source="manual", source_job_id="a")
    job, created = _add(
        workspace,
        company="Test Co, Inc.",
        title="Embedded Internship (Summer 2027)",
        location="Waco, Texas",
        source="greenhouse",
        source_job_id="gh-9",
        jd_text="we use FreeRTOS",
    )
    assert created is False
    assert sorted(source.source for source in job.sources) == ["greenhouse", "manual"]
    assert job.jd_text == "we use FreeRTOS"     # a later source fills a gap
    assert jobs_core.count_jobs(workspace.conn) == 1


def test_authoritative_source_replaces_an_older_description(workspace):
    _add(workspace, source="jobspy", jd_text="short snippet", season="Summer 2027",
         observed_at="2026-01-01T00:00:00Z")
    job, _ = _add(workspace, source="greenhouse", source_job_id="gh-1",
                  jd_text="Qualifications: must have junior standing.", season="Fall 2027",
                  observed_at="2026-02-01T00:00:00Z")
    assert job.jd_text == "Qualifications: must have junior standing."
    assert job.season == "Fall 2027"


def test_find_by_url_ignores_tracking_parameters(workspace):
    job, _ = _add(workspace, url="https://example.invalid/jobs/7")
    found = jobs_core.find_by_url(workspace.conn, "https://example.invalid/jobs/7?utm_source=x")
    assert found is not None and found.id == job.id


def test_filters_narrow_by_track_tier_and_text(workspace):
    first, _ = _add(workspace, tracks=["embedded"])
    second, _ = _add(workspace, company="Other Co", title="Quant Intern",
                     url="https://example.invalid/2", tracks=["quant"])
    jobs_core.set_tier(workspace.conn, second.id, "A", reason="test")

    conn = workspace.conn
    assert [j.id for j in jobs_core.list_jobs(conn, jobs_core.JobFilter(tracks=["quant"]))] == [second.id]
    assert [j.id for j in jobs_core.list_jobs(conn, jobs_core.JobFilter(tiers=["A"]))] == [second.id]
    assert [j.id for j in jobs_core.list_jobs(conn, jobs_core.JobFilter(text="Other"))] == [second.id]
    assert len(jobs_core.list_jobs(conn, jobs_core.JobFilter())) == 2


def test_closed_jobs_leave_the_default_list_but_stay_findable(workspace):
    job, _ = _add(workspace)
    jobs_core.close_job(workspace.conn, job.id, reason="posting removed")
    assert jobs_core.list_jobs(workspace.conn) == []
    still_there = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert [j.id for j in still_there] == [job.id]
    assert still_there[0].closed_at is not None


def test_company_search_is_literal_and_does_not_match_title_or_location(workspace):
    track = workspace.config.track_names[0]
    matching, _ = _add(workspace, company="Acme_100% Labs", tracks=[track])
    _add(workspace, company="Other Co", title="Acme_100% Labs Intern",
         location="Acme_100% Labs, TX", url="https://example.invalid/other")
    _add(workspace, company="AcmeX100 Labs", url="https://example.invalid/wildcard")
    filt = jobs_core.JobFilter(company_text="  aCME_100%  ", tracks=[track])
    assert [job.id for job in jobs_core.list_jobs(workspace.conn, filt)] == [matching.id]
    assert jobs_core.count_jobs(workspace.conn, filt) == 1
    jobs_core.close_job(workspace.conn, matching.id, reason="posting removed")
    assert jobs_core.list_jobs(workspace.conn, filt) == []

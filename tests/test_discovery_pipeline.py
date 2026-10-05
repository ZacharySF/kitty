"""The discover pipeline end to end, with fake adapters instead of the network."""

from applyai.config import WatchlistCompany
from applyai.core import jobs as jobs_core
from applyai.core import sources as sources_core
from applyai.core.discovery.base import AdapterResult, NormalizedJob
from applyai.core.discovery.pipeline import CompanyResolver, run_discover
from applyai.core.discovery.report import build_report


def _job(source, sid, title="Software Engineer Intern", **extra):
    fields = dict(source=source, source_job_id=sid, company="Acme", title=title,
                  url=f"https://acme.example/jobs/{sid}", location="Austin, TX")
    fields.update(extra)
    return NormalizedJob(**fields)


def _adapter(name, result):
    """An adapter class that returns a fixed result (or raises it)."""
    class Fixed:
        def __init__(self, ctx):
            pass

        def fetch(self, since):
            if isinstance(result, Exception):
                raise result
            return result
    Fixed.name = name
    return Fixed


def _run(workspace, adapters):
    return run_discover(workspace.settings, workspace.config, adapters=adapters,
                        only=list(adapters), http_factory=lambda name: None)


def test_a_track_limited_to_some_cities_only_keeps_postings_there(workspace):
    # The track with allow_cities (part-time near home) must drop postings elsewhere.
    track = next(t for t in workspace.config.tracks.tracks if t.location_rules.allow_cities)
    source, title = track.sources[0], track.search_terms[0].title()
    near, far = track.location_rules.allow_cities[0], "Somewhere Else, ZZ"
    result = _run(workspace, {source: _adapter(source, AdapterResult([
        _job(source, "near", title=title, location=f"{near}, TX"),
        _job(source, "far", title=title, location=far),
    ], "ok"))})

    kept = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(tracks=[track.name]))
    assert [job.location for job in kept] == [f"{near}, TX"]
    assert result.sources[source].unrouted == 1


def test_an_employer_limited_city_keeps_only_those_employers(workspace):
    track = next(t for t in workspace.config.tracks.tracks if t.location_rules.allow_cities)
    city, employers = "Additional Town", ["Coffee Chain"]
    track.location_rules.allow_city_employers = {city: employers}
    source, title = track.sources[0], track.search_terms[0].title()
    _run(workspace, {source: _adapter(source, AdapterResult([
        _job(source, "chain", title=title, location=f"{city}, TX", company=f"{employers[0]} #1234"),
        _job(source, "other", title=title, location=f"{city}, TX", company="Some Local Shop"),
    ], "ok"))})

    kept = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(tracks=[track.name]))
    assert [job.url for job in kept] == ["https://acme.example/jobs/chain"]


def test_a_crashing_source_is_recorded_and_the_run_goes_on(workspace):
    result = _run(workspace, {
        "greenhouse": _adapter("greenhouse", RuntimeError("boom")),
        "lever": _adapter("lever", AdapterResult([_job("lever", "1")], "ok")),
        "ashby": _adapter("ashby", AdapterResult([], "rate_limited", "429 from api.ashbyhq.com")),
    })
    assert result.sources["lever"].new == 1

    health = {s.name: s for s in sources_core.list_sources(workspace.conn)}
    assert health["greenhouse"].last_status == "error" and "boom" in health["greenhouse"].last_error
    # Rate limited is never "0 jobs": the count stays empty and the status says why.
    assert health["ashby"].last_status == "rate_limited" and health["ashby"].last_count is None
    assert health["lever"].last_status == "ok" and health["lever"].last_count == 1


def test_postings_route_filter_and_store(workspace):
    _run(workspace, {"greenhouse": _adapter("greenhouse", AdapterResult([
        _job("greenhouse", "ok", description="Pursuing a bachelor's degree."),
        _job("greenhouse", "far", location="London, UK"),
        _job("greenhouse", "misc", title="Office Coordinator Intern"),
    ], "ok"))})

    stored = {job.title + "|" + (job.location or ""): job
              for job in jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))}
    assert len(stored) == 2                                  # the unrouted one is not stored
    kept = stored["Software Engineer Intern|Austin, TX"]
    assert kept.tracks == ["software"] and kept.eligibility == "eligible"
    far = stored["Software Engineer Intern|London, UK"]
    assert far.eligibility == "ineligible" and far.queue_state == "not_eligible"


def test_discover_gives_every_kept_job_a_free_tier_and_keeps_mine(workspace):
    adapter = {"greenhouse": _adapter("greenhouse", AdapterResult([
        _job("greenhouse", "ok", description="Open to freshmen and sophomores."),
        _job("greenhouse", "far", location="London, UK"),
        _job("greenhouse", "mine", title="Backend Engineer Intern"),
    ], "ok"))}
    _run(workspace, adapter)
    jobs = {job.title + "|" + (job.location or ""): job
            for job in jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))}
    assert jobs["Software Engineer Intern|Austin, TX"].tier == "A"            # freshman-friendly
    assert jobs["Software Engineer Intern|London, UK"].tier == "C"           # filtered, with reason
    assert "outside the US" in jobs["Software Engineer Intern|London, UK"].tier_reason
    mine = jobs["Backend Engineer Intern|Austin, TX"]
    assert mine.tier == "B" and mine.tier_source == "rules"

    jobs_core.set_tier(workspace.conn, mine.id, "C", reason="not for me", tier_source="manual")
    workspace.conn.commit()
    _run(workspace, adapter)
    assert jobs_core.get_job(workspace.conn, mine.id).tier == "C"            # my tier stays


def test_same_posting_from_two_sources_is_one_job(workspace):
    simplify = _job("simplify2027", "uuid-1", url="https://job-boards.greenhouse.io/acme/jobs/9?utm_source=Simplify")
    board = _job("greenhouse", "acme:9", url="https://job-boards.greenhouse.io/acme/jobs/9",
                 title="Software Engineering Intern - Summer 2027",
                 description="Must have junior standing.")
    result = _run(workspace, {
        "simplify2027": _adapter("simplify2027", AdapterResult([simplify], "ok")),
        "greenhouse": _adapter("greenhouse", AdapterResult([board], "ok")),
    })
    assert result.sources["simplify2027"].new == 1 and result.sources["greenhouse"].merged == 1

    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert sorted(s.source for s in job.sources) == ["greenhouse", "simplify2027"]
    # Simplify had no description; the board's copy settled the verdict.
    assert job.eligibility == "ineligible" and job.queue_state == "not_eligible"


def test_simplify_inactive_closes_the_job(workspace):
    _run(workspace, {"simplify2027": _adapter("simplify2027", AdapterResult([_job("simplify2027", "u1")], "ok"))})
    result = _run(workspace, {"simplify2027": _adapter(
        "simplify2027", AdapterResult([_job("simplify2027", "u1", closed=True)], "ok"))})
    assert result.sources["simplify2027"].closed == 1
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert not job.is_active and job.closed_at


def test_discover_rejudges_stored_jobs_with_the_current_rules(workspace):
    text = "If you are graduating prior to May 2027 please apply to Entry Level roles. Pursuing a BS in CS."
    _run(workspace, {"greenhouse": _adapter("greenhouse", AdapterResult(
        [_job("greenhouse", "acme:1", description=text)], "ok"))})
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter())
    # What an older rule decided and stored.
    jobs_core.set_eligibility(workspace.conn, job.id, "ineligible", "graduation in 2027 required")
    jobs_core.set_queue_state(workspace.conn, job.id, "not_eligible")

    result = _run(workspace, {"lever": _adapter("lever", AdapterResult([], "ok"))})

    assert result.rejudged == 1
    job = jobs_core.get_job(workspace.conn, job.id)
    assert job.eligibility == "eligible" and job.queue_state == "new"


def test_a_job_closes_only_after_clean_runs_without_it(workspace):
    from applyai.core.discovery.base import BoardOutcome
    listed = AdapterResult([_job("greenhouse", "acme:1")], "ok", boards=[BoardOutcome("acme", "ok", 1)])
    empty = AdapterResult([], "ok", boards=[BoardOutcome("acme", "ok", 0)])
    failing = AdapterResult([], "error", "boom", boards=[BoardOutcome("acme", "error", error="boom")])
    close_after = workspace.config.sources.close_after_missed_runs

    _run(workspace, {"greenhouse": _adapter("greenhouse", listed)})
    for _ in range(close_after - 1):
        _run(workspace, {"greenhouse": _adapter("greenhouse", empty)})
    _run(workspace, {"greenhouse": _adapter("greenhouse", failing)})   # a failing board is not a miss
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert job.is_active

    result = _run(workspace, {"greenhouse": _adapter("greenhouse", empty)})
    assert result.sources["greenhouse"].closed == 1
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert not job.is_active and job.closed_at


def test_a_job_stays_open_while_any_source_still_lists_it(workspace):
    url = "https://job-boards.greenhouse.io/acme/jobs/9"
    both = {
        "simplify2027": _adapter("simplify2027", AdapterResult([_job("simplify2027", "u1", url=url)], "ok")),
        "greenhouse": _adapter("greenhouse", AdapterResult([_job("greenhouse", "acme:9", url=url)], "ok")),
    }
    only_simplify = {
        "simplify2027": both["simplify2027"],
        "greenhouse": _adapter("greenhouse", AdapterResult([], "ok")),
    }
    _run(workspace, both)
    for _ in range(workspace.config.sources.close_after_missed_runs + 1):
        _run(workspace, only_simplify)
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert job.is_active and {s.source for s in job.sources} == {"simplify2027", "greenhouse"}


def test_company_names_resolve_to_the_watchlist():
    from applyai.config import WatchlistFile

    resolver = CompanyResolver(WatchlistFile(companies=[
        WatchlistCompany(name="Akuna Capital", ats="greenhouse", slug="akunacapital"),
        WatchlistCompany(name="Susquehanna (SIG)", ats="none", notes="own site"),
        WatchlistCompany(name="Sonos", ats="workday", slug="sonos/wd1/Student_Opportunities"),
    ]))
    assert resolver.resolve("Akuna Capital University", "https://akunacapital.com/x") == "Akuna Capital"
    assert resolver.resolve("SIG", "https://careers.sig.com/1") == "Susquehanna (SIG)"
    assert resolver.resolve("Sonos Inc", "https://sonos.wd1.myworkdayjobs.com/Sonos/job/1") == "Sonos"
    assert resolver.resolve("Someone Else", "https://example.com") == "Someone Else"


def test_report_groups_filtered_reasons_and_finds_freshman_companies(workspace):
    from applyai.config import WatchlistFile

    watchlist = WatchlistFile(companies=[
        WatchlistCompany(name="Acme", ats="greenhouse", slug="acme", notes="[F] Launch program"),
    ])
    object.__setattr__(workspace.config, "watchlist", watchlist)
    result = _run(workspace, {"greenhouse": _adapter("greenhouse", AdapterResult([
        _job("greenhouse", "1", location="London, UK"),
        _job("greenhouse", "2", location="Paris, France", title="Firmware Intern"),
        _job("greenhouse", "3", description="Open to freshmen."),
    ], "ok"))})

    report = build_report(workspace.conn, result.run_id, watchlist)
    assert report.new_by_eligibility == {"ineligible": 2, "boosted": 1}
    assert report.top_filtered[0].rule == "outside the US" and report.top_filtered[0].count == 2
    [acme] = report.freshman_open
    assert acme.company == "Acme" and acme.open_postings == 3 and acme.not_filtered == 1


def test_seen_posting_gains_new_track_without_resetting_workflow(workspace):
    from dataclasses import replace

    first, second = workspace.config.tracks.tracks[:2]
    source = first.sources[0]
    first = first.model_copy(update={"search_terms": ["firmware intern"]})
    second = second.model_copy(update={
        "sources": [source], "search_terms": ["unrelated speciality intern"],
    })
    workspace.config = replace(workspace.config, tracks=workspace.config.tracks.model_copy(
        update={"tracks": [first, second]},
    ))
    adapter = {source: _adapter(source, AdapterResult([
        _job(source, "reroute", title="Firmware Intern"),
    ], "ok"))}
    _run(workspace, adapter)
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter())
    assert job.tracks == [first.name]
    jobs_core.set_queue_state(workspace.conn, job.id, "skipped")

    second = second.model_copy(update={"search_terms": ["firmware intern"]})
    workspace.config = replace(workspace.config, tracks=workspace.config.tracks.model_copy(
        update={"tracks": [first, second]},
    ))
    _run(workspace, adapter)
    refreshed = jobs_core.get_job(workspace.conn, job.id)
    assert set(refreshed.tracks) == {first.name, second.name}
    assert refreshed.queue_state == "skipped"


def test_bounded_search_absence_does_not_close_a_job(workspace):
    source = workspace.config.tracks.tracks[0].sources[0]
    title = workspace.config.tracks.tracks[0].search_terms[0]
    _run(workspace, {source: _adapter(source, AdapterResult([
        _job(source, "ranked", title=title),
    ], "ok", stats={"complete_snapshot": False}))})
    for _ in range(workspace.config.sources.close_after_missed_runs + 1):
        _run(workspace, {source: _adapter(source, AdapterResult(
            [], "ok", stats={"complete_snapshot": False},
        ))})
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert job.is_active and job.closed_at is None


def test_unverified_program_requirements_remain_ambiguous_after_rejudge(workspace):
    track = workspace.config.tracks.tracks[0]
    source, title = track.sources[0], track.search_terms[0]
    note = "Research program: GPA and completed credits require verification."
    _run(workspace, {source: _adapter(source, AdapterResult([
        _job(source, "research", title=title,
             description="Open to undergraduates. Minimum GPA 3.0 and 30 completed credits.",
             deadline_at="2027-01-03T23:00:00Z",
             raw={"record_kind": "research_program", "eligibility_note": note}),
        _job(source, "foreign-research", title=title, location="London, UK",
             raw={"record_kind": "research_program", "eligibility_note": note}),
    ], "ok", stats={"complete_snapshot": False}))})
    _run(workspace, {source: _adapter(source, AdapterResult(
        [], "ok", stats={"complete_snapshot": False},
    ))})
    jobs = {job.location: job for job in jobs_core.list_jobs(
        workspace.conn, jobs_core.JobFilter(include_closed=True),
    )}
    assert jobs["Austin, TX"].eligibility == "ambiguous"
    assert jobs["Austin, TX"].eligibility_reason == note
    assert jobs["Austin, TX"].deadline_at == "2027-01-03T23:00:00Z"
    assert jobs["London, UK"].eligibility == "ineligible"


def test_research_page_rollover_keeps_cycles_distinct_and_expires_old_deadline(workspace, monkeypatch):
    from applyai.core.discovery import pipeline

    monkeypatch.setattr(pipeline, "now_iso", lambda: "2026-10-04T00:00:00Z")
    track = workspace.config.tracks.tracks[0]
    source, title = track.sources[0], track.search_terms[0]
    url = "https://university.example/research"
    jobs = [
        _job(source, "old-cycle", title=title + " Summer 2026", url=url,
             deadline_at="2026-01-03T23:00:00Z", raw={"record_kind": "research_program"}),
        _job(source, "new-cycle", title=title + " Summer 2027", url=url,
             deadline_at="2027-01-03T23:00:00Z", raw={"record_kind": "research_program"}),
    ]
    _run(workspace, {source: _adapter(source, AdapterResult(
        jobs, "ok", stats={"complete_snapshot": False},
    ))})
    rows = workspace.conn.execute("SELECT title,is_active FROM jobs ORDER BY id").fetchall()
    assert [(r["title"], r["is_active"]) for r in rows] == [
        (title + " Summer 2026", 0), (title + " Summer 2027", 1),
    ]


def test_search_absence_abstains_without_vetoing_snapshot_closure(workspace):
    track = workspace.config.tracks.tracks[0]
    snapshot, search = track.sources[:2]
    url = "https://employer.example/opening"
    _run(workspace, {
        snapshot: _adapter(snapshot, AdapterResult([_job(snapshot, "a", title=track.search_terms[0], url=url)], "ok")),
        search: _adapter(search, AdapterResult([_job(search, "b", title=track.search_terms[0], url=url)],
                                             "ok", stats={"complete_snapshot": False})),
    })
    for _ in range(workspace.config.sources.close_after_missed_runs):
        _run(workspace, {
            snapshot: _adapter(snapshot, AdapterResult([], "ok")),
            search: _adapter(search, AdapterResult([], "ok", stats={"complete_snapshot": False})),
        })
    [job] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert not job.is_active


def test_program_expiry_uses_updated_published_deadline_not_personal_deadline(workspace, monkeypatch):
    from applyai.core.discovery import pipeline

    clock = ["2026-10-04T00:00:00Z"]
    monkeypatch.setattr(pipeline, "now_iso", lambda: clock[0])
    track = workspace.config.tracks.tracks[0]
    source = track.sources[0]
    job = _job(source, "program", title=track.search_terms[0], deadline_at="2027-01-03T23:00:00Z",
               raw={"record_kind": "research_program"})
    adapter = _adapter(source, AdapterResult([job], "ok", stats={"complete_snapshot": False}))
    _run(workspace, {source: adapter})
    [stored] = jobs_core.list_jobs(workspace.conn)
    jobs_core.set_deadline(workspace.conn, stored.id, "2026-10-01T00:00:00Z")
    clock[0] = "2027-01-04T00:00:00Z"
    job.deadline_at = "2027-02-01T00:00:00Z"
    _run(workspace, {source: adapter})
    [stored] = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert stored.is_active
    assert stored.deadline_at == "2026-10-01T00:00:00Z"


def test_structured_listing_keeps_distinct_jobs_and_closure_is_per_posting(workspace):
    import json
    from applyai.core.discovery.public_pages import PublicPage, parse_job_postings

    track = workspace.config.tracks.tracks[0]
    source = track.sources[0]
    records = [{"@type": "JobPosting", "identifier": str(index),
                "title": track.search_terms[0] + " " + specialty,
                "hiringOrganization": {"name": "Example"}, "description": "Open to undergraduates."}
               for index, specialty in enumerate(("Alpha", "Beta"))]
    def postings(records):
        page = PublicPage("https://employer.example/careers",
                          '<script type="application/ld+json">' + json.dumps(records) + '</script>',
                          "2026-10-04T00:00:00Z")
        return parse_job_postings(page, source=source)

    _run(workspace, {source: _adapter(source, AdapterResult(
        postings(records), "ok", stats={"complete_snapshot": False},
    ))})
    records[0]["status"] = "closed"
    _run(workspace, {source: _adapter(source, AdapterResult(
        postings(records[:1]), "ok", stats={"complete_snapshot": False},
    ))})
    jobs = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert {job.title: job.is_active for job in jobs} == {
        track.search_terms[0] + " Alpha": False, track.search_terms[0] + " Beta": True,
    }
    third = records[1] | {"identifier": "2", "title": track.search_terms[0] + " Gamma"}
    _run(workspace, {source: _adapter(source, AdapterResult(
        postings([third]), "ok", stats={"complete_snapshot": False},
    ))})
    jobs = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(include_closed=True))
    assert {job.title: job.is_active for job in jobs} == {
        track.search_terms[0] + " Alpha": False, track.search_terms[0] + " Beta": True,
        track.search_terms[0] + " Gamma": True,
    }


def test_local_routing_accepts_mainland_employers_but_not_city_names_elsewhere(workspace):
    track = next(t for t in workspace.config.tracks.tracks if t.location_rules.allow_cities)
    source = track.sources[0]
    candidates = [
        _job(source, str(i), title="Cashier", company="Local Grocery", location=f"{city}, Texas")
        for i, city in enumerate(track.location_rules.allow_cities)
    ]
    candidates.extend([
        _job(source, "houston", title="Cashier", location="Houston, TX"),
        _job(source, "wrong-state", title="Cashier", location="Lancaster, PA"),
        _job(source, "county", title="Cashier", location="Waco County, TX"),
        _job(source, "remote", title="Cashier", location="Hewitt, TX", is_remote=True),
    ])
    result = _run(workspace, {source: _adapter(source, AdapterResult(candidates, "ok"))})
    kept = jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter(tracks=[track.name]))
    assert {job.location for job in kept} == {f"{city}, Texas" for city in track.location_rules.allow_cities}
    assert result.sources[source].unrouted == 4


def test_stored_rerouting_needs_no_rediscovery_and_preserves_hand_choices_and_closure(workspace):
    track = next(t for t in workspace.config.tracks.tracks if t.location_rules.allow_cities)
    source = track.sources[0]
    stored = {}
    for state in ("skipped", "later", "not_eligible", "closed"):
        job, _ = jobs_core.upsert_job(
            workspace.conn, company=f"Shop {state}", title="Cashier",
            url=f"https://shop.example/{state}", location="Hewitt, TX",
            source=source, source_job_id=state,
        )
        if state == "closed":
            jobs_core.close_job(workspace.conn, job.id, reason="source explicitly inactive")
        elif state == "not_eligible":
            jobs_core.mark_not_eligible(workspace.conn, job.id)
        else:
            jobs_core.set_queue_state(workspace.conn, job.id, state)
        workspace.conn.execute("UPDATE jobs SET favorited_at = ?, fill_state = 'attempted', "
                               "fill_attempted_at = ? WHERE id = ?",
                               ("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", job.id))
        stored[state] = job.id
    _run(workspace, {})
    for state, ident in stored.items():
        job = jobs_core.get_job(workspace.conn, ident)
        if state == "closed":
            assert not job.is_active and job.closed_at is not None and not job.tracks
        else:
            assert job.tracks == [track.name]
            assert job.queue_state == state
        assert job.favorited_at == "2026-10-01T00:00:00Z"
        assert job.fill_state == "attempted"
        assert job.fill_attempted_at == "2026-10-02T00:00:00Z"

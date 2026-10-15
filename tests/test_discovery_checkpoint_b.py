"""Checkpoint B adapters: Hacker News, USAJOBS and JobSpy, without the network.

HN and JobSpy fixtures are trimmed real responses. USAJOBS needs an API key to
fetch anything, so its fixture follows the documented response shape
(developer.usajobs.gov, GET /api/Search) with made-up announcements.
"""

from dataclasses import replace
from types import SimpleNamespace

from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.hackernews import ITEM_URL, SEARCH_URL, HackerNewsAdapter
from kitty.core.discovery.jobspy_source import JobSpyAdapter, plan_searches
from kitty.core.discovery.usajobs import UsaJobsAdapter, search_url
from fakes import FakeHttp, fixture


def _context(workspace, http):
    return DiscoverContext(workspace.config, lambda name: http)


# ------------------------------------------------------------- hacker news


def test_hackernews_keeps_intern_posts_from_the_newest_hiring_thread(workspace):
    thread = fixture("hn_thread.json")
    thread["children"].append({
        "id": 2005, "text": "Example Science | Research Engineer | Remote<p>We also hire interns.",
    })
    http = FakeHttp({SEARCH_URL: fixture("hn_search.json"), ITEM_URL.format(id=thread["id"]): thread})
    result = HackerNewsAdapter(_context(workspace, http)).fetch(None)

    assert result.status == "ok"
    # "Who wants to be hired?" and last month's thread are not the one read.
    assert http.calls[1][1] == ITEM_URL.format(id=thread["id"])
    by_id = {job.source_job_id: job for job in result.jobs}
    robotics = by_id["1016"]
    assert robotics.company == "Example Robotics Lab"
    assert robotics.title == "Software Engineering Intern"
    assert robotics.location == "ONSITE (Zürich, Switzerland)"
    assert robotics.url == "https://news.ycombinator.com/item?id=1016"
    assert robotics.description and "<p>" not in robotics.description
    # Internship in a later first-line segment is still recognized.
    assert by_id["1012"].title.endswith("(internship)")
    assert by_id["1014"].company == "Example Security"
    assert by_id["2005"].title == "Research Engineer (internships mentioned in post)"


def test_hackernews_skips_non_intern_and_unsplittable_posts(workspace):
    thread = fixture("hn_thread.json")
    thread["children"].extend([
        {"id": 2001, "text": "Example Internals | Engineer | Remote<p>Build compiler internals."},
        {"id": 2002, "text": "Example Graduate | Engineer | Remote<p>We cannot hire students."},
        {"id": 2003, "text": "We hire interns, but this post has no structured company line."},
    ])
    http = FakeHttp({SEARCH_URL: fixture("hn_search.json"), ITEM_URL.format(id=thread["id"]): thread})
    result = HackerNewsAdapter(_context(workspace, http)).fetch(None)

    kept = {job.source_job_id for job in result.jobs}
    assert "1009" not in kept           # full-time senior role
    assert "2001" not in kept           # internals is not internship
    assert "2002" not in kept           # student alone is not internship
    assert "2003" not in kept           # no Company | Role line
    assert result.stats["unparseable"] == 1
    assert result.stats["comments"] == len(thread["children"])


# ----------------------------------------------------------------- usajobs


def test_usajobs_without_a_key_is_an_error_not_zero_jobs(workspace, monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    http = FakeHttp({})
    result = UsaJobsAdapter(_context(workspace, http)).fetch(None)

    assert result.status == "error"
    assert "USAJOBS_API_KEY" in result.error
    assert http.calls == []


def test_usajobs_keeps_student_and_intern_announcements(workspace, monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "test-key")
    monkeypatch.setenv("USAJOBS_EMAIL", "me@example.edu")
    options = workspace.config.sources.sources["usajobs"].options
    queries, per_page = options["queries"], options["results_per_page"]
    http = FakeHttp({search_url(queries[0], 1, per_page): fixture("usajobs_search.json"),
                     search_url(queries[1], 1, per_page): {"SearchResult": {"SearchResultItems": []}}})
    http.client = SimpleNamespace(headers={})
    result = UsaJobsAdapter(_context(workspace, http)).fetch(None)

    assert result.status == "ok"
    assert http.client.headers == {"Host": "data.usajobs.gov", "User-Agent": "me@example.edu",
                                   "Authorization-Key": "test-key"}
    by_id = {job.source_job_id: job for job in result.jobs}
    assert set(by_id) == {"1053", "1054", "1056"}     # the IT Specialist is dropped
    nasa = by_id["1053"]
    assert nasa.company == "Example Federal Organization"
    assert nasa.location == "Houston, Texas" and nasa.country == "United States"
    assert nasa.posted_at == "2026-09-15T00:00:00Z"
    assert nasa.season == "Summer 2027"
    assert "avionics bench testing" in nasa.description
    assert by_id["1054"].title == "Engineering Technician (student program)"


def test_usajobs_rate_limit_is_reported_and_stops_the_source(workspace, monkeypatch):
    from kitty.core.discovery.http import RateLimited

    monkeypatch.setenv("USAJOBS_API_KEY", "test-key")
    options = workspace.config.sources.sources["usajobs"].options
    first = search_url(options["queries"][0], 1, options["results_per_page"])
    http = FakeHttp({first: RateLimited("429 from data.usajobs.gov")})
    http.client = SimpleNamespace(headers={})
    result = UsaJobsAdapter(_context(workspace, http)).fetch(None)

    assert result.status == "rate_limited"
    assert len(http.calls) == 1


# ------------------------------------------------------------------ jobspy


#: Every site the adapter supports, so multi-site behaviour is tested whatever
#: sources.yaml currently turns on.
ALL_SITES = ["indeed", "google", "zip_recruiter", "glassdoor"]


def _all_sites(config):
    """The shipped config with every JobSpy site enabled."""
    jobspy = config.sources.sources["jobspy"]
    options = {**jobspy.options, "sites": ALL_SITES}
    sources = {**config.sources.sources, "jobspy": jobspy.model_copy(update={"options": options})}
    return replace(config, sources=config.sources.model_copy(update={"sources": sources}))


def _jobspy_context(workspace, tmp_path):
    """A context whose jobspy client only carries pacing and cache settings."""
    http = SimpleNamespace(min_interval=0, cache_dir=tmp_path / "jobspy", ttl_seconds=3600)
    return DiscoverContext(_all_sites(workspace.config), lambda name: http)


def test_jobspy_plan_covers_each_jobspy_track_and_keeps_linkedin_off(workspace):
    config = _all_sites(workspace.config)
    plan = plan_searches(config.tracks.tracks, config.sources.sources["jobspy"].options)
    jobspy_tracks = [t for t in workspace.config.tracks.tracks if "jobspy" in t.sources]

    assert "linkedin" not in {s.site for s in plan}
    for track in jobspy_tracks:
        assert any(s.term == track.search_terms[0] for s in plan)
        if track.location_rules.near:
            near = [s for s in plan if s.term == track.search_terms[0]]
            assert all(s.distance == track.location_rules.near.radius_miles for s in near)
    # Interleaved: no two consecutive calls go to the same site.
    assert all(a.site != b.site for a, b in zip(plan, plan[1:]))


def test_jobspy_plan_searches_more_terms_for_tracks_that_ask_for_them(workspace):
    config = _all_sites(workspace.config)
    options = dict(config.sources.sources["jobspy"].options, max_terms_per_track=1)
    track = next(t for t in config.tracks.tracks if "jobspy" in t.sources and len(t.search_terms) > 1)
    options["max_terms_by_track"] = {track.name: 2}
    plan = plan_searches(config.tracks.tracks, options)
    assert {s.term for s in plan if s.term in track.search_terms} >= set(track.search_terms[:2])


def test_jobspy_all_terms_and_nationwide_locations_do_not_expand_local_tracks(workspace):
    config = workspace.config
    internship = next(t for t in config.tracks.tracks if "jobspy" in t.sources and not t.location_rules.near)
    local = next(t for t in config.tracks.tracks if "jobspy" in t.sources and t.location_rules.near)
    plan = plan_searches([internship, local], {
        "sites": ["indeed"], "max_terms_per_track": 0,
        "max_terms_by_track": {local.name: 1}, "locations": ["United States", "Texas", "Remote"],
    })
    assert {(s.term, s.location) for s in plan if s.distance is None} == {
        (term, location) for term in internship.search_terms
        for location in ("United States", "Texas", "Remote")
    }
    local_searches = [s for s in plan if s.distance is not None]
    assert {s.location for s in local_searches} == {
        f"{near.city}, {near.state}" for near in (local.location_rules.near, *local.location_rules.also_near)
    }
    assert all(s.location not in {"United States", "Texas", "Remote"} for s in local_searches)

def test_jobspy_plan_adds_each_extra_place_with_its_own_terms(workspace):
    from kitty.config import NearRule

    config = _all_sites(workspace.config)
    track = next(t for t in config.tracks.tracks if t.location_rules.near)
    extra = NearRule(city="Somewhere", state="ZZ", radius_miles=5, terms=["only-here"])
    rules = track.location_rules.model_copy(update={"also_near": [extra]})
    tracks = [track.model_copy(update={"location_rules": rules})]
    plan = plan_searches(tracks, config.sources.sources["jobspy"].options)
    there = [s for s in plan if s.location == "Somewhere, ZZ"]
    assert {s.term for s in there} == {"only-here"} and all(s.distance == 5 for s in there)
    assert any(s.location.startswith(track.location_rules.near.city) for s in plan)


def test_extra_local_search_centers_inherit_all_normal_terms_without_broadening_nationwide(workspace):
    from kitty.config import NearRule

    track = next(t for t in workspace.config.tracks.tracks if t.location_rules.near)
    rules = track.location_rules.model_copy(update={
        "also_near": [NearRule(city="Hewitt", state="TX", radius_miles=10)],
    })
    track = track.model_copy(update={"location_rules": rules, "search_terms": ["cashier", "housekeeper", "dishwasher"]})
    searches = plan_searches([track], {
        "sites": ["indeed"], "max_terms_per_track": 1,
        "max_terms_by_track": {track.name: 0}, "locations": ["United States", "Remote"],
    })
    assert {(s.term, s.distance) for s in searches if s.location == "Hewitt, TX"} == {
        ("cashier", 10), ("housekeeper", 10), ("dishwasher", 10),
    }
    assert all(s.location not in {"United States", "Remote"} for s in searches)


def test_jobspy_parses_real_records_and_keeps_interns(workspace, tmp_path):
    records = fixture("jobspy_records.json")
    calls = []

    def scrape(kwargs):
        calls.append(kwargs)
        site = kwargs["site_name"][0]
        return [r for r in records if r["site"] == site], []

    adapter = JobSpyAdapter(_jobspy_context(workspace, tmp_path), scrape=scrape)
    result = adapter.fetch(None)

    assert result.status == "ok"
    assert result.jobs
    for job in result.jobs:
        assert job.source == "jobspy"
        assert job.source_job_id.split(":", 1)[0] in {"indeed", "google", "zip_recruiter", "glassdoor"}
        assert job.url.startswith("http") and job.company and job.title
    indeed = [c for c in calls if c["site_name"] == ["indeed"]]
    assert all("job_type" not in c and "is_remote" not in c and "easy_apply" not in c for c in indeed)

    # A rerun inside the TTL is served from the cache: no calls at all.
    calls.clear()
    again = JobSpyAdapter(_jobspy_context(workspace, tmp_path), scrape=scrape).fetch(None)
    assert calls == []
    assert {j.source_job_id for j in again.jobs} == {j.source_job_id for j in result.jobs}


def test_jobspy_429_on_one_site_is_partial_and_other_sites_continue(workspace, tmp_path):
    records = fixture("jobspy_records.json")
    calls = []

    def scrape(kwargs):
        site = kwargs["site_name"][0]
        calls.append(site)
        if site == "zip_recruiter":
            return [], ["JobSpy:ZipRecruiter: 429 Response - Blocked by ZipRecruiter for too many requests"]
        return [r for r in records if r["site"] == site], []

    result = JobSpyAdapter(_jobspy_context(workspace, tmp_path), scrape=scrape).fetch(None)
    config = _all_sites(workspace.config)
    plan = plan_searches(config.tracks.tracks, config.sources.sources["jobspy"].options)

    assert result.status == "partial"
    assert "429" in result.error
    assert calls.count("zip_recruiter") == 1                     # never asked again this run
    assert calls.count("indeed") == sum(1 for s in plan if s.site == "indeed")
    zip_outcomes = [b for b in result.boards if b.board.startswith("zip_recruiter")]
    assert all(b.status == "rate_limited" for b in zip_outcomes)
    assert result.jobs


def test_jobspy_every_site_blocked_is_rate_limited_not_zero_jobs(workspace, tmp_path):
    def scrape(kwargs):
        return [], ["JobSpy:Indeed: responded with status code: 403"]

    result = JobSpyAdapter(_jobspy_context(workspace, tmp_path), scrape=scrape).fetch(None)
    assert result.status == "rate_limited"
    assert result.jobs == []

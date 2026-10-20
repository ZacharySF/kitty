"""hiringcafe against synthetic landing-page JSON; no browser, no network."""

from contextlib import contextmanager

import pytest

from kitty.config import EligibilityRules, Track
from kitty.core.discovery import hiringcafe
from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.eligibility import evaluate
from kitty.core.discovery.hiringcafe import (
    MAX_TOPICS, PAGE_URL, BotCheck, HiringCafeAdapter, page_props, parse_hiringcafe, plan_topics, slugify,
)
from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.discovery.pipeline import run_discover


def _hit(n: int, *, title="Software Engineer Intern", company="Example Co", cluster=None, **data) -> dict:
    processed = {
        "company_name": company,
        "workplace_cities": ["Austin, Texas, US"],
        "workplace_countries": ["US"],
        "workplace_type": "Onsite",
        "estimated_publish_date_millis": 1788825600000,       # 2026-09-08T00:00:00Z
        "visa_sponsorship": False,
        "security_clearance": None,
        "bachelors_degree_requirement": "Required",
        "masters_degree_requirement": "Not Mentioned",
        "doctorate_degree_requirement": "Not Mentioned",
        "hourly_min_compensation": 22,
        "hourly_max_compensation": 29.25,
        "job_category": "Engineering",
        "role_type": "Individual Contributor",
    } | data
    return {
        "apply_url": f"https://jobs.example.invalid/{n}?utm_source=hiringcafe",
        "job_information": {"title": title},
        "v5_processed_job_data": processed,
        "strict_dedup_cluster_id": cluster or f"cl_{n}",
        "source_and_board_token": "grnhse___example",
    }


def _page(*hits, **extra) -> dict:
    return {"ssrHits": list(hits), "ssrTotalCount": 40, "ssrPageSize": 20,
            "ssrIsLastPage": False, "ssrError": None} | extra


def _ctx(workspace, terms: dict[str, list[str]] | None = None, options: dict | None = None):
    config = workspace.config
    if terms is not None:
        tracks = [Track(name=name, search_terms=found, sources=["hiringcafe"]) for name, found in terms.items()]
        object.__setattr__(config.tracks, "tracks", tracks)
    if options is not None:
        object.__setattr__(config.sources.sources["hiringcafe"], "options", options)
    return DiscoverContext(config, lambda name: None, {}, workspace.settings)


def _pages(answers: dict):
    """A PageSession answering url -> pageProps (an Exception value is raised)."""
    calls: list[str] = []

    @contextmanager
    def session():
        def load(url):
            calls.append(url)
            answer = answers.get(url, HttpError(f"HTTP 404 from {url}"))
            if isinstance(answer, Exception):
                raise answer
            return answer
        yield load

    return session, calls


def _adapter(ctx, answers):
    session, calls = _pages(answers)
    sleeps: list[float] = []
    return HiringCafeAdapter(ctx, pages=session, sleep=sleeps.append), calls, sleeps


# ------------------------------------------------------------------- parsing


def test_a_hit_maps_onto_a_normalized_job():
    remote = _hit(2, title="  Firmware   Intern  (Summer 2027) ", company="Remote Co",
                  workplace_type="Remote", workplace_cities=[], security_clearance="Not Mentioned")
    multi = _hit(3, workplace_cities=["Austin, Texas, US", "Toronto, Ontario, CA"],
                 workplace_countries=["US", "CA"], workplace_type="Hybrid")
    jobs, stats = parse_hiringcafe(_page(_hit(1), remote, multi), "software engineer intern")

    assert stats == {"hits": 3, "total": 40, "page_size": 20, "last_page": False, "ssr_error": None,
                     "kept": 3, "bad_hits": 0, "not_intern": 0}
    job = jobs[0]
    assert job.source == "hiringcafe"
    assert job.url == "https://jobs.example.invalid/1?utm_source=hiringcafe"
    assert job.source_job_id == "https://jobs.example.invalid/1"          # tracking stripped
    assert (job.company, job.title) == ("Example Co", "Software Engineer Intern")
    assert (job.location, job.is_remote, job.country) == ("Austin, Texas, US", False, "United States")
    assert job.posted_at.startswith("2026-09-08T00:00:00")
    assert job.degrees is None and job.season is None and not job.closed
    assert job.raw["topic"] == "software engineer intern"
    assert job.raw["comp"]["hourly_min"] == 22 and job.raw["comp"]["hourly_max"] == 29.25
    assert job.raw["cluster"] == "cl_1" and job.raw["workplace_type"] == "Onsite"
    assert job.raw["eligibility_note"] is None

    assert jobs[1].title == "Firmware Intern (Summer 2027)" and jobs[1].season == "Summer 2027"
    assert (jobs[1].location, jobs[1].is_remote) == ("Remote", True)
    assert jobs[2].location == "Austin, Texas, US; Toronto, Ontario, CA"
    assert jobs[2].country == "United States" and jobs[2].is_remote is False


def test_bad_hits_and_non_internships_are_counted_not_kept():
    no_url = _hit(1) | {"apply_url": "/viewjob/abc"}
    no_company = _hit(2, company="")
    no_data = {"apply_url": "https://jobs.example.invalid/3", "job_information": {"title": "Intern"}}
    full_time = _hit(4, title="Senior Software Engineer")
    jobs, stats = parse_hiringcafe(_page(no_url, no_company, no_data, full_time, "junk"), "software engineer intern")
    assert jobs == [] and stats["bad_hits"] == 4 and stats["not_intern"] == 1

    # A topic that is not an internship term keeps every title.
    jobs, _ = parse_hiringcafe(_page(_hit(4, title="Senior Software Engineer")), "software engineer")
    assert [job.title for job in jobs] == ["Senior Software Engineer"]


def test_graduate_only_requirements_are_dropped_like_simplify():
    rules = EligibilityRules()
    masters = _hit(1, bachelors_degree_requirement="Not Mentioned", masters_degree_requirement="Required")
    phd = _hit(2, bachelors_degree_requirement="Not Mentioned", masters_degree_requirement="Preferred",
               doctorate_degree_requirement="Required")
    both_ok = _hit(3, masters_degree_requirement="Required")                  # bachelor's also required
    preferred = _hit(4, bachelors_degree_requirement="Preferred", doctorate_degree_requirement="Required")
    jobs, _ = parse_hiringcafe(_page(masters, phd, both_ok, preferred), "software engineer intern")

    assert [job.degrees for job in jobs] == [["Master's"], ["PhD"], None, None]
    verdicts = [evaluate(title=job.title, description=None, location=job.location, country=job.country,
                         degrees=job.degrees, rules=rules) for job in jobs]
    assert [v.eligibility for v in verdicts[:2]] == ["ineligible", "ineligible"]
    assert all(v.eligibility != "ineligible" for v in verdicts[2:])


def test_a_stated_clearance_becomes_an_eligibility_note_but_unstated_sponsorship_does_not():
    secret = _hit(1, security_clearance="Secret")
    sponsored = _hit(2, visa_sponsorship=True, security_clearance="None")
    jobs, _ = parse_hiringcafe(_page(secret, sponsored), "software engineer intern")
    assert "Secret" in jobs[0].raw["eligibility_note"] and jobs[0].raw["security_clearance"] == "Secret"
    assert jobs[1].raw["eligibility_note"] is None and jobs[1].raw["visa_sponsorship"] is True


def test_next_data_without_page_props_is_an_error():
    assert page_props('{"props": {"pageProps": {"ssrHits": []}}}') == {"ssrHits": []}
    with pytest.raises(HttpError, match="no props.pageProps"):
        page_props('{"props": {}}')
    with pytest.raises(ValueError):
        page_props("<html>not json</html>")


# -------------------------------------------------------------------- topics


def test_topics_are_slugged_round_robin_deduplicated_and_capped():
    assert slugify("GPU software intern") == "gpu-software-intern"
    assert slugify("  C++ / CUDA   intern!! ") == "c-cuda-intern"
    assert slugify("***") == ""
    assert len(slugify("x " * 100)) <= 80 and not slugify("x " * 100).endswith("-")

    tracks = [
        Track(name="a", search_terms=["software engineer intern", "SRE intern", "!!!"], sources=["hiringcafe"]),
        Track(name="b", search_terms=["Software Engineer  Intern", "FPGA intern"], sources=["hiringcafe"]),
        Track(name="c", search_terms=["barista"], sources=["jobspy"]),           # does not select hiringcafe
    ]
    assert plan_topics(tracks, 40) == [
        ("software engineer intern", "software-engineer-intern"),
        ("SRE intern", "sre-intern"), ("FPGA intern", "fpga-intern"),
    ]
    assert len(plan_topics(tracks, 2)) == 2
    many = [Track(name="m", search_terms=[f"topic {i} intern" for i in range(100)], sources=["hiringcafe"])]
    assert len(plan_topics(many, MAX_TOPICS)) == MAX_TOPICS


def test_max_topics_out_of_bounds_and_no_terms_are_errors(workspace):
    ctx = _ctx(workspace, {"software": ["software engineer intern"]}, {"max_topics": MAX_TOPICS + 1})
    result = HiringCafeAdapter(ctx, pages=_pages({})[0]).fetch(None)
    assert result.status == "error" and "max_topics" in result.error

    ctx = _ctx(workspace, {"software": []}, {})
    result = HiringCafeAdapter(ctx, pages=_pages({})[0]).fetch(None)
    assert result.status == "error" and "search_terms" in result.error


# ------------------------------------------------------------------- fetching


def test_postings_are_deduplicated_across_topics_by_url_and_cluster(workspace):
    ctx = _ctx(workspace, {"software": ["software engineer intern"], "embedded": ["firmware intern"]})
    adapter, calls, sleeps = _adapter(ctx, {
        PAGE_URL + "software-engineer-intern": _page(_hit(1), _hit(2, cluster="cl_shared")),
        PAGE_URL + "firmware-intern": _page(
            _hit(1, title="Firmware Intern"),                        # same posting URL
            _hit(3, title="Firmware Intern", cluster="cl_shared"),    # same cluster, other URL
            _hit(4, title="Firmware Intern"),
        ),
    })
    result = adapter.fetch(None)

    assert result.status == "ok" and result.error is None
    assert calls == [PAGE_URL + "software-engineer-intern", PAGE_URL + "firmware-intern"]
    assert sorted(job.source_job_id for job in result.jobs) == [
        "https://jobs.example.invalid/1", "https://jobs.example.invalid/2", "https://jobs.example.invalid/4"]
    assert result.stats["duplicates"] == 1 and result.stats["cluster_duplicates"] == 1
    assert result.stats["complete_snapshot"] is False                 # ranked pages never close jobs
    assert [b.count for b in result.boards] == [2, 1]
    assert all(job.observed_at for job in result.jobs)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 5                    # paced between the two loads


def test_an_unanswered_bot_check_is_an_error_and_stops_the_run(workspace):
    ctx = _ctx(workspace, {"software": ["software engineer intern", "SRE intern", "devops intern"]})
    adapter, calls, _ = _adapter(ctx, {
        PAGE_URL + "software-engineer-intern": BotCheck("Cloudflare's bot check did not clear"),
        PAGE_URL + "sre-intern": _page(_hit(1)),
    })
    result = adapter.fetch(None)
    assert result.status == "error" and result.jobs == []
    assert "bot check did not clear" in result.error and "2 of 3 topics not tried" in result.error
    assert calls == [PAGE_URL + "software-engineer-intern"]


def test_some_topics_failing_makes_the_run_partial(workspace):
    ctx = _ctx(workspace, {"software": ["software engineer intern", "SRE intern", "devops intern", "linux intern"]})
    adapter, _, _ = _adapter(ctx, {
        PAGE_URL + "software-engineer-intern": _page(_hit(1)),
        PAGE_URL + "sre-intern": _page(ssrError="search backend unavailable"),
        PAGE_URL + "devops-intern": {"somethingElse": True},          # no ssrHits
        # linux-intern: the loader fails (HTTP 404)
    })
    result = adapter.fetch(None)
    assert result.status == "partial" and len(result.jobs) == 1
    assert "3 of 4 boards had problems" in result.error
    assert "ssrError" in result.error and "no ssrHits" in result.error and "404" in result.error


def test_malformed_postings_do_not_report_a_healthy_source(workspace):
    ctx = _ctx(workspace, {"software": ["software engineer intern"]})
    malformed = _hit(2) | {"apply_url": "/viewjob/invalid"}
    adapter, _, _ = _adapter(ctx, {
        PAGE_URL + "software-engineer-intern": _page(_hit(1), malformed),
    })
    result = adapter.fetch(None)
    assert result.status == "partial"
    assert [job.source_job_id for job in result.jobs] == ["https://jobs.example.invalid/1"]
    assert "malformed postings" in result.error
    adapter, _, _ = _adapter(ctx, {
        PAGE_URL + "software-engineer-intern": _page(malformed),
    })
    result = adapter.fetch(None)
    assert result.status == "error" and not result.jobs


def test_a_rate_limit_stops_the_run(workspace):
    ctx = _ctx(workspace, {"software": ["software engineer intern", "SRE intern"]})
    adapter, calls, _ = _adapter(ctx, {PAGE_URL + "software-engineer-intern": RateLimited("too many requests")})
    result = adapter.fetch(None)
    assert result.status == "rate_limited" and len(calls) == 1

    adapter, _, _ = _adapter(ctx, {
        PAGE_URL + "software-engineer-intern": _page(_hit(1)),
        PAGE_URL + "sre-intern": RateLimited("too many requests"),
    })
    assert adapter.fetch(None).status == "partial"


def test_a_browser_that_will_not_start_is_an_error(workspace):
    ctx = _ctx(workspace, {"software": ["software engineer intern"]})

    @contextmanager
    def no_firefox():
        raise hiringcafe.BrowserError("Firefox not found")
        yield

    result = HiringCafeAdapter(ctx, pages=no_firefox).fetch(None)
    assert result.status == "error" and "Firefox not found" in result.error


# ------------------------------------------------------- the Firefox loader


class _FakeFrame:
    def __init__(self, probe):
        self.probe = probe

    def call(self, function, *args, **kwargs):
        return self.probe


class _FakeTab:
    def __init__(self, probe):
        self.main = _FakeFrame(probe)
        self.visited: list[str] = []

    def goto(self, url, **kwargs):
        self.visited.append(url)

    def close(self):
        pass


def _firefox(monkeypatch, workspace, probe):
    tab = _FakeTab(probe)
    profiles = []

    @contextmanager
    def fake_headless(settings, *, profile_dir=None):
        profiles.append(profile_dir)
        yield object()

    monkeypatch.setattr(hiringcafe, "headless_firefox", fake_headless)
    monkeypatch.setattr(hiringcafe, "open_tab", lambda bidi, background=False: tab)
    return hiringcafe.firefox_pages(workspace.settings, wait=0), tab, profiles


def test_firefox_loader_uses_its_own_profile_and_reads_next_data(monkeypatch, workspace):
    session, tab, profiles = _firefox(monkeypatch, workspace, {
        "title": "Software Engineer Intern Jobs", "body": "",
        "data": '{"props": {"pageProps": {"ssrHits": [], "ssrError": null}}}',
    })
    with session as load:
        assert load(PAGE_URL + "software-engineer-intern") == {"ssrHits": [], "ssrError": None}
    assert profiles == [workspace.settings.data_dir / "hiringcafe-firefox"]
    assert profiles[0] != workspace.settings.browser_profile_dir


@pytest.mark.parametrize(("probe", "error", "match"), [
    ({"title": "Just a moment...", "body": "Verify you are human", "data": None}, BotCheck, "did not clear"),
    ({"title": "Attention Required!", "body": "Sorry, you have been blocked", "data": None}, BotCheck, "refused"),
    ({"title": "Access denied", "body": "Error 1015 You are being rate limited", "data": None}, RateLimited, "too many"),
    ({"title": "HiringCafe", "body": "Something else", "data": None}, HttpError, "no __NEXT_DATA__"),
])
def test_firefox_loader_reports_what_stopped_the_page(monkeypatch, workspace, probe, error, match):
    session, _, _ = _firefox(monkeypatch, workspace, probe)
    with session as load, pytest.raises(error, match=match):
        load(PAGE_URL + "software-engineer-intern")


# ------------------------------------------------------------------ pipeline


def test_discover_stores_hiringcafe_postings_with_their_eligibility(workspace):
    _ctx(workspace, {"software": ["software engineer intern"]})       # narrows the tracks for this run
    session, _ = _pages({PAGE_URL + "software-engineer-intern": _page(
        _hit(1, company="Plain Co"),
        _hit(2, company="Cleared Co", security_clearance="Top Secret"),
        _hit(3, company="Grad Co", bachelors_degree_requirement="Not Mentioned",
             masters_degree_requirement="Required"),
    )})
    adapters = {"hiringcafe": lambda ctx: HiringCafeAdapter(ctx, pages=session, sleep=lambda s: None)}
    result = run_discover(workspace.settings, workspace.config, adapters=adapters,
                          only=["hiringcafe"], http_factory=lambda name: None)

    tally = result.sources["hiringcafe"]
    assert tally.status == "ok" and tally.fetched == 3 and tally.new == 3 and tally.unrouted == 0
    rows = {row["source_job_id"]: row for row in workspace.conn.execute(
        "SELECT s.source_job_id, j.eligibility, j.eligibility_reason FROM jobs j "
        "JOIN job_sources s ON s.job_id = j.id WHERE s.source = 'hiringcafe'")}
    assert rows["https://jobs.example.invalid/2"]["eligibility"] == "ambiguous"
    assert "Top Secret" in rows["https://jobs.example.invalid/2"]["eligibility_reason"]
    assert rows["https://jobs.example.invalid/3"]["eligibility"] == "ineligible"

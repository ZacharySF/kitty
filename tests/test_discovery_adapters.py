"""Each adapter against a trimmed real response, plus the failure paths."""

import io
import logging
import tarfile

from applyai.config import WatchlistCompany, WatchlistFile
from applyai.core.discovery.ats import (
    AshbyAdapter, GreenhouseAdapter, LeverAdapter, SmartRecruitersAdapter,
)
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.discovery.repos import NUFT_TARBALL, NuftAdapter, parse_simplify
from applyai.core.discovery.workday import WorkdayAdapter, fetch_board, posted_on_to_iso
from fakes import FIXTURES, FakeHttp, fixture


def _context(workspace, companies, routes, previous=None):
    """A DiscoverContext whose watchlist is `companies` and whose HTTP is fake."""
    http = FakeHttp(routes)
    config = workspace.config
    object.__setattr__(config, "watchlist", WatchlistFile(companies=companies))
    return DiscoverContext(config, lambda name: http, previous or {}), http


def _company(name, ats, slug):
    return WatchlistCompany(name=name, ats=ats, slug=slug, tracks=["software"])


# ------------------------------------------------------------------ repos


def test_simplify_keeps_the_2027_cycle_and_marks_closed_listings(caplog):
    listings = fixture("simplify_listings.json")
    listings[0]["brand_new_field"] = "x"
    with caplog.at_level(logging.WARNING):
        jobs, stats = parse_simplify(listings)

    assert stats["not_2027_cycle"] == 1               # the Summer 2026-only listing
    assert len(jobs) == len(listings) - 1
    assert any(job.closed for job in jobs)            # active=false
    assert stats["unknown_fields"] == ["brand_new_field"]
    assert "brand_new_field" in caplog.text           # logged, not fatal
    # 'Software Engineering' is folded into 'Software'.
    assert "Software Engineering" not in stats["categories"]
    contributed = next(job for job in jobs if job.raw["contributor"] != "Simplify")
    assert contributed.source == "simplify2027"       # the source field is a username
    assert any(job.degrees == ["PhD"] for job in jobs)


def _nuft_tarball():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted((FIXTURES / "nuft").iterdir()):
            archive.add(path, arcname=f"2027QuantInternships-main/data/{path.name}")
    return buffer.getvalue()


def test_nuft_turns_each_role_link_into_a_posting(workspace):
    ctx, _ = _context(workspace, [], {NUFT_TARBALL: _nuft_tarball()})
    result = NuftAdapter(ctx).fetch(None)

    assert result.status == "ok"
    assert result.stats["firms"] == 3 and result.stats["firms_with_roles"] == 2
    titles = {job.title for job in result.jobs if job.company == "Example Markets"}
    assert "Quantitative Trader Intern (Chicago)" in titles
    chicago = next(job for job in result.jobs if job.title == "Quantitative Trader Intern (Chicago)")
    assert chicago.location == "Chicago"               # a place label beats the firm's list
    assert all(job.season == "Summer 2027" for job in result.jobs)


# -------------------------------------------------------------------- ATS


def test_greenhouse_keeps_only_internships(workspace):
    url = "https://boards-api.greenhouse.io/v1/boards/astranis/jobs?content=true"
    ctx, _ = _context(workspace, [_company("Astranis", "greenhouse", "astranis")],
                      {url: fixture("greenhouse_board.json")})
    result = GreenhouseAdapter(ctx).fetch(None)

    assert result.status == "ok"
    assert result.stats["postings_seen"] == 3
    assert [job.title for job in result.jobs] == ["Antenna Intern (Summer 2027)", "Antenna Intern (Winter 2027)"]
    job = result.jobs[0]
    assert job.company == "Astranis" and job.season == "Summer 2027"
    assert job.description and "&lt;" not in job.description


def test_lever_ashby_and_smartrecruiters_parse(workspace):
    lever_url = "https://api.lever.co/v0/postings/palantir?mode=json"
    ashby_url = "https://api.ashbyhq.com/posting-api/job-board/skydio?includeCompensation=true"
    sr_list = fixture("smartrecruiters_list.json")
    sr_base = "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings"
    intern_id = sr_list["content"][0]["id"]
    ctx, _ = _context(workspace, [
        _company("Palantir", "lever", "palantir"),
        _company("Skydio", "ashby", "skydio"),
        _company("ServiceNow", "smartrecruiters", "ServiceNow"),
    ], {
        lever_url: fixture("lever_board.json"),
        ashby_url: fixture("ashby_board.json"),
        f"{sr_base}?limit=100&offset=0": sr_list,
        f"{sr_base}/{intern_id}": fixture("smartrecruiters_detail.json"),
    })

    lever = LeverAdapter(ctx).fetch(None)
    assert [job.title for job in lever.jobs] == ["Deployment Strategist, Internship"]

    ashby = AshbyAdapter(ctx).fetch(None)
    assert len(ashby.jobs) == 1 and ashby.jobs[0].country == "Switzerland"

    sr = SmartRecruitersAdapter(ctx).fetch(None)
    assert [job.title for job in sr.jobs] == ["Finance Intern - Undergrad Summer 2027"]
    assert sr.jobs[0].description
    assert sr.jobs[0].url == fixture("smartrecruiters_detail.json")["postingUrl"]


def test_one_broken_board_makes_the_source_partial_not_failed(workspace):
    good = "https://boards-api.greenhouse.io/v1/boards/astranis/jobs?content=true"
    ctx, _ = _context(workspace, [
        _company("Astranis", "greenhouse", "astranis"),
        _company("Gone Co", "greenhouse", "goneco"),               # no route: 404
    ], {good: fixture("greenhouse_board.json")})
    result = GreenhouseAdapter(ctx).fetch(None)

    assert result.status == "partial"
    assert "goneco" in result.error
    assert len(result.jobs) == 2


def test_a_429_stops_the_source_and_is_reported(workspace):
    first = "https://boards-api.greenhouse.io/v1/boards/one/jobs?content=true"
    ctx, http = _context(workspace, [
        _company("One", "greenhouse", "one"), _company("Two", "greenhouse", "two"),
    ], {first: RateLimited("429 from boards-api.greenhouse.io")})
    result = GreenhouseAdapter(ctx).fetch(None)

    assert result.status == "rate_limited"
    assert len(http.calls) == 1                     # never retried, never moved on
    assert result.jobs == []


def test_a_board_that_drops_to_zero_is_a_warning(workspace):
    url = "https://boards-api.greenhouse.io/v1/boards/astranis/jobs?content=true"
    ctx, _ = _context(workspace, [_company("Astranis", "greenhouse", "astranis")],
                      {url: {"jobs": []}}, previous={"greenhouse": {"astranis": 12}})
    result = GreenhouseAdapter(ctx).fetch(None)
    assert result.status == "error" or result.status == "partial"
    assert "0 postings" in result.error


# ---------------------------------------------------------------- workday


def test_workday_pages_with_search_text_and_fetches_intern_details(workspace):
    slug = "sonos/wd1/Student_Opportunities"
    api = "https://sonos.wd1.myworkdayjobs.com/wday/cxs/sonos/Student_Opportunities"
    listing = fixture("workday_sonos_list.json")
    detail = fixture("workday_sonos_detail.json")
    ctx, http = _context(workspace, [_company("Sonos", "workday", slug)], {
        f"{api}/jobs": listing,
        f"{api}{listing['jobPostings'][0]['externalPath']}": detail,
        f"{api}{listing['jobPostings'][1]['externalPath']}": detail,
    })
    result = WorkdayAdapter(ctx).fetch(None)

    assert result.status == "ok"
    job = result.jobs[0]
    assert job.country == "United States of America"
    assert job.url == detail["jobPostingInfo"]["externalUrl"]
    assert job.posted_at == "2026-09-23T00:00:00Z"


def test_workday_trusts_total_from_the_first_page_only(workspace):
    slug = "big/wd5/External"
    api = "https://big.wd5.myworkdayjobs.com/wday/cxs/big/External"
    page = lambda n: {"total": 0, "jobPostings": [   # noqa: E731 - later pages say total 0
        {"title": "Senior Engineer", "externalPath": f"/job/x/{n}-{i}", "locationsText": "Austin, TX"}
        for i in range(20)
    ]}
    ctx, http = _context(workspace, [_company("Big", "workday", slug)], {
        (f"{api}/jobs", 0): {**page(0), "total": 45},
        (f"{api}/jobs", 20): page(1),
        (f"{api}/jobs", 40): {"total": 0, "jobPostings": page(2)["jobPostings"][:5]},
    })
    ctx.config.sources.sources["workday"].options["search_queries"] = ["intern"]
    result = WorkdayAdapter(ctx).fetch(None)
    assert result.status == "ok"
    assert [body["offset"] for method, _, body in http.calls if method == "POST"] == [0, 20, 40]
    assert result.jobs == []


def test_workday_finds_internships_after_an_irrelevant_page(workspace):
    slug = "big/wd5/External"
    api = "https://big.wd5.myworkdayjobs.com/wday/cxs/big/External"
    page = lambda n, title: {"total": 0, "jobPostings": [   # noqa: E731 - later pages say total 0
        {"title": title, "externalPath": f"/job/x/{n}-{i}", "locationsText": "Austin, TX"}
        for i in range(20)
    ]}
    detail = {"jobPostingInfo": {"title": "Software Intern", "location": "Austin, TX"}}
    routes = {
        (f"{api}/jobs", 0): {**page(0, "Software Intern"), "total": 45},
        (f"{api}/jobs", 20): page(1, "Senior Engineer"),
        (f"{api}/jobs", 40): {"total": 0, "jobPostings": page(2, "Software Intern")["jobPostings"][:5]},
    }
    routes.update({f"{api}/job/x/0-{i}": detail for i in range(20)})
    routes.update({f"{api}/job/x/2-{i}": detail for i in range(5)})
    ctx, http = _context(workspace, [_company("Big", "workday", slug)], routes)
    ctx.config.sources.sources["workday"].options["search_queries"] = ["intern"]
    result = WorkdayAdapter(ctx).fetch(None)
    assert result.status == "ok"
    assert {job.source_job_id for job in result.jobs} == {
        f"{slug}:/job/x/{page}-{i}" for page, count in ((0, 20), (2, 5)) for i in range(count)
    }


def test_workday_bound_reports_partial_and_preserves_detail_failures(workspace):
    slug = "bounded/wd1/External"
    api = "https://bounded.wd1.myworkdayjobs.com/wday/cxs/bounded/External"
    listings = [
        {"title": "Software Intern", "externalPath": f"/job/{i}", "locationsText": "Austin, TX"}
        for i in range(3)
    ]
    ctx, http = _context(workspace, [_company("Bounded", "workday", slug)], {
        f"{api}/jobs": {"total": 10, "jobPostings": listings},
        f"{api}/job/0": {"jobPostingInfo": {"jobDescription": "Build real systems."}},
        f"{api}/job/1": HttpError("HTTP 503"),
        f"{api}/job/2": {"jobPostingInfo": {"jobDescription": "Build control systems."}},
    })
    ctx.config.sources.sources["workday"].options.update(
        search_queries=["intern"], max_results_per_query=3,
    )
    result = WorkdayAdapter(ctx).fetch(None)
    assert result.status == "partial" and result.boards[0].status == "partial"
    assert "truncated" in result.error and "503" in result.error
    assert result.stats["complete_snapshot"] is False
    assert {job.url.rsplit("/", 1)[-1]: job.description for job in result.jobs} == {
        "0": "Build real systems.", "1": None, "2": "Build control systems.",
    }


def test_workday_queries_find_distinct_titles_and_deduplicate_details():
    company = _company("Queries", "workday", "queries/wd1/External")
    api = "https://queries.wd1.myworkdayjobs.com/wday/cxs/queries/External"
    intern = {"title": "Software Intern", "externalPath": "/job/intern"}
    coop = {"title": "Systems Engineering Co Op", "externalPath": "/job/coop"}
    student = {"title": "Student Engineer", "externalPath": "/job/student"}

    class QueryHttp(FakeHttp):
        def post_json(self, url, body):
            self.calls.append(("POST", url, body))
            listings = {"intern": [intern], "co-op": [intern, coop], "student": [student]}
            postings = listings[body["searchText"]]
            return {"total": len(postings), "jobPostings": postings}

    http = QueryHttp({f"{api}{listing['externalPath']}": {"jobPostingInfo": {}}
                      for listing in (intern, coop, student)})
    fetched = fetch_board(company, http, queries=("intern", "co-op", "student"))
    assert not fetched.problems
    assert {job.title for job in fetched.jobs} == {item["title"] for item in (intern, coop, student)}
    assert sum(method == "GET" and url == f"{api}/job/intern" for method, url, _ in http.calls) == 1


def test_workday_late_page_failure_retains_earlier_jobs():
    company = _company("Partial", "workday", "partial/wd1/External")
    api = "https://partial.wd1.myworkdayjobs.com/wday/cxs/partial/External"
    listing = {"title": "Software Intern", "externalPath": "/job/1"}
    http = FakeHttp({
        (f"{api}/jobs", 0): {"total": 2, "jobPostings": [listing]},
        (f"{api}/jobs", 1): HttpError("HTTP 500"),
        f"{api}/job/1": {"jobPostingInfo": {"jobDescription": "Build software."}},
    })
    fetched = fetch_board(company, http, queries=("intern",))
    assert [job.description for job in fetched.jobs] == ["Build software."]
    assert any("500" in error for error in fetched.problems)


def test_workday_rate_limit_preserves_list_entry_and_stops_requests():
    company = _company("Limited", "workday", "limited/wd1/External")
    api = "https://limited.wd1.myworkdayjobs.com/wday/cxs/limited/External"
    listing = {"title": "Software Intern", "externalPath": "/job/1"}
    http = FakeHttp({
        f"{api}/jobs": {"total": 2, "jobPostings": [listing]},
        f"{api}/job/1": RateLimited("HTTP 429"),
    })
    fetched = fetch_board(company, http, queries=("intern", "student"))
    assert fetched.rate_limited
    assert [job.title for job in fetched.jobs] == ["Software Intern"]
    assert len(http.calls) == 2


def test_posted_on_text():
    assert posted_on_to_iso("Posted Today") is not None
    assert posted_on_to_iso("Posted 30+ Days Ago") < posted_on_to_iso("Posted Yesterday")
    assert posted_on_to_iso("something else") is None

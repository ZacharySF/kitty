"""The Starbucks careers adapter, against trimmed copies of the real API answers."""

from dataclasses import replace

from applyai.config import EligibilityRules
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.eligibility import evaluate
from applyai.core.discovery.http import RateLimited
from applyai.core.discovery.starbucks import StarbucksAdapter, details_url, search_url
from fakes import FakeHttp, fixture


def _context(workspace, http, locations):
    sources = workspace.config.sources
    starbucks = sources.sources["starbucks"].model_copy(update={"options": {"locations": locations}})
    config = replace(workspace.config, sources=sources.model_copy(
        update={"sources": {**sources.sources, "starbucks": starbucks}}))
    return DiscoverContext(config, lambda name: http)


def test_keeps_only_the_searched_citys_stores_with_their_pay(workspace):
    search, details = fixture("starbucks_search.json"), fixture("starbucks_details.json")
    city = search["data"]["positions"][0]["standardizedLocations"][0].split(",")[0]
    in_city = [p for p in search["data"]["positions"] if p["standardizedLocations"][0].startswith(city)]
    routes = {search_url(f"{city}, TX", 0): search}
    routes.update({details_url(p["id"]): details for p in in_city})
    http = FakeHttp(routes)

    result = StarbucksAdapter(_context(workspace, http, [f"{city}, TX"])).fetch(None)

    assert result.status == "ok"
    assert sorted(job.source_job_id for job in result.jobs) == sorted(str(p["id"]) for p in in_city)
    # one search page (it was short) plus one details request per kept store, none for the far one
    assert http.requests_made == 1 + len(in_city)
    job = result.jobs[0]
    assert job.company == "Starbucks" and job.location.startswith(city) and job.url.startswith("https://")
    assert job.description.startswith("Pay: $15.25 - $17.31 per hour")
    verdict = evaluate(title=job.title, description=job.description, location=job.location,
                       country=job.country, degrees=None, rules=EligibilityRules(min_hourly_pay=12))
    assert verdict.eligibility == "eligible"


def test_a_rate_limited_city_is_reported_not_counted_as_empty(workspace):
    http = FakeHttp({search_url("Hewitt, TX", 0): RateLimited("429 from apply.starbucks.com")})
    result = StarbucksAdapter(_context(workspace, http, ["Hewitt, TX", "Waco, TX"])).fetch(None)
    assert result.status == "rate_limited" and result.jobs == []


def test_intervening_neighbor_city_does_not_hide_later_local_stores(workspace):
    city = "Hewitt, TX"
    neighbor = {"id": "far", "name": "Barista", "standardizedLocations": ["Houston, TX, US"]}
    local = {"id": "local", "name": "Barista", "standardizedLocations": ["Hewitt, TX, US"],
             "positionUrl": "/careers/job/local"}
    http = FakeHttp({
        search_url(city, 0): {"data": {"positions": [neighbor] * 10, "count": 11}},
        search_url(city, 10): {"data": {"positions": [local], "count": 11}},
        details_url("local"): {"data": {"jobDescription": "Serve customers."}},
    })
    result = StarbucksAdapter(_context(workspace, http, [city])).fetch(None)
    assert [job.source_job_id for job in result.jobs] == ["local"]
    assert result.status == "ok" and result.stats["complete_snapshot"] is False


def test_store_page_cap_is_partial_not_a_complete_snapshot(workspace):
    city = "Hewitt, TX"
    neighbor = {"id": "far", "standardizedLocations": ["Houston, TX, US"]}
    http = FakeHttp({
        search_url(city, offset): {"data": {"positions": [neighbor] * 10, "count": 50}}
        for offset in (0, 10, 20)
    })
    result = StarbucksAdapter(_context(workspace, http, [city])).fetch(None)
    assert result.status == "error"  # no inventory, but partial city coverage is reported
    assert "page cap 3 reached" in result.error
    assert result.stats["complete_snapshot"] is False

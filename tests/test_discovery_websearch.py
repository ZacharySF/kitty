"""Public discovery keeps source evidence and never fetches blocked/private targets."""
import json
import socket
from types import SimpleNamespace

import httpx
import pytest

from kitty.core.discovery import public_pages, websearch
from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.http import HttpError, PoliteHttp, RateLimited
from kitty.core.discovery.public_pages import PublicPage, PublicPages, parse_job_postings
from kitty.core.discovery.websearch import WebSearchAdapter


def posting(**changes):
    return {"@type": "JobPosting", "title": "Firmware Intern - Summer 2027",
            "hiringOrganization": {"name": "Exact Employer, Inc."},
            "identifier": {"value": "REQ-007"}, "url": "/jobs/7",
            "jobLocation": {"address": {"addressLocality": "Austin", "addressRegion": "TX",
                                        "addressCountry": "US"}},
            "description": "<p>Build firmware.</p>", **changes}


def markup(*records):
    return '<script type="application/ld+json">' + json.dumps({"@graph": list(records)}) + '</script>'


@pytest.fixture
def public_dns(monkeypatch):
    def resolve(host, port, **kwargs):
        address = host if host in {"127.0.0.1", "169.254.169.254", "::1"} else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    monkeypatch.setattr(public_pages.socket, "getaddrinfo", resolve)


def fetcher(tmp_path, routes, *, ttl=0, **kwargs):
    requests = []
    def handle(request):
        requests.append(request)
        key = (request.headers["host"], request.url.raw_path.decode())
        status, headers, text = routes.get(key, (404, {}, ""))
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(text.encode()))
    http = PoliteHttp(tmp_path, min_interval=0, cache_ttl_minutes=ttl)
    pages = PublicPages(http, transport=httpx.MockTransport(handle), **kwargs)
    return pages, http, requests


def test_parser_preserves_identity_dates_qualifications_and_expiry():
    record = posting(validThrough="2000-12-31T23:59:59Z", datePosted="2000-11-01",
                     applicationStartDate="2000-11-02", qualifications="<p>Current undergraduate.</p>",
                     educationRequirements={"@type": "EducationalOccupationalCredential",
                                            "credentialCategory": "Bachelor's degree"})
    page = PublicPage("https://employer.example/jobs", markup(record, record), "2026-01-01T00:00:00Z")
    jobs = parse_job_postings(page, source="websearch", query="firmware internship")
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.company, job.title, job.url) == ("Exact Employer, Inc.", "Firmware Intern - Summer 2027", "https://employer.example/jobs/7")
    assert job.location == "Austin, TX, US" and job.country == "US"
    assert job.description == "Build firmware.\n\nCurrent undergraduate.\n\nBachelor's degree"
    assert job.deadline_at == "2000-12-31T23:59:59Z"
    assert job.posted_at == "2000-11-01T00:00:00Z" and job.opens_at == "2000-11-02T00:00:00Z"
    assert job.closed and job.raw["identifier"] == {"value": "REQ-007"}
    assert job.raw["provenance"]["query"] == "firmware internship"
    assert job.raw["provenance"]["url"] == page.url
    assert job.raw["provenance"]["expired"] is True
    assert job.raw["provenance"]["explicitly_closed"] is False


def test_parser_keeps_distinct_ids_and_employers_and_remote_country():
    jobs = parse_job_postings(PublicPage("https://employer.example/list", markup(
        posting(), posting(identifier={"value": "REQ-008"}),
        posting(hiringOrganization={"name": "Other Employer"}),
        posting(identifier=None, title="Software Intern", jobLocation=None,
                jobLocationType="TELECOMMUTE", applicantLocationRequirements={"@type": "Country", "name": "Canada"}),
    ), "now"), source="websearch")
    assert len({job.source_job_id for job in jobs}) == 4
    assert jobs[-1].country == "Canada" and jobs[-1].location is None and jobs[-1].is_remote


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/",
                                  "http://localhost/", "https://user:pass@example.com/", "file:///etc/passwd",
                                  "https://example.com:8000/"])
def test_rejects_private_targets_before_network(tmp_path, public_dns, url):
    pages, http, requests = fetcher(tmp_path, {})
    try:
        with pytest.raises(HttpError):
            pages.fetch(url)
        assert requests == []
    finally:
        pages.close()
        http.close()


def test_redirect_rechecks_dns_and_never_sends_credentials(tmp_path, public_dns):
    routes = {("public.example", "/robots.txt"): (200, {}, "User-agent: *\nAllow: /"),
              ("public.example", "/job"): (302, {"location": "http://127.0.0.1/secrets"}, "")}
    pages, http, requests = fetcher(tmp_path, routes)
    http.client.headers["Authorization"] = "private"
    try:
        with pytest.raises(HttpError, match="non-public"):
            pages.fetch("https://public.example/job")
        assert len(requests) == 2
        assert all(request.url.host == "93.184.216.34" for request in requests)
        assert all(request.extensions["sni_hostname"] == "public.example" for request in requests)
        assert all("authorization" not in request.headers for request in requests)
    finally:
        pages.close()
        http.close()


def test_robots_longest_match_wildcards_ties_and_agent_groups(tmp_path, public_dns):
    rules = """User-agent: *
Disallow: /
Allow: /careers
Disallow: /careers/private/*
Allow: /careers/private/public$
Disallow: /careers/tie
Allow: /careers/tie
User-agent: OtherBot
Disallow: /careers
"""
    routes = {("public.example", "/robots.txt"): (200, {}, rules),
              ("public.example", "/careers/tie"): (200, {}, "allowed"),
              ("public.example", "/careers/private/public"): (200, {}, "allowed")}
    pages, http, requests = fetcher(tmp_path, routes)
    try:
        assert pages.fetch("https://public.example/careers/tie").text == "allowed"
        assert pages.fetch("https://public.example/careers/private/public").text == "allowed"
        with pytest.raises(HttpError, match="robots.txt disallows"):
            pages.fetch("https://public.example/careers/private/public/more")
        with pytest.raises(HttpError, match="robots.txt disallows"):
            pages.fetch("https://public.example/elsewhere")
        assert len(requests) == 3
        specific = public_pages._Robots("User-agent: *\nDisallow: /\nUser-agent: kitty\nAllow: /jobs\nDisallow: /jobs/private")
        assert specific.allows("https://public.example/jobs/open")
        assert not specific.allows("https://public.example/jobs/private")
        assert specific.allows("https://public.example/elsewhere")
    finally:
        pages.close()
        http.close()


@pytest.mark.parametrize("status,text", [(403, ""), (503, "unavailable"), (200, "<html>Login required</html>")])
def test_robots_failure_never_becomes_empty_jobs(tmp_path, public_dns, status, text):
    pages, http, requests = fetcher(tmp_path, {("public.example", "/robots.txt"): (status, {}, text)})
    try:
        with pytest.raises(HttpError):
            pages.fetch("https://public.example/job")
        assert len(requests) == 1
    finally:
        pages.close()
        http.close()


def test_bounded_body_cache_and_rate_limit(tmp_path, public_dns):
    routes = {("public.example", "/job"): (200, {}, "cached"),
              ("public.example", "/large"): (200, {}, "x" * 101),
              ("public.example", "/busy"): (429, {}, "")}
    pages, http, requests = fetcher(tmp_path, routes, ttl=60, max_bytes=100)
    try:
        assert pages.fetch("https://public.example/job").text == "cached"
        assert pages.fetch("https://public.example/job").text == "cached"
        assert http.cache_hits == 1 and len(requests) == 2
        with pytest.raises(HttpError, match="exceeds"):
            pages.fetch("https://public.example/large")
        with pytest.raises(RateLimited):
            pages.fetch("https://public.example/busy")
        count = len(requests)
        with pytest.raises(RateLimited):
            pages.fetch("https://public.example/job")
        assert len(requests) == count
    finally:
        pages.close()
        http.close()


def test_brave_credentials_never_follow_redirect(tmp_path, public_dns):
    path = "/res/v1/web/search?q=intern"
    routes = {("api.search.brave.com", path): (302, {"location": "https://elsewhere.example/"}, "")}
    pages, http, requests = fetcher(tmp_path, routes)
    try:
        with pytest.raises(HttpError, match="HTTP 302"):
            pages.brave_json("https://api.search.brave.com" + path, "secret")
        assert len(requests) == 1 and requests[0].headers["X-Subscription-Token"] == "secret"
        with pytest.raises(HttpError, match="Not a Brave"):
            pages.brave_json("https://api.search.brave.com.evil.example" + path, "secret")
        assert len(requests) == 1
    finally:
        pages.close()
        http.close()


def context(workspace, options, http):
    # Keep the real configured track list rather than inventing track names/counts.
    tracks = [track.model_copy(update={"sources": [*track.sources, "websearch"]})
              for track in workspace.config.tracks.tracks]
    config = SimpleNamespace(sources=SimpleNamespace(sources={"websearch": SimpleNamespace(options=options)}),
                             tracks=SimpleNamespace(tracks=tracks))
    return DiscoverContext(config, lambda source: http)


def test_search_requires_opt_in_even_with_key(workspace, monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "present")
    ctx = context(workspace, {}, None)
    result = WebSearchAdapter(ctx).fetch(None)
    assert result.status == "error" and "enable_search" in result.error
    assert result.stats["complete_snapshot"] is False
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY")
    assert WebSearchAdapter(ctx).fetch(None).status == "error"


def test_seed_only_fetches_real_postings_not_text_or_foreign_roles(workspace, tmp_path, public_dns, monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    text = markup(posting(), posting(identifier="staff", title="Student Affairs Director"),
                  posting(identifier="abroad", jobLocation={"address": {"addressCountry": "Canada", "addressLocality": "Toronto"}}))
    routes = {("public.example", "/job"): (200, {}, text),
              ("public.example", "/snippet"): (200, {}, "Firmware internship in Austin, apply now!")}
    pages, http, requests = fetcher(tmp_path, routes)
    monkeypatch.setattr(websearch, "PublicPages", lambda client: pages)
    try:
        result = WebSearchAdapter(context(workspace, {"seed_urls": ["https://public.example/job", "https://public.example/snippet"]}, http)).fetch(None)
        assert result.status == "ok"
        assert [job.title for job in result.jobs] == ["Firmware Intern - Summer 2027"]
        assert result.stats["skipped_nonstudent"] == result.stats["skipped_foreign"] == 1
        assert result.jobs[0].raw["provenance"]["discovery_url"] == "https://public.example/job"
        assert result.stats["pages"][1]["postings"] == 0
        assert all(request.headers["host"] == "public.example" for request in requests)
    finally:
        http.close()


def test_search_budget_and_evidence_dedupe(workspace, tmp_path, public_dns, monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret")
    api_calls, fetched = [], []
    class Pages:
        def __init__(self, http):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def brave_json(self, url, key):
            api_calls.append(url)
            return {"web": {"results": [{"url": "https://public.example/job", "description": "Invented snippet"},
                                          {"url": "https://public.example/other"}, {"url": "https://public.example/over-budget"}]}}
        def fetch(self, url):
            fetched.append(url)
            return PublicPage(url, markup(posting()), "2026-01-01T00:00:00Z")
    monkeypatch.setattr(websearch, "PublicPages", Pages)
    http = SimpleNamespace(requests_made=0, cache_hits=0)
    ctx = context(workspace, {"enable_search": True, "max_queries": 2, "max_results": 2, "max_pages": 1}, http)
    result = WebSearchAdapter(ctx).fetch(None)
    assert len(api_calls) == min(2, len(websearch.track_queries(ctx)))
    assert fetched == ["https://public.example/job"]
    assert result.stats["page_budget_exhausted"] is True
    assert result.jobs[0].description == "Build firmware."
    assert result.jobs[0].raw["provenance"]["queries"] == result.stats["queries"]
    excluded_terms = {term for track in ctx.config.tracks.tracks if track.name in {"parttime", "research"}
                      for term in track.search_terms}
    eligible_terms = {term for track in ctx.config.tracks.tracks if track.name not in {"parttime", "research"}
                     for term in track.search_terms}
    assert not any(query == f'{term} internship "United States"' for query in result.stats["queries"]
                   for term in excluded_terms - eligible_terms)


def test_seed_failure_reports_partial_and_keeps_all_blocked_urls(workspace, tmp_path, public_dns, monkeypatch):
    pages, http, requests = fetcher(tmp_path, {("public.example", "/job"): (200, {}, markup(posting()))})
    monkeypatch.setattr(websearch, "PublicPages", lambda client: pages)
    seeds = ["https://public.example/job", "http://127.0.0.1/secret", "https://public.example/missing"]
    try:
        result = WebSearchAdapter(context(workspace, {"seed_urls": seeds}, http)).fetch(None)
        assert result.status == "partial" and result.jobs[0].company == "Exact Employer, Inc."
        assert [error["target"] for error in result.stats["errors"]] == seeds[1:]
        assert result.stats["complete_snapshot"] is False
    finally:
        http.close()


def test_mixed_public_private_dns_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(public_pages.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
    ])
    pages, http, requests = fetcher(tmp_path, {})
    try:
        with pytest.raises(HttpError, match="non-public"):
            pages.fetch("https://public.example/job")
        assert requests == []
    finally:
        pages.close()
        http.close()


def test_redirected_resource_obeys_destination_robots(tmp_path, public_dns):
    routes = {
        ("public.example", "/job"): (302, {"location": "https://destination.example/job"}, ""),
        ("destination.example", "/robots.txt"): (200, {}, "User-agent: *\nDisallow: /"),
    }
    pages, http, requests = fetcher(tmp_path, routes)
    try:
        with pytest.raises(HttpError, match="robots.txt disallows"):
            pages.fetch("https://public.example/job")
        assert [(request.headers["host"], request.url.path) for request in requests] == [
            ("public.example", "/robots.txt"), ("public.example", "/job"),
            ("destination.example", "/robots.txt"),
        ]
    finally:
        pages.close()
        http.close()


def test_robots_repeated_wildcards_match_without_backtracking():
    rules = public_pages._Robots("User-agent: *\nDisallow: /" + "*a" * 20 + "b$")
    assert rules.allows("https://employer.example/" + "a" * 100)
    assert not rules.allows("https://employer.example/" + "a" * 100 + "b")
    assert rules.allows("https://employer.example/" + "a" * 100 + "bc")


def test_robots_delay_is_host_scoped_and_excessive_wait_is_deferred(tmp_path, public_dns, monkeypatch):
    clock = [100.0]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(public_pages.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(public_pages.time, "sleep", sleep)
    pages, http, requests = fetcher(tmp_path, {
        ("slow.example", "/robots.txt"): (200, {}, "User-agent: *\nCrawl-delay: 3"),
        ("slow.example", "/jobs"): (200, {}, "slow"),
        ("fast.example", "/robots.txt"): (200, {}, "User-agent: *\nAllow: /"),
        ("fast.example", "/jobs"): (200, {}, "fast"),
        ("deferred.example", "/robots.txt"): (200, {}, "User-agent: *\nCrawl-delay: 7200"),
    })
    try:
        pages.fetch("https://slow.example/jobs")
        pages.fetch("https://fast.example/jobs")
        assert waits == [3.0]
        with pytest.raises(HttpError):
            pages.fetch("https://deferred.example/jobs")
        assert waits == [3.0]
        assert all(r.headers["host"] != "deferred.example" or r.url.path == "/robots.txt" for r in requests)
    finally:
        pages.close()
        http.close()

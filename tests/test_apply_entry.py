"""Reaching the form (proposal-05 Step 3): liveness markers, redirect resolution, routing.

Every network answer is a local HTTP fixture (httpx.MockTransport); DNS is
stubbed to a public address so the real public-page guard runs. No test makes
a live request. All jobs and employers are synthetic.
"""
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import httpx
import pytest

from kitty.core import jobs as jobs_core
from kitty.core.apply import resolve
from kitty.core.apply.urls import (
    clean_url, detect_ats, redirect_source, route_apply_link, unsupported_flow,
)
from kitty.core.discovery import http as http_module, public_pages
from kitty.core.discovery.http import PoliteHttp
from kitty.core.discovery.public_pages import PublicPages


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def public_dns(monkeypatch):
    """Every name resolves to one public documentation-range address; nothing leaves the process."""
    monkeypatch.setattr(public_pages.socket, "getaddrinfo", lambda host, port, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])


class Sites:
    """Local HTTP fixtures keyed by (host, path?query); records every request it answers."""

    def __init__(self, tmp_path, routes: dict, *, interval: float = 0):
        self.routes = routes
        self.requests: list[tuple[str, str, str, bytes]] = []
        transport = httpx.MockTransport(self.handle)
        http = PoliteHttp(tmp_path / "cache", min_interval=interval, cache_ttl_minutes=0,
                          client=httpx.Client(transport=transport))
        self.net = resolve.Net(http, PublicPages(http, max_redirects=resolve.MAX_REDIRECTS, transport=transport))

    def handle(self, request: httpx.Request) -> httpx.Response:
        host, target = request.headers["host"], request.url.raw_path.decode()
        self.requests.append((request.method, host, target, request.content))
        status, headers, text = self.routes.get((host, target), (404, {}, "<h1>Not here</h1>"))
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(text.encode()))

    def fetched(self) -> list[tuple[str, str]]:
        return [(host, target) for _, host, target, _ in self.requests if target != "/robots.txt"]


@pytest.fixture
def sites(tmp_path, public_dns):
    made = []

    def make(routes, **kwargs):
        made.append(Sites(tmp_path, routes, **kwargs))
        return made[-1]
    yield make
    for fixture in made:
        fixture.net.close()


def _job(workspace, url, *, title="Firmware Engineering Intern", location="Austin, TX", **fields):
    job, _ = jobs_core.upsert_job(workspace.conn, company="Synthetic Robotics", title=title, url=url,
                                  location=location, tracks=[workspace.config.track_names[0]], **fields)
    return job


def _page(body: str, *, title: str = "Firmware Engineering Intern") -> str:
    return f'<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>'


def redirect(location: str, status: int = 302):
    return (status, {"location": location}, "")


GREENHOUSE_OPEN = (200, {}, json.dumps({"id": 4321, "title": "Firmware Engineering Intern", "questions": []}))
WORKDAY_OPEN = (200, {}, json.dumps({"jobPostingInfo": {"title": "Firmware Engineering Intern"}}))
GH_API = "boards-api.greenhouse.io"
GH_QUESTIONS = "/v1/boards/synthbots/jobs/4321?questions=true"
WD_HOST = "synth.wd5.myworkdayjobs.com"


# -------------------------------------------------------------- URL routing

@pytest.mark.parametrize("url, family", [
    ("https://careers-synth.icims.com/jobs/1234/login", "icims"),
    ("https://synth.eightfold.ai/careers/job/56789", "eightfold"),
    ("https://careers.synth-defense.com/careers/job/56789", "eightfold"),
    ("https://apply.synth-coffee.com/careers/apply?pid=56789&domain=synth-coffee.com", "eightfold"),
    ("https://careers.synth-servers.com/us/en/job/R1234/Firmware-Intern", "phenom"),
    ("https://synth.phenompeople.com/us/en/job/1234", "phenom"),
    ("https://jobs.synth-energy.com/en_US/externaljobs/JobDetail/Firmware-Intern/123456", "avature"),
    ("https://synth.avature.net/careers/JobDetail/1", "avature"),
    ("https://jobs.synth-grocers.com/en/sites/CX_2001/job/123456", "oracle"),
    ("https://jobs.synth-grocers.com/en-US/sites/CX/job/123456/apply/email", "oracle"),
    # Coarse or unconfirmed signatures stay custom.
    ("https://careers.synth-defense.com/job/firmware-intern/1427797200/", "other"),
    ("https://careers.synth-grocers.com/jobs/12345", "other"),
    ("http://careers.synth-defense.com/careers/job/56789", "other"),
    ("https://oraclecloud.com.example.invalid/hcmUI/CandidateExperience/en/sites/CX/job/1", "other"),
    ("https://synth.oraclecloud.com/en/sites/CX_1/job/1", "other"),
])
def test_detect_ats_recognizes_observed_platforms_behind_employer_domains(url, family):
    assert detect_ats(url) == family


@pytest.mark.parametrize("url, kind", [
    ("https://www.indeed.com/viewjob?jk=abc", "indeed_apply"),
    ("https://smartapply.indeed.com/beta/indeedapply/form", "indeed_apply"),
    ("https://www.tesla.com/careers/search/job/firmware-intern-123", "bot_wall"),
    ("https://lifeattiktok.com/search/7300000000", "employer_portal"),
    ("https://careers.tiktok.com/resume/7300000000/apply", "employer_portal"),
    ("https://jobs.bytedance.com/en/resume/7300000000/apply", "employer_portal"),
    ("https://www.amazon.jobs/en/jobs/2700000/firmware-intern", "employer_portal"),
    ("https://www.metacareers.com/jobs/1234567890", "employer_portal"),
    ("https://synth.paradox.ai/co/Synth/Job", "conversational"),
    ("https://www.trylitos.com/jobs/abc", "third_party_account"),
])
def test_excluded_flows_are_typed_handoffs(url, kind):
    note = unsupported_flow(url)
    assert note.startswith(f"unsupported_flow: {kind}: ")


@pytest.mark.parametrize("url", [
    "https://indeed.com.example.invalid/viewjob", "https://notindeed.com/x", "https://teslafans.example/x",
    "https://job-boards.greenhouse.io/synthbots/jobs/4321",
])
def test_exclusions_use_domain_boundaries(url):
    assert unsupported_flow(url) is None


def test_redirect_sources_and_clean_urls():
    assert redirect_source("https://zapply.jobs/r/abc") == "zapply"
    assert redirect_source("https://synth.recruitics.com/c/1") == "recruitics"
    assert redirect_source("https://click.appcast.io/t/abc") == "appcast"
    assert redirect_source("https://trylitos.com/jobs/1") == "trylitos"
    assert redirect_source("https://job-boards.greenhouse.io/x/jobs/1") is None
    assert clean_url("https://Apply.Synth-Coffee.com/careers/job/56789?utm_source=appcast_indeedo&hl=en"
                     "&domain=synth-coffee.com&_ccid=17000&gh_jid=55#top") == (
        "https://apply.synth-coffee.com/careers/job/56789?hl=en&domain=synth-coffee.com&gh_jid=55")


@pytest.mark.parametrize("page, destination, expected", [
    ("https://careers.synth-grocers.com/jobs/12345", "https://careers-synth.icims.com/jobs/12345/login?utm_source=x",
     ("https://careers-synth.icims.com/jobs/12345/login", None)),
    # A header "Apply" to a board home names no posting.
    ("https://careers.synth-grocers.com/jobs/12345", "https://synth.wd5.myworkdayjobs.com/External", (None, None)),
    # Unrecognized destinations and already-recognized pages are not routed.
    ("https://careers.synth-grocers.com/jobs/12345", "https://careers.synth-grocers.com/apply/12345", (None, None)),
    ("https://synth.wd5.myworkdayjobs.com/External/job/X/Intern_R1", "https://careers-synth.icims.com/jobs/1/login",
     (None, None)),
])
def test_apply_links_route_only_to_a_recognized_posting(page, destination, expected):
    assert route_apply_link(page, destination) == expected


def test_apply_link_to_an_excluded_flow_is_a_handoff():
    destination, flow = route_apply_link("https://careers.synth.com/jobs/12345", "https://smartapply.indeed.com/x/12345")
    assert destination is None and flow.startswith("unsupported_flow: indeed_apply:")


# ------------------------------------------------------------ closed markers

@pytest.mark.parametrize("status", [404, 410])
def test_http_404_or_410_on_the_posting_is_closed(sites, status):
    fixture = sites({("careers.synth.com", "/jobs/12345"): (status, {}, "<h1>Gone</h1>")})
    check = resolve.liveness("https://careers.synth.com/jobs/12345", fixture.net)
    assert (check.status, check.reason) == ("closed", f"The posting page returned HTTP {status}")


@pytest.mark.parametrize("markup", [
    "<h1>This position is no longer accepting applications</h1>",
    "<div role='alert'><span>Sorry, this job is no longer available.</span></div>",
    "<p>The page you are looking for doesn't exist.</p>",
])
def test_primary_closed_notice_is_closed(sites, markup):
    fixture = sites({("careers.synth.com", "/jobs/12345"): (200, {}, _page(markup))})
    check = resolve.liveness("https://careers.synth.com/jobs/12345", fixture.net)
    assert check.status == "closed" and check.reason.startswith("The posting page says:")


def test_closed_wording_deep_in_a_long_live_page_is_not_closure(sites):
    body = "<h1>Firmware Engineering Intern</h1>" + "<p>Build robot firmware.</p>" * 200 + (
        "<footer><p>Similar roles: this job is no longer available in Ohio.</p></footer>")
    fixture = sites({("careers.synth.com", "/jobs/12345"): (200, {}, _page(body))})
    assert resolve.liveness("https://careers.synth.com/jobs/12345", fixture.net).status == "live"


def test_greenhouse_questions_endpoint_404_is_closed_and_200_is_live(sites):
    url = "https://job-boards.greenhouse.io/synthbots/jobs/4321"
    fixture = sites({(GH_API, GH_QUESTIONS): (404, {}, '{"status":404}')})
    check = resolve.liveness(url, fixture.net)
    assert (check.status, check.family) == ("closed", "greenhouse")
    assert check.reason == "Greenhouse ?questions=true returned HTTP 404"
    assert fixture.fetched() == [(GH_API, GH_QUESTIONS)]
    fixture = sites({(GH_API, GH_QUESTIONS): GREENHOUSE_OPEN})
    assert resolve.liveness(url, fixture.net).status == "live"


@pytest.mark.parametrize("cxs, shell, status", [
    ((403, {}, '{"errorCode":"S22","message":"permission denied"}'),
     '<meta property="og:title" content="">', "closed"),
    ((403, {}, '{"errorCode":"S22","message":"permission denied"}'),
     '<meta property="og:title" content="Firmware Engineering Intern">', "unknown"),
    ((403, {}, '{"errorCode":"S11"}'), '<meta property="og:title" content="">', "unknown"),
    ((404, {}, "{}"), "", "closed"),
    (WORKDAY_OPEN, "", "live"),
])
def test_workday_cxs_403_s22_needs_the_empty_og_title_shell(sites, cxs, shell, status):
    url = f"https://{WD_HOST}/en-US/External/job/Austin-TX/Firmware-Intern_R123/apply"
    fixture = sites({
        (WD_HOST, "/wday/cxs/synth/External/job/Austin-TX/Firmware-Intern_R123"): cxs,
        (WD_HOST, "/en-US/External/job/Austin-TX/Firmware-Intern_R123"): (200, {}, _page(shell)),
    })
    check = resolve.liveness(url, fixture.net)
    assert (check.status, check.family) == (status, "workday")
    if status == "closed" and cxs[0] == 403:
        assert "403 S22" in check.reason and "og:title" in check.reason


def test_robots_disallow_is_unknown_and_never_fetched(sites):
    fixture = sites({
        ("careers.synth.com", "/robots.txt"): (200, {}, "User-agent: *\nDisallow: /jobs/\n"),
        ("careers.synth.com", "/jobs/12345"): (404, {}, ""),
    })
    check = resolve.liveness("https://careers.synth.com/jobs/12345", fixture.net)
    assert check.status == "unknown" and "robots.txt disallows" in check.reason
    assert fixture.fetched() == []


def test_requests_to_one_host_are_spaced_one_second(sites, monkeypatch):
    waits = []
    monkeypatch.setattr(http_module.time, "sleep", waits.append)
    fixture = sites({("careers.synth.com", "/jobs/1"): (200, {}, _page("<h1>Open</h1>")),
                     ("careers.synth.com", "/jobs/2"): (200, {}, _page("<h1>Open</h1>"))},
                    interval=resolve.REQUEST_INTERVAL)
    resolve.liveness("https://careers.synth.com/jobs/1", fixture.net)
    resolve.liveness("https://careers.synth.com/jobs/2", fixture.net)
    # robots.txt, /jobs/1, /jobs/2 on one host: each later slot is one second after the last
    # (sleep is stubbed, so the reserved waits accumulate: about 1 s, then about 2 s).
    assert len(waits) == 2 and 0.9 < waits[0] <= 1.0 and 1.9 < waits[1] <= 2.0
    assert resolve.REQUEST_INTERVAL == 1.0


def test_open_net_paces_one_request_per_second_per_host(tmp_path):
    net = resolve.open_net(SimpleNamespace(cache_dir=tmp_path))
    try:
        assert net.http.min_interval == 1.0 and net.pages.max_redirects == resolve.MAX_REDIRECTS
    finally:
        net.close()


# ----------------------------------------------------------------- resolvers

def test_zapply_follows_http_redirects_to_the_clean_employer_posting(workspace, sites):
    job = _job(workspace, "https://zapply.jobs/r/abc123")
    fixture = sites({
        ("zapply.jobs", "/r/abc123"): redirect("https://zapply.jobs/go/abc123", 308),
        ("zapply.jobs", "/go/abc123"): redirect(
            "https://job-boards.greenhouse.io/synthbots/jobs/4321?gh_src=zapply&utm_source=github"),
        ("job-boards.greenhouse.io", "/synthbots/jobs/4321?gh_src=zapply&utm_source=github"): (
            200, {}, _page("<h1>Firmware Engineering Intern</h1>")),
        (GH_API, GH_QUESTIONS): GREENHOUSE_OPEN,
    })
    check = resolve.check_job(job, fixture.net)
    assert (check.status, check.source, check.family) == ("live", "zapply", "greenhouse")
    assert check.resolved_url == "https://job-boards.greenhouse.io/synthbots/jobs/4321"


def test_redirects_to_a_careers_home_page_store_nothing(workspace, sites):
    job = _job(workspace, "https://jobs.recruitics.com/c/home")
    fixture = sites({
        ("jobs.recruitics.com", "/c/home"): redirect("https://careers.synth.com/?utm_source=rc"),
        ("careers.synth.com", "/?utm_source=rc"): (200, {}, _page("<h1>Join Synthetic Robotics</h1>")),
        ("jobs.synth.com", "/listing/12345"): redirect("https://www.synth.com/careers"),
        ("www.synth.com", "/careers"): (200, {}, _page("<h1>Careers</h1>")),
    })
    check = resolve.check_job(job, fixture.net)
    assert (check.status, check.resolved_url) == ("unknown", None) and "names no posting" in check.reason
    check = resolve.liveness("https://jobs.synth.com/listing/12345", fixture.net)
    assert (check.status, check.resolved_url) == ("unknown", None)


def test_zapply_landing_on_its_listing_is_closed(workspace, sites):
    job = _job(workspace, "https://zapply.jobs/r/old")
    fixture = sites({
        ("zapply.jobs", "/r/old"): redirect("https://zapply.jobs/go/old", 308),
        ("zapply.jobs", "/go/old"): redirect("https://zapply.jobs/jobs/"),
        ("zapply.jobs", "/jobs/"): (200, {}, _page("<h1>All jobs</h1>")),
    })
    check = resolve.check_job(job, fixture.net)
    assert check.status == "closed" and "generic /jobs/ listing" in check.reason


def test_recruitics_to_an_employer_portal_is_a_handoff_with_the_resolved_url(workspace, sites):
    job = _job(workspace, "https://jobs.recruitics.com/c/meta1")
    portal = ("www.metacareers.com", "/jobs/1234567890/?utm_source=recruitics")
    fixture = sites({("jobs.recruitics.com", "/c/meta1"): redirect("https://" + "".join(portal)),
                     portal: (200, {}, _page("<h1>Firmware Engineering Intern</h1>"))})
    check = resolve.check_job(job, fixture.net)
    assert check.status == "handoff" and check.reason.startswith("unsupported_flow: employer_portal:")
    assert check.resolved_url == "https://www.metacareers.com/jobs/1234567890/"
    # The redirect's own landing page can still show the posting gone.
    fixture.routes[portal] = (200, {}, _page("<h2>This position is no longer available</h2>"))
    assert resolve.check_job(job, fixture.net).status == "closed"


def test_recruitics_to_a_jibe_front_routes_by_its_apply_link_to_icims(workspace, sites):
    job = _job(workspace, "https://jobs.recruitics.com/c/jibe1")
    posting = _page('<h1>Firmware Engineering Intern</h1><a class="btn" href='
                    '"https://careers-synth.icims.com/jobs/12345/login?mode=job&amp;iis=Recruitics">'
                    '<span>Apply Now</span></a><a href="/jobs">Apply</a>')
    fixture = sites({
        ("jobs.recruitics.com", "/c/jibe1"): redirect("https://careers.synth-grocers.com/jobs/12345?src=rc"),
        ("careers.synth-grocers.com", "/jobs/12345?src=rc"): (200, {}, posting),
    })
    check = resolve.check_job(job, fixture.net)
    # Two different "Apply" destinations disagree: not routed by the static page.
    assert check.status == "live" and check.resolved_url == "https://careers.synth-grocers.com/jobs/12345"
    fixture.routes[("careers.synth-grocers.com", "/jobs/12345?src=rc")] = (200, {}, posting.replace(
        '<a href="/jobs">Apply</a>', ""))
    check = resolve.check_job(job, fixture.net)
    assert (check.status, check.family) == ("live", "icims")
    assert check.resolved_url == "https://careers-synth.icims.com/jobs/12345/login?mode=job&iis=Recruitics"


APPCAST_PAGE = """<!doctype html><title>Redirecting...</title><script>
  function navigateTo(parent, self, url) { (parent || self).location = url; }
  navigateTo(window.parent, window, "{target}");
</script>Redirecting..."""


def test_appcast_javascript_redirect_resolves_to_the_clean_eightfold_posting(workspace, sites):
    job = _job(workspace, "https://click.appcast.io/t/abc")
    target = (r"https:\/\/apply.synth-coffee.com\/careers\/job\/56789?utm_source=appcast_indeedo"
              r"\u0026hl=en\u0026domain=synth-coffee.com\u0026_ccid=17000")
    fixture = sites({
        ("click.appcast.io", "/t/abc"): (200, {}, APPCAST_PAGE.replace("{target}", target)),
        ("apply.synth-coffee.com", "/careers/job/56789?hl=en&domain=synth-coffee.com"): (
            200, {}, _page("<h1>Firmware Engineering Intern</h1>")),
    })
    check = resolve.check_job(job, fixture.net)
    assert (check.status, check.source, check.family) == ("live", "appcast", "eightfold")
    assert check.resolved_url == "https://apply.synth-coffee.com/careers/job/56789?hl=en&domain=synth-coffee.com"


def test_appcast_malformed_target_is_a_broken_redirect_handoff(workspace, sites):
    job = _job(workspace, "https://click.appcast.io/t/bad")
    fixture = sites({("click.appcast.io", "/t/bad"): (200, {}, APPCAST_PAGE.replace("{target}", "http://n/abc"))})
    check = resolve.check_job(job, fixture.net)
    assert check.status == "handoff" and check.reason.startswith("unsupported_flow: broken_redirect:")
    assert fixture.fetched() == [("click.appcast.io", "/t/bad")]


def _litos(board: str) -> str:
    return _page(f'<img class="logo" src="/api/company-logo?board={board}&amp;size=64">'
                 '<a href="https://www.trylitos.com/start?job=1">Apply with Litos</a>')


def test_trylitos_searches_the_hinted_workday_board_by_exact_title(workspace, sites):
    job = _job(workspace, "https://www.trylitos.com/jobs/wd1")
    listing = {"total": 2, "jobPostings": [
        {"title": "Firmware Engineering Intern", "externalPath": "/job/Austin-TX/Firmware-Engineering-Intern_R123",
         "locationsText": "Austin, TX"},
        {"title": "Firmware Engineering Intern II", "externalPath": "/job/Austin-TX/Firmware-II_R124",
         "locationsText": "Austin, TX"},
    ]}
    fixture = sites({
        ("www.trylitos.com", "/jobs/wd1"): (200, {}, _litos(f"https%3A%2F%2F{WD_HOST}%2Fen-US%2FExternal")),
        (WD_HOST, "/wday/cxs/synth/External/jobs"): (200, {}, json.dumps(listing)),
        (WD_HOST, "/wday/cxs/synth/External/job/Austin-TX/Firmware-Engineering-Intern_R123"): WORKDAY_OPEN,
    })
    check = resolve.check_job(job, fixture.net)
    assert (check.status, check.source, check.family) == ("live", "trylitos", "workday")
    assert check.resolved_url == f"https://{WD_HOST}/External/job/Austin-TX/Firmware-Engineering-Intern_R123"
    (search,) = [request for request in fixture.requests if request[0] == "POST"]
    assert json.loads(search[3])["searchText"] == "Firmware Engineering Intern"
    # The search URL's robots.txt was read before the POST.
    hosts = [(method, host, target) for method, host, target, _ in fixture.requests if host == WD_HOST]
    assert hosts[0] == ("GET", WD_HOST, "/robots.txt")


def test_trylitos_greenhouse_board_breaks_title_ties_by_location(workspace, sites):
    job = _job(workspace, "https://www.trylitos.com/jobs/gh1", location="Denver, CO")
    board = {"jobs": [
        {"id": 4321, "title": "Firmware Engineering Intern", "location": {"name": "Denver, CO"}},
        {"id": 4322, "title": "Firmware Engineering Intern", "location": {"name": "Boston, MA"}},
    ]}
    fixture = sites({
        ("www.trylitos.com", "/jobs/gh1"): (200, {}, _litos("https%3A%2F%2Fjob-boards.greenhouse.io%2Fsynthbots")),
        (GH_API, "/v1/boards/synthbots/jobs"): (200, {}, json.dumps(board)),
        (GH_API, GH_QUESTIONS): GREENHOUSE_OPEN,
    })
    check = resolve.check_job(job, fixture.net)
    assert (check.status, check.resolved_url) == ("live", "https://job-boards.greenhouse.io/synthbots/jobs/4321")


def test_trylitos_without_a_unique_posting_is_a_third_party_account_handoff(workspace, sites):
    job = _job(workspace, "https://www.trylitos.com/jobs/gh2")
    board = {"jobs": [{"id": 9, "title": "Mechanical Intern", "location": {"name": "Austin, TX"}}]}
    fixture = sites({
        ("www.trylitos.com", "/jobs/gh2"): (200, {}, _litos("https%3A%2F%2Fjob-boards.greenhouse.io%2Fsynthbots")),
        (GH_API, "/v1/boards/synthbots/jobs"): (200, {}, json.dumps(board)),
    })
    check = resolve.check_job(job, fixture.net)
    assert check.status == "handoff" and check.reason.startswith("unsupported_flow: third_party_account:")
    assert "0 postings with exactly this title" in check.reason and check.resolved_url is None


def test_removed_trylitos_posting_is_closed(workspace, sites):
    job = _job(workspace, "https://www.trylitos.com/jobs/gone")
    fixture = sites({("www.trylitos.com", "/jobs/gone"): (404, {}, "")})
    assert resolve.check_job(job, fixture.net).status == "closed"


def test_indeed_is_an_unsupported_flow_without_any_request(workspace, sites):
    job = _job(workspace, "https://www.indeed.com/viewjob?jk=abc")
    fixture = sites({})
    check = resolve.check_job(job, fixture.net)
    assert check.status == "handoff" and check.reason.startswith("unsupported_flow: indeed_apply:")
    assert fixture.requests == []


def test_reserved_and_local_urls_cost_no_request(workspace, sites):
    fixture = sites({})
    for url in ("https://boards.example.invalid/jobs/1", "file:///tmp/form.html", "https://localhost/jobs/1"):
        assert resolve.check_job(_job(workspace, url, title=url), fixture.net).status == "unknown"
    assert fixture.requests == []


# ----------------------------------------------------------------- recording

def _routes_for_three():
    return {
        ("careers.synth.com", "/jobs/closed"): (404, {}, ""),
        ("zapply.jobs", "/r/live"): redirect("https://job-boards.greenhouse.io/synthbots/jobs/4321"),
        ("job-boards.greenhouse.io", "/synthbots/jobs/4321"): (200, {}, _page("<h1>Firmware Engineering Intern</h1>")),
        (GH_API, GH_QUESTIONS): GREENHOUSE_OPEN,
    }


def _state(conn):
    return [tuple(row) for row in conn.execute(
        "SELECT id, is_active, apply_url, verification_status, fill_state, fill_blocker FROM jobs ORDER BY id")]


def test_dry_run_reports_changes_and_writes_nothing(workspace, sites):
    jobs = [_job(workspace, "https://careers.synth.com/jobs/closed", title="Closed Intern"),
            _job(workspace, "https://zapply.jobs/r/live", title="Live Intern"),
            _job(workspace, "https://www.indeed.com/viewjob?jk=1", title="Indeed Intern")]
    fixture = sites(_routes_for_three())
    before = _state(workspace.conn)
    facts = workspace.conn.execute("SELECT COUNT(*) FROM source_facts").fetchone()[0]
    results = resolve.resolve_jobs(workspace.conn, workspace.settings, jobs, dry_run=True, net=fixture.net)
    assert [result.changes for result in results] == [("close",), ("apply_url",), ("handoff",)]
    assert _state(workspace.conn) == before
    assert workspace.conn.execute("SELECT COUNT(*) FROM source_facts").fetchone()[0] == facts


def test_resolve_closes_stores_the_employer_url_and_files_handoffs(workspace, sites):
    closed = _job(workspace, "https://careers.synth.com/jobs/closed", title="Closed Intern")
    live = _job(workspace, "https://zapply.jobs/r/live", title="Live Intern")
    indeed = _job(workspace, "https://www.indeed.com/viewjob?jk=1", title="Indeed Intern")
    fixture = sites(_routes_for_three())
    resolve.resolve_jobs(workspace.conn, workspace.settings, [closed, live, indeed], dry_run=False, net=fixture.net)

    closed = jobs_core.get_job(workspace.conn, closed.id)
    assert (closed.is_active, closed.verification_status) == (False, "closed")
    assert closed.verification_reason == "The posting page returned HTTP 404"
    assert closed.id not in {job.id for job in jobs_core.list_jobs(workspace.conn, jobs_core.JobFilter())}
    live = jobs_core.get_job(workspace.conn, live.id)
    assert live.apply_url == "https://job-boards.greenhouse.io/synthbots/jobs/4321" and live.is_active
    assert detect_ats(live.apply_url or live.url) == "greenhouse"
    fact = workspace.conn.execute("SELECT source, source_url, accepted FROM source_facts "
                                  "WHERE job_id = ? AND field = 'apply_url' ORDER BY id DESC", (live.id,)).fetchone()
    assert tuple(fact) == ("resolve", "https://zapply.jobs/r/live", 1)
    indeed = jobs_core.get_job(workspace.conn, indeed.id)
    assert indeed.fill_state == "blocked" and indeed.fill_blocker.startswith("unsupported_flow: indeed_apply:")
    # A second pass changes nothing.
    again = resolve.resolve_jobs(workspace.conn, workspace.settings,
                                 [jobs_core.get_job(workspace.conn, job_id) for job_id in (live.id, indeed.id)],
                                 dry_run=False, net=fixture.net)
    assert [result.changes for result in again] == [(), ()]


def test_resolve_never_replaces_a_prepared_tab(workspace, sites):
    from kitty.core.apply.state import record_attempt

    job = _job(workspace, "https://www.indeed.com/viewjob?jk=2")
    record_attempt(workspace.conn, job.id, "ready", context_id="ctx-1", url=job.url)
    fixture = sites({})
    (result,) = resolve.resolve_jobs(workspace.conn, workspace.settings, [jobs_core.get_job(workspace.conn, job.id)],
                                     dry_run=False, net=fixture.net)
    assert result.changes == ()
    assert jobs_core.get_job(workspace.conn, job.id).fill_state == "ready"


# ------------------------------------------------------ before each attempt

def test_fill_jobs_checks_liveness_first_and_closed_jobs_never_open_a_browser(workspace, sites, monkeypatch):
    from kitty.core.apply import filler

    job = _job(workspace, "https://careers.synth.com/jobs/closed")
    fixture = sites({("careers.synth.com", "/jobs/closed"): (200, {}, _page(
        "<h2>This job has been filled</h2>"))})
    monkeypatch.setattr(resolve, "open_net", lambda settings: fixture.net)
    monkeypatch.setattr(filler, "ensure_firefox", lambda *a: pytest.fail("a closed posting must not open Firefox"))
    (report,) = filler.fill_jobs(workspace.settings, workspace.config, [job.id])
    assert report.status == "blocked" and report.note.startswith("closed:") and report.cause == "unavailable"
    assert not jobs_core.get_job(workspace.conn, job.id).is_active
    row = workspace.conn.execute("SELECT stage, outcome, cause FROM apply_attempts").fetchone()
    assert tuple(row) == ("live-check", "closed", "closed")


def test_excluded_flow_is_a_recorded_handoff_attempt_without_a_browser(workspace, sites, monkeypatch):
    from kitty.core.apply import filler

    job = _job(workspace, "https://www.tesla.com/careers/search/job/firmware-intern-123")
    monkeypatch.setattr(resolve, "open_net", lambda settings: sites({}).net)
    monkeypatch.setattr(filler, "ensure_firefox", lambda *a: pytest.fail("an excluded flow must not open Firefox"))
    (report,) = filler.fill_jobs(workspace.settings, workspace.config, [job.id])
    assert report.status == "blocked" and report.note.startswith("unsupported_flow: bot_wall:")
    assert report.cause == "human_boundary"
    row = workspace.conn.execute("SELECT stage, outcome, cause FROM apply_attempts").fetchone()
    assert tuple(row) == ("live-check", "unsupported", "unsupported_flow")


def test_stored_handoff_for_the_same_url_is_sticky_until_the_url_changes(workspace):
    from kitty.core.apply.filler import preparation_blocker
    from kitty.core.apply.state import record_attempt

    job = _job(workspace, "https://click.appcast.io/t/bad")
    note = "unsupported_flow: broken_redirect: appcast's redirect target is malformed (n)."
    record_attempt(workspace.conn, job.id, "blocked", blocker=note, url=job.url)
    assert preparation_blocker(jobs_core.get_job(workspace.conn, job.id)) == note
    workspace.conn.execute("UPDATE jobs SET apply_url = ? WHERE id = ?",
                           ("https://job-boards.greenhouse.io/synthbots/jobs/4321", job.id))
    assert preparation_blocker(jobs_core.get_job(workspace.conn, job.id)) is None


def test_reviewed_plans_are_checked_but_their_url_is_never_rewritten(workspace, sites):
    job = _job(workspace, "https://zapply.jobs/r/live")
    fixture = sites(_routes_for_three())
    (result,) = resolve.before_attempt(workspace.conn, workspace.settings, [job.id], write_url=False, net=fixture.net)
    assert result.check.resolved_url and result.changes == ()
    assert jobs_core.get_job(workspace.conn, job.id).apply_url is None


# ----------------------------------------------------------------------- CLI

def test_cli_resolve_dry_run_reports_without_writing(workspace, sites, monkeypatch):
    from typer.testing import CliRunner

    from kitty import cli

    _job(workspace, "https://careers.synth.com/jobs/closed", title="Closed Intern")
    _job(workspace, "https://zapply.jobs/r/live", title="Live Intern")
    # Each run opens (and closes) its own fetchers.
    monkeypatch.setattr(resolve, "open_net", lambda settings: sites(_routes_for_three()).net)
    monkeypatch.setattr(cli, "_open", lambda: SimpleNamespace(
        settings=workspace.settings, config=workspace.config, conn=workspace.conn, close=lambda: None))
    before = _state(workspace.conn)
    shown = CliRunner().invoke(cli.app, ["apply", "resolve", "--dry-run"])
    assert shown.exit_code == 0, shown.output
    text = " ".join(shown.output.split())
    assert "Dry run: nothing is written." in text
    assert "would close" in text
    assert "would set apply URL: https://job-boards.greenhouse.io/synthbots/jobs/4321" in text
    assert "2 job(s) would change" in text
    assert _state(workspace.conn) == before

    as_json = CliRunner().invoke(cli.app, ["apply", "resolve", "--dry-run", "--json"])
    assert as_json.exit_code == 0, as_json.output
    found = json.loads(as_json.output)
    assert found["dry_run"] is True and {tuple(r["changes"]) for r in found["results"]} == {("close",), ("apply_url",)}
    assert _state(workspace.conn) == before
    assert CliRunner().invoke(cli.app, ["apply", "--dry-run"]).exit_code == 1
    assert CliRunner().invoke(cli.app, ["apply", "resolve", "--headless"]).exit_code == 1

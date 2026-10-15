"""Stage 5 finder: caps and quotas, sticky rejections, approval into the watchlist,
honest adapter statuses, and parsers against privacy-safe synthetic responses."""

import json
import shutil
from datetime import date

import pytest

from kitty.config import ConfigError, WatchlistCompany, WatchlistFile, load_config
from kitty.core import companies as companies_core
from kitty.core import finder as finder_core
from kitty.core.discovery.hackernews import ITEM_URL, SEARCH_URL
from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.finder.ats_detect import detect_ats
from kitty.core.finder.base import CandidateBatch, FinderContext, RawCandidate, SourceText
from kitty.core.finder.conferences import sponsor_names
from kitty.core.finder.github import GithubAdapter, OrgRepos, collect_orgs, org_candidate
from kitty.core.finder.hn import HackerNewsCompaniesAdapter
from kitty.core.finder.sbir import CSV_URL, RECENT_YEARS, SbirAdapter, parse_awards
from kitty.core.finder.yc import parse_hiring
from conftest import REPO_ROOT
from fakes import FIXTURES, FakeHttp, fixture

GREENHOUSE_JOBS = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


@pytest.fixture
def config(workspace):
    """The workspace config plus the committed skills.yaml.example as skills.yaml."""
    shutil.copy(REPO_ROOT / "config" / "skills.yaml.example", workspace.settings.config_dir / "skills.yaml")
    loaded = load_config(workspace.settings.config_dir)
    assert loaded.skills.areas, "skills.yaml.example must list areas"
    return loaded


def _firm(name, area, source="fake"):
    """A candidate whose text names the area's first two keywords (enough to be proposed)."""
    assert len(area.keywords) >= 2
    text = f"{name} works on {area.keywords[0]} and {area.keywords[1]}."
    return RawCandidate(name=name, texts=[SourceText(source, "description", text)],
                        evidence=[f"{source}: test"])


def _run(workspace, config, make_firms, *, cap=None, routes=None):
    """run_finder with one fake adapter returning fresh `make_firms()` and fake HTTP."""

    class FakeAdapter:
        name = "fake"

        def __init__(self, ctx):
            pass

        def fetch(self):
            return CandidateBatch(make_firms(), "ok")

    return finder_core.run_finder(workspace.settings, config, cap=cap,
                                  http_factory=lambda name: FakeHttp(routes or {}),
                                  adapters={"fake": FakeAdapter})


def _many_per_area(config, extra=2):
    """quota + `extra` matching firms for every live area."""
    return [_firm(f"{area.name} firm {i}", area)
            for area in config.skills.areas if area.per_run_quota > 0
            for i in range(area.per_run_quota + extra)]


# ------------------------------------------------------------- caps and quotas


def test_area_quota_and_hard_cap_are_never_exceeded(workspace, config):
    quotas = {a.name: a.per_run_quota for a in config.skills.areas}

    small = _run(workspace, config, lambda: _many_per_area(config), cap=4)
    assert len(small.proposed) == 4
    assert all(count <= quotas[area] for area, count in small.proposed_by_area.items())

    full = _run(workspace, config, lambda: _many_per_area(config))
    assert len(full.proposed) == min(finder_core.DEFAULT_CAP, sum(quotas.values()))
    assert all(count <= quotas[area] for area, count in full.proposed_by_area.items())
    assert full.skipped["over the run cap"] + full.skipped["over its area's per_run_quota"] > 0

    stored = workspace.conn.execute("SELECT COUNT(*) FROM firm_candidates").fetchone()[0]
    assert stored == len(small.proposed) + len(full.proposed)
    run = workspace.conn.execute("SELECT kind, stats_json FROM runs WHERE id = ?", (full.run_id,)).fetchone()
    assert run["kind"] == "finder"
    assert json.loads(run["stats_json"])["proposed"] == len(full.proposed)


def test_one_keyword_alone_is_not_enough(workspace, config):
    area = config.skills.areas[0]
    weak = RawCandidate("Weak Match", [SourceText("fake", "description", f"we like {area.keywords[0]}")])
    result = _run(workspace, config, lambda: [weak])
    assert result.proposed == []
    assert result.skipped == {"only one keyword matched": 1}


# ------------------------------------------------------------ skipping firms


def test_rejected_firm_is_never_proposed_again(workspace, config):
    area = config.skills.areas[0]
    first = _run(workspace, config, lambda: [_firm("Gamma Silicon", area)])
    [candidate] = first.proposed
    finder_core.reject(workspace.conn, candidate.id)

    again = _run(workspace, config, lambda: [_firm("Gamma Silicon, Inc.", area)])
    assert again.proposed == []
    assert again.skipped["already known (watchlist or proposed before)"] == 1
    assert [c.display_name for c in finder_core.list_candidates(workspace.conn, "rejected")] == ["Gamma Silicon"]
    with pytest.raises(ValueError):
        finder_core.approve(workspace.conn, workspace.settings, config, candidate.id)


def test_watchlist_firms_are_skipped(workspace, config):
    area = config.skills.areas[0]
    in_yaml = "Example Watchlisted"
    object.__setattr__(config, "watchlist", WatchlistFile(companies=[
        WatchlistCompany(name=in_yaml, ats="greenhouse", slug="example-watchlisted"),
    ]))
    companies_core.upsert_company(workspace.conn, "Only In The Database", from_watchlist=True)

    result = _run(workspace, config, lambda: [_firm(in_yaml, area), _firm("Only In The Database", area)])
    assert result.proposed == []
    assert result.skipped["already known (watchlist or proposed before)"] == 2


# ------------------------------------------------------------------ approving


@pytest.mark.parametrize("has_board", [True, False])
@pytest.mark.parametrize("initial", [
    "# My watchlist\ncompanies: [] # approved firms\n",
    "# My watchlist\ncompanies: # approved firms\n"
    "  - name: Existing Example\n    ats: greenhouse\n    slug: existing-example\n",
])
def test_approve_appends_a_valid_watchlist_entry_and_creates_the_company(workspace, config, has_board, initial):
    area = next(a for a in config.skills.areas if a.per_run_quota > 0)
    routes = {GREENHOUSE_JOBS.format(slug="examplesatellites"): fixture("greenhouse_board.json")} if has_board else {}
    [candidate] = _run(workspace, config, lambda: [_firm("Example Satellites", area)], routes=routes).proposed
    assert (candidate.ats_kind == "greenhouse") is has_board

    path = workspace.settings.config_dir / "watchlist.yaml"
    path.write_text(initial, encoding="utf-8")
    finder_core.approve(workspace.conn, workspace.settings, config, candidate.id)

    assert "# My watchlist\n" in path.read_text(encoding="utf-8")
    assert "# approved firms\n" in path.read_text(encoding="utf-8")
    reloaded = load_config(workspace.settings.config_dir)
    [entry] = [c for c in reloaded.watchlist.companies if c.name == "Example Satellites"]
    if "Existing Example" in initial:
        assert [c.name for c in reloaded.watchlist.companies] == ["Existing Example", "Example Satellites"]
    assert entry.ats == ("greenhouse" if has_board else "none")
    assert entry.slug == ("examplesatellites" if has_board else "")
    assert set(entry.tracks) <= set(config.track_names)

    company = companies_core.find_company(workspace.conn, "Example Satellites")
    assert company is not None and company.from_watchlist
    assert company.ats_kind == ("greenhouse" if has_board else "other")
    assert finder_core.list_candidates(workspace.conn, "approved")[0].id == candidate.id
    assert finder_core.decision_counts(workspace.conn) == {"pending": 0, "approved": 1, "rejected": 0}


def test_a_broken_watchlist_is_left_exactly_as_it_was(workspace, config):
    area = config.skills.areas[0]
    [candidate] = _run(workspace, config, lambda: [_firm("Delta Devices", area)]).proposed
    path = workspace.settings.config_dir / "watchlist.yaml"
    path.write_text("companies: [\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        finder_core.approve(workspace.conn, workspace.settings, config, candidate.id)
    assert path.read_text(encoding="utf-8") == "companies: [\n"
    assert finder_core.list_candidates(workspace.conn)[0].id == candidate.id     # still pending


# --------------------------------------------------------- honest statuses


class _LimitedHttp(FakeHttp):
    def __init__(self, error):
        super().__init__({})
        self.error = error

    def _answer(self, key):
        raise self.error


@pytest.mark.parametrize("error", [RateLimited("429 from api.github.com"),
                                   HttpError("HTTP 403 from https://api.github.com/search")])
def test_a_rate_limited_adapter_is_reported_not_zero_found(workspace, config, error):
    result = finder_core.run_finder(workspace.settings, config,
                                    http_factory=lambda name: _LimitedHttp(error),
                                    adapters={"github": GithubAdapter})
    report = result.adapters["github"]
    assert report.status == "rate_limited"
    assert report.error
    stats = json.loads(workspace.conn.execute(
        "SELECT stats_json FROM runs WHERE id = ?", (result.run_id,)).fetchone()[0])
    assert stats["adapters"]["github"]["status"] == "rate_limited"
    assert stats["adapters"]["github"]["stats"]["queries"] == 0      # it stopped at the first 403/429


def test_a_crashing_adapter_does_not_stop_the_run(workspace, config):
    class Broken:
        name = "broken"

        def __init__(self, ctx):
            pass

        def fetch(self):
            raise KeyError("surprise")

    area = config.skills.areas[0]

    class Good:
        name = "good"

        def __init__(self, ctx):
            pass

        def fetch(self):
            return CandidateBatch([_firm("Epsilon Labs Corp", area)], "ok")

    result = finder_core.run_finder(workspace.settings, config, http_factory=lambda name: FakeHttp({}),
                                    adapters={"broken": Broken, "good": Good})
    assert result.adapters["broken"].status == "error"
    assert [c.display_name for c in result.proposed] == ["Epsilon Labs Corp"]


# ----------------------------------------------------------------- parsers


def test_yc_keeps_us_or_remote_companies_only():
    payload = fixture("finder/yc_hiring.json")
    candidates, stats = parse_hiring(payload)
    names = {c.name for c in candidates}
    assert stats["not_us_or_inactive"] == 1
    assert "Example Tax" not in names                    # Portugal / Europe only
    simulation = next(c for c in candidates if c.name == "Example Simulation")
    assert simulation.website and simulation.slug_hints == ["example-simulation"]
    assert {t.label for t in simulation.texts} == {"description", "tags"}


def test_hn_counts_every_company_post_not_only_intern_posts(workspace):
    search, thread = fixture("hn_search.json"), fixture("hn_thread.json")
    thread["children"].append({"id": 2004, "text": "Unstructured hiring announcement"})
    routes = {SEARCH_URL: search, ITEM_URL.format(id=thread["id"]): thread}
    ctx = FinderContext(workspace.config, lambda name: FakeHttp(routes))
    batch = HackerNewsCompaniesAdapter(ctx).fetch()
    names = [c.name for c in batch.candidates]
    assert batch.status == "ok"
    assert "Example Europe" in names                     # a full-time-only post
    assert batch.stats["deleted_or_unparseable"] == 1


def test_sponsor_names_come_from_logo_alt_text():
    page = (FIXTURES / "finder" / "hotchips_sponsors.html").read_text(encoding="utf-8")
    assert sponsor_names(page, "Hot Chips") == [
        "Volt AI", "Google Cloud", "Etched", "XSight", "Normal Computing", "Mojo Vision",
    ]


def test_github_keeps_organizations_that_look_like_employers():
    orgs: dict[str, OrgRepos] = {}
    collect_orgs(fixture("finder/github_search.json"), orgs)
    assert set(orgs) == {"example-radio", "example-rtl"}   # the user-owned repo is dropped

    radio = org_candidate(fixture("finder/github_org_ettus.json"), orgs["example-radio"])
    assert radio is not None and radio.name == "Example Radio"
    assert radio.website == "https://example.com"
    assert org_candidate(fixture("finder/github_org_pulp.json"), orgs["example-rtl"]) is None


# ------------------------------------------------------------- ATS detection


def test_a_board_under_another_company_name_is_not_a_match():
    http = FakeHttp({GREENHOUSE_JOBS.format(slug="ettusresearch"): fixture("greenhouse_board.json")})
    detection = detect_ats(http, RawCandidate("Ettus Research", website="https://ettus.com"))
    assert detection.hit is None


def test_smartrecruiters_empty_answer_for_any_slug_is_not_a_board():
    empty = {"offset": 0, "limit": 10, "totalFound": 0, "content": []}
    routes = {f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=10": empty
              for slug in ("zetafirm", "zeta")}
    detection = detect_ats(FakeHttp(routes), RawCandidate("Zeta Firm", website="https://zeta.io"))
    assert detection.hit is None


# -------------------------------------------------------------------- SBIR


def _sbir_text():
    return (FIXTURES / "finder" / "sbir_awards.csv").open(encoding="utf-8", newline="")


def test_sbir_merges_each_firms_recent_awards():
    with _sbir_text() as text:
        candidates, stats = parse_awards(text, today=date(2026, 10, 1))
    by_name = {c.name: c for c in candidates}
    flexnode = by_name["Flexnode Inc"]                     # three 2024 award rows, one firm
    assert "LIQUID-COOLED MICRO DATA CENTER" in flexnode.texts[0].text
    assert "3 award(s) 2024" in flexnode.evidence[0]
    assert by_name["Agile Data Decisions, Inc."].website == "http://agiledd.ai"
    assert stats["rows"] == 9 and stats["firms"] == len(candidates)


def test_sbir_ignores_awards_older_than_the_window():
    with _sbir_text() as text:
        candidates, stats = parse_awards(text, today=date(2024 + RECENT_YEARS + 1, 1, 1))
    assert candidates == [] and stats["recent_rows"] == 0


def test_sbir_reports_a_changed_csv_as_an_error_not_zero_firms(config):
    http = FakeHttp({CSV_URL: b"Firm,Title\nA,B\n"})
    batch = SbirAdapter(FinderContext(config, lambda name: http)).fetch()
    assert batch.status == "error" and "missing columns" in batch.error


def test_sbir_rate_limit_is_reported(config):
    http = FakeHttp({CSV_URL: RateLimited("429 from data.www.sbir.gov")})
    batch = SbirAdapter(FinderContext(config, lambda name: http)).fetch()
    assert batch.status == "rate_limited"

"""vansh2027, speedyapply2027, derec4 and Workable against synthetic responses."""

from datetime import timedelta

from kitty.config import WatchlistCompany, WatchlistFile
from kitty.core.apply.urls import detect_ats, form_url
from kitty.core.discovery.ats import WORKABLE_WIDGET, WorkableAdapter
from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.pipeline import CompanyResolver
from kitty.core.discovery.repos import (
    DEREC4_FILES, SPEEDYAPPLY_READMES, DereC4Adapter, SpeedyApplyAdapter, parse_derec4, parse_vansh,
)
from kitty.dates import now
from kitty.models import Job
from fakes import FIXTURES, FakeHttp, fixture


def _context(workspace, companies, routes):
    """A DiscoverContext whose watchlist is `companies` and whose HTTP is fake."""
    http = FakeHttp(routes)
    config = workspace.config
    object.__setattr__(config, "watchlist", WatchlistFile(companies=companies))
    return DiscoverContext(config, lambda name: http, {}), http


def _readme(name: str) -> bytes:
    return (FIXTURES / "speedyapply" / name).read_bytes()


# --------------------------------------------------------------------- vansh


def test_vansh_keeps_recent_listings_and_marks_closed_ones():
    listings = fixture("vansh_listings.json")      # active, inactive, a 2025 leftover
    recent = int((now() - timedelta(days=30)).timestamp())
    listings[0]["date_posted"] = listings[1]["date_posted"] = recent

    jobs, stats = parse_vansh(listings)

    assert stats["older_than_180_days"] == 1
    assert [job.company for job in jobs] == [row["company_name"] for row in listings[:2]]
    assert [job.closed for job in jobs] == [False, True]
    assert jobs[0].source == "vansh2027" and jobs[0].raw["season"] == "Summer"
    assert jobs[0].season is None                 # "Summer" alone names no year
    assert stats["unknown_fields"] == []


# --------------------------------------------------------------- speedyapply


def test_speedyapply_reads_every_table_of_both_readmes(workspace):
    ctx, _ = _context(workspace, [], {
        SPEEDYAPPLY_READMES["swe"]: _readme("swe_readme.md"),
        SPEEDYAPPLY_READMES["ai"]: _readme("ai_readme.md"),
    })
    result = SpeedyApplyAdapter(ctx).fetch(None)

    assert result.status == "ok"
    assert result.stats["readmes"]["swe"]["tables"] == {"swe/faang": 2, "swe/quant": 2, "swe/other": 4}
    assert result.stats["readmes"]["ai"]["tables"] == {"ai/faang": 2, "ai/other": 2}
    assert result.stats["duplicates"] == 1        # Example Credit's posting is on both lists
    assert len(result.jobs) == 11

    citadel = next(job for job in result.jobs if job.company == "Example Capital")
    assert citadel.title == "Software Engineer - Intern - US"
    assert citadel.location == "Houston, TX +2"
    assert citadel.url == "https://capital.example.invalid/careers/details/software-engineer-intern-us/"
    assert citadel.raw["salary"] == "$125/hr" and citadel.raw["section"] == "quant"
    assert citadel.posted_at.startswith((now().date() - timedelta(days=80)).isoformat())

    harvey = next(job for job in result.jobs if job.company == "Example Legal")
    assert harvey.season == "Summer 2027" and harvey.raw["salary"] is None
    # Two Example Visual postings differ only in gh_jid: both survive as separate jobs.
    pinterest = {job.source_job_id for job in result.jobs if job.company == "Example Visual"}
    assert len(pinterest) == 2


def test_speedyapply_reports_a_missing_readme_and_a_changed_layout(workspace):
    ctx, _ = _context(workspace, [], {SPEEDYAPPLY_READMES["swe"]: _readme("swe_readme.md")})
    partial = SpeedyApplyAdapter(ctx).fetch(None)       # the AI README answers 404
    assert partial.status == "partial" and "ai:" in partial.error
    assert len(partial.jobs) == 8

    ctx, _ = _context(workspace, [], {
        SPEEDYAPPLY_READMES["swe"]: b"# moved to a website\n",
        SPEEDYAPPLY_READMES["ai"]: b"# moved to a website\n",
    })
    broken = SpeedyApplyAdapter(ctx).fetch(None)
    assert broken.status == "error" and "no internship tables" in broken.error


# -------------------------------------------------------------------- derec4

_DEREC4_README = """# Internships and Newgrad Job Listings
| Badge | Count |
| --- | --- |
| not a | listing |

| Company | Role | Location | Age |
| --- | --- | --- | --- |
| Example Grid | [Power Systems Intern 🛂](https://grid.example.invalid/jobs/1?utm_source=x) | Austin, TX | 0d |
| Example Rail | [Signals Intern 🇺🇸](https://rail.example.invalid/jobs/2) | Fort Worth, TX | 2mo |
| Example Vault | [Quant Intern 🔒](https://vault.example.invalid/jobs/3) | Chicago, IL | 1w |
| Example Broken | Role without a link | Remote | 3d |
""".encode()
_DEREC4_NEWGRAD = """| Company | Role | Location | Age |
| --- | --- | --- | --- |
| Example Fab | [Process Technician, Nights (Wednesday, Thursday,...](https://fab.example.invalid/jobs/9) | Wilton, CT | 0d |
| Example Grid | [Power Systems Intern](https://grid.example.invalid/jobs/1) | Austin, TX | 0d |
""".encode()
_DEREC4_UNFILTERED = """| Company | Role | Location | Age |
| --- | --- | --- | --- |
| Example Fab | [Process Technician, Nights (Wednesday, Thursday, Friday) 🎓](https://fab.example.invalid/jobs/9) | Wilton, CT | 1d |
| Example Lab | [Research Intern \\| Summer 2027](https://lab.example.invalid/jobs/4) | Remote | 5d |
""".encode()


def test_derec4_reads_every_list_once_with_eligibility_markers(workspace):
    ctx, _ = _context(workspace, [], {
        DEREC4_FILES["internships"]: _DEREC4_README,
        DEREC4_FILES["newgrad"]: _DEREC4_NEWGRAD,
        DEREC4_FILES["unfiltered"]: _DEREC4_UNFILTERED,
    })
    result = DereC4Adapter(ctx).fetch(None)

    assert result.status == "partial" and "internships: 1 malformed listing rows" in result.error
    assert result.stats["files"]["internships"] == {"rows": 3, "tables": 1, "bad_rows": 1}  # badge table ignored
    assert result.stats["duplicates"] == 2
    by_company = {job.company: job for job in result.jobs}
    assert set(by_company) == {"Example Grid", "Example Rail", "Example Vault", "Example Fab", "Example Lab"}

    grid = by_company["Example Grid"]
    assert grid.title == "Power Systems Intern" and grid.raw["list"] == "internships"  # first copy wins
    assert grid.raw["flags"] == ["no_sponsorship"] and "sponsorship" in grid.raw["eligibility_note"]
    rail = by_company["Example Rail"]
    assert "citizenship" in rail.raw["eligibility_note"]
    assert rail.posted_at.startswith((now().date() - timedelta(days=60)).isoformat())
    assert by_company["Example Vault"].closed and by_company["Example Vault"].raw["eligibility_note"] is None
    # The README's cut title is replaced by the unfiltered list's full spelling.
    assert by_company["Example Fab"].title == "Process Technician, Nights (Wednesday, Thursday, Friday)"
    assert by_company["Example Lab"].title == "Research Intern | Summer 2027"
    assert by_company["Example Lab"].season == "Summer 2027"


def test_derec4_preserves_parentheses_and_html_entities_in_posting_urls():
    # Public SuccessFactors listings use literal parentheses inside the URL path.
    url = "https://jobs.example.invalid/job/Intern-(Summer-2027)/123/?ats=successfactors&ref=Simplify"
    markdown = f"""| Company | Role | Location | Age |
| --- | --- | --- | --- |
| Example Travel | [Guest Travel Services Intern]({url.replace('&', '&amp;')}) | Miramar, FL | 0d |
"""
    jobs, stats = parse_derec4(markdown, "internships")

    assert stats == {"rows": 1, "tables": 1, "bad_rows": 0}
    assert jobs[0].url == url
    assert jobs[0].source_job_id == "https://jobs.example.invalid/job/Intern-(Summer-2027)/123?ats=successfactors"


def test_derec4_reports_a_missing_file_and_a_changed_layout(workspace):
    ctx, _ = _context(workspace, [], {DEREC4_FILES["internships"]: _DEREC4_README})
    partial = DereC4Adapter(ctx).fetch(None)          # the other two answer 404
    assert partial.status == "partial" and "newgrad:" in partial.error and "unfiltered:" in partial.error
    assert len(partial.jobs) == 3

    ctx, _ = _context(workspace, [], {url: b"# moved to a website\n" for url in DEREC4_FILES.values()})
    broken = DereC4Adapter(ctx).fetch(None)
    assert broken.status == "error" and "no listing tables" in broken.error


def test_derec4_malformed_tables_are_not_a_complete_snapshot(workspace):
    malformed = b"""| Company | Role | Location | Age |
| --- | --- | --- | --- |
| Example Broken | No posting link | Remote | 0d |
"""
    ctx, _ = _context(workspace, [], {url: malformed for url in DEREC4_FILES.values()})
    result = DereC4Adapter(ctx).fetch(None)
    assert result.status == "error" and result.jobs == []
    assert all(f"{name}: 1 malformed listing rows" in result.error for name in DEREC4_FILES)


# ------------------------------------------------------------------ workable


def test_workable_keeps_internships_with_board_links(workspace):
    company = WatchlistCompany(name="Example Aviation", ats="workable", slug="example",
                               tracks=["embedded"])
    board = fixture("workable_board.json")
    ctx, _ = _context(workspace, [company], {WORKABLE_WIDGET.format(slug="example"): board})
    result = WorkableAdapter(ctx).fetch(None)

    assert result.status == "ok"
    assert result.stats["postings_seen"] == len(board["jobs"])
    assert [job.title for job in result.jobs] == ["Intern - Software Engineering (Summer 2027)"]
    job = result.jobs[0]
    assert job.url == board["jobs"][1]["url"]
    assert job.apply_url == board["jobs"][1]["application_url"]
    assert CompanyResolver.board_of(job.url) == ("workable", "example")
    assert job.location == "Laramie, Wyoming, United States" and job.country == "United States"
    assert job.description and "<" not in job.description
    assert job.season == "Summer 2027"


def test_workable_links_resolve_to_the_board_and_its_form():
    assert CompanyResolver.board_of("https://apply.workable.com/sample-company/j/SAMPLE123/apply") == (
        "workable", "sample-company")
    assert CompanyResolver.board_of("https://apply.workable.com/j/SAMPLE123") is None
    page = "https://apply.workable.com/sample-company/j/SAMPLE123/"
    assert detect_ats(page) == "workable"
    job = Job(id=1, company_id=1, company_name="Sample Company", title="Intern", url=page)
    assert form_url(job, "workable") == "https://apply.workable.com/sample-company/j/SAMPLE123/apply"


# ------------------------------------------------------- gh_jid in job URLs


def test_gh_jid_postings_stay_apart_and_old_stripped_urls_still_match(workspace):
    """canonical_url used to drop gh_jid; jobs stored then must neither duplicate nor collapse."""
    from kitty.core import jobs as jobs_core
    from kitty.core.discovery.base import AdapterResult, NormalizedJob
    from kitty.core.discovery.pipeline import run_discover

    track = next(t for t in workspace.config.tracks.tracks if "speedyapply2027" in t.sources)
    source, title = track.sources[0], track.search_terms[0].title() + " Intern"
    akuna = "https://capital-two.example.invalid/careers/job/900011/?gh_jid=900011"
    stored, _ = jobs_core.upsert_job(workspace.conn, company="Example Capital Two", title=title, url=akuna,
                                     location="Chicago, IL", source=source, source_job_id="a1")
    workspace.conn.execute("UPDATE jobs SET url = 'https://capital-two.example.invalid/careers/job/900011' "
                           "WHERE id = ?", (stored.id,))                # stored before the fix

    def job(src, sid, url, company, location):
        return NormalizedJob(source=src, source_job_id=sid, company=company, title=title,
                             url=url, location=location)

    class Fixed:
        name = "speedyapply2027"

        def __init__(self, ctx):
            pass

        def fetch(self, since):
            return AdapterResult([
                # Same Akuna posting, other location text: matched through the old URL form.
                job("speedyapply2027", akuna, akuna, "Example Capital Two", "Chicago, IL +1"),
                # Two Example Visual postings on one careers page, told apart by gh_jid only.
                job("speedyapply2027", "p1", "https://careers.visual.example.invalid/jobs/?gh_jid=1",
                    "Example Visual", "San Francisco, CA"),
                job("speedyapply2027", "p2", "https://careers.visual.example.invalid/jobs/?gh_jid=2",
                    "Example Visual", "Remote"),
            ], "ok")

    result = run_discover(workspace.settings, workspace.config, adapters={"speedyapply2027": Fixed},
                          only=["speedyapply2027"], http_factory=lambda name: None)

    assert result.sources["speedyapply2027"].merged == 1
    assert result.sources["speedyapply2027"].new == 2
    sources = workspace.conn.execute(
        "SELECT source FROM job_sources WHERE job_id = ? ORDER BY source", (stored.id,)).fetchall()
    assert [row[0] for row in sources] == sorted({source, "speedyapply2027"})


def test_postings_merged_only_because_gh_jid_was_dropped_are_split_apart(workspace):
    from kitty.core import jobs as jobs_core
    from kitty.core.discovery.base import AdapterResult, NormalizedJob
    from kitty.core.discovery.pipeline import run_discover

    track = next(t for t in workspace.config.tracks.tracks if "greenhouse" in t.sources)
    term = track.search_terms[0].title()
    page = "https://www.jumptrading.com/hr/job"
    owner, other = f"{term} Intern", f"Campus {term} Intern"
    stored, _ = jobs_core.upsert_job(workspace.conn, company="Jump Trading", title=owner, url=page,
                                     location="Chicago, IL", source="greenhouse", source_job_id="jump:1",
                                     source_url=page)
    jobs_core.add_job_source(workspace.conn, stored.id, "greenhouse", source_job_id="jump:2",
                             source_url=page)              # merged by the stripped URL

    def posting(sid, title):
        return NormalizedJob(source="greenhouse", source_job_id=f"jump:{sid}", company="Jump Trading",
                             title=title, url=f"{page}?gh_jid={sid}", location="Chicago, IL")

    class Board:
        name = "greenhouse"

        def __init__(self, ctx):
            pass

        def fetch(self, since):
            return AdapterResult([posting(2, other), posting(1, owner)], "ok")

    def run():
        return run_discover(workspace.settings, workspace.config, adapters={"greenhouse": Board},
                            only=["greenhouse"], http_factory=lambda name: None).sources["greenhouse"]

    first = run()
    assert (first.new, first.seen_again) == (1, 1)
    rows = workspace.conn.execute("SELECT title, url FROM jobs ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [(owner, f"{page}?gh_jid=1"), (other, f"{page}?gh_jid=2")]

    again = run()                                           # settled: nothing moves any more
    assert (again.new, again.seen_again) == (0, 2)


def test_other_spellings_of_a_stored_posting_url_merge_into_it(workspace):
    """Lists write the same posting's URL differently; each must join the stored job."""
    from kitty.core import jobs as jobs_core
    from kitty.core.discovery.base import AdapterResult, NormalizedJob
    from kitty.core.discovery.pipeline import run_discover

    track = next(t for t in workspace.config.tracks.tracks if "speedyapply2027" in t.sources)
    title = track.search_terms[0].title() + " Intern"
    pairs = [   # (stored by another source, the same posting as a list writes it)
        ("https://adobe.wd5.myworkdayjobs.com/external_experienced/job/San-Jose/Intern_R1",
         "https://adobe.wd5.myworkdayjobs.com/en-US/External_Experienced/job/San-Jose/Intern_R1"),
        ("https://apply.workable.com/example/j/EXAMPLE012/apply",
         "https://apply.workable.com/example/j/EXAMPLE012/"),
        ("https://boards.greenhouse.io/example/jobs/900013",
         "https://job-boards.greenhouse.io/example/jobs/900013?gh_jid=900013"),
    ]
    for index, (stored_url, _) in enumerate(pairs):
        jobs_core.upsert_job(workspace.conn, company=f"Co{index}", title=title, url=stored_url,
                             location="Austin, TX", source=track.sources[0], source_job_id=str(index))

    class Lists:
        name = "speedyapply2027"

        def __init__(self, ctx):
            pass

        def fetch(self, since):
            return AdapterResult([
                NormalizedJob(source="speedyapply2027", source_job_id=url, company=f"Co{index}",
                              title=title, url=url, location="Austin, TX +2")
                for index, (_, url) in enumerate(pairs)
            ], "ok")

    tally = run_discover(workspace.settings, workspace.config, adapters={"speedyapply2027": Lists},
                         only=["speedyapply2027"], http_factory=lambda name: None).sources["speedyapply2027"]
    assert (tally.merged, tally.new) == (3, 0)


def test_workable_repeated_shortcode_preserves_us_location():
    from kitty.config import EligibilityRules
    from kitty.core.discovery.ats import parse_workable
    from kitty.core.discovery.eligibility import evaluate

    company = WatchlistCompany(name="Mixed Board", ats="workable", slug="mixed")
    common = {"shortcode": "one", "title": "Trading Intern", "description": "Open to undergraduates."}
    rows = [common | {"city": "London", "country": "United Kingdom"},
            common | {"city": "New York", "country": "United States"}]
    [job] = parse_workable(company, {"jobs": rows})
    verdict = evaluate(title=job.title, description=job.description, location=job.location,
                       country=job.country, degrees=None, rules=EligibilityRules())
    assert verdict.eligibility == "eligible"
    assert "London" in job.location and "New York" in job.location
    [foreign] = parse_workable(company, {"jobs": rows[:1]})
    verdict = evaluate(title=foreign.title, description=foreign.description, location=foreign.location,
                       country=foreign.country, degrees=None, rules=EligibilityRules())
    assert verdict.eligibility == "ineligible"

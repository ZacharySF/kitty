"""Adapters for the community internship repos.

SimplifyJobs/Summer2027-Internships: the machine-readable listings.json on the
dev branch, not the README. Its `source` field is the contributor's username,
not the ATS. `degrees` feeds the eligibility filter; `active=false` closes a job.

vanshb03/Summer2027-Internships: a listings.json built by the same scripts, but
an older shape: one `season` word ("Summer", "Fall") instead of `terms`, and no
category or degrees. The previous cycle is archived separately, yet a few
listings from 2025 are still marked active, so only recent postings are kept.

northwesternfintech/2027QuantInternships (NUFT): one YAML file per firm under
data/, shaped {name, website, locations, notes, roles: [{role_type, links:
[{url, label?}]}]}. No dates, degrees or active flag; a firm with nothing open
has `roles: []`. The whole repo is fetched as one tarball (one request).

speedyapply/2027-SWE-College-Jobs and 2027-AI-College-Jobs: no data file (the
tables are generated from a private database), so the README's USA internship
tables are parsed. Each table sits between <!-- TABLE_..._START/END --> markers;
rows are | company | position | location | [salary] | posting | age |, with
HTML in the company and posting cells. A posting has no id, so its URL is it.
"""

from __future__ import annotations

import html
import io
import logging
import re
import tarfile
from datetime import datetime, timedelta

import yaml

from applyai.core.discovery.base import AdapterResult, NormalizedJob
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.discovery.text import season_from, unix_to_iso, us_location
from applyai.dates import now, to_iso
from applyai.normalize import canonical_url

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ Simplify

SIMPLIFY_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/"
    "dev/.github/scripts/listings.json"
)
SIMPLIFY_FIELDS = {
    "id", "company_name", "title", "locations", "url", "date_posted", "date_updated",
    "terms", "active", "is_visible", "source", "company_url", "category", "sponsorship",
    "degrees",
}
#: Category spellings seen in the file, mapped onto one name each.
SIMPLIFY_CATEGORIES = {
    "Software Engineering": "Software",
    "Data Science, AI & Machine Learning": "AI/ML/Data",
    "Hardware Engineering": "Hardware",
    "Quantitative Finance": "Quant",
    "Product Management": "Product",
}
#: Listings with no season ("N/A") are kept only if posted this recently.
UNDATED_TERM_MAX_AGE = timedelta(days=180)


def _simplify_relevant(item: dict) -> bool:
    """True for listings aimed at the 2027-28 cycle (or undated and recent)."""
    terms = item.get("terms") or []
    if any(re.search(r"20(27|28)", term) for term in terms):
        return True
    if terms and all(term == "N/A" for term in terms):
        posted = item.get("date_posted")
        return bool(posted) and now().timestamp() - posted < UNDATED_TERM_MAX_AGE.total_seconds()
    return False


def parse_simplify(listings: list[dict]) -> tuple[list[NormalizedJob], dict]:
    """listings.json to NormalizedJobs, plus stats (including unknown fields)."""
    jobs: list[NormalizedJob] = []
    unknown: set[str] = set()
    categories: dict[str, int] = {}
    skipped_old = 0
    for item in listings:
        unknown |= set(item) - SIMPLIFY_FIELDS
        if item.get("is_visible") is False:
            continue
        if not _simplify_relevant(item):
            skipped_old += 1
            continue
        category = SIMPLIFY_CATEGORIES.get(item.get("category") or "", item.get("category"))
        categories[category or "none"] = categories.get(category or "none", 0) + 1
        terms = item.get("terms") or []
        jobs.append(NormalizedJob(
            source="simplify2027",
            source_job_id=item["id"],
            company=item["company_name"],
            title=item["title"],
            url=item["url"],
            location="; ".join(item.get("locations") or []) or None,
            posted_at=unix_to_iso(item.get("date_posted")),
            season=season_from(" ".join(terms), item.get("title")),
            degrees=item.get("degrees") or None,
            closed=not item.get("active", True),
            raw={
                "id": item["id"], "category": category, "terms": terms,
                "degrees": item.get("degrees"), "sponsorship": item.get("sponsorship"),
                "contributor": item.get("source"),        # a username, not the ATS
                "date_updated": item.get("date_updated"),
            },
        ))
    if unknown:
        log.warning("simplify2027: unknown fields in listings.json: %s", sorted(unknown))
    stats = {
        "listings": len(listings), "kept": len(jobs), "not_2027_cycle": skipped_old,
        "closed": sum(job.closed for job in jobs), "categories": categories,
        "unknown_fields": sorted(unknown),
    }
    return jobs, stats


class SimplifyAdapter:
    name = "simplify2027"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        http = self.ctx.http_for(self.name)
        try:
            listings = http.get_json(SIMPLIFY_URL)
        except RateLimited as exc:
            return AdapterResult([], "rate_limited", str(exc))
        except (HttpError, ValueError) as exc:
            return AdapterResult([], "error", f"{type(exc).__name__}: {exc}")
        jobs, stats = parse_simplify(listings)
        for job in jobs:
            job.observed_at = getattr(http, "retrieved_at", None)
        return AdapterResult(jobs, "ok", stats=stats)


# --------------------------------------------------------------------- Vansh

VANSH_URL = (
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/"
    "dev/.github/scripts/listings.json"
)
VANSH_FIELDS = {
    "id", "company_name", "title", "locations", "url", "date_posted", "date_updated",
    "season", "active", "is_visible", "source", "company_url", "sponsorship",
}
#: The file states no cycle year, so a posting older than this is taken to be
#: a leftover from the previous cycle (2025 listings still marked active).
VANSH_MAX_AGE = timedelta(days=180)


def parse_vansh(listings: list[dict]) -> tuple[list[NormalizedJob], dict]:
    """vanshb03 listings.json to NormalizedJobs, plus stats (including unknown fields)."""
    jobs: list[NormalizedJob] = []
    unknown: set[str] = set()
    skipped_old = 0
    oldest = now().timestamp() - VANSH_MAX_AGE.total_seconds()
    for item in listings:
        unknown |= set(item) - VANSH_FIELDS
        if item.get("is_visible") is False:
            continue
        if (item.get("date_posted") or 0) < oldest:
            skipped_old += 1
            continue
        jobs.append(NormalizedJob(
            source="vansh2027",
            source_job_id=item["id"],
            company=item["company_name"],
            title=item["title"],
            url=item["url"],
            location="; ".join(item.get("locations") or []) or None,
            posted_at=unix_to_iso(item.get("date_posted")),
            season=season_from(item.get("title")),   # the file's season word has no year
            closed=not item.get("active", True),
            raw={
                "id": item["id"], "season": item.get("season"),
                "sponsorship": item.get("sponsorship"),
                "contributor": item.get("source"),        # a username, not the ATS
                "date_updated": item.get("date_updated"),
            },
        ))
    if unknown:
        log.warning("vansh2027: unknown fields in listings.json: %s", sorted(unknown))
    stats = {
        "listings": len(listings), "kept": len(jobs), "older_than_180_days": skipped_old,
        "closed": sum(job.closed for job in jobs), "unknown_fields": sorted(unknown),
    }
    return jobs, stats


class VanshAdapter:
    name = "vansh2027"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        http = self.ctx.http_for(self.name)
        try:
            listings = http.get_json(VANSH_URL)
        except RateLimited as exc:
            return AdapterResult([], "rate_limited", str(exc))
        except (HttpError, ValueError) as exc:
            return AdapterResult([], "error", f"{type(exc).__name__}: {exc}")
        jobs, stats = parse_vansh(listings)
        for job in jobs:
            job.observed_at = getattr(http, "retrieved_at", None)
        return AdapterResult(jobs, "ok", stats=stats)



# ---------------------------------------------------------------------- NUFT

NUFT_TARBALL = (
    "https://codeload.github.com/northwesternfintech/2027QuantInternships/tar.gz/refs/heads/main"
)
NUFT_ROLE_TITLES = {
    "QT": "Quantitative Trader",
    "QR": "Quantitative Researcher",
    "QD": "Quantitative Developer",
    "SWE": "Software Engineer",
    "HW": "Hardware Engineer",
    "FPGA": "FPGA Engineer",
    "ML": "Machine Learning Researcher",
}
#: Phrases in a firm's notes that mean it effectively recruits at a few schools.
_SCHOOL_SELECTIVE = re.compile(
    r"school[- ]selective|target schools?|only (recruits?|hires?) (from|at)|"
    r"\b(MIT|CMU|Harvard|Stanford|Princeton|Caltech|Berkeley|UIUC|Ivy|Ivies)\b[^.]{0,20}\bonly\b",
    re.I,
)


def parse_nuft_firm(filename: str, firm: dict) -> list[NormalizedJob]:
    """One data/<firm>.yaml file to NormalizedJobs (one per role link)."""
    notes = (firm.get("notes") or "").strip()
    selective = notes if _SCHOOL_SELECTIVE.search(notes) else None
    jobs = []
    for role in firm.get("roles") or []:
        role_type = str(role.get("role_type") or "").strip()
        base_title = f"{NUFT_ROLE_TITLES.get(role_type, role_type)} Intern"
        for link in role.get("links") or []:
            label = (link.get("label") or "").strip()
            label_is_place = bool(label) and us_location(label) is not None
            jobs.append(NormalizedJob(
                source="nuft2027",
                source_job_id=f"{filename}:{link['url']}",
                company=firm["name"],
                title=f"{base_title} ({label})" if label else base_title,
                url=link["url"],
                location=label if label_is_place else firm.get("locations"),
                season="Summer 2027",
                school_selective_note=selective,
                raw={"firm": firm["name"], "role_type": role_type, "label": label,
                     "locations": firm.get("locations"), "notes": notes},
            ))
    return jobs


def parse_nuft_tarball(data: bytes) -> tuple[list[NormalizedJob], dict]:
    """Every data/*.yaml in the repo tarball to NormalizedJobs, plus stats."""
    jobs: list[NormalizedJob] = []
    firms = firms_open = 0
    bad_files: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = member.name.split("/")
            if len(parts) != 3 or parts[1] != "data" or not parts[2].endswith(".yaml"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                firm = yaml.safe_load(handle.read().decode("utf-8"))
                found = parse_nuft_firm(parts[2], firm)
            except (yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
                bad_files.append(f"{parts[2]}: {type(exc).__name__}")
                continue
            firms += 1
            firms_open += bool(found)
            jobs.extend(found)
    return jobs, {"firms": firms, "firms_with_roles": firms_open, "links": len(jobs), "bad_files": bad_files}


class NuftAdapter:
    name = "nuft2027"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        http = self.ctx.http_for(self.name)
        try:
            data = http.get_bytes(NUFT_TARBALL)
        except RateLimited as exc:
            return AdapterResult([], "rate_limited", str(exc))
        except HttpError as exc:
            return AdapterResult([], "error", str(exc))
        try:
            jobs, stats = parse_nuft_tarball(data)
        except tarfile.TarError as exc:
            return AdapterResult([], "error", f"could not read the repo tarball: {exc}")
        status = "partial" if stats["bad_files"] else "ok"
        error = f"unparseable: {', '.join(stats['bad_files'])}" if stats["bad_files"] else None
        for job in jobs:
            job.observed_at = getattr(http, "retrieved_at", None)
        return AdapterResult(jobs, status, error, stats=stats)


# ---------------------------------------------------------------- speedyapply

#: Each repo's README holds its USA internship tables (new-grad and
#: international lists live in other files and are not read).
SPEEDYAPPLY_READMES = {
    "swe": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "ai": "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
}
_SA_TABLE = re.compile(r"<!-- TABLE_(?:(\w+?)_)?START -->(.*?)<!-- TABLE_(?:\w+?_)?END -->", re.S)
_SA_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_SA_HREF = re.compile(r'href="([^"]+)"')
_SA_TAG = re.compile(r"<[^>]+>")
_SA_AGE = re.compile(r"^(\d+)\s*d$")
_SA_COLUMNS = ("company", "position", "location", "posting", "age")


def _sa_cells(line: str) -> list[str]:
    """One markdown table row to its raw cells (escaped pipes stay inside a cell)."""
    inner = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in _SA_CELL_SPLIT.split(inner)]


def _sa_text(cell: str) -> str:
    """A cell's visible text: tags dropped, entities and escaped pipes undone."""
    return html.unescape(_SA_TAG.sub("", cell)).replace("\\|", "|").strip()


def parse_speedyapply_readme(markdown: str, repo: str) -> tuple[list[NormalizedJob], dict]:
    """One speedyapply README's internship tables to NormalizedJobs, plus stats.

    A table whose header lacks a needed column is skipped and named in
    stats['bad_tables'], so a format change shows up instead of silently
    yielding nothing.
    """
    jobs: list[NormalizedJob] = []
    sections: dict[str, int] = {}
    bad_tables: list[str] = []
    bad_rows = 0
    today = now().date()                             # ages are whole days
    for match in _SA_TABLE.finditer(markdown):
        section = (match.group(1) or "other").lower()
        lines = [line for line in match.group(2).splitlines() if line.strip().startswith("|")]
        if not lines:
            continue
        header = [_sa_text(cell).lower() for cell in _sa_cells(lines[0])]
        if any(column not in header for column in _SA_COLUMNS):
            bad_tables.append(f"{repo}/{section}: header {header}")
            continue
        at = {column: header.index(column) for column in header}
        count = 0
        for line in lines[2:]:                       # after the header and |---| rows
            cells = _sa_cells(line)
            href = _SA_HREF.search(cells[at["posting"]]) if len(cells) == len(header) else None
            if href is None:
                bad_rows += 1
                continue
            url = html.unescape(href.group(1))
            title = _sa_text(cells[at["position"]])
            age = _SA_AGE.match(_sa_text(cells[at["age"]]))
            salary = _sa_text(cells[at["salary"]]) if "salary" in at else ""
            jobs.append(NormalizedJob(
                source="speedyapply2027",
                source_job_id=canonical_url(url),
                company=_sa_text(cells[at["company"]]),
                title=title,
                url=url,
                location=_sa_text(cells[at["location"]]) or None,
                posted_at=to_iso(today - timedelta(days=int(age.group(1)))) if age else None,
                season=season_from(title),
                raw={"repo": repo, "section": section, "salary": salary or None,
                     "age": _sa_text(cells[at["age"]])},
            ))
            count += 1
        sections[f"{repo}/{section}"] = count
    stats = {"rows": len(jobs), "tables": sections, "bad_tables": bad_tables, "bad_rows": bad_rows}
    return jobs, stats


class SpeedyApplyAdapter:
    name = "speedyapply2027"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        """Both READMEs; one failing makes the run partial (and closes nothing)."""
        http = self.ctx.http_for(self.name)
        jobs: list[NormalizedJob] = []
        seen: set[str] = set()
        stats: dict = {"readmes": {}, "duplicates": 0}
        problems: list[str] = []
        for repo, url in SPEEDYAPPLY_READMES.items():
            try:
                markdown = http.get_bytes(url).decode("utf-8")
            except RateLimited as exc:
                return AdapterResult([], "rate_limited", str(exc))
            except (HttpError, UnicodeDecodeError) as exc:
                problems.append(f"{repo}: {type(exc).__name__}: {exc}")
                continue
            found, readme_stats = parse_speedyapply_readme(markdown, repo)
            stats["readmes"][repo] = readme_stats
            if not readme_stats["tables"]:
                problems.append(f"{repo}: no internship tables found (README format changed?)")
            problems.extend(readme_stats["bad_tables"])
            for job in found:
                job.observed_at = getattr(http, "retrieved_at", None)
                if job.source_job_id in seen:      # listed in both repos
                    stats["duplicates"] += 1
                    continue
                seen.add(job.source_job_id)
                jobs.append(job)
        if not jobs and problems:
            return AdapterResult([], "error", "; ".join(problems), stats=stats)
        status = "partial" if problems else "ok"
        return AdapterResult(jobs, status, "; ".join(problems) or None, stats=stats)

"""Adapter for USAJOBS, the federal job board (https://developer.usajobs.gov).

GET https://data.usajobs.gov/api/search with three headers: Host, User-Agent
(my email, as USAJOBS asks) and Authorization-Key (USAJOBS_API_KEY from .env,
which Settings.load() puts into the environment). Without a key the adapter
reports an error without touching the network, so a missing key never looks
like "0 jobs".

What is searched comes from `options.queries` in sources.yaml: by default the
Students hiring path (Pathways internships and student trainee jobs, across
the US, which includes NASA Johnson Space Center in Houston) and titles
containing "intern". Each query is paged with ResultsPerPage up to `max_pages`.
Only student-path or internship-titled announcements are kept.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import urlencode

from applyai.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.discovery.text import html_to_text, is_internship_title, iso_to_utc, season_from

SEARCH_URL = "https://data.usajobs.gov/api/search"
HOST = "data.usajobs.gov"
DEFAULT_QUERIES: list[dict] = [{"HiringPath": "student"}, {"PositionTitle": "intern"}]
DEFAULT_RESULTS_PER_PAGE = 250       # the API allows up to 500
DEFAULT_MAX_PAGES = 4
#: An auth failure means every other query would fail the same way.
_REJECTED = re.compile(r"HTTP (401|403)\b")


def search_url(query: dict, page: int, per_page: int) -> str:
    """The search URL for one page of one query (sorted params, so it caches stably)."""
    params = {**query, "ResultsPerPage": per_page, "Page": page}
    return f"{SEARCH_URL}?{urlencode(sorted(params.items()))}"


def _hiring_paths(details: dict) -> list[str]:
    paths = details.get("HiringPath") or []
    return [str(p).lower() for p in (paths if isinstance(paths, list) else [paths])]


def _description(descriptor: dict, details: dict) -> str | None:
    """Summary, duties and qualifications as plain text, in the order a reader wants them."""
    parts = [
        details.get("JobSummary"),
        details.get("MajorDuties") if isinstance(details.get("MajorDuties"), str)
        else "\n".join(details.get("MajorDuties") or []),
        descriptor.get("QualificationSummary"),
        details.get("Education"),
    ]
    return html_to_text("\n\n".join(p for p in parts if p))


def parse_item(item: dict) -> NormalizedJob | None:
    """One SearchResultItem to a NormalizedJob, or None when it is not a student/intern job."""
    descriptor = item.get("MatchedObjectDescriptor") or {}
    details = (descriptor.get("UserArea") or {}).get("Details") or {}
    title = descriptor.get("PositionTitle") or ""
    student_path = "student" in _hiring_paths(details)
    if not (student_path or is_internship_title(title)):
        return None
    if not is_internship_title(title):
        title = f"{title} (student program)"      # the hiring path says so, the title does not
    locations = descriptor.get("PositionLocation") or []
    countries = {loc.get("CountryCode") for loc in locations if loc.get("CountryCode")}
    apply_uris = descriptor.get("ApplyURI") or []
    organization = descriptor.get("OrganizationName") or descriptor.get("DepartmentName") or "US Government"
    return NormalizedJob(
        source="usajobs",
        source_job_id=str(item.get("MatchedObjectId") or descriptor["PositionID"]),
        company=organization,
        title=title,
        url=descriptor["PositionURI"],
        location=descriptor.get("PositionLocationDisplay"),
        is_remote=True if "remote" in (descriptor.get("PositionLocationDisplay") or "").lower() else None,
        country=countries.pop() if len(countries) == 1 else None,
        apply_url=apply_uris[0] if apply_uris else None,
        description=_description(descriptor, details),
        posted_at=iso_to_utc(descriptor.get("PublicationStartDate")),
        season=season_from(title, details.get("JobSummary")),
        raw={
            "MatchedObjectId": item.get("MatchedObjectId"),
            "PositionID": descriptor.get("PositionID"),
            "DepartmentName": descriptor.get("DepartmentName"),
            "HiringPath": details.get("HiringPath"),
            "LowGrade": details.get("LowGrade"), "HighGrade": details.get("HighGrade"),
            "ApplicationCloseDate": descriptor.get("ApplicationCloseDate"),
        },
    )


def parse_search(payload: dict) -> tuple[list[NormalizedJob], int, int]:
    """One search page to (kept jobs, items on the page, total matching the query)."""
    result = payload.get("SearchResult") or {}
    items = result.get("SearchResultItems") or []
    jobs = [job for job in (parse_item(item) for item in items) if job is not None]
    return jobs, len(items), int(result.get("SearchResultCountAll") or 0)


def query_name(query: dict) -> str:
    """'HiringPath=student' - how a query is named on the Sources screen."""
    return "&".join(f"{key}={value}" for key, value in sorted(query.items()))


class UsaJobsAdapter:
    name = "usajobs"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        key = os.environ.get("USAJOBS_API_KEY", "").strip()
        if not key:
            return AdapterResult([], "error", "USAJOBS_API_KEY is not set in .env")
        email = os.environ.get("USAJOBS_EMAIL", "").strip() or self.ctx.config.profile.email
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        queries = options.get("queries") or DEFAULT_QUERIES
        per_page = int(options.get("results_per_page", DEFAULT_RESULTS_PER_PAGE))
        max_pages = int(options.get("max_pages", DEFAULT_MAX_PAGES))

        http = self.ctx.http_for(self.name)
        # USAJOBS identifies callers by these headers; this client is used only for USAJOBS.
        http.client.headers.update({"Host": HOST, "User-Agent": email, "Authorization-Key": key})

        jobs: dict[str, NormalizedJob] = {}
        outcomes: list[BoardOutcome] = []
        seen = 0
        for query in queries:
            if any(o.status == "rate_limited" for o in outcomes):
                outcomes.append(BoardOutcome(query_name(query), "rate_limited", error="skipped after a 429"))
                continue
            outcome, found, items = self._run_query(http, query, per_page, max_pages)
            if outcome.status == "error" and _REJECTED.search(outcome.error or ""):
                return AdapterResult([], "error", f"USAJOBS rejected the API key or email: {outcome.error}")
            outcomes.append(outcome)
            seen += items
            for job in found:
                jobs.setdefault(job.source_job_id, job)

        # No `boards` on the result: every query is read in full, so when all of
        # them answered cleanly an announcement missing from the results really is
        # gone (close_missing counts the miss); any problem makes the run partial.
        status, error = summarize_boards(outcomes)
        if status in ("error", "rate_limited") and jobs:
            status = "partial"                   # some pages answered before the trouble
        return AdapterResult(
            list(jobs.values()), status, error,
            stats={"queries": {o.board: o.error or o.count for o in outcomes},
                   "announcements_seen": seen, "kept": len(jobs),
                   "requests": http.requests_made, "cache_hits": http.cache_hits},
        )

    def _run_query(self, http, query: dict, per_page: int, max_pages: int):
        """Page through one query. Returns (outcome, kept jobs, announcements seen)."""
        name = query_name(query)
        found: list[NormalizedJob] = []
        seen = total = 0
        for page in range(1, max_pages + 1):
            try:
                payload = http.get_json(search_url(query, page, per_page))
            except RateLimited as exc:
                return BoardOutcome(name, "rate_limited", error=str(exc)), found, seen
            except (HttpError, ValueError) as exc:
                return BoardOutcome(name, "error", error=f"{type(exc).__name__}: {exc}"), found, seen
            kept, items, total = parse_search(payload)
            for job in kept:
                job.observed_at = getattr(http, "retrieved_at", None)
            found.extend(kept)
            seen += items
            if items < per_page or seen >= total:
                return BoardOutcome(name, "ok", len(found)), found, seen
        return BoardOutcome(
            name, "partial", len(found),
            error=f"read {seen} of {total} announcements; raise max_pages to read the rest",
        ), found, seen

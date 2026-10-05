"""Adapters for the watchlist job boards: Greenhouse, Lever, Ashby, SmartRecruiters, Workable.

Each polls every watchlist company on that ATS, keeps only internship-looking
titles, and reports per-board health. One broken board never stops the others,
and a 429 stops that source for the rest of the run instead of being retried.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable

from applyai.config import WatchlistCompany
from applyai.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, PoliteHttp, RateLimited
from applyai.core.discovery.text import (
    html_to_text, is_internship_title, iso_to_utc, season_from, unix_to_iso,
)

#: Parses one board: (company, http) -> (all postings on the board, the intern ones).
BoardParser = Callable[[WatchlistCompany, PoliteHttp], tuple[int, list[NormalizedJob]]]


def run_boards(
    source: str, ctx: DiscoverContext, parse_board: BoardParser
) -> AdapterResult:
    """Poll each watchlist board for `source` and collect jobs and health."""
    http = ctx.http_for(source)
    jobs: list[NormalizedJob] = []
    outcomes: list[BoardOutcome] = []
    board_counts: dict[str, int] = {}
    rate_limited = False

    for company in ctx.boards(source):
        if rate_limited:
            outcomes.append(BoardOutcome(company.slug, "rate_limited", error="skipped after a 429"))
            continue
        try:
            total, found = parse_board(company, http)
        except RateLimited as exc:
            rate_limited = True
            outcomes.append(BoardOutcome(company.slug, "rate_limited", error=str(exc)))
            continue
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            outcomes.append(BoardOutcome(company.slug, "error", error=f"{type(exc).__name__}: {exc}"))
            continue
        board_counts[company.slug] = total
        if ctx.dropped_to_zero(source, company.slug, total):
            outcomes.append(BoardOutcome(
                company.slug, "partial", 0, error="returned 0 postings; it had postings last run",
            ))
        else:
            outcomes.append(BoardOutcome(company.slug, "ok", len(found)))
        for job in found:
            if job.observed_at is None:
                job.observed_at = getattr(http, "retrieved_at", None)
        jobs.extend(found)

    status, error = summarize_boards(outcomes)
    return AdapterResult(
        jobs=jobs, status=status, error=error, boards=outcomes,
        stats={
            "boards": len(outcomes), "postings_seen": sum(board_counts.values()),
            "intern_postings": len(jobs), "board_counts": board_counts,
            "requests": http.requests_made, "cache_hits": http.cache_hits,
        },
    )


def _interns(jobs: Iterable[NormalizedJob]) -> list[NormalizedJob]:
    return [job for job in jobs if is_internship_title(job.title)]


# ---------------------------------------------------------------- greenhouse


def parse_greenhouse(company: WatchlistCompany, payload: dict) -> list[NormalizedJob]:
    """Greenhouse board JSON (with content=true) to NormalizedJobs."""
    jobs = []
    for item in payload.get("jobs", []):
        title = item.get("title") or ""
        jobs.append(NormalizedJob(
            source="greenhouse",
            source_job_id=f"{company.slug}:{item['id']}",
            company=company.name,
            title=title,
            url=item["absolute_url"],
            location=(item.get("location") or {}).get("name"),
            description=html_to_text(item.get("content")),
            posted_at=iso_to_utc(item.get("first_published") or item.get("updated_at")),
            season=season_from(title),
            raw={k: item.get(k) for k in ("id", "title", "location", "absolute_url", "updated_at", "first_published")},
        ))
    return jobs


class GreenhouseAdapter:
    name = "greenhouse"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        def board(company: WatchlistCompany, http: PoliteHttp):
            payload = http.get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{company.slug}/jobs?content=true"
            )
            everything = parse_greenhouse(company, payload)
            return len(everything), _interns(everything)

        return run_boards(self.name, self.ctx, board)


# --------------------------------------------------------------------- lever


def parse_lever(company: WatchlistCompany, payload: list) -> list[NormalizedJob]:
    """Lever postings JSON to NormalizedJobs."""
    jobs = []
    for item in payload:
        categories = item.get("categories") or {}
        title = item.get("text") or ""
        description = "\n\n".join(
            part for part in (
                item.get("descriptionPlain"),
                *[html_to_text(section.get("content")) or "" for section in item.get("lists", [])],
                item.get("additionalPlain"),
            ) if part
        )
        workplace = (item.get("workplaceType") or "").lower()
        jobs.append(NormalizedJob(
            source="lever",
            source_job_id=f"{company.slug}:{item['id']}",
            company=company.name,
            title=title,
            url=item["hostedUrl"],
            apply_url=item.get("applyUrl"),
            location=categories.get("location"),
            is_remote=True if workplace == "remote" else (False if workplace else None),
            country=item.get("country"),
            description=description or None,
            posted_at=unix_to_iso(item.get("createdAt"), millis=True),
            season=season_from(title),
            raw={k: item.get(k) for k in ("id", "text", "categories", "hostedUrl", "createdAt", "workplaceType")},
        ))
    return jobs


class LeverAdapter:
    name = "lever"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        def board(company: WatchlistCompany, http: PoliteHttp):
            payload = http.get_json(f"https://api.lever.co/v0/postings/{company.slug}?mode=json")
            everything = parse_lever(company, payload)
            interns = [
                job for job in everything
                if is_internship_title(job.title)
                or (job.raw or {}).get("categories", {}).get("commitment", "").lower().startswith("intern")
            ]
            return len(everything), interns

        return run_boards(self.name, self.ctx, board)


# --------------------------------------------------------------------- ashby


def parse_ashby(company: WatchlistCompany, payload: dict) -> list[NormalizedJob]:
    """Ashby job-board JSON to NormalizedJobs. Uses workplaceType, not isRemote."""
    jobs = []
    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue
        title = item.get("title") or ""
        locations = [item.get("location")] + [
            loc.get("location") for loc in item.get("secondaryLocations") or []
        ]
        address = ((item.get("address") or {}).get("postalAddress") or {})
        workplace = (item.get("workplaceType") or "").lower()
        jobs.append(NormalizedJob(
            source="ashby",
            source_job_id=f"{company.slug}:{item['id']}",
            company=company.name,
            title=title,
            url=item["jobUrl"],
            apply_url=item.get("applyUrl"),
            location="; ".join(loc for loc in locations if loc) or None,
            is_remote=True if workplace == "remote" else (False if workplace else None),
            country=address.get("addressCountry"),
            description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")),
            posted_at=iso_to_utc(item.get("publishedAt")),
            season=season_from(title),
            raw={k: item.get(k) for k in ("id", "title", "location", "employmentType", "workplaceType", "jobUrl", "publishedAt")},
        ))
    return jobs


class AshbyAdapter:
    name = "ashby"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        def board(company: WatchlistCompany, http: PoliteHttp):
            payload = http.get_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{company.slug}?includeCompensation=true"
            )
            everything = parse_ashby(company, payload)
            interns = [
                job for job in everything
                if is_internship_title(job.title) or (job.raw or {}).get("employmentType") == "Intern"
            ]
            return len(everything), interns

        return run_boards(self.name, self.ctx, board)


# ----------------------------------------------------------- smartrecruiters

SR_PAGE = 100


def parse_smartrecruiters_listing(company: WatchlistCompany, item: dict) -> NormalizedJob:
    """One SmartRecruiters list entry to a NormalizedJob (description filled later)."""
    location = item.get("location") or {}
    return NormalizedJob(
        source="smartrecruiters",
        source_job_id=f"{company.slug}:{item['id']}",
        company=company.name,
        title=item.get("name") or "",
        url=f"https://jobs.smartrecruiters.com/{company.slug}/{item['id']}",
        location=location.get("fullLocation") or ", ".join(
            part for part in (location.get("city"), location.get("region"), location.get("country")) if part
        ) or None,
        is_remote=location.get("remote"),
        country=location.get("country"),
        posted_at=iso_to_utc(item.get("releasedDate")),
        season=season_from(item.get("name")),
        raw={k: item.get(k) for k in ("id", "name", "releasedDate", "location", "experienceLevel", "typeOfEmployment")},
    )


def smartrecruiters_description(detail: dict) -> str | None:
    """Join the job-ad sections of a SmartRecruiters posting into plain text."""
    sections = ((detail.get("jobAd") or {}).get("sections") or {})
    parts = [html_to_text((sections.get(name) or {}).get("text")) for name in (
        "jobDescription", "qualifications", "additionalInformation",
    )]
    return "\n\n".join(p for p in parts if p) or None


class SmartRecruitersAdapter:
    name = "smartrecruiters"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        def board(company: WatchlistCompany, http: PoliteHttp):
            base = f"https://api.smartrecruiters.com/v1/companies/{company.slug}/postings"
            items: list[dict] = []
            offset = 0
            while True:
                page = http.get_json(f"{base}?limit={SR_PAGE}&offset={offset}")
                items.extend(page.get("content", []))
                offset += SR_PAGE
                if offset >= page.get("totalFound", 0) or not page.get("content"):
                    break
            interns = []
            for item in items:
                level = ((item.get("experienceLevel") or {}).get("id") or "")
                if not (is_internship_title(item.get("name") or "") or level == "internship"):
                    continue
                job = parse_smartrecruiters_listing(company, item)
                detail = http.get_json(f"{base}/{item['id']}")
                job.observed_at = getattr(http, "retrieved_at", None)
                job.description = smartrecruiters_description(detail)
                job.url = detail.get("postingUrl") or job.url
                job.apply_url = detail.get("applyUrl")
                interns.append(job)
            return len(items), interns

        return run_boards(self.name, self.ctx, board)


# ------------------------------------------------------------------ workable
# The public widget endpoint lists every published job with its description in
# one request. apply.workable.com/robots.txt disallows nothing (checked 2026-10-03).

WORKABLE_WIDGET = "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"


def _workable_location(places: list[dict]) -> str | None:
    """'City, Region, Country' per listed location, joined with '; '."""
    joined = [
        ", ".join(part for part in (place.get("city"), place.get("region") or place.get("state"),
                                    place.get("country")) if part)
        for place in places
    ]
    return "; ".join(dict.fromkeys(place for place in joined if place)) or None


def parse_workable(company: WatchlistCompany, payload: dict) -> list[NormalizedJob]:
    """Workable widget JSON (details=true) to NormalizedJobs.

    Job links are built under the account (apply.workable.com/<slug>/j/<code>/)
    rather than the API's short /j/<code> link, so the URL names the board.
    """
    # The widget repeats a shortcode for every location. Store one requisition
    # with their union: a London-first row must not hide its New York opening.
    grouped: dict[str, tuple[dict, list[dict]]] = {}
    for item in payload.get("jobs", []):
        _, places = grouped.setdefault(item["shortcode"], (item, []))
        places.extend(item.get("locations") or [item])
    jobs = []
    for code, (item, places) in grouped.items():
        title = item.get("title") or ""
        page = f"https://apply.workable.com/{company.slug}/j/{code}/"
        jobs.append(NormalizedJob(
            source="workable",
            source_job_id=f"{company.slug}:{code}",
            company=company.name,
            title=title,
            url=page,
            apply_url=f"{page}apply/",
            location=_workable_location(places),
            is_remote=item.get("telecommuting"),
            country="; ".join(dict.fromkeys(place["country"] for place in places if place.get("country"))) or None,
            description=html_to_text(item.get("description")),
            posted_at=iso_to_utc(f"{item['published_on']}T00:00:00Z") if item.get("published_on") else None,
            season=season_from(title),
            raw={**{k: item.get(k) for k in ("shortcode", "title", "employment_type", "experience",
                                           "department", "published_on")},
                 "locations": [{k: place.get(k) for k in ("city", "region", "state", "country")} for place in places]},
        ))
    return jobs


def _workable_intern(job: NormalizedJob) -> bool:
    """An intern title, or Workable's own employment type / experience says internship."""
    raw = job.raw or {}
    stated = f"{raw.get('employment_type') or ''} {raw.get('experience') or ''}".lower()
    return is_internship_title(job.title) or "intern" in stated


class WorkableAdapter:
    name = "workable"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        def board(company: WatchlistCompany, http: PoliteHttp):
            payload = http.get_json(WORKABLE_WIDGET.format(slug=company.slug))
            everything = parse_workable(company, payload)
            return len(everything), [job for job in everything if _workable_intern(job)]

        return run_boards(self.name, self.ctx, board)

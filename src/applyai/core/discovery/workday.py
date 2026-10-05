"""Workday adapter (fragile).

Workday's public CXS API has sharp edges, all handled here:
  * limit is at most 20 (larger values are HTTP 400);
  * only the first page carries `total`; later pages report 0;
  * it returns at most 2,000 results, then empty pages;
  * keyword searches are fuzzy, so non-intern pages cannot end pagination.
Search queries and the per-query result bound are configurable. Listings are
deduplicated across queries; only internship titles need detail requests.
Bounded keyword searches are never evidence that an absent posting closed.

Boards run a few at a time in threads; pacing is per host, so each tenant still
sees at least `min_interval` seconds between requests. A single 429 stops the
whole source for the rest of the run.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from applyai.config import WatchlistCompany
from applyai.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, PoliteHttp, RateLimited
from applyai.core.discovery.text import html_to_text, is_internship_title, season_from
from applyai.dates import now, to_iso

PAGE_SIZE = 20                  # Workday rejects anything larger
MAX_RESULTS = 2000              # Workday stops paginating here
DEFAULT_QUERIES = ("intern", "co-op", "student")
DEFAULT_MAX_RESULTS = 200
PARALLEL_BOARDS = 4


def board_urls(slug: str) -> tuple[str, str]:
    """('https://tenant.wdN.myworkdayjobs.com', '<host>/wday/cxs/tenant/site') for a slug."""
    tenant, wd, site = slug.split("/")
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    return host, f"{host}/wday/cxs/{tenant}/{site}"


def posted_on_to_iso(text: str | None) -> str | None:
    """'Posted 7 Days Ago' / 'Posted Today' / 'Posted 30+ Days Ago' to a timestamp."""
    if not text:
        return None
    lowered = text.lower()
    if "today" in lowered:
        return to_iso(now())
    if "yesterday" in lowered:
        return to_iso(now() - timedelta(days=1))
    match = re.search(r"(\d+)\+?\s*days?", lowered)
    return to_iso(now() - timedelta(days=int(match.group(1)))) if match else None


def parse_workday_detail(company: WatchlistCompany, listing: dict, detail: dict) -> NormalizedJob:
    """A Workday list entry plus its detail JSON to a NormalizedJob."""
    info = detail.get("jobPostingInfo") or {}
    host, _ = board_urls(company.slug)
    site = company.slug.split("/")[2]
    locations = [info.get("location") or listing.get("locationsText")]
    locations += info.get("additionalLocations") or []
    country = ((info.get("country") or {}).get("descriptor"))
    title = info.get("title") or listing.get("title") or ""
    start = info.get("startDate")
    return NormalizedJob(
        source="workday",
        source_job_id=f"{company.slug}:{info.get('jobReqId') or listing['externalPath']}",
        company=company.name,
        title=title,
        url=info.get("externalUrl") or f"{host}/{site}{listing['externalPath']}",
        location="; ".join(loc for loc in locations if loc) or None,
        is_remote=True if (info.get("remoteType") or "").lower() == "remote" else None,
        country=country,
        description=html_to_text(info.get("jobDescription")),
        posted_at=f"{start}T00:00:00Z" if start else posted_on_to_iso(listing.get("postedOn")),
        season=season_from(title),
        raw={"listing": listing, "jobReqId": info.get("jobReqId"), "country": country},
    )


@dataclass
class BoardFetch:
    """Retained postings and problems from a possibly incomplete board search."""

    total: int = 0
    jobs: list[NormalizedJob] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    rate_limited: bool = False
    truncated_queries: int = 0
    pages: int = 0


def fetch_board(
    company: WatchlistCompany, http: PoliteHttp, *,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    max_results: int = DEFAULT_MAX_RESULTS,
    stop: threading.Event | None = None,
) -> BoardFetch:
    """Page each bounded query, retaining successes when pages or details fail."""
    _, api = board_urls(company.slug)
    result = BoardFetch()
    seen: set[str] = set()
    limit = min(MAX_RESULTS, max_results)
    if limit < 1:
        raise ValueError("max_results_per_query must be positive")

    def problem(exc: Exception) -> None:
        result.problems.append(f"{type(exc).__name__}: {exc}")
        if isinstance(exc, RateLimited):
            result.rate_limited = True
            if stop is not None:
                stop.set()

    for query in queries:
        total = None
        offset = 0
        while offset < limit:
            if result.rate_limited or (stop is not None and stop.is_set()):
                if not result.rate_limited:
                    result.rate_limited = True
                    result.problems.append("skipped after a 429")
                return result
            try:
                page_size = min(PAGE_SIZE, limit - offset)
                page = http.post_json(f"{api}/jobs", {
                    "appliedFacets": {}, "limit": page_size, "offset": offset,
                    "searchText": query,
                })
                listing_observed_at = getattr(http, "retrieved_at", None)
                if total is None:
                    total = int(page["total"])
                    result.total += total
                postings = page["jobPostings"]
                if not isinstance(postings, list):
                    raise ValueError("jobPostings is not a list")
            except (HttpError, ValueError, KeyError, TypeError) as exc:
                problem(exc)
                break
            result.pages += 1
            if not postings:
                if offset < total:
                    result.problems.append(f"{query!r}: empty page at {offset} before total {total}")
                break
            for listing in postings[:page_size]:
                if not isinstance(listing, dict):
                    result.problems.append(f"{query!r}: invalid listing")
                    continue
                path = listing.get("externalPath")
                if not path:
                    result.problems.append(f"{query!r}: listing missing externalPath")
                    continue
                if path in seen or not is_internship_title(listing.get("title") or ""):
                    continue
                seen.add(path)
                try:
                    detail = http.get_json(f"{api}{path}")
                    if not isinstance(detail, dict) or not isinstance(detail.get("jobPostingInfo"), dict):
                        raise ValueError("missing jobPostingInfo in detail")
                    job = parse_workday_detail(company, listing, detail)
                    job.observed_at = getattr(http, "retrieved_at", None)
                except (HttpError, ValueError, KeyError, TypeError) as exc:
                    problem(exc)
                    # The real list entry is still useful, but is not a complete detail.
                    job = parse_workday_detail(company, listing, {})
                    job.observed_at = listing_observed_at
                result.jobs.append(job)
                if result.rate_limited or (stop is not None and stop.is_set()):
                    if not result.rate_limited:
                        result.rate_limited = True
                        result.problems.append("skipped after a 429")
                    return result
            offset += min(len(postings), page_size)
            if offset >= total:
                break
        else:
            result.truncated_queries += 1
            result.problems.append(f"{query!r}: truncated at {limit} of {total} search hits")
    return result


class WorkdayAdapter:
    name = "workday"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        http = self.ctx.http_for(self.name)
        stop = threading.Event()
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        queries = options.get("search_queries", DEFAULT_QUERIES)
        if not isinstance(queries, (list, tuple)) or not queries or any(
            not isinstance(query, str) or not query.strip() for query in queries
        ):
            return AdapterResult([], "error", "search_queries must be a nonempty list of text queries",
                                 stats={"complete_snapshot": False})
        queries = tuple(dict.fromkeys(query.strip() for query in queries))
        max_results = int(options.get("max_results_per_query", DEFAULT_MAX_RESULTS))
        workers = int(options.get("parallel_boards", PARALLEL_BOARDS))
        if max_results < 1 or workers < 1:
            return AdapterResult([], "error", "max_results_per_query and parallel_boards must be positive",
                                 stats={"complete_snapshot": False})

        def one(company: WatchlistCompany) -> tuple[BoardOutcome, int, list[NormalizedJob]]:
            if stop.is_set():
                return BoardOutcome(company.slug, "rate_limited", error="skipped after a 429"), 0, []
            try:
                fetched = fetch_board(company, http, queries=queries, max_results=max_results, stop=stop)
            except RateLimited as exc:
                stop.set()
                return BoardOutcome(company.slug, "rate_limited", error=str(exc)), 0, []
            except (HttpError, ValueError, KeyError, TypeError) as exc:
                return BoardOutcome(company.slug, "error", error=f"{type(exc).__name__}: {exc}"), 0, []
            total, jobs = fetched.total, fetched.jobs
            if fetched.problems:
                status = "rate_limited" if fetched.rate_limited else ("partial" if fetched.pages else "error")
                return BoardOutcome(company.slug, status, len(jobs),
                                    "; ".join(fetched.problems[:5])), total, jobs
            if self.ctx.dropped_to_zero(self.name, company.slug, total):
                return BoardOutcome(company.slug, "partial", len(jobs),
                                    "0 search hits; it had hits last run"), total, jobs
            return BoardOutcome(company.slug, "ok", len(jobs)), total, jobs

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, self.ctx.boards(self.name)))

        outcomes = [outcome for outcome, _, _ in results]
        jobs = [job for _, _, found in results for job in found]
        board_counts = {o.board: total for o, total, _ in results if o.status in ("ok", "partial")}
        status, error = summarize_boards(outcomes)
        if status == "error" and (jobs or any(o.status == "partial" for o in outcomes)):
            status = "partial"
        if status == "rate_limited" and jobs:
            status = "partial"
        return AdapterResult(
            jobs=jobs, status=status, error=error, boards=outcomes,
            stats={
                "boards": len(outcomes), "postings_seen": sum(board_counts.values()),
                "intern_postings": len(jobs), "board_counts": board_counts,
                "requests": http.requests_made, "cache_hits": http.cache_hits,
                "complete_snapshot": False,
            },
        )

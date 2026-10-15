"""Opt-in Brave discovery, or free seed URLs; only fetched JobPosting records count."""
from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import urlencode

from kitty.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.discovery.public_pages import PublicPages, parse_job_postings
from kitty.core.discovery.text import is_internship_title, us_location
from kitty.normalize import canonical_url

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
# Avoid support staff whose customers happen to be students/interns.
_NON_STUDENT = re.compile(
    r"\b(?:student (?:affairs|services|success|recruitment)|"
    r"(?:internship|student|intern) (?:program )?(?:coordinator|manager|director|advisor|adviser|recruiter)|"
    r"placement (?:officer|coordinator))\b", re.I,
)


def student_job(job: NormalizedJob) -> bool:
    """Require student-role evidence, never a search snippet or a generic trainee title."""
    if _NON_STUDENT.search(job.title):
        return False
    if is_internship_title(job.title):
        if re.search(r"\b(?:trainee|placement)\b", job.title, re.I) and not re.search(
            r"\b(?:intern(?:ship)?|co[- ]?op|student|summer analyst|summer associate|apprentice)\b",
            job.title, re.I,
        ):
            return bool(re.search(r"\b(?:currently enrolled|current(?:ly)? .*student|undergraduate)\b",
                                  job.description or "", re.I))
        return True
    employment = (job.raw or {}).get("employmentType") or []
    employment = employment if isinstance(employment, list) else [employment]
    return any(str(value).upper() in {"INTERN", "INTERNSHIP"} for value in employment)


def track_queries(ctx: DiscoverContext) -> list[str]:
    """Round-robin track terms so a bounded run does not only cover the first track."""
    tracks = [track for track in ctx.config.tracks.tracks
              if track.name not in {"parttime", "research"} and "websearch" in track.sources]
    queries = []
    for index in range(max((len(track.search_terms) for track in tracks), default=0)):
        for track in tracks:
            if index < len(track.search_terms):
                term = track.search_terms[index].strip()
                if term:
                    query = f'{term} internship "United States"'
                    if query not in queries:
                        queries.append(query)
    return queries


def _limit(options: dict, name: str, default: int, maximum: int) -> int:
    value = options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


class WebSearchAdapter:
    name = "websearch"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        stats = {"complete_snapshot": False, "queries": [], "pages": [], "duplicates": 0,
                 "postings_seen": 0, "skipped_nonstudent": 0, "skipped_foreign": 0}
        try:
            max_queries = _limit(options, "max_queries", 4, 20)
            max_pages = _limit(options, "max_pages", 20, 100)
            max_results = _limit(options, "max_results", 5, 20)
            enable_search = options.get("enable_search", False)
            if not isinstance(enable_search, bool):
                raise ValueError("enable_search must be a boolean")
            seeds = options.get("seed_urls", [])
            if not isinstance(seeds, list) or any(not isinstance(url, str) or not url.strip() for url in seeds):
                raise ValueError("seed_urls must be a list of nonempty URLs")
        except ValueError as exc:
            return AdapterResult([], "error", str(exc), stats=stats)
        stats["budget"] = {"max_queries": max_queries, "max_pages": max_pages, "max_results": max_results}
        key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if not seeds and not enable_search:
            return AdapterResult([], "error", "No seed_urls; Brave search requires enable_search: true and BRAVE_SEARCH_API_KEY", stats=stats)
        outcomes = []
        candidates: dict[str, list[str | None]] = {}
        for url in seeds:
            candidates.setdefault(url.strip(), [None])
        http = self.ctx.http_for(self.name)
        jobs: dict[str, NormalizedJob] = {}
        with PublicPages(http) as pages:
            if enable_search and not key:
                outcomes.append(BoardOutcome("Brave search", "error", error="BRAVE_SEARCH_API_KEY is not set in .env"))
            elif enable_search:
                queries = track_queries(self.ctx)[:max_queries]
                if not queries:
                    outcomes.append(BoardOutcome("Brave search", "error", error="No internship tracks select websearch with search_terms"))
                for query in queries:
                    stats["queries"].append(query)
                    url = SEARCH_URL + "?" + urlencode({"q": query, "count": max_results, "country": "US", "search_lang": "en"})
                    try:
                        payload = pages.brave_json(url, key)
                        web = payload.get("web") or {}
                        results = web.get("results", []) if isinstance(web, dict) else None
                        if not isinstance(results, list):
                            raise HttpError("Brave returned invalid web results")
                        hits = 0
                        for hit in results[:max_results]:
                            hit_url = hit.get("url") if isinstance(hit, dict) else None
                            if isinstance(hit_url, str) and hit_url.strip():
                                provenance = candidates.setdefault(hit_url, [])
                                if query not in provenance:
                                    provenance.append(query)
                                hits += 1
                        outcomes.append(BoardOutcome(query, "ok", hits))
                    except (HttpError, ValueError) as exc:
                        status = "rate_limited" if isinstance(exc, RateLimited) else "error"
                        outcomes.append(BoardOutcome(query, status, error=str(exc)))
                        # An API failure is not a reason to spend more requests; seeds still run.
                        break
            visited = set()
            for url, queries in candidates.items():
                try:
                    identity = canonical_url(url)
                except ValueError:
                    identity = url  # fetch reports the unsafe URL alongside other failures
                if identity in visited:
                    for job in jobs.values():
                        provenance = job.raw["provenance"]
                        if canonical_url(provenance["discovery_url"]) == identity:
                            provenance["queries"] = list(dict.fromkeys(
                                provenance["queries"] + [q for q in queries if q]
                            ))
                    continue
                if len(visited) >= max_pages:
                    stats["page_budget_exhausted"] = True
                    break
                visited.add(identity)
                try:
                    page = pages.fetch(url)
                    found = parse_job_postings(page, source=self.name, query=next((q for q in queries if q), None))
                    stats["postings_seen"] += len(found)
                    kept = 0
                    for job in found:
                        if not student_job(job):
                            stats["skipped_nonstudent"] += 1
                            continue
                        countries = job.country.split("; ") if job.country else [None]
                        if all(us_location(job.location, country) is False for country in countries):
                            stats["skipped_foreign"] += 1
                            continue
                        provenance = job.raw["provenance"]
                        provenance["discovery_url"] = url
                        provenance["queries"] = [q for q in queries if q]
                        previous = jobs.get(job.source_job_id)
                        if previous:
                            stats["duplicates"] += 1
                            prior = previous.raw["provenance"]
                            prior["queries"] = list(dict.fromkeys(prior["queries"] + provenance["queries"]))
                        else:
                            jobs[job.source_job_id] = job
                        kept += 1
                    stats["pages"].append({"url": url, "final_url": page.url, "postings": len(found), "kept": kept})
                    outcomes.append(BoardOutcome(url, "ok", kept))
                except (HttpError, ValueError) as exc:
                    status = "rate_limited" if isinstance(exc, RateLimited) else "error"
                    outcomes.append(BoardOutcome(url, status, error=str(exc)))
                    stats["pages"].append({"url": url, "error": str(exc), "status": status})
        status, error = summarize_boards(outcomes)
        if jobs and status in {"error", "rate_limited"}:
            status = "partial"
        stats.update({"kept": len(jobs), "requests": http.requests_made, "cache_hits": http.cache_hits,
                      "errors": [{"target": outcome.board, "status": outcome.status, "error": outcome.error}
                                 for outcome in outcomes if outcome.status != "ok"]})
        return AdapterResult(list(jobs.values()), status, error, stats=stats)

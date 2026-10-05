"""Adapter for the job aggregators, through the python-jobspy library (fragile).

JobSpy scrapes Indeed, Google Jobs, ZipRecruiter and Glassdoor (LinkedIn only
when sources.yaml lists it, which it does not by default). These are scrapers,
not APIs, so this module is deliberately conservative:

  * a bounded search plan: for each track that lists `jobspy` in its sources,
    its first `max_terms_per_track` search terms, in that track's location
    (see `plan_searches`), on each site;
  * `results_wanted` stays small, and calls to the same site are at least
    `min_interval_seconds` apart (sites are interleaved, so the run is not
    sites x searches x interval long);
  * every search's results are cached on disk for the source's TTL, so a rerun
    inside the TTL makes no calls at all;
  * JobSpy logs failures instead of raising them, so its log records are
    captured per call. A 429 or a block (403) marks that search rate_limited
    and no further calls go to that site this run; other sites carry on and
    the source reports 'partial'. A site whose searches fail twice in a row
    for any other reason (a scraper broken by a site change) is also left
    alone for the rest of the run. Nothing is retried by this module. (The
    library's own Google and Glassdoor sessions retry 5xx/429 up to 3 times
    with backoff inside one call; that cannot be switched off without patching
    the library.)

Indeed accepts only one of hours_old / job_type+is_remote / easy_apply per
search. This module only ever sends hours_old, and a "Remote" location goes to
Indeed as the location text "Remote" rather than is_remote. Google ignores the
normal search fields and needs `google_search_term` written like a Google Jobs
query ("firmware intern jobs in Texas in the last month").

Search results are a sample, not a full listing, so a posting missing from
one run's results is not evidence it closed. Each search is reported as its
own board ("indeed | firmware intern | Texas"), and close_missing only counts a
miss when a posting's id prefix ("indeed") names a clean board, which these
never do. JobSpy postings therefore close only through other sources.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from applyai.config import Track
from applyai.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.text import html_to_text, is_internship_title, iso_to_utc, season_from
from applyai.dates import now_iso, to_iso
from applyai.normalize import canonical_url

DEFAULT_SITES = ["indeed", "google", "zip_recruiter", "glassdoor"]
DEFAULT_RESULTS_WANTED = 20
DEFAULT_MAX_TERMS_PER_TRACK = 1
DEFAULT_HOURS_OLD = 336                     # two weeks
#: Where internship searches look when a track has no `near` rule.
DEFAULT_LOCATIONS = ["Texas", "Remote"]
REMOTE = "remote"
#: A site whose searches fail this many times in a row gets no more calls this run.
SITE_FAILURE_LIMIT = 2

#: A scrape call: JobSpy keyword arguments -> (posting records, problem log lines).
ScrapeFn = Callable[[dict], tuple[list[dict], list[str]]]

_BLOCKED = re.compile(r"\b(429|403)\b|blocked|too many requests|rate limit", re.I)


@dataclass(frozen=True)
class Search:
    """One call to one site: a track's search term in one location."""

    site: str
    term: str
    location: str
    distance: int | None = None        # miles, for a track's `near` rule

    @property
    def name(self) -> str:
        return f"{self.site} | {self.term} | {self.location}"

    @property
    def interns_only(self) -> bool:
        """A term that asks for interns keeps only intern-looking titles."""
        return is_internship_title(self.term)


def plan_searches(tracks: list[Track], options: dict) -> list[Search]:
    """Every search this run makes, interleaved by site so each site gets a rest between calls.

    Location: a track with a `near` rule (part-time work) searches that city
    within its radius, plus each `also_near` place with that place's own terms.
    Every other track searches each of `options.locations` (default Texas plus
    Remote). Each track searches its first `max_terms_per_track` terms, or its
    own count from `max_terms_by_track`; zero means all configured terms.
    """
    sites = options.get("sites") or DEFAULT_SITES
    max_terms = int(options.get("max_terms_per_track", DEFAULT_MAX_TERMS_PER_TRACK))
    by_track = options.get("max_terms_by_track") or {}
    locations = options.get("locations") or DEFAULT_LOCATIONS
    plan: list[Search] = []

    def add(term: str, location: str, distance: int | None) -> None:
        for site in sites:
            search = Search(site, term, location, distance)
            if search not in plan:
                plan.append(search)

    for track in tracks:
        if "jobspy" not in track.sources:
            continue
        rules = track.location_rules
        count = int(by_track.get(track.name, max_terms))
        if count < 0:
            raise ValueError("max_terms_per_track and max_terms_by_track must be nonnegative")
        terms = track.search_terms[:count] if count else track.search_terms
        if rules.near is None:
            for term in terms:
                for location in locations:
                    add(term, location, None)
            continue
        for near in (rules.near, *rules.also_near):
            for term in near.terms or terms:
                add(term, f"{near.city}, {near.state}", near.radius_miles)
    return plan


def _google_time_phrase(hours_old: int) -> str:
    """How Google Jobs phrases a posting-age filter."""
    if hours_old <= 24:
        return "since yesterday"
    if hours_old <= 72:
        return "in the last 3 days"
    if hours_old <= 168:
        return "in the last week"
    return "in the last month"


def scrape_kwargs(search: Search, *, results_wanted: int, hours_old: int) -> dict:
    """JobSpy keyword arguments for one search, respecting each site's quirks."""
    remote = search.location.lower() == REMOTE
    kwargs: dict = {
        "site_name": [search.site],
        "search_term": search.term,
        "results_wanted": results_wanted,
        "hours_old": hours_old,          # Indeed's single allowed filter; never job_type/easy_apply
        "country_indeed": "usa",
        "description_format": "html",
    }
    if remote and search.site != "indeed":
        kwargs["is_remote"] = True
        kwargs["location"] = "United States"
    else:
        kwargs["location"] = search.location      # Indeed reads "Remote" as a place
    if search.distance is not None:
        kwargs["distance"] = search.distance
    if search.site == "google":
        if remote:
            query = f"remote {search.term} jobs"
        elif search.distance is not None:
            query = f"{search.term} jobs near {search.location}"
        else:
            query = f"{search.term} jobs in {search.location}"
        kwargs["google_search_term"] = f"{query} {_google_time_phrase(hours_old)}"
    return kwargs


# ------------------------------------------------------------ calling JobSpy


class _Capture(logging.Handler):
    """Collects JobSpy's log lines that report a failure."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.problems: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if record.levelno >= logging.WARNING or "status code" in message:
            self.problems.append(f"{record.name}: {message}")


def scrape_with_jobspy(kwargs: dict) -> tuple[list[dict], list[str]]:
    """Run one JobSpy search; return its postings as plain dicts and any logged failures.

    JobSpy's loggers ("JobSpy:Indeed", ...) print to the terminal and report
    HTTP failures (Indeed's even at INFO level) instead of raising. For the
    length of the call their handlers are swapped for a capture handler, which
    keeps the TUI clean and lets a 429 be reported instead of looking like 0
    jobs.
    """
    import jobspy                              # heavy (pandas); only loaded when this source runs

    capture = _Capture()
    loggers = [logging.getLogger(name) for name in list(logging.Logger.manager.loggerDict)
               if name.startswith("JobSpy")]
    saved = [(logger, logger.handlers[:]) for logger in loggers]
    for logger in loggers:
        logger.handlers = [capture]
    try:
        frame = jobspy.scrape_jobs(verbose=2, **kwargs)
    finally:
        for logger, handlers in saved:
            logger.handlers = handlers
    if frame is None or frame.empty:
        return [], capture.problems
    records = json.loads(frame.to_json(orient="records", date_format="iso", default_handler=str))
    return records, capture.problems


# ------------------------------------------------------------------ parsing


def parse_record(record: dict, search: Search) -> NormalizedJob | None:
    """One JobSpy posting to a NormalizedJob; None when it has no title, company or URL."""
    title = (record.get("title") or "").strip()
    company = (record.get("company") or "").strip()
    url = record.get("job_url")
    if not (title and company and url):
        return None
    site = record.get("site") or search.site
    job_id = record.get("id") or canonical_url(url)
    date_posted = record.get("date_posted")
    return NormalizedJob(
        source="jobspy",
        source_job_id=f"{site}:{job_id}",
        company=company,
        title=title,
        url=url,
        location=record.get("location"),
        is_remote=record.get("is_remote") if isinstance(record.get("is_remote"), bool) else None,
        apply_url=record.get("job_url_direct"),
        description=with_pay_line(record, html_to_text(record.get("description"))),
        posted_at=iso_to_utc(date_posted[:10]) if isinstance(date_posted, str) else None,
        season=season_from(title),
        raw={"site": site, "id": record.get("id"), "job_url": url, "job_type": record.get("job_type"),
             "date_posted": date_posted, "search": search.name},
    )


def with_pay_line(record: dict, description: str | None) -> str | None:
    """The description with the site's own hourly pay figures as a first line.

    Indeed shows pay beside the posting, often not in its text; JobSpy returns
    it as min/max_amount + interval. Written as "$15 - $18 per hour" so the
    eligibility pay rule reads it like any other posting's pay.
    """
    low, high = record.get("min_amount"), record.get("max_amount")
    if record.get("interval") != "hourly" or not isinstance(low or high, (int, float)):
        return description
    low = low if isinstance(low, (int, float)) else high
    high = high if isinstance(high, (int, float)) else low
    rate = f"${low:g}" if low == high else f"${low:g} - ${high:g}"
    line = f"Pay (listed on {record.get('site') or 'the site'}): {rate} per hour"
    return f"{line}\n\n{description}" if description else line


# ------------------------------------------------------------------ caching


class SearchCache:
    """Each search's records on disk, fresh for `ttl_seconds`."""

    def __init__(self, directory: Path, ttl_seconds: int) -> None:
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self.retrieved_at: str | None = None

    def _path(self, kwargs: dict) -> Path:
        digest = hashlib.sha256(json.dumps(kwargs, sort_keys=True).encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def get(self, kwargs: dict) -> list[dict] | None:
        path = self._path(kwargs)
        self.retrieved_at = None
        if self.ttl_seconds and path.exists() and time.time() - path.stat().st_mtime < self.ttl_seconds:
            self.retrieved_at = to_iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def put(self, kwargs: dict, records: list[dict]) -> None:
        self.retrieved_at = now_iso()
        if not self.ttl_seconds:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(kwargs)
        path.write_text(json.dumps(records), encoding="utf-8")
        self.retrieved_at = to_iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))


def site_summary(outcomes: list[BoardOutcome]) -> dict[str, str]:
    """One line per site: 'ok: N postings from M searches', or its first problem.

    The Sources screen shows the problem lines joined, which reads better than
    a list of 44 search names.
    """
    by_site: dict[str, list[BoardOutcome]] = {}
    for outcome in outcomes:
        by_site.setdefault(outcome.board.split(" | ", 1)[0], []).append(outcome)
    lines: dict[str, str] = {}
    for site, results in by_site.items():
        bad = [o for o in results if o.status != "ok"]
        if not bad:
            lines[site] = f"ok: {sum(o.count for o in results)} postings from {len(results)} searches"
            continue
        first = next((o for o in bad if not (o.error or "").startswith("skipped")), bad[0])
        label = "blocked" if first.status == "rate_limited" else "failed"
        lines[site] = f"{label} on {len(bad)} of {len(results)} searches: {first.error}"
    return lines


# ------------------------------------------------------------------ adapter


class JobSpyAdapter:
    name = "jobspy"

    def __init__(self, ctx: DiscoverContext, scrape: ScrapeFn | None = None) -> None:
        self.ctx = ctx
        self.scrape = scrape or scrape_with_jobspy
        self._last_call: dict[str, float] = {}

    def fetch(self, since: datetime | None) -> AdapterResult:
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        plan = plan_searches(self.ctx.config.tracks.tracks, options)
        if not plan:
            return AdapterResult([], "error", "no track lists jobspy in its sources, so nothing to search")

        # The source's own pacing and TTL, as configured in sources.yaml.
        http = self.ctx.http_for(self.name)
        self.min_interval = http.min_interval
        cache = SearchCache(http.cache_dir / "searches", http.ttl_seconds)
        results_wanted = int(options.get("results_wanted", DEFAULT_RESULTS_WANTED))
        hours_old = int(options.get("hours_old", DEFAULT_HOURS_OLD))

        jobs: dict[str, NormalizedJob] = {}
        outcomes: list[BoardOutcome] = []
        stopped: dict[str, str] = {}             # site -> why it gets no more calls this run
        failures: dict[str, int] = {}            # site -> failed searches in a row
        stats = {"searches": len(plan), "calls": 0, "cached": 0, "postings_seen": 0,
                 "not_intern": 0, "incomplete": 0, "by_site": {}, "complete_snapshot": False}

        for search in plan:
            kwargs = scrape_kwargs(search, results_wanted=results_wanted, hours_old=hours_old)
            records = cache.get(kwargs)
            if records is not None:
                stats["cached"] += 1
                outcome = BoardOutcome(search.name, "ok", len(records))
            elif search.site in stopped:
                status = "rate_limited" if stopped[search.site].startswith("refused") else "error"
                outcomes.append(BoardOutcome(search.name, status, error=f"skipped: {stopped[search.site]}"))
                continue
            else:
                stats["calls"] += 1
                outcome, records = self._call(search, kwargs)
                failures[search.site] = failures.get(search.site, 0) + 1 if outcome.status == "error" else 0
                if outcome.status == "rate_limited":
                    stopped[search.site] = f"refused '{search.name}': {outcome.error}"
                elif failures[search.site] >= SITE_FAILURE_LIMIT:
                    stopped[search.site] = f"{SITE_FAILURE_LIMIT} searches in a row failed: {outcome.error}"
                if outcome.status == "ok":
                    cache.put(kwargs, records)
            outcomes.append(outcome)
            self._collect(search, records, jobs, stats, observed_at=cache.retrieved_at or now_iso())

        status, _ = summarize_boards(outcomes)
        if status in ("error", "rate_limited") and jobs:
            status = "partial"                   # some searches answered
        stats["sites"] = site_summary(outcomes)
        problems = {site: line for site, line in stats["sites"].items() if not line.startswith("ok")}
        error = "; ".join(f"{site}: {line}" for site, line in problems.items()) or None
        return AdapterResult(list(jobs.values()), status, error, stats=stats, boards=outcomes)

    def _call(self, search: Search, kwargs: dict) -> tuple[BoardOutcome, list[dict]]:
        """One paced scrape call, turned into an honest outcome."""
        self._wait_for_site(search.site)
        try:
            records, problems = self.scrape(kwargs)
        except Exception as exc:                 # JobSpy raises assorted errors for bad input or HTML
            problems, records = [f"{type(exc).__name__}: {exc}"], []
        if problems:
            detail = "; ".join(problems)[:300]
            if any(_BLOCKED.search(p) for p in problems):
                return BoardOutcome(search.name, "rate_limited", error=detail), records
            if not records:
                return BoardOutcome(search.name, "error", error=detail), records
            return BoardOutcome(search.name, "partial", len(records), error=detail), records
        return BoardOutcome(search.name, "ok", len(records)), records

    def _wait_for_site(self, site: str) -> None:
        """Sleep until `min_interval` has passed since the last call to `site`."""
        last = self._last_call.get(site)
        if last is not None:
            wait = last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last_call[site] = time.monotonic()

    def _collect(self, search: Search, records: list[dict], jobs: dict[str, NormalizedJob],
                 stats: dict, *, observed_at: str) -> None:
        """Add one search's postings to `jobs`, keeping intern titles for intern searches."""
        stats["postings_seen"] += len(records)
        stats["by_site"][search.site] = stats["by_site"].get(search.site, 0) + len(records)
        for record in records:
            job = parse_record(record, search)
            if job is None:
                stats["incomplete"] += 1
            elif search.interns_only and not is_internship_title(job.title):
                stats["not_intern"] += 1
            else:
                job.observed_at = observed_at
                jobs.setdefault(job.source_job_id, job)

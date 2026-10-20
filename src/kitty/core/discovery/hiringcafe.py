"""hiring.cafe: an aggregator of employer career pages, read through its landing pages.

robots.txt allows the /jobs/<topic> landing pages but not ?page= or
?searchState=, so only page 1 (about 20 postings) of /jobs/<slug> is read, one
page per track search term (e.g. "software engineer intern" ->
/jobs/software-engineer-intern). The terms of use allow personal use and forbid
republishing, so the postings stay in the local database.

Plain HTTP gets Cloudflare's "Just a moment..." check, so the pages are loaded in
headless Firefox through kitty's WebDriver BiDi driver, with a profile of its own
(data/hiringcafe-firefox), never the form filler's profile or my everyday one.
The browser stays what it is: no changed user agent, no hidden automation flag,
no solved captchas. A check that does not clear by itself within CHALLENGE_WAIT
stops the run with an error instead of reporting 0 jobs.

Each page carries its results in the Next.js __NEXT_DATA__ script, under
props.pageProps: ssrHits (the postings), ssrTotalCount, ssrPageSize,
ssrIsLastPage and ssrError. A hit's apply_url is the employer's own posting; its
facts live in job_information (title) and v5_processed_job_data (company,
cities, countries, workplace type, publish date, degree requirements, visa,
clearance, pay). The same posting found under two topics, or listed twice under
one strict_dedup_cluster_id, is kept once.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime

from kitty.config import Track
from kitty.core.apply.bidi import BidiError, ConnectionLost, open_tab
from kitty.core.apply.browser import BrowserError, headless_firefox
from kitty.core.discovery.base import AdapterResult, BoardOutcome, NormalizedJob, summarize_boards
from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.discovery.text import is_internship_title, iso_to_utc, season_from, unix_to_iso
from kitty.dates import now_iso
from kitty.normalize import canonical_url
from kitty.settings import Settings

PAGE_URL = "https://hiring.cafe/jobs/"
#: Landing pages per run, at most; the track terms are taken round-robin up to it.
MAX_TOPICS = 40
#: Seconds between page loads, at least (sources.yaml may ask for more).
MIN_INTERVAL = 5.0
#: Seconds a page may sit on Cloudflare's check before the run gives up.
CHALLENGE_WAIT = 30.0
#: The browser profile, under data/: separate from the form filler's data/firefox.
PROFILE_DIR = "hiringcafe-firefox"
_SLUG_MAX = 80

#: A function that loads one landing page and returns its props.pageProps.
PageLoader = Callable[[str], dict]
#: Opens a browser (or a test double) for one run and yields its PageLoader.
PageSession = Callable[[], AbstractContextManager[PageLoader]]


class BotCheck(HttpError):
    """Cloudflare's bot check did not clear, or it refused the browser outright."""


# ------------------------------------------------------------------- parsing

_GRAD_REQUIREMENTS = (("masters_degree_requirement", "Master's"), ("doctorate_degree_requirement", "PhD"))
_UNDERGRAD_REQUIREMENTS = ("bachelors_degree_requirement", "associates_degree_requirement")
_NO_CLEARANCE = {"", "none", "not mentioned", "not required", "not indicated", "no"}


def _grad_only_degrees(data: dict) -> list[str] | None:
    """Simplify-style degrees when only graduate degrees qualify, else None.

    Spelled like Simplify's ("Master's", "PhD") so eligibility.GRAD_DEGREES drops
    these postings exactly as it drops Simplify's graduate-only listings. A
    bachelor's (or associate's) degree that is required or preferred means
    undergraduates qualify, so nothing is reported.
    """
    if any(data.get(key) in ("Required", "Preferred") for key in _UNDERGRAD_REQUIREMENTS):
        return None
    return [name for key, name in _GRAD_REQUIREMENTS if data.get(key) == "Required"] or None


def _country(codes: list) -> str | None:
    codes = [code for code in codes if isinstance(code, str) and code.strip()]
    if not codes:
        return None
    return "United States" if "US" in codes else "; ".join(codes)


def _strings(value) -> list[str]:
    return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def parse_hiringcafe(page_props: dict, topic: str) -> tuple[list[NormalizedJob], dict]:
    """One landing page's props.pageProps to NormalizedJobs, plus stats.

    A hit without an http(s) apply_url, title or company counts as bad, so a
    format change shows up in stats. When the topic is an internship term, hits
    whose titles are not internships are skipped (counted as not_intern).
    """
    hits = page_props.get("ssrHits")
    stats = {
        "hits": len(hits) if isinstance(hits, list) else None,
        "total": page_props.get("ssrTotalCount"), "page_size": page_props.get("ssrPageSize"),
        "last_page": page_props.get("ssrIsLastPage"), "ssr_error": page_props.get("ssrError"),
        "kept": 0, "bad_hits": 0, "not_intern": 0,
    }
    interns_only = is_internship_title(topic)
    jobs: list[NormalizedJob] = []
    for hit in hits if isinstance(hits, list) else []:
        info = hit.get("job_information") if isinstance(hit, dict) else None
        data = hit.get("v5_processed_job_data") if isinstance(hit, dict) else None
        if not isinstance(info, dict) or not isinstance(data, dict):
            stats["bad_hits"] += 1
            continue
        url = hit.get("apply_url")
        title = " ".join(str(info.get("title") or data.get("core_job_title") or "").split())
        company = " ".join(str(data.get("company_name") or "").split())
        try:
            job_id = canonical_url(url) if isinstance(url, str) and url.startswith(("https://", "http://")) else None
        except ValueError:
            job_id = None
        if not (job_id and title and company):
            stats["bad_hits"] += 1
            continue
        if interns_only and not is_internship_title(title):
            stats["not_intern"] += 1
            continue
        workplace = data.get("workplace_type")
        cities = _strings(data.get("workplace_cities"))
        clearance = data.get("security_clearance")
        clearance = clearance.strip() if isinstance(clearance, str) else None
        clearance_required = bool(clearance) and clearance.lower() not in _NO_CLEARANCE
        jobs.append(NormalizedJob(
            source="hiringcafe",
            source_job_id=job_id,
            company=company,
            title=title,
            url=url,
            location="; ".join(cities) or ("Remote" if workplace == "Remote" else None),
            is_remote=None if workplace not in ("Remote", "Hybrid", "Onsite") else workplace == "Remote",
            country=_country(data.get("workplace_countries") or []),
            posted_at=(unix_to_iso(data.get("estimated_publish_date_millis"), millis=True)
                       if isinstance(data.get("estimated_publish_date_millis"), (int, float))
                       else iso_to_utc(data.get("estimated_publish_date"))),
            season=season_from(title),
            degrees=_grad_only_degrees(data),
            closed=hit.get("is_expired") is True,
            raw={
                "topic": topic,
                "visa_sponsorship": data.get("visa_sponsorship"),
                "security_clearance": clearance,
                "workplace_type": workplace,
                "cities": cities,
                "degree_requirements": {
                    key.split("_")[0]: data.get(key) for key in (
                        "associates_degree_requirement", "bachelors_degree_requirement",
                        "masters_degree_requirement", "doctorate_degree_requirement")
                },
                "comp": {
                    "hourly_min": data.get("hourly_min_compensation"),
                    "hourly_max": data.get("hourly_max_compensation"),
                    "currency": data.get("listed_compensation_currency"),
                    "frequency": data.get("listed_compensation_frequency"),
                },
                "job_category": data.get("job_category"),
                "role_type": data.get("role_type"),
                "requirements_summary": data.get("requirements_summary"),
                "cluster": hit.get("strict_dedup_cluster_id"),
                "board": hit.get("source_and_board_token"),
                # visa_sponsorship=false is hiring.cafe's value for "not offered or not
                # stated", so only a stated clearance becomes a note.
                "eligibility_note": (f"hiring.cafe lists a required security clearance ({clearance})."
                                     if clearance_required else None),
            },
        ))
    stats["kept"] = len(jobs)
    return jobs, stats


# -------------------------------------------------------------------- topics


def slugify(term: str) -> str:
    """'GPU software intern' -> 'gpu-software-intern' (empty when nothing is left)."""
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")[:_SLUG_MAX].strip("-")


def plan_topics(tracks: list[Track], limit: int) -> list[tuple[str, str]]:
    """(term, slug) for the tracks that select hiringcafe, round-robin, one per slug.

    Round-robin so a capped run still covers every track's first terms.
    """
    chosen = [track for track in tracks if "hiringcafe" in track.sources]
    topics: dict[str, str] = {}
    for index in range(max((len(track.search_terms) for track in chosen), default=0)):
        for track in chosen:
            if index < len(track.search_terms):
                term = " ".join(track.search_terms[index].split())
                slug = slugify(term)
                if slug and slug not in topics.values():
                    topics[term] = slug
    return list(topics.items())[:limit]


# ------------------------------------------------------------------- browser

_PROBE = """() => {
  const data = document.getElementById('__NEXT_DATA__');
  return {
    title: document.title,
    data: data ? data.textContent : null,
    body: document.body ? document.body.innerText.slice(0, 3000) : '',
  };
}"""
_CHALLENGE = re.compile(r"just a moment|verify you are human|checking your browser", re.I)
_REFUSED = re.compile(r"you have been blocked|access denied|error 10(?:0[0-9]|20)\b", re.I)
_RATE_LIMITED = re.compile(r"error 1015|you are being rate limited|too many requests", re.I)


def page_props(next_data: str) -> dict:
    """props.pageProps out of the __NEXT_DATA__ script text."""
    data = json.loads(next_data)
    props = data.get("props") if isinstance(data, dict) else None
    props = props.get("pageProps") if isinstance(props, dict) else None
    if not isinstance(props, dict):
        raise HttpError("__NEXT_DATA__ has no props.pageProps (layout changed?)")
    return props


@contextmanager
def firefox_pages(settings: Settings, *, wait: float = CHALLENGE_WAIT) -> Iterator[PageLoader]:
    """Headless Firefox on its own profile; yields a loader for one page at a time."""
    profile = settings.data_dir / PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    with headless_firefox(settings, profile_dir=profile) as bidi:
        tab = open_tab(bidi, background=True)

        def load(url: str) -> dict:
            try:
                tab.goto(url)
            except ConnectionLost:
                raise
            except BidiError as exc:
                raise HttpError(f"Firefox could not load the page: {exc}") from None
            deadline = time.monotonic() + wait
            seen: dict = {}
            while True:
                try:
                    seen = tab.main.call(_PROBE) or {}
                except ConnectionLost:
                    raise
                except BidiError:
                    seen = {}                    # the check navigates away mid-call; look again
                if seen.get("data"):
                    return page_props(seen["data"])
                text = f"{seen.get('title') or ''}\n{seen.get('body') or ''}"
                if _RATE_LIMITED.search(text):
                    raise RateLimited("hiring.cafe (Cloudflare) says too many requests")
                if _REFUSED.search(text):
                    raise BotCheck(f"Cloudflare refused headless Firefox ({seen.get('title') or 'no title'})")
                if time.monotonic() >= deadline:
                    if _CHALLENGE.search(text):
                        raise BotCheck(f"Cloudflare's bot check did not clear in headless Firefox "
                                       f"within {wait:.0f}s (page title: {seen.get('title')!r})")
                    raise HttpError(f"no __NEXT_DATA__ after {wait:.0f}s "
                                    f"(page title: {seen.get('title')!r}; layout changed?)")
                time.sleep(1.0)

        try:
            yield load
        finally:
            try:
                tab.close()
            except BidiError:
                pass


# ------------------------------------------------------------------- adapter


class HiringCafeAdapter:
    name = "hiringcafe"

    def __init__(
        self,
        ctx: DiscoverContext,
        *,
        pages: PageSession | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.ctx = ctx
        self.pages = pages
        self.sleep = sleep

    def _firefox(self) -> AbstractContextManager[PageLoader]:
        if self.ctx.settings is None:
            raise HttpError("no workspace settings, so no browser profile directory")
        return firefox_pages(self.ctx.settings)

    def fetch(self, since: datetime | None) -> AdapterResult:
        """Page 1 of each topic's landing page; the first bot-check refusal or 429 ends the run.

        A topic that fails otherwise makes the run partial. Landing pages are
        ranked searches, so a posting missing from one never closes a job.
        """
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        stats: dict = {"complete_snapshot": False, "topics": [], "duplicates": 0,
                       "cluster_duplicates": 0, "not_tried": 0}
        limit = options.get("max_topics", MAX_TOPICS)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_TOPICS:
            return AdapterResult([], "error", f"max_topics must be an integer between 1 and {MAX_TOPICS}",
                                 stats=stats)
        topics = plan_topics(self.ctx.config.tracks.tracks, limit)
        if not topics:
            return AdapterResult([], "error", "No track selects hiringcafe with search_terms", stats=stats)
        interval = max(MIN_INTERVAL, source.min_interval_seconds if source else MIN_INTERVAL)

        by_id: dict[str, NormalizedJob] = {}
        clusters: dict[str, str] = {}             # strict_dedup_cluster_id -> kept source_job_id
        outcomes: list[BoardOutcome] = []
        stopped: str | None = None
        try:
            with (self.pages or self._firefox)() as load:
                last_load: float | None = None
                for index, (topic, slug) in enumerate(topics):
                    if last_load is not None:
                        self.sleep(max(0.0, interval - (time.monotonic() - last_load)))
                    url = PAGE_URL + slug
                    try:
                        props = load(url)
                    except (BotCheck, RateLimited) as exc:
                        outcomes.append(BoardOutcome(
                            topic, "rate_limited" if isinstance(exc, RateLimited) else "error", error=str(exc)))
                        stats["topics"].append({"topic": topic, "url": url, "error": str(exc)})
                        stats["not_tried"] = len(topics) - index - 1
                        stopped = str(exc)
                        break
                    except (HttpError, ValueError) as exc:
                        outcomes.append(BoardOutcome(topic, "error", error=f"{type(exc).__name__}: {exc}"))
                        stats["topics"].append({"topic": topic, "url": url, "error": str(exc)})
                        continue
                    finally:
                        last_load = time.monotonic()
                    retrieved = now_iso()
                    found, page_stats = parse_hiringcafe(props, topic)
                    stats["topics"].append({"topic": topic, "url": url, **page_stats})
                    if page_stats["ssr_error"]:
                        outcomes.append(BoardOutcome(topic, "error",
                                                     error=f"hiring.cafe reported ssrError: {page_stats['ssr_error']}"))
                        continue
                    if page_stats["hits"] is None:
                        outcomes.append(BoardOutcome(topic, "error", error="page has no ssrHits (layout changed?)"))
                        continue
                    kept = 0
                    for job in found:
                        job.observed_at = retrieved
                        cluster = job.raw.get("cluster")
                        if job.source_job_id in by_id:
                            stats["duplicates"] += 1
                            continue
                        if cluster and cluster in clusters:
                            stats["cluster_duplicates"] += 1
                            continue
                        by_id[job.source_job_id] = job
                        if cluster:
                            clusters[cluster] = job.source_job_id
                        kept += 1
                    if page_stats["bad_hits"]:
                        outcomes.append(BoardOutcome(
                            topic, "partial" if found else "error", kept,
                            error=f"{page_stats['bad_hits']} malformed postings on landing page"))
                    else:
                        outcomes.append(BoardOutcome(topic, "ok", kept))
        except (BrowserError, BidiError, HttpError) as exc:   # no Firefox, or it died mid-run
            outcomes.append(BoardOutcome("headless Firefox", "error", error=f"{type(exc).__name__}: {exc}"))

        status, error = summarize_boards(outcomes)
        jobs = list(by_id.values())
        if jobs and status in ("error", "rate_limited"):
            status = "partial"
        if stopped and stats["not_tried"]:
            error = f"{error}; stopped with {stats['not_tried']} of {len(topics)} topics not tried"
        stats.update({"planned_topics": len(topics), "kept": len(jobs)})
        return AdapterResult(jobs, status, error, stats=stats, boards=outcomes)

"""Public YC internship discovery: directory candidates require current detail evidence."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from kitty.core.discovery.base import AdapterResult, NormalizedJob
from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.discovery.public_pages import PublicPage, PublicPages
from kitty.core.discovery.text import html_to_text, season_from

DIRECTORIES = ["https://www.ycombinator.com/internships",
               "https://www.ycombinator.com/jobs/role/software-engineer/intern"]


class _DataPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.data: dict = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        value = dict(attrs).get("data-page")
        if value:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                self.data = parsed


def page_props(page: PublicPage) -> dict:
    parser = _DataPage()
    parser.feed(page.text)
    props = parser.data.get("props")
    if not isinstance(props, dict):
        raise ValueError(f"YC public data-page missing at {page.url}")
    return props


def _yc_url(value: str) -> str:
    url = urljoin(DIRECTORIES[0], value)
    if urlsplit(url).hostname != "www.ycombinator.com" or urlsplit(url).scheme != "https":
        raise ValueError("YC listing points outside public YC HTTPS")
    return url


def parse_detail(page: PublicPage, expected_id: str, *, now: datetime,
                 max_inactive_days: int = 180) -> tuple[NormalizedJob | None, str]:
    """Keep explicit closure evidence, without inferring closure from missing data."""
    props = page_props(page)
    job = props.get("job")
    if not isinstance(job, dict) or str(job.get("id")) != expected_id:
        raise ValueError("YC detail job missing or mismatches directory id")
    title = str(job.get("title") or "")
    company = str(job.get("companyName") or "")
    description = html_to_text(job.get("description")) or ""
    cycles = re.findall(r"\b(spring|summer|fall|winter)\s+(20\d{2})\b", title, re.I)
    cycles += [(season, year) for year, season in
               re.findall(r"\b(20\d{2})\s+(spring|summer|fall|winter)\b", title, re.I)]
    ends = [(int(year), {"spring": 5, "summer": 8, "fall": 12, "winter": 3}[season.lower()])
            for season, year in cycles]
    state = None
    if job.get("closed") or job.get("isClosed") or re.search(
        r"(?:no longer|not currently) (?:accepting|hiring)|position (?:is |has been )?(?:closed|filled)",
        description, re.I,
    ):
        state = "closed"
    elif ends and max(ends) < (now.year, now.month):
        state = "past_cycle"
    if state:
        return NormalizedJob(
            source="yc_internships", source_job_id=expected_id, company=company,
            title=title, url=page.url, closed=True,
            raw={"record_kind": "internship", "job": job, "evidence_url": page.url,
                 "retrieved_at": page.retrieved_at, "closure_reason": state},
        ), state
    if str(job.get("type", "")).lower() != "internship":
        return None, "not_internship"
    if job.get("isIncomplete"):
        return None, "incomplete"
    location = str(job.get("location") or "")
    if not re.search(r"\b(?:US|USA|United States)\b", location, re.I):
        return None, "US_location_unconfirmed"
    if not description or not job.get("applyUrl") or props.get("showDirectApply") is not True:
        return None, "active_application_unconfirmed"
    active = str(job.get("lastActive") or "").lower()
    age = re.search(r"(\d+|an?|one)\s+(minute|hour|day|week|month|year)", active)
    if age:
        count = int(age[1]) if age[1].isdigit() else 1
        days = count * {"minute": 1/1440, "hour": 1/24, "day": 1, "week": 7, "month": 31, "year": 366}[age[2]]
        if days > max_inactive_days:
            return None, "stale_last_active"
    if not title or not company:
        raise ValueError("YC detail has no company or title")
    qualification = "\n".join(f"{key}: {job[key]}" for key in ("salaryRange", "minSchoolYear", "minExperience", "visa") if job.get(key))
    school_year = str(job.get("minSchoolYear") or "").lower()
    for standing in ("junior", "senior"):
        if school_year.startswith(standing):
            qualification += f"\nRequired minimum: {standing} standing ({job['minSchoolYear']})."
            break
    return NormalizedJob(
        source="yc_internships", source_job_id=expected_id, company=company,
        title=title, url=page.url, apply_url=job["applyUrl"], country="US",
        location=location, is_remote="remote" in location.lower(),
        description=qualification + "\n" + description,
        season=season_from(title + " " + description),
        raw={"record_kind": "internship", "job": job, "evidence_url": page.url,
             "retrieved_at": page.retrieved_at, "last_active": active,
             "eligibility_note": None if age else
                 "YC lists an application link but no posting activity date; verify recency and qualifications."},
    ), "open"


class YCInternshipsAdapter:
    name = "yc_internships"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        source = self.ctx.config.sources.sources.get(self.name)
        options = source.options if source else {}
        max_details = max(1, min(100, int(options.get("max_details", 30))))
        max_age = max(1, min(366, int(options.get("max_inactive_days", 180))))
        urls = options.get("directory_urls", DIRECTORIES)
        jobs, errors, evidence = [], [], []
        candidates: dict[str, dict] = {}
        limited = False
        with PublicPages(self.ctx.http_for(self.name)) as pages:
            for url in list(urls)[:5]:
                try:
                    page = pages.fetch(_yc_url(url))
                    rows = page_props(page).get("jobPostings")
                    if not isinstance(rows, list):
                        raise ValueError("YC jobPostings array missing")
                    for row in rows:
                        if isinstance(row, dict) and str(row.get("type", "")).lower() == "internship" and row.get("id") and row.get("url"):
                            candidates[str(row["id"])] = row
                except (HttpError, ValueError) as exc:
                    limited |= isinstance(exc, RateLimited)
                    errors.append({"url": url, "error": str(exc)})
            for identity, row in list(candidates.items())[:max_details]:
                url = str(row["url"])
                try:
                    page = pages.fetch(_yc_url(url))
                    job, state = parse_detail(page, identity, now=datetime.now(timezone.utc), max_inactive_days=max_age)
                    evidence.append({"url": page.url, "state": state})
                    if job:
                        jobs.append(job)
                except (HttpError, ValueError) as exc:
                    limited |= isinstance(exc, RateLimited)
                    errors.append({"url": url, "error": str(exc)})
        status = ("partial" if evidence else "rate_limited" if limited else "error") if errors else "ok"
        return AdapterResult(jobs, status, "; ".join(f"{e['url']}: {e['error']}" for e in errors) or None,
                             stats={"complete_snapshot": False, "candidates": len(candidates),
                                    "truncated": len(candidates) > max_details or len(urls) > 5,
                                    "evidence": evidence, "errors": errors})

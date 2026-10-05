"""Adapter for the monthly Hacker News "Ask HN: Who is hiring?" thread.

Uses the HN Algolia API (public, no key): one search for the newest story by
the `whoishiring` account, then one request for that story's comment tree.
Each top-level comment is one company's post, conventionally starting with a
line like "Company | Role | Location | Full-time | https://...".

Only posts that offer internships, co-ops or student roles are kept: the first
line reads as one (`is_internship_title`), or the body names an intern role.
The body check skips the bare word "student", which turns up in posts that are
not hiring students ("fair for every student"). Posts whose first line has no
"|" cannot be split into company and role, so they are skipped and counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from applyai.core.discovery.base import AdapterResult, NormalizedJob
from applyai.core.discovery.context import DiscoverContext
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.discovery.text import html_to_text, is_internship_title, iso_to_utc, season_from, us_location

SEARCH_URL = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&hitsPerPage=10"
)
ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"
THREAD_PREFIX = "ask hn: who is hiring?"

#: Intern words looked for in a post's body (not "student": see module docstring).
_BODY_INTERN = re.compile(r"\b(intern|interns|internship|internships|co-?op|co-?ops)\b", re.I)
_NOT_INTERN = re.compile(r"\b(internal|international)\b", re.I)
_URL = re.compile(r"https?://\S+")
_WORK_MODE = re.compile(r"\b(remote|onsite|on-site|hybrid|in[- ]person)\b", re.I)
_EMPLOYMENT = re.compile(
    r"^\s*(full[- ]?time|part[- ]?time|ft|pt|contract(or)?|permanent|interns?|internships?|"
    r"visa\b.*|no visa.*|equity.*)\s*(/.*)?$",
    re.I,
)
_PAY = re.compile(r"[$€£]|\b\d+\s*k\b", re.I)


@dataclass
class FirstLine:
    """The pieces of a post's "Company | Role | Location | ..." line."""

    company: str
    role: str
    location: str | None
    intern_segment: str | None   # the segment that names the internship, if any


def _clean_company(segment: str) -> str:
    """'*Fastly' -> 'Fastly'; 'Senzing (entity resolution SDK)' -> 'Senzing'."""
    text = _URL.sub(" ", segment).strip(" *_")
    text = re.sub(r"\s*\([^)]*\)", "", text)
    return re.sub(r"\s+", " ", text).strip(" -–,")


def _is_location(segment: str) -> bool:
    return bool(_WORK_MODE.search(segment)) or us_location(segment) is not None


def parse_first_line(line: str) -> FirstLine | None:
    """Split a post's first line into company, role and location. None if it has no '|'."""
    segments = [s.strip(" *_") for s in line.split("|")]
    segments = [s for s in segments if s]
    if len(segments) < 2:
        return None
    company = _clean_company(segments[0])
    if not company:
        return None

    role = None
    locations: list[str] = []
    intern_segment = None
    for segment in segments[1:]:
        text = _URL.sub(" ", segment).strip()
        if not text:
            continue
        if is_internship_title(text) and intern_segment is None:
            intern_segment = text
        if _is_location(text):
            locations.append(text)
        elif _EMPLOYMENT.match(text) or _PAY.search(text):
            continue
        elif role is None:
            role = text
    if role is None and intern_segment is not None:
        role = intern_segment
    return FirstLine(company, role or "Multiple roles", "; ".join(locations) or None, intern_segment)


def _title(first: FirstLine) -> str:
    """The posting title: the role, marked when the role alone does not say internship.

    "(internship)" means the first line offers one; "(internships mentioned in
    post)" means only the body does, so the role named may not be the intern one.
    """
    if is_internship_title(first.role):
        return first.role
    if first.intern_segment:
        return f"{first.role} (internship)"
    return f"{first.role} (internships mentioned in post)"


def _offers_internship(first_line: str, body: str) -> bool:
    return is_internship_title(first_line) or bool(_BODY_INTERN.search(_NOT_INTERN.sub(" ", body)))


def parse_thread(thread: dict) -> tuple[list[NormalizedJob], dict]:
    """A "Who is hiring?" item tree to NormalizedJobs (intern posts only), plus stats."""
    stats = {"thread_id": thread.get("id"), "thread_title": thread.get("title"),
             "comments": 0, "deleted": 0, "intern_posts": 0, "not_intern": 0, "unparseable": 0}
    jobs: list[NormalizedJob] = []
    for comment in thread.get("children") or []:
        stats["comments"] += 1
        text = html_to_text(comment.get("text"))
        if not text:
            stats["deleted"] += 1
            continue
        first_line = text.split("\n", 1)[0]
        if not _offers_internship(first_line, text):
            stats["not_intern"] += 1
            continue
        first = parse_first_line(first_line)
        if first is None:
            stats["unparseable"] += 1
            continue
        stats["intern_posts"] += 1
        comment_id = str(comment["id"])
        jobs.append(NormalizedJob(
            source="hackernews",
            source_job_id=comment_id,
            company=first.company,
            title=_title(first),
            url=f"https://news.ycombinator.com/item?id={comment_id}",
            location=first.location,
            is_remote=True if re.search(r"\bremote\b", first_line, re.I) else None,
            description=text,
            posted_at=iso_to_utc(comment.get("created_at")),
            season=season_from(first_line),
            raw={"id": comment["id"], "author": comment.get("author"),
                 "created_at": comment.get("created_at"), "first_line": first_line,
                 "thread_id": thread.get("id")},
        ))
    return jobs, stats


def latest_thread_id(search: dict) -> int | None:
    """The newest "Ask HN: Who is hiring?" story id in an Algolia search answer."""
    hits = [h for h in search.get("hits", []) if (h.get("title") or "").lower().startswith(THREAD_PREFIX)]
    if not hits:
        return None
    newest = max(hits, key=lambda h: h.get("created_at_i") or 0)
    return int(newest["objectID"])


class HackerNewsAdapter:
    name = "hackernews"

    def __init__(self, ctx: DiscoverContext) -> None:
        self.ctx = ctx

    def fetch(self, since: datetime | None) -> AdapterResult:
        """Every intern post in the newest thread. Old threads age out through close_missing."""
        http = self.ctx.http_for(self.name)
        try:
            thread_id = latest_thread_id(http.get_json(SEARCH_URL))
            if thread_id is None:
                return AdapterResult([], "error", "no 'Ask HN: Who is hiring?' story found by whoishiring")
            thread = http.get_json(ITEM_URL.format(id=thread_id))
        except RateLimited as exc:
            return AdapterResult([], "rate_limited", str(exc))
        except (HttpError, ValueError) as exc:
            return AdapterResult([], "error", f"{type(exc).__name__}: {exc}")
        jobs, stats = parse_thread(thread)
        for job in jobs:
            job.observed_at = getattr(http, "retrieved_at", None)
        stats.update(requests=http.requests_made, cache_hits=http.cache_hits)
        return AdapterResult(jobs, "ok", stats=stats)

"""Candidate firms named in the newest Hacker News "Ask HN: Who is hiring?" thread.

Reuses the discovery adapter's pieces: the same two Algolia URLs (so the
cached responses from `applyai discover` are reused, not fetched again), the
same newest-thread pick and the same "Company | Role | Location" first-line
parser. Unlike discovery, every post counts, not only intern posts: a firm
hiring engineers today is worth watching for next summer's internships.
"""

from __future__ import annotations

import re

from applyai.core.discovery.hackernews import ITEM_URL, SEARCH_URL, latest_thread_id, parse_first_line
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.discovery.text import html_to_text
from applyai.core.finder.base import CandidateBatch, FinderContext, RawCandidate, SourceText

#: Same name as the discovery source, so both share one cache and one pacing rule.
NAME = "hackernews"
_URL = re.compile(r"https?://[^\s|)]+")
_INTERN = re.compile(r"\b(intern|interns|internship|internships|co-?op)\b", re.I)


def post_candidate(comment: dict) -> RawCandidate | None:
    """One top-level comment to a RawCandidate; None when deleted or not 'Company | ...'."""
    text = html_to_text(comment.get("text"))
    if not text:
        return None
    first_line = text.split("\n", 1)[0]
    first = parse_first_line(first_line)
    if first is None:
        return None
    link = _URL.search(first_line)
    interns = " (mentions internships)" if _INTERN.search(text) else ""
    return RawCandidate(
        name=first.company,
        texts=[SourceText(NAME, "HN post", text)],
        website=link.group(0) if link else None,
        evidence=[f"{NAME}: https://news.ycombinator.com/item?id={comment.get('id')}{interns}"],
    )


def parse_companies(thread: dict) -> tuple[list[RawCandidate], dict]:
    """Every parseable post in a thread to candidates, plus counts."""
    stats = {"thread_id": thread.get("id"), "thread_title": thread.get("title"),
             "posts": 0, "deleted_or_unparseable": 0}
    candidates = []
    for comment in thread.get("children") or []:
        stats["posts"] += 1
        candidate = post_candidate(comment)
        if candidate is None:
            stats["deleted_or_unparseable"] += 1
        else:
            candidates.append(candidate)
    return candidates, stats


class HackerNewsCompaniesAdapter:
    """Firms posting in this month's "Who is hiring?" thread."""

    name = NAME

    def __init__(self, ctx: FinderContext) -> None:
        self.ctx = ctx

    def fetch(self) -> CandidateBatch:
        http = self.ctx.http_for(self.name)
        try:
            thread_id = latest_thread_id(http.get_json(SEARCH_URL))
            if thread_id is None:
                return CandidateBatch([], "error", "no 'Ask HN: Who is hiring?' story found by whoishiring")
            thread = http.get_json(ITEM_URL.format(id=thread_id))
        except RateLimited as exc:
            return CandidateBatch([], "rate_limited", str(exc))
        except (HttpError, ValueError) as exc:
            return CandidateBatch([], "error", f"{type(exc).__name__}: {exc}")
        candidates, stats = parse_companies(thread)
        stats.update(requests=http.requests_made, cache_hits=http.cache_hits)
        return CandidateBatch(candidates, "ok", stats=stats)

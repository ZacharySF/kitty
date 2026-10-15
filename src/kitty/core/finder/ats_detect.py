"""Find a proposed firm's public job board by trying likely slugs on the boards Stage 2 polls.

For each firm a few slugs are tried (the source's own identifier first, then
ones made from the name and the website's domain), on Greenhouse, Lever,
Ashby, SmartRecruiters and Workable, stopping at the first hit. A board counts only when
it lists at least one posting and, where the API names the company, that name
matches the firm: a slug like "glasgow" can belong to somebody else. At most
MAX_SLUGS x 5 requests per firm, each host paced and every answer cached.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from kitty.core.discovery.http import HttpError, PoliteHttp, RateLimited
from kitty.core.finder.base import RawCandidate
from kitty.normalize import norm_company

MAX_SLUGS = 3


@dataclass(frozen=True)
class AtsHit:
    kind: str
    slug: str
    board_url: str
    postings: int


@dataclass(frozen=True)
class Detection:
    """The board found (or None) and one evidence line per attempt that told us something."""

    hit: AtsHit | None
    evidence: list[str]
    rate_limited: bool = False


def _greenhouse(http: PoliteHttp, slug: str) -> tuple[int, str | None]:
    payload = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = payload.get("jobs") or []
    return len(jobs), (jobs[0].get("company_name") if jobs else None)


def _lever(http: PoliteHttp, slug: str) -> tuple[int, str | None]:
    payload = http.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=20")
    return (len(payload) if isinstance(payload, list) else 0), None


def _ashby(http: PoliteHttp, slug: str) -> tuple[int, str | None]:
    payload = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return len(payload.get("jobs") or []), None


def _smartrecruiters(http: PoliteHttp, slug: str) -> tuple[int, str | None]:
    """SmartRecruiters answers 200 with 0 postings for any slug, so the company name decides."""
    payload = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=10")
    content = payload.get("content") or []
    name = ((content[0].get("company") or {}).get("name")) if content else None
    return int(payload.get("totalFound") or 0), name


def _workable(http: PoliteHttp, slug: str) -> tuple[int, str | None]:
    """Workable's widget names the account, so a slug owned by another firm is caught."""
    payload = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    return len(payload.get("jobs") or []), payload.get("name")


#: kind -> (probe returning (postings, company name the API states), public board URL).
PROBES: dict[str, tuple[Callable[[PoliteHttp, str], tuple[int, str | None]], str]] = {
    "greenhouse": (_greenhouse, "https://job-boards.greenhouse.io/{slug}"),
    "lever": (_lever, "https://jobs.lever.co/{slug}"),
    "ashby": (_ashby, "https://jobs.ashbyhq.com/{slug}"),
    "smartrecruiters": (_smartrecruiters, "https://careers.smartrecruiters.com/{slug}"),
    "workable": (_workable, "https://apply.workable.com/{slug}/"),
}


def slug_guesses(candidate: RawCandidate) -> list[str]:
    """Likely board slugs, best first, at most MAX_SLUGS: hints, name joined, name hyphenated, domain."""
    words = norm_company(candidate.name).split()
    guesses = [*candidate.slug_hints, "".join(words), "-".join(words)]
    if candidate.website:
        host = urlsplit(candidate.website if "//" in candidate.website else f"//{candidate.website}").netloc
        labels = [p for p in host.lower().split(".") if p not in ("www", "")]
        if len(labels) >= 2:
            guesses.append(labels[-2])
    cleaned = [re.sub(r"[^a-z0-9-]", "", g.lower()) for g in guesses]
    return list(dict.fromkeys(g for g in cleaned if g))[:MAX_SLUGS]


def names_match(firm: str, stated: str | None) -> bool:
    """True when the API states no name, or one name contains the other once normalized."""
    if not stated:
        return True
    a, b = norm_company(firm).replace(" ", ""), norm_company(stated).replace(" ", "")
    return bool(a) and bool(b) and (a in b or b in a)


def detect_ats(http: PoliteHttp, candidate: RawCandidate) -> Detection:
    """Try each slug guess on each board until one lists postings under this firm's name."""
    evidence: list[str] = []
    for slug in slug_guesses(candidate):
        for kind, (probe, board) in PROBES.items():
            try:
                postings, stated = probe(http, slug)
            except RateLimited:
                evidence.append(f"{kind} rate limited us; stopped looking")
                return Detection(None, evidence, rate_limited=True)
            except (HttpError, ValueError, AttributeError):
                continue                                   # 404: no board under this slug
            if postings == 0:
                if kind != "smartrecruiters":
                    evidence.append(f"{kind} board '{slug}' exists but lists 0 postings")
                continue
            if not names_match(candidate.name, stated):
                evidence.append(f"{kind} board '{slug}' belongs to {stated!r}, not this firm")
                continue
            hit = AtsHit(kind, slug, board.format(slug=slug), postings)
            note = "" if stated else " (API names no company: check it is theirs)"
            evidence.append(f"{kind} board '{slug}' lists {postings} posting(s){note}")
            return Detection(hit, evidence)
    tried = ", ".join(slug_guesses(candidate)) or "none"
    evidence.append(f"no board on {', '.join(PROBES)} for slugs: {tried}")
    return Detection(None, evidence)

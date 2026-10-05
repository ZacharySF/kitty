"""Candidate firms from conference exhibitor and sponsor lists (config/conferences.yaml).

The file is optional and mine to write. Each conference names the skills.yaml
areas its sponsors belong to, and either lists company names directly or gives
a sponsor-page URL. Sponsor pages are almost always a wall of logos, so names
are read from the logos' alt text ("NVIDIA logo" -> "NVIDIA"). A page whose
robots.txt disallows us is skipped and reported, never fetched.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from applyai.config import Conference
from applyai.core.discovery.base import BoardOutcome, summarize_boards
from applyai.core.discovery.http import USER_AGENT, HttpError, PoliteHttp, RateLimited
from applyai.core.finder.base import CandidateBatch, FinderContext, RawCandidate, SourceText
from applyai.normalize import norm_company

NAME = "conferences"
_IMG_ALT = re.compile(r"<img\b[^>]*?\balt\s*=\s*\"([^\"]*)\"", re.I)
_LOGO_WORD = re.compile(r"\s*\b(logo|logotype)\b\s*$", re.I)
#: Alt texts that name the page furniture, not a sponsor.
_NOT_A_FIRM = {"", "logo", "sponsor", "sponsors", "banner", "image", "icon", "menu", "search", "close"}


def sponsor_names(page: str, conference_name: str) -> list[str]:
    """Company names from a sponsor page's logo alt texts, once each, in page order."""
    names: dict[str, str] = {}
    own = norm_company(conference_name)
    for alt in _IMG_ALT.findall(page):
        name = re.sub(r"\s+", " ", _LOGO_WORD.sub("", html.unescape(alt))).strip(" -|,")
        # "Mojo Visionlogo": the space before "logo" was lost on the page.
        if name.lower().endswith("logo") and len(name) > 4:
            name = name[:-4].strip()
        key = norm_company(name)
        if name.lower() in _NOT_A_FIRM or not key or key == own:
            continue
        names.setdefault(key, name)
    return list(names.values())


def robots_allows(http: PoliteHttp, url: str) -> bool:
    """True when the site's robots.txt lets us fetch `url` (a missing robots.txt allows it)."""
    parts = urlsplit(url)
    parser = RobotFileParser()
    try:
        text = http.get_bytes(f"{parts.scheme}://{parts.netloc}/robots.txt").decode("utf-8", "replace")
    except RateLimited:
        raise
    except HttpError as exc:
        if str(exc).startswith("HTTP 4"):      # no robots.txt: everything allowed
            return True
        raise
    parser.parse(text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def conference_candidates(conference: Conference, names: list[str]) -> list[RawCandidate]:
    """One RawCandidate per sponsor name, vouched for in the conference's areas."""
    where = conference.url or "config/conferences.yaml"
    return [
        RawCandidate(
            name=name,
            texts=[SourceText(NAME, "sponsor of", conference.name)],
            evidence=[f"{NAME}: exhibits at or sponsors {conference.name} ({where})"],
            hint_areas=list(conference.areas),
        )
        for name in names
    ]


class ConferencesAdapter:
    """Sponsors and exhibitors of the conferences in config/conferences.yaml."""

    name = NAME

    def __init__(self, ctx: FinderContext) -> None:
        self.ctx = ctx

    def fetch(self) -> CandidateBatch:
        conferences = self.ctx.config.conferences.conferences
        if not conferences:
            return CandidateBatch([], "ok", stats={"conferences": 0,
                                                    "note": "no conferences in config/conferences.yaml"})
        http = self.ctx.http_for(self.name)
        candidates: list[RawCandidate] = []
        outcomes: list[BoardOutcome] = []
        for conference in conferences:
            names = list(conference.companies)
            outcome = BoardOutcome(conference.name, "ok")
            if conference.url:
                try:
                    if robots_allows(http, conference.url):
                        page = http.get_bytes(conference.url).decode("utf-8", "replace")
                        names.extend(sponsor_names(page, conference.name))
                    else:
                        outcome = BoardOutcome(conference.name, "error", error="robots.txt disallows the page")
                except RateLimited as exc:
                    outcome = BoardOutcome(conference.name, "rate_limited", error=str(exc))
                except HttpError as exc:
                    outcome = BoardOutcome(conference.name, "error", error=str(exc))
            found = conference_candidates(conference, list(dict.fromkeys(names)))
            outcome.count = len(found)
            outcomes.append(outcome)
            candidates.extend(found)
        status, error = summarize_boards(outcomes)
        stats = {"conferences": {o.board: o.count for o in outcomes},
                 "requests": http.requests_made, "cache_hits": http.cache_hits}
        return CandidateBatch(candidates, status, error, stats)

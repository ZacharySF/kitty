"""Candidate firms from the yc-oss mirror of Y Combinator's company directory.

https://yc-oss.github.io/api/ is a static JSON export, rebuilt daily. One
request fetches every YC company that is hiring right now (about 1,500
companies, 2.5 MB), cached for a day. Only active or public companies with a
US or remote region are kept; the owner works in the US.
"""

from __future__ import annotations

from kitty.core.discovery.http import HttpError, RateLimited
from kitty.core.finder.base import CandidateBatch, FinderContext, RawCandidate, SourceText

HIRING_URL = "https://yc-oss.github.io/api/companies/hiring.json"
NAME = "yc-oss"

#: A company with any of these regions can hire someone in the US.
_US_REGIONS = {"United States of America", "America / Canada", "Remote", "Fully Remote", "Partly Remote"}
_LIVE_STATUSES = {"Active", "Public"}


def yc_candidate(company: dict) -> RawCandidate | None:
    """One yc-oss company record to a RawCandidate; None when it is not a fit to look at."""
    name = (company.get("name") or "").strip()
    if not name or company.get("status") not in _LIVE_STATUSES:
        return None
    if not _US_REGIONS.intersection(company.get("regions") or []):
        return None
    texts = []
    description = " ".join(filter(None, [company.get("one_liner"), company.get("long_description")]))
    if description.strip():
        texts.append(SourceText(NAME, "description", description.strip()))
    tags = [*(company.get("tags") or []), *(company.get("industries") or [])]
    if tags:
        texts.append(SourceText(NAME, "tags", ", ".join(dict.fromkeys(tags))))
    page = company.get("url") or ""
    return RawCandidate(
        name=name,
        texts=texts,
        website=company.get("website") or None,
        evidence=[f"{NAME}: {page} ({company.get('batch') or 'batch unknown'}, "
                  f"team {company.get('team_size') or '?'}, {company.get('all_locations') or 'location unknown'})"],
        slug_hints=[company["slug"]] if company.get("slug") else [],
    )


def parse_hiring(payload: list) -> tuple[list[RawCandidate], dict]:
    """The hiring.json list to candidates, plus counts of what was dropped and why."""
    stats = {"companies": len(payload), "not_us_or_inactive": 0}
    candidates = []
    for company in payload:
        candidate = yc_candidate(company)
        if candidate is None:
            stats["not_us_or_inactive"] += 1
        else:
            candidates.append(candidate)
    return candidates, stats


class YcAdapter:
    """YC companies that are hiring, with their tags and descriptions."""

    name = NAME

    def __init__(self, ctx: FinderContext) -> None:
        self.ctx = ctx

    def fetch(self) -> CandidateBatch:
        http = self.ctx.http_for(self.name)
        try:
            payload = http.get_json(HIRING_URL)
        except RateLimited as exc:
            return CandidateBatch([], "rate_limited", str(exc))
        except (HttpError, ValueError) as exc:
            return CandidateBatch([], "error", f"{type(exc).__name__}: {exc}")
        if not isinstance(payload, list):
            return CandidateBatch([], "error", "hiring.json is not a list: the format changed")
        candidates, stats = parse_hiring(payload)
        stats.update(requests=http.requests_made, cache_hits=http.cache_hits)
        return CandidateBatch(candidates, "ok", stats=stats)

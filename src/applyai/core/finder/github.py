"""Candidate firms from GitHub: organizations that own popular repos in my skill areas.

Unauthenticated GitHub allows 10 search requests a minute and 60 other API
requests an hour, so a run makes at most MAX_QUERIES searches (one per
skills.yaml area, its first three keywords OR-ed) and at most MAX_ORG_LOOKUPS
organization lookups (orgs with the most matching repos first, firms already
known skipped), all cached for a week. GitHub signals its rate limit
with 403 as well as 429; both stop the adapter for the run and are reported
as rate_limited, never retried.

Most organizations behind popular hardware repos are open-source projects or
universities, not employers. An organization is kept only when its profile
names a website and does not describe itself as a university, lab, foundation
or community.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

from applyai.config import SkillArea
from applyai.core.discovery.http import HttpError, RateLimited
from applyai.core.finder.base import CandidateBatch, FinderContext, RawCandidate, SourceText
from applyai.normalize import norm_company

NAME = "github"
#: The search endpoint and the rest of the API are paced and cached separately.
SEARCH_CLIENT = "github-search"
API_CLIENT = "github-api"
SEARCH_URL = "https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=50"
ORG_URL = "https://api.github.com/orgs/{login}"
MAX_QUERIES = 6
MAX_ORG_LOOKUPS = 20
KEYWORDS_PER_QUERY = 3
MIN_STARS = 30
_NOT_EMPLOYER = re.compile(
    r"\b(universit\w*|college|school|lab|labs|laboratory|institute|foundation|community|"
    r"consortium|students?|course|research group)\b", re.I,
)


@dataclass
class OrgRepos:
    """The repos one organization showed up with across this run's searches."""

    login: str
    stars: int = 0
    repos: list[dict] = field(default_factory=list)


def search_query(area: SkillArea) -> str:
    """The search string for one area: its first keywords OR-ed, popular non-fork repos."""
    terms = " OR ".join(f'"{keyword}"' for keyword in area.keywords[:KEYWORDS_PER_QUERY])
    return f"{terms} stars:>={MIN_STARS} fork:false archived:false"


def is_rate_limit(exc: HttpError) -> bool:
    """GitHub answers an exhausted rate limit with 403 (or 429, raised as RateLimited)."""
    return isinstance(exc, RateLimited) or str(exc).startswith("HTTP 403")


def collect_orgs(payload: dict, orgs: dict[str, OrgRepos]) -> None:
    """Add the organization-owned repos of one search answer to `orgs` (keyed by login)."""
    for repo in payload.get("items") or []:
        owner = repo.get("owner") or {}
        if owner.get("type") != "Organization" or not owner.get("login"):
            continue
        entry = orgs.setdefault(owner["login"].lower(), OrgRepos(owner["login"]))
        if any(r.get("full_name") == repo.get("full_name") for r in entry.repos):
            continue
        entry.stars += int(repo.get("stargazers_count") or 0)
        entry.repos.append(repo)


def org_candidate(profile: dict, repos: OrgRepos) -> RawCandidate | None:
    """An organization profile plus its repos to a RawCandidate; None when it is not an employer."""
    name = (profile.get("name") or profile.get("login") or "").strip()
    blog = (profile.get("blog") or "").strip()
    description = (profile.get("description") or "").strip()
    if not name or not blog or _NOT_EMPLOYER.search(f"{name} {description}"):
        return None
    website = blog if blog.startswith("http") else f"https://{blog}"
    repo_text = "; ".join(
        f"{r.get('name')}: {r.get('description') or ''} [{', '.join(r.get('topics') or [])}]"
        for r in repos.repos
    )
    texts = [SourceText(NAME, "repos", repo_text)]
    if description:
        texts.insert(0, SourceText(NAME, "org description", description))
    top = max(repos.repos, key=lambda r: r.get("stargazers_count") or 0)
    return RawCandidate(
        name=name,
        texts=texts,
        website=website,
        evidence=[f"{NAME}: https://github.com/{repos.login} ({len(repos.repos)} matching repo(s), "
                  f"top {top.get('full_name')} with {top.get('stargazers_count')} stars)"],
        slug_hints=[repos.login.lower()],
    )


class GithubAdapter:
    """Organizations with popular public repos matching my skills.yaml areas."""

    name = NAME

    def __init__(self, ctx: FinderContext) -> None:
        self.ctx = ctx

    def fetch(self) -> CandidateBatch:
        areas = [a for a in self.ctx.config.skills.areas if a.keywords and a.per_run_quota > 0]
        search = self.ctx.http_for(SEARCH_CLIENT)
        api = self.ctx.http_for(API_CLIENT)
        stats: dict = {"queries": 0, "orgs_seen": 0, "orgs_looked_up": 0, "not_employer": 0,
                       "areas_not_searched": [a.name for a in areas[MAX_QUERIES:]]}
        problems: list[str] = []
        limited = False

        orgs: dict[str, OrgRepos] = {}
        for area in areas[:MAX_QUERIES]:
            try:
                collect_orgs(search.get_json(SEARCH_URL.format(query=quote(search_query(area)))), orgs)
                stats["queries"] += 1
            except HttpError as exc:
                problems.append(f"search {area.name}: {exc}")
                if is_rate_limit(exc):
                    limited = True
                    break
            except ValueError as exc:
                problems.append(f"search {area.name}: bad JSON: {exc}")
        stats["orgs_seen"] = len(orgs)

        # Orgs that matched with several repos first; known firms are not worth a lookup.
        fresh = [o for o in orgs.values() if norm_company(o.login) not in self.ctx.known]
        stats["orgs_already_known"] = len(orgs) - len(fresh)
        candidates = []
        ranked = sorted(fresh, key=lambda o: (-len(o.repos), -o.stars, o.login.lower()))
        for entry in ranked[:MAX_ORG_LOOKUPS]:
            if limited:
                break
            try:
                profile = api.get_json(ORG_URL.format(login=entry.login))
            except HttpError as exc:
                problems.append(f"org {entry.login}: {exc}")
                limited = is_rate_limit(exc)
                continue
            except ValueError as exc:
                problems.append(f"org {entry.login}: bad JSON: {exc}")
                continue
            stats["orgs_looked_up"] += 1
            candidate = org_candidate(profile, entry)
            if candidate is None:
                stats["not_employer"] += 1
            else:
                candidates.append(candidate)

        stats.update(requests=search.requests_made + api.requests_made,
                     cache_hits=search.cache_hits + api.cache_hits)
        return CandidateBatch(candidates, *self._status(stats, problems, limited), stats=stats)

    @staticmethod
    def _status(stats: dict, problems: list[str], limited: bool) -> tuple[str, str | None]:
        """ok, partial (some calls failed), rate_limited or error (nothing worked)."""
        if not problems:
            return "ok", None
        detail = "; ".join(problems[:4]) + (f"; and {len(problems) - 4} more" if len(problems) > 4 else "")
        if stats["queries"] == 0:
            return ("rate_limited" if limited else "error"), detail
        return "partial", detail

"""Bounded, favorites-first live checks of stored postings, never whole boards.

Only exact authoritative posting details verify availability. Not-found/gone
responses from those detail APIs verify removal; other HTTP failures, search
absence, login pages and unexpected redirects remain uncertainty. Workday CXS and
Oracle Candidate Experience checks request one exact detail, never dropdowns or
board searches; their undocumented removal flags do not establish closure.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

from kitty.config import Config, WatchlistCompany
from kitty.core import jobs as jobs_core, likeness
from kitty.core.discovery.ats import parse_greenhouse, parse_lever, smartrecruiters_description
from kitty.core.discovery.base import NormalizedJob
from kitty.core.discovery.http import HttpError, PoliteHttp, RateLimited
from kitty.core.discovery.employers import _detail_url as oracle_detail_url, parse_oracle
from kitty.core.discovery.public_pages import PageHTTPError, PublicPages, parse_job_postings
from kitty.core.discovery.workday import board_urls, parse_workday_detail
from kitty.dates import now_iso, parse, to_iso
from kitty.db.connection import transaction
from kitty.models import Job
from kitty.normalize import canonical_url
from kitty.settings import Settings


@dataclass(frozen=True)
class PostingCheck:
    status: str
    reason: str
    url: str
    observed_at: str | None = None
    posting: NormalizedJob | None = None


@dataclass(frozen=True)
class RefreshResult:
    job_id: int
    check: PostingCheck
    changed_fields: frozenset[str] = frozenset()


def select_jobs(conn: sqlite3.Connection, *, limit: int = 10, track: str | None = None,
                job_ids: list[int] | None = None) -> list[Job]:
    """Favorites first; missing/stale descriptions first within favorites/ranked pool.

    Ties retain the normal best-first ordering. Explicit IDs may recheck closed
    postings; the automatic selection leaves applied/skipped/closed jobs alone.
    """
    if not 1 <= limit <= 100:
        raise ValueError("refresh limit must be between 1 and 100")
    jobs = jobs_core.list_jobs(conn, jobs_core.JobFilter(
        tracks=[track] if track else None, include_closed=bool(job_ids),
        not_applied=not bool(job_ids), order="best",
    ))
    if job_ids:
        wanted = set(job_ids)
        jobs = [job for job in jobs if job.id in wanted]
        missing = wanted - {job.id for job in jobs}
        if missing:
            raise ValueError(f"Unknown jobs or outside selected track: {sorted(missing)}")
    else:
        jobs = [job for job in jobs if job.queue_state not in {"skipped", "applied"}]
    jobs.sort(key=lambda job: (
        not bool(job.favorited_at),
        0 if not job.jd_text else 1 if job.freshness != "verified" else 2,
    ))
    return jobs[:limit]


def _detail_endpoint(job: Job, url: str) -> tuple[str, str | None, str | None, str | None]:
    """Known public single-posting APIs only. No listing endpoints or credentials."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = [part for part in parts.path.split("/") if part]
    query_id = parse_qs(parts.query).get("gh_jid", [None])[0]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}:
        if len(path) >= 3 and path[1] == "jobs":
            slug, identity = path[0], path[2]
            return f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs/{quote(identity)}?content=true", "greenhouse", slug, identity
    if query_id:
        for source in job.sources:
            if source.source == "greenhouse" and source.source_job_id and ":" in source.source_job_id:
                slug, identity = source.source_job_id.split(":", 1)
                if identity == query_id:
                    return f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs/{quote(identity)}?content=true", "greenhouse", slug, identity
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and len(path) >= 2:
        slug, identity = path[:2]
        api = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
        return f"https://{api}/v0/postings/{quote(slug)}/{quote(identity)}?mode=json", "lever", slug, identity
    if host == "jobs.smartrecruiters.com" and len(path) >= 2:
        slug, identity = path[0], path[1].split("-", 1)[0]
        return f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings/{quote(identity)}", "smartrecruiters", slug, identity
    workday_host = re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if workday_host:
        if path and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", path[0]):
            path = path[1:]
        if path and path[-1] == "apply":
            path = path[:-1]
        if len(path) >= 3 and path[1] == "job":
            tenant, wd = workday_host.groups()
            slug = f"{tenant}/{wd}/{path[0]}"
            _, api = board_urls(slug)
            identity = "/" + "/".join(path[1:])
            return api + identity, "workday", slug, identity
    # Only the native Oracle tenant proves where its public REST API lives.
    # Vanity domains can map to a different tenant; never guess that mapping.
    if host.endswith(".oraclecloud.com"):
        match = re.fullmatch(
            r"((?:/hcmUI/CandidateExperience)?/[a-z]{2}(?:-[A-Z]{2})?/sites/([^/]+))"
            r"/job/([A-Za-z0-9_-]+)(?:/apply)?/?", parts.path,
        )
        if match:
            portal_path, site, identity = match.groups()
            slug = f"https://{host}{portal_path}"
            board = {"kind": "oracle", "url": slug, "site": site,
                     "api_url": f"https://{host}/hcmRestApi/resources/latest"}
            return oracle_detail_url(board, identity), "oracle", slug, identity
    return url, None, None, None


def _same_posting_url(left: str, right: str) -> bool:
    from kitty.core.discovery.pipeline import _url_spellings

    def without_apply(url: str) -> str:
        return canonical_url(url).removesuffix("/apply")

    return bool(set(_url_spellings(without_apply(left))) & set(_url_spellings(without_apply(right))))


def _same_detail_url(left: str, right: str) -> bool:
    """API authority includes the exact origin, path and finder/site parameters."""
    a, b = urlsplit(left), urlsplit(right)
    return (
        a.scheme.lower() == b.scheme.lower()
        and a.netloc.lower() == b.netloc.lower()
        and a.path.rstrip("/") == b.path.rstrip("/")
        and parse_qs(a.query, keep_blank_values=True) == parse_qs(b.query, keep_blank_values=True)
    )


def check_posting(job: Job, pages: PublicPages) -> PostingCheck:
    """Read one stored posting publicly; no database writes and no paid calls."""
    url = job.apply_url or job.url
    endpoint, kind, slug, identity = _detail_endpoint(job, url)
    if kind is None:
        # Application forms often omit the JD while the corresponding detail has it.
        url = url.removesuffix("/apply").removesuffix("/apply/")
        endpoint = url
    try:
        page = pages.fetch(endpoint)
        if re.search(r"captcha challenge|complete (?:the )?(?:captcha|security check)|verify (?:that )?you are human|access denied|sign in to (?:view|continue)|log in to (?:view|continue)", page.text, re.I):
            return PostingCheck("blocked", "Public page requires verification or authentication", page.url)
        if kind:
            if not _same_detail_url(page.url, endpoint):
                return PostingCheck("unverified", "Detail API redirected away from this exact posting", page.url)
            payload = json.loads(page.text)
            if not isinstance(payload, dict):
                return PostingCheck("unverified", "Detail API returned no posting object", page.url)
            if kind == "workday":
                info = payload.get("jobPostingInfo")
                if not isinstance(info, dict) or not isinstance(info.get("externalUrl"), str):
                    return PostingCheck("unverified", "Workday detail lacks exact posting identity", page.url)
                returned_endpoint, returned_kind, _, _ = _detail_endpoint(job, info["externalUrl"])
                if returned_kind != "workday" or not _same_detail_url(returned_endpoint, endpoint):
                    return PostingCheck("unverified", "Workday detail identifies another posting, tenant or site", page.url)
                # Neither canApply nor posted has a documented closure contract.
                if (info.get("canApply") is False or info.get("posted") is False
                        or info.get("active") is False or info.get("status") or info.get("state")):
                    return PostingCheck("unverified", "Workday application availability is uncertain; not removal evidence", page.url)
                company = WatchlistCompany(name=job.company_name, ats=kind, slug=slug)
                posting = parse_workday_detail(company, {"externalPath": identity}, payload)
            elif kind == "oracle":
                items = payload.get("items")
                if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
                    return PostingCheck("unverified", "Oracle detail lacks one exact posting; absence is not closure", page.url)
                record = items[0]
                site = slug.rsplit("/", 1)[1]
                if str(record.get("Id", "")) != identity or (
                    "SiteNumber" in record and str(record["SiteNumber"]) != site
                ):
                    return PostingCheck("unverified", "Oracle detail did not identify the requested posting and site", page.url)
                if (record.get("canApply") is False or record.get("active") is False
                        or record.get("status") or record.get("state")):
                    return PostingCheck("unverified", "Oracle application availability is uncertain; not removal evidence", page.url)
                posting = parse_oracle({"company": job.company_name, "url": slug}, record, page, "")
            else:
                returned_id = str(payload.get("id", ""))
                if returned_id != identity:
                    return PostingCheck("unverified", "Detail API did not identify the requested posting", page.url)
                status = str(payload.get("status", payload.get("state", ""))).lower()
                if status in {"closed", "expired", "filled", "inactive", "unpublished"} or payload.get("active") is False:
                    return PostingCheck("closed", "Exact ATS detail explicitly marks the posting unavailable", page.url, page.retrieved_at)
                company = WatchlistCompany(name=job.company_name, ats=kind, slug=slug)
                if kind == "greenhouse":
                    posting = parse_greenhouse(company, {"jobs": [payload]})[0]
                elif kind == "lever":
                    posting = parse_lever(company, [payload])[0]
                else:
                    posting = NormalizedJob(source=kind, source_job_id=f"{slug}:{identity}",
                                            company=job.company_name, title=payload.get("name") or "",
                                            url=job.url, description=smartrecruiters_description(payload), raw=payload)
            if not posting.title or not posting.description:
                return PostingCheck("unverified", "Exact detail lacks title or qualification evidence", page.url)
            return PostingCheck("open", "Authoritative ATS detail is active", page.url, page.retrieved_at, posting)
        host = (urlsplit(page.url).hostname or "").lower()
        if any(host == domain or host.endswith("." + domain) for domain in (
            "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com", "simplify.jobs", "github.com",
        )):
            return PostingCheck("unverified", "Aggregator evidence cannot verify employer availability", page.url)
        postings = parse_job_postings(page, source="refresh")
        identities = {source.source_job_id for source in job.sources
                      if source.source in {"websearch", "refresh"} and source.source_job_id}
        was_listing = any(source.url_is_listing for source in job.sources)
        exact = []
        for candidate in postings:
            if not (_same_posting_url(candidate.url, job.url) or _same_posting_url(candidate.url, url)):
                continue
            listing = ((candidate.raw or {}).get("provenance") or {}).get("url_is_listing")
            if identities:
                if candidate.source_job_id not in identities:
                    continue
            elif was_listing or listing:
                continue
            if was_listing:
                candidate.raw["provenance"]["url_is_listing"] = True
            exact.append(candidate)
        if len(exact) != 1:
            return PostingCheck("unverified", "No unique exact JobPosting evidence; absence is not closure", page.url)
        posting = exact[0]
        if posting.closed:
            return PostingCheck("closed", "Exact published posting explicitly closed or past validThrough", page.url, page.retrieved_at, posting)
        if not posting.description:
            return PostingCheck("unverified", "Posting has no qualification evidence", page.url)
        return PostingCheck("open", "Exact public JobPosting detail is active", page.url, page.retrieved_at, posting)
    except PageHTTPError as exc:
        if kind and exc.status_code in {404, 410} and _same_detail_url(exc.url, endpoint):
            return PostingCheck(
                "closed", f"Authoritative {kind} posting detail returned HTTP {exc.status_code}",
                exc.url, exc.observed_at,
            )
        status = "blocked" if exc.status_code in {401, 403, 429} else "unverified" if exc.status_code in {404, 410} else "error"
        return PostingCheck(status, f"{exc}; no exact posting removal evidence", exc.url, exc.observed_at)
    except RateLimited as exc:
        return PostingCheck("blocked", str(exc), endpoint)
    except HttpError as exc:
        status = "blocked" if "robots" in str(exc).lower() else "error"
        return PostingCheck(status, str(exc), endpoint)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return PostingCheck("error", f"Invalid posting evidence: {type(exc).__name__}: {exc}", endpoint)


def persist_check(conn: sqlite3.Connection, job_id: int, check: PostingCheck) -> set[str]:
    """Record every attempt, never advance verified_at on uncertainty or failure."""
    if check.status not in {"open", "closed", "blocked", "error", "unverified"}:
        raise ValueError(f"Invalid posting status: {check.status}")
    timestamp = now_iso()
    parsed_observation = parse(check.observed_at) if check.observed_at else None
    if check.observed_at and parsed_observation is None:
        raise ValueError(f"Invalid source observation timestamp: {check.observed_at!r}")
    observed_at = to_iso(parsed_observation) if parsed_observation else timestamp
    previous = conn.execute("SELECT closed_at, verified_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if previous is None:
        raise ValueError(f"Unknown job {job_id}")
    latest = max(previous["closed_at"] or "", previous["verified_at"] or "")
    if check.status in {"open", "closed"} and observed_at < latest:
        conn.execute(
            "UPDATE jobs SET verification_checked_at = ?, verification_reason = ? WHERE id = ?",
            (timestamp, f"Older {check.status} evidence ignored ({observed_at}); {check.reason}", job_id),
        )
        return set()
    changed: set[str] = set()
    if check.posting is not None and check.status in {"open", "closed"}:
        posting = check.posting
        changed = jobs_core.merge_source_facts(
            conn, job_id, source=posting.source, source_url=check.url,
            observed_at=observed_at, title=posting.title,
            jd_text=posting.description, location=posting.location, is_remote=posting.is_remote,
            apply_url=posting.apply_url, country=posting.country, degrees=posting.degrees,
            posted_at=posting.posted_at, source_deadline_at=posting.deadline_at,
            source_opens_at=posting.opens_at,
        )
        jobs_core.add_job_source(conn, job_id, posting.source,
                                 source_job_id=posting.source_job_id, source_url=check.url,
                                 raw_json=json.dumps(posting.raw, default=str) if posting.raw else None)
    conn.execute(
        "UPDATE jobs SET verification_status = ?, verification_checked_at = ?, "
        "verification_reason = ? WHERE id = ?", (check.status, timestamp, check.reason, job_id),
    )
    if check.status == "open":
        conn.execute("UPDATE jobs SET verified_at = ?, is_active = 1, closed_at = NULL WHERE id = ?",
                     (observed_at, job_id))
    elif check.status == "closed":
        jobs_core.close_job(conn, job_id, reason=check.reason)
    return changed


def refresh_jobs(conn: sqlite3.Connection, config: Config, settings: Settings, *,
                 limit: int = 10, track: str | None = None, job_ids: list[int] | None = None,
                 pages: PublicPages | None = None) -> list[RefreshResult]:
    """Fetch at most limit exact details, rejudge free rules, and refresh likeness."""
    from kitty.core.discovery.pipeline import rejudge
    from kitty.core.triage.rules import apply_rules_tiers

    if track and track not in config.track_names:
        raise ValueError(f"Unknown track: {track}")
    selected = select_jobs(conn, limit=limit, track=track, job_ids=job_ids)
    http = None
    if pages is None:
        # A one-hour cache prevents rapid repeated preflight checks hammering sites;
        # the cached observation time, not now, determines verification freshness.
        http = PoliteHttp(settings.cache_dir / "refresh", min_interval=2, cache_ttl_minutes=60)
        pages = PublicPages(http)
    results = []
    try:
        for job in selected:
            check = check_posting(job, pages)
            with transaction(conn):
                changed = persist_check(conn, job.id, check)
                if changed:
                    rejudge(conn, config.tracks.tracks, job_ids=[job.id])
            results.append(RefreshResult(job.id, check, frozenset(changed)))
        apply_rules_tiers(conn, config)
        likeness.rescore(conn)
        return results
    finally:
        if http is not None:
            pages.close()
            http.close()

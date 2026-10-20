"""`kitty discover`: run the adapters, then route, filter and store what they found.

For each enabled source, in order:
  1. fetch (a failure is recorded on the Sources screen and the run goes on);
  2. for each posting: match it to a watchlist company, route it to tracks,
     judge eligibility, and store it through `jobs.upsert_job`;
  3. record the source's health and counts.

Dedupe is layered: a posting already seen from the same source updates that
job; otherwise the same canonical URL or the same dedupe_key (company | title |
location) from another source becomes one job with two sources.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from kitty.config import Config, Track, WatchlistFile
from kitty.core import jobs as jobs_core
from kitty.core import likeness
from kitty.core import sources as sources_core
from kitty.core.evidence import chosen_facts, source_authority
from kitty.core.discovery.ats import (
    AshbyAdapter, GreenhouseAdapter, LeverAdapter, SmartRecruitersAdapter, WorkableAdapter,
)
from kitty.core.discovery.base import AdapterResult, NormalizedJob
from kitty.core.discovery.context import DiscoverContext
from kitty.core.discovery.eligibility import Verdict, evaluate, merge_rules
from kitty.core.discovery.employers import EmployersAdapter, LocalEmployersAdapter
from kitty.core.discovery.hackernews import HackerNewsAdapter
from kitty.core.discovery.hiringcafe import HiringCafeAdapter
from kitty.core.discovery.http import PoliteHttp
from kitty.core.discovery.jobspy_source import JobSpyAdapter
from kitty.core.discovery.repos import (
    DereC4Adapter, NuftAdapter, SimplifyAdapter, SpeedyApplyAdapter, VanshAdapter,
)
from kitty.core.discovery.research import ResearchAdapter
from kitty.core.discovery.routing import location_allowed, route
from kitty.core.discovery.starbucks import StarbucksAdapter
from kitty.core.discovery.startups import YCInternshipsAdapter
from kitty.core.discovery.usajobs import UsaJobsAdapter
from kitty.core.discovery.websearch import WebSearchAdapter
from kitty.core.discovery.workday import WorkdayAdapter
from kitty.core.triage.rules import apply_rules_tiers
from kitty.db import connect
from kitty.db.connection import transaction
from kitty.dates import now_iso, parse, to_iso
from kitty.normalize import canonical_url, norm_company, norm_title
from kitty.settings import Settings

log = logging.getLogger(__name__)

#: Every adapter, in the order discover runs them. The repos go first so their
#: postings name the job before a board's copy is merged in; the aggregators
#: (Checkpoint B, hiring.cafe) go last, JobSpy (slow, scraped) at the very end.
ADAPTERS = {
    "simplify2027": SimplifyAdapter,
    "nuft2027": NuftAdapter,
    "vansh2027": VanshAdapter,
    "speedyapply2027": SpeedyApplyAdapter,
    "derec4": DereC4Adapter,
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "workable": WorkableAdapter,
    "workday": WorkdayAdapter,
    "employers": EmployersAdapter,
    "local_employers": LocalEmployersAdapter,
    "usajobs": UsaJobsAdapter,
    "research": ResearchAdapter,
    "yc_internships": YCInternshipsAdapter,
    "websearch": WebSearchAdapter,
    "hackernews": HackerNewsAdapter,
    "starbucks": StarbucksAdapter,
    "hiringcafe": HiringCafeAdapter,
    "jobspy": JobSpyAdapter,
}

#: How many unrouted postings to keep as examples per source.
UNROUTED_EXAMPLES = 5


# ----------------------------------------------------------- company names


class CompanyResolver:
    """Maps a posting's company onto its watchlist name when it is the same firm.

    A board URL is the strongest signal (a Simplify link to
    job-boards.greenhouse.io/anthropic is Anthropic); an exact normalized name
    match is next, then a watchlist name the source's name starts with.
    Anything else keeps the name the source gave.
    """

    def __init__(self, watchlist: WatchlistFile) -> None:
        self.by_name: dict[str, str] = {}
        self.by_board: dict[tuple[str, str], str] = {}
        for company in watchlist.companies:
            self.by_name[norm_company(company.name)] = company.name
            for piece in company.name.replace("(", "/").replace(")", "").split("/"):
                if len(piece.strip()) >= 3:
                    self.by_name.setdefault(norm_company(piece), company.name)
            if company.ats == "workday":
                tenant, wd, _site = company.slug.split("/")
                self.by_board[("workday", f"{tenant}.{wd}".lower())] = company.name
            elif company.ats != "none":
                self.by_board[(company.ats, company.slug.lower())] = company.name

    @staticmethod
    def board_of(url: str) -> tuple[str, str] | None:
        """(ats, slug) for a job URL on a known board, else None."""
        parts = urlsplit(url)
        host = parts.netloc.lower()
        path = [p for p in parts.path.split("/") if p]
        if host.endswith(".myworkdayjobs.com"):
            return "workday", host.removesuffix(".myworkdayjobs.com")
        if not path:
            return None
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            return "greenhouse", path[0].lower()
        if host == "jobs.lever.co":
            return "lever", path[0].lower()
        if host == "jobs.ashbyhq.com":
            return "ashby", path[0].lower()
        if host == "jobs.smartrecruiters.com":
            return "smartrecruiters", path[0].lower()
        if host == "apply.workable.com" and path[0] != "j":
            return "workable", path[0].lower()
        if host.endswith(".workable.com") and host != "apply.workable.com":
            return "workable", host.removesuffix(".workable.com")
        return None

    def resolve(self, name: str, url: str) -> str:
        board = self.board_of(url)
        if board and board in self.by_board:
            return self.by_board[board]
        norm = norm_company(name)
        if norm in self.by_name:
            return self.by_name[norm]
        # "Akuna Capital University" is Akuna Capital: the longest watchlist name
        # the source's name starts with, as whole words, wins.
        prefixes = [known for known in self.by_name if len(known) >= 4 and norm.startswith(known + " ")]
        return self.by_name[max(prefixes, key=len)] if prefixes else name


# ----------------------------------------------------------------- storing


@dataclass
class SourceTally:
    """What happened to one source's postings in this run."""

    status: str = "ok"
    error: str | None = None
    fetched: int = 0
    new: int = 0
    merged: int = 0          # same job already known from another source
    seen_again: int = 0
    closed: int = 0
    unrouted: int = 0
    unrouted_examples: list[str] = field(default_factory=list)
    new_by_track: Counter = field(default_factory=Counter)
    new_by_eligibility: Counter = field(default_factory=Counter)
    adapter_stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status, "error": self.error, "fetched": self.fetched,
            "new": self.new, "merged": self.merged, "seen_again": self.seen_again,
            "closed": self.closed, "unrouted": self.unrouted,
            "unrouted_examples": self.unrouted_examples,
            "new_by_track": dict(self.new_by_track),
            "new_by_eligibility": dict(self.new_by_eligibility),
            "adapter": self.adapter_stats,
        }


def _known_job_id(conn: sqlite3.Connection, source: str, source_job_id: str) -> int | None:
    row = conn.execute(
        "SELECT job_id FROM job_sources WHERE source = ? AND source_job_id = ?",
        (source, source_job_id),
    ).fetchone()
    return int(row["job_id"]) if row else None


def _drop_gh_jid(canonical: str) -> tuple[str, str | None]:
    """(the URL without gh_jid, the gh_jid value): the form stored before 2026-10-03.

    canonical_url used to treat gh_jid as tracking and drop it, so jobs stored
    before then carry the shorter form.
    """
    parts = urlsplit(canonical)
    query = parse_qsl(parts.query)
    job_id = next((value for key, value in query if key == "gh_jid"), None)
    kept = [(key, value) for key, value in query if key != "gh_jid"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), "")), job_id


_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.I)


_GREENHOUSE_HOSTS = {
    "boards.greenhouse.io": ("job-boards.greenhouse.io",),
    "job-boards.greenhouse.io": ("boards.greenhouse.io",),
}


def _url_spellings(canonical: str) -> list[str]:
    """Other ways the same posting's URL is written by other sources, best first.

    - the pre-2026-10-03 form without gh_jid, only when the gh_jid value is in
      its path (akunacapital.com/careers/job/8018847/?gh_jid=8018847): a bare
      careers page such as pinterestcareers.com/jobs stood for every posting;
    - with and without "www."; boards.greenhouse.io and job-boards.greenhouse.io;
    - Workable: the posting page and its /apply form;
    - Workday: without the /en-US/ locale segment (compared ignoring case,
      since lists lowercase the site name).
    """
    spellings = [canonical]
    stripped, gh_jid = _drop_gh_jid(canonical)
    if gh_jid and gh_jid in urlsplit(stripped).path:
        spellings.append(stripped)
    for spelling in list(spellings):
        parts = urlsplit(spelling)
        host = parts.netloc
        other_hosts = [host.removeprefix("www.") if host.startswith("www.") else f"www.{host}",
                       *_GREENHOUSE_HOSTS.get(host, ())]
        for other_host in other_hosts:
            spellings.append(urlunsplit((parts.scheme, other_host, parts.path, parts.query, "")))
        if host == "apply.workable.com":
            path = parts.path.removesuffix("/apply") if parts.path.endswith("/apply") else f"{parts.path}/apply"
            spellings.append(urlunsplit((parts.scheme, host, path, parts.query, "")))
        if host.endswith(".myworkdayjobs.com"):
            segments = parts.path.split("/")
            if len(segments) > 1 and _LOCALE.match(segments[1]):
                path = "/".join(segments[:1] + segments[2:])
                spellings.append(urlunsplit((parts.scheme, host, path, parts.query, "")))
    return list(dict.fromkeys(spellings))


def _job_id_by_url(conn: sqlite3.Connection, url: str) -> int | None:
    """The stored job whose URL is this posting's canonical URL or another spelling of it."""
    canonical = canonical_url(url)
    workday = urlsplit(canonical).netloc.endswith(".myworkdayjobs.com")
    collate = " COLLATE NOCASE" if workday else ""
    for spelling in _url_spellings(canonical):
        row = conn.execute(
            f"SELECT candidate.id FROM (SELECT id FROM jobs WHERE url = ?{collate} UNION "
            f"SELECT job_id FROM job_sources WHERE source_url = ?{collate}) candidate "
            "WHERE NOT EXISTS (SELECT 1 FROM job_sources s WHERE s.job_id = candidate.id "
            "AND json_extract(s.raw_json, '$.provenance.url_is_listing') = 1) LIMIT 1",
            (spelling, spelling),
        ).fetchone()
        if row:
            return int(row[0])
    return None


def _claim_gh_jid_url(conn: sqlite3.Connection, job_id: int, job: NormalizedJob) -> bool:
    """Settle a known posting whose URL carries gh_jid; False = store it as its own job.

    Before 2026-10-03 canonical_url dropped gh_jid, so different postings on one
    careers page (jumptrading.com/hr/job?gh_jid=...) were merged into one job
    by URL. When such a posting is seen again: if it is the job's own posting
    (same title, or the only posting on that page), the job gets its full URL
    back; otherwise its source row is removed so the caller stores it fresh.
    """
    canonical = canonical_url(job.url)
    page, gh_jid = _drop_gh_jid(canonical)
    if not gh_jid:
        return True
    stored = conn.execute("SELECT url, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
    stored_page, stored_jid = _drop_gh_jid(stored["url"])
    if stored_page != page or stored_jid == gh_jid:
        return True
    if stored_jid is None:
        on_page = sum(
            _drop_gh_jid(row[0] or "")[0] == page
            for row in conn.execute("SELECT source_url FROM job_sources WHERE job_id = ?", (job_id,))
        )
        if on_page <= 1 or norm_title(job.title) == norm_title(stored["title"]):
            conn.execute("UPDATE jobs SET url = ? WHERE id = ?", (canonical, job_id))
            return True
    conn.execute("DELETE FROM job_sources WHERE source = ? AND source_job_id = ?",
                 (job.source, job.source_job_id))
    return False


def _raw_json(job: NormalizedJob) -> str | None:
    raw = job.raw
    if job.observed_at:
        raw = (raw or {}) | {"retrieved_at": job.observed_at}
    if raw and raw.get("record_kind") == "research_program":
        # Published dates are independent of the user's own reminder deadline.
        raw = raw | {"_published_deadline_at": job.deadline_at}
    return json.dumps(raw, default=str) if raw is not None else None


def _with_eligibility_note(verdict: Verdict, note: str | None) -> Verdict:
    """Preserve qualifications that the rules cannot verify, without undoing a rejection."""
    if note and verdict.eligibility != "ineligible":
        return Verdict("ambiguous", note)
    return verdict


def _observed_at(job: NormalizedJob) -> str:
    """Original detail/response time, including both public-page provenance shapes."""
    raw = job.raw or {}
    supplied = job.observed_at or (raw.get("provenance") or {}).get("retrieved_at") or raw.get("retrieved_at")
    if not supplied:
        return now_iso()
    parsed = parse(supplied)
    if parsed is None:
        raise ValueError(f"Invalid source observation timestamp: {supplied!r}")
    return to_iso(parsed)


def _merge_posting(conn: sqlite3.Connection, job_id: int, job: NormalizedJob) -> None:
    jobs_core.merge_source_facts(
        conn, job_id, source=job.source, source_url=job.url,
        observed_at=_observed_at(job),
        title=job.title, jd_text=job.description, location=job.location,
        country=job.country, degrees=job.degrees, apply_url=job.apply_url,
        is_remote=job.is_remote, season=job.season, posted_at=job.posted_at,
        source_deadline_at=job.deadline_at, source_opens_at=job.opens_at,
    )


def _record_availability(conn: sqlite3.Connection, job_id: int, job: NormalizedJob) -> None:
    from kitty.core.evidence import DIRECT_SOURCES

    if job.source not in DIRECT_SOURCES or not job.description or job.closed:
        return
    timestamp = _observed_at(job)
    conn.execute(
        "UPDATE jobs SET verification_status = 'open', verified_at = ?, "
        "verification_checked_at = ?, verification_reason = ?, "
        "is_active = 1, closed_at = NULL WHERE id = ? "
        "AND (closed_at IS NULL OR closed_at <= ?) "
        "AND (verified_at IS NULL OR verified_at <= ?)",
        (timestamp, now_iso(), f"{job.source}: authoritative posting detail", job_id, timestamp, timestamp),
    )


def _close_expired_research(conn: sqlite3.Connection) -> int:
    """An explicit program deadline expires a cycle even after its page rolls forward."""
    rows = conn.execute(
        "SELECT j.id FROM jobs j WHERE j.is_active = 1 "
        "AND EXISTS (SELECT 1 FROM job_sources s WHERE s.job_id = j.id "
        "AND json_extract(s.raw_json, '$.record_kind') = 'research_program' "
        "AND julianday(COALESCE(j.source_deadline_at, "
        "json_extract(s.raw_json, '$._published_deadline_at'))) <= julianday(?))",
        (now_iso(),),
    ).fetchall()
    for row in rows:
        jobs_core.close_job(conn, row["id"], reason="research program application deadline passed")
    return len(rows)


def _reroute_stored(conn: sqlite3.Connection, tracks: list[Track]) -> int:
    """Add newly matching tracks from stored evidence, never revive or reset a job."""
    by_name = {track.name: track for track in tracks}
    rows = conn.execute(
        "SELECT j.id, j.title, j.jd_text, j.location, j.is_remote, "
        "c.display_name AS company, s.source FROM jobs j "
        "JOIN companies c ON c.id = j.company_id "
        "JOIN job_sources s ON s.job_id = j.id WHERE j.is_active = 1"
    )
    added = 0
    for row in rows:
        for match in route(row["title"], row["jd_text"], row["source"], tracks):
            if not location_allowed(by_name[match.track], row["location"], row["is_remote"], row["company"]):
                continue
            if conn.execute("SELECT 1 FROM job_tracks WHERE job_id = ? AND track = ?",
                            (row["id"], match.track)).fetchone():
                continue
            jobs_core.link_track(conn, row["id"], match.track, match.reason)
            added += 1
    return added


def rejudge(conn: sqlite3.Connection, tracks: list[Track], *, job_ids: list[int] | None = None) -> int:
    """Re-evaluate source qualifications without undoing hand workflow decisions.

    Queued/later favorites receive current eligibility too; only automatic new/
    not_eligible transitions change queue state. Explicit hand rejection survives.
    """
    by_name = {t.name: t for t in tracks}
    rows = conn.execute(
        """
        SELECT j.id, j.title, j.jd_text, j.location, j.country, j.degrees,
               j.eligibility, j.eligibility_reason, j.queue_state,
               GROUP_CONCAT(jt.track) AS tracks,
               (SELECT json_extract(s.raw_json, '$.eligibility_note')
                  FROM job_sources s WHERE s.job_id = j.id
                   AND json_extract(s.raw_json, '$.eligibility_note') IS NOT NULL
                 LIMIT 1) AS eligibility_note
          FROM jobs j JOIN job_tracks jt ON jt.job_id = j.id
         WHERE j.is_active = 1
           AND COALESCE(j.eligibility_reason, '') != ?
         GROUP BY j.id
        """,
        (jobs_core.HAND_NOT_ELIGIBLE,),
    ).fetchall()
    changed = 0
    for row in rows:
        if job_ids is not None and row["id"] not in job_ids:
            continue
        rules = [by_name[name].eligibility_rules for name in row["tracks"].split(",") if name in by_name]
        if not rules:
            continue
        verdict = evaluate(
            title=row["title"], description=row["jd_text"], location=row["location"],
            country=row["country"], degrees=json.loads(row["degrees"]) if row["degrees"] else None,
            rules=merge_rules(rules),
        )
        verdict = _with_eligibility_note(verdict, row["eligibility_note"])
        if (verdict.eligibility, verdict.reason) == (row["eligibility"], row["eligibility_reason"]):
            continue
        jobs_core.set_eligibility(conn, row["id"], verdict.eligibility, verdict.reason)
        dropped = verdict.eligibility == "ineligible"
        if row["queue_state"] in ("new", "not_eligible") and dropped != (row["queue_state"] == "not_eligible"):
            jobs_core.set_queue_state(conn, row["id"], "not_eligible" if dropped else "new")
        changed += verdict.eligibility != row["eligibility"]
    return changed


def store_posting(
    conn: sqlite3.Connection,
    job: NormalizedJob,
    *,
    tracks: list[Track],
    resolver: CompanyResolver,
    selective_companies: set[str],
    tally: SourceTally,
) -> None:
    """Route, judge and store one posting, updating `tally`."""
    by_track = {t.name: t for t in tracks}
    matches = [] if job.closed else [
        match for match in route(job.title, job.description, job.source, tracks)
        if location_allowed(by_track[match.track], job.location, job.is_remote, job.company)
    ]
    known = _known_job_id(conn, job.source, job.source_job_id)
    if known is not None and not _claim_gh_jid_url(conn, known, job):
        known = None                             # it was merged into another posting's job
    if known is not None:
        provenance = (job.raw or {}).get("provenance")
        if isinstance(provenance, dict):
            # A listing does not become a unique detail URL when only one role remains.
            was_listing = conn.execute(
                "SELECT 1 FROM job_sources WHERE source = ? AND source_job_id = ? "
                "AND source_url = ? AND json_extract(raw_json, '$.provenance.url_is_listing') = 1",
                (job.source, job.source_job_id, canonical_url(job.url)),
            ).fetchone()
            if was_listing:
                provenance["url_is_listing"] = True
        _merge_posting(conn, known, job)
        jobs_core.add_job_source(conn, known, job.source, source_job_id=job.source_job_id,
                                 source_url=canonical_url(job.url), raw_json=_raw_json(job))
        # A previously seen posting can gain a track after vocabulary expands.
        # Keep existing/manual track links and workflow decisions intact.
        for match in matches:
            jobs_core.link_track(conn, known, match.track, match.reason)
        if job.closed:
            active = conn.execute("SELECT is_active, verified_at, closed_at FROM jobs WHERE id = ?", (known,)).fetchone()
            chosen = chosen_facts(conn, known).get("jd_text")
            latest = max(active["verified_at"] or "", active["closed_at"] or "") if active else ""
            if _observed_at(job) < latest:
                conn.execute(
                    "UPDATE jobs SET verification_checked_at = ?, verification_reason = ? WHERE id = ?",
                    (now_iso(), f"{job.source}: older closure evidence ignored", known),
                )
            elif chosen and source_authority(job.source) < chosen["authority"]:
                conn.execute(
                    "UPDATE jobs SET verification_status = 'unverified', verification_checked_at = ?, "
                    "verification_reason = ? WHERE id = ?",
                    (now_iso(), f"{job.source} reports closure; stronger source requires a direct recheck", known),
                )
            elif active and active["is_active"]:
                jobs_core.close_job(conn, known, reason=f"{job.source}: posting marked inactive")
                tally.closed += 1
        else:
            conn.execute(
                "UPDATE jobs SET last_seen_at = ? WHERE id = ?", (now_iso(), known),
            )
            tally.seen_again += 1
        _record_availability(conn, known, job)
        return
    if job.closed:
        return                                   # closed before we ever saw it

    if not matches:
        tally.unrouted += 1
        if len(tally.unrouted_examples) < UNROUTED_EXAMPLES:
            tally.unrouted_examples.append(f"{job.company} - {job.title}")
        return

    company = resolver.resolve(job.company, job.url)
    verdict = evaluate(
        title=job.title, description=job.description, location=job.location,
        country=job.country, degrees=job.degrees,
        rules=merge_rules([by_track[m.track].eligibility_rules for m in matches]),
    )
    verdict = _with_eligibility_note(verdict, (job.raw or {}).get("eligibility_note"))

    # Neither a multi-posting listing nor a reused program page identifies one opening.
    raw = job.raw or {}
    provenance = raw.get("provenance")
    listing_url = isinstance(provenance, dict) and provenance.get("url_is_listing") is True
    same_url = None if listing_url or raw.get("record_kind") == "research_program" else _job_id_by_url(conn, job.url)
    if same_url is not None:
        _merge_posting(conn, same_url, job)
        jobs_core.add_job_source(conn, same_url, job.source, source_job_id=job.source_job_id,
                                 source_url=canonical_url(job.url), raw_json=_raw_json(job))
        for match in matches:
            jobs_core.link_track(conn, same_url, match.track, match.reason)
        tally.merged += 1
        _record_availability(conn, same_url, job)
        return
    selective = company in selective_companies or bool(job.school_selective_note)
    stored, created = jobs_core.upsert_job(
        conn,
        company=company, title=job.title, url=job.url, location=job.location,
        source=job.source, source_job_id=job.source_job_id,
        application_cycle=job.source_job_id if (job.raw or {}).get("record_kind") == "research_program" else None,
        source_url=canonical_url(job.url), raw_json=_raw_json(job),
        is_remote=job.is_remote, apply_url=job.apply_url, season=job.season,
        jd_text=job.description, posted_at=job.posted_at,
        deadline_at=job.deadline_at, opens_at=job.opens_at,
        eligibility=verdict.eligibility, eligibility_reason=verdict.reason,
        school_selective=True if selective else None,
        school_selective_note=job.school_selective_note,
        country=job.country, degrees=job.degrees,
        observed_at=_observed_at(job),
    )
    _record_availability(conn, stored.id, job)
    for match in matches:
        jobs_core.link_track(conn, stored.id, match.track, match.reason)

    if not created:
        tally.merged += 1
        return

    tally.new += 1
    tally.new_by_eligibility[verdict.eligibility] += 1
    for match in matches:
        tally.new_by_track[match.track] += 1
    if verdict.eligibility == "ineligible":
        jobs_core.set_queue_state(conn, stored.id, "not_eligible")


def close_missing(
    conn: sqlite3.Connection, source: str, result: AdapterResult, close_after: int
) -> int:
    """Count this run's misses for `source` and close jobs every source has stopped listing.

    Only a clean answer counts as a miss: for board-based sources that means the
    posting's own board answered 'ok' (board = source_job_id before the first
    ':'); for a repo it means the whole source answered 'ok'. An erroring,
    rate-limited or suddenly empty board never closes anything. Returns how many
    jobs were closed.
    """
    # Search windows, ranked results and bounded crawls are not board snapshots.
    # Absence from one of these runs is not evidence that a posting closed.
    if result.stats.get("complete_snapshot") is False:
        # A seen hit remains positive evidence; an absent search hit abstains
        # from another source's closure vote. This branch never closes a job.
        seen = {job.source_job_id for job in result.jobs}
        rows = conn.execute("SELECT id, source_job_id FROM job_sources WHERE source = ?", (source,))
        conn.executemany("UPDATE job_sources SET missed_runs = ? WHERE id = ?", [
            (0 if row["source_job_id"] in seen else close_after, row["id"]) for row in rows
        ])
        return 0
    if result.boards:
        clean_boards: set[str] | None = {b.board for b in result.boards if b.status == "ok"}
    elif result.status == "ok":
        clean_boards = None                      # the whole source answered
    else:
        return 0
    seen = {job.source_job_id for job in result.jobs}
    rows = conn.execute(
        "SELECT id, source_job_id FROM job_sources WHERE source = ?", (source,)
    ).fetchall()
    listed = [(row["id"],) for row in rows if row["source_job_id"] in seen]
    missed = [
        (row["id"],) for row in rows
        if row["source_job_id"] not in seen
        and (clean_boards is None or (row["source_job_id"] or "").split(":", 1)[0] in clean_boards)
    ]
    conn.executemany("UPDATE job_sources SET missed_runs = 0 WHERE id = ?", listed)
    conn.executemany("UPDATE job_sources SET missed_runs = missed_runs + 1 WHERE id = ?", missed)

    stale = conn.execute(
        """
        SELECT j.id FROM jobs j
        WHERE j.is_active = 1
          AND EXISTS (SELECT 1 FROM job_sources s
                      WHERE s.job_id = j.id AND s.source = ? AND s.missed_runs >= ?)
          AND NOT EXISTS (SELECT 1 FROM job_sources s
                          WHERE s.job_id = j.id AND s.missed_runs < ?)
        """,
        (source, close_after, close_after),
    ).fetchall()
    for row in stale:
        jobs_core.close_job(conn, row["id"],
                            reason=f"not listed by any source for {close_after} runs")
    return len(stale)


# ---------------------------------------------------------------- the run


@dataclass
class DiscoverResult:
    run_id: int
    started_at: str
    sources: dict[str, SourceTally]
    rejudged: int = 0            # stored jobs whose eligibility changed under the current rules


def _previous_board_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Per-board posting counts from the last finished discover run."""
    counts: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT stats_json FROM runs WHERE kind = 'discover' AND finished_at IS NOT NULL "
        "AND stats_json IS NOT NULL ORDER BY id DESC LIMIT 20"
    ):
        stats = json.loads(row["stats_json"])
        for name, source in (stats.get("sources") or {}).items():
            if name not in counts:
                boards = (source.get("adapter") or {}).get("board_counts")
                if boards:
                    counts[name] = boards
    return counts


def run_discover(
    settings: Settings,
    config: Config,
    *,
    only: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
    http_factory: Callable[[str], PoliteHttp] | None = None,
    adapters: dict | None = None,
) -> DiscoverResult:
    """Run discovery and store everything. Opens its own connection (thread-safe).

    `only` limits the run to named sources. `http_factory` and `adapters` exist
    so tests can run the whole pipeline without the network.
    """
    say = progress or (lambda message: None)
    registry = adapters or ADAPTERS
    conn = connect(settings.db_path)
    clients: dict[str, PoliteHttp] = {}

    def http_for(name: str) -> PoliteHttp:
        if name not in clients:
            if http_factory:
                clients[name] = http_factory(name)
            else:
                source = config.sources.sources.get(name)
                clients[name] = PoliteHttp(
                    settings.cache_dir / "http" / name,
                    min_interval=source.min_interval_seconds if source else 2.0,
                    cache_ttl_minutes=source.cache_ttl_minutes if source else 180,
                )
        return clients[name]

    try:
        ctx = DiscoverContext(config, http_for, _previous_board_counts(conn), settings)
        resolver = CompanyResolver(config.watchlist)
        selective = {c.name for c in config.watchlist.companies if c.school_selective}
        run_id = sources_core.start_run(conn, "discover")
        started = conn.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
        tallies: dict[str, SourceTally] = {}

        for name, adapter_class in registry.items():
            source_config = config.sources.sources.get(name)
            if only is not None and name not in only:
                continue
            if only is None and (source_config is None or not source_config.enabled):
                continue
            sources_core.ensure_source(conn, name, source_config.kind if source_config else "other")
            last = conn.execute("SELECT last_success_at FROM sources WHERE name = ?", (name,)).fetchone()
            say(f"{name}: fetching")
            try:
                result = adapter_class(ctx).fetch(parse(last["last_success_at"]) if last else None)
            except Exception as exc:          # an adapter bug must not stop the run
                log.exception("adapter %s crashed", name)
                result = AdapterResult([], "error", f"adapter crashed: {type(exc).__name__}: {exc}")

            tally = SourceTally(status=result.status, error=result.error,
                                fetched=len(result.jobs), adapter_stats=result.stats)
            say(f"{name}: {result.status}, {len(result.jobs)} postings; storing")
            with transaction(conn):
                for job in result.jobs:
                    store_posting(conn, job, tracks=config.tracks.tracks, resolver=resolver,
                                  selective_companies=selective, tally=tally)
                tally.closed += close_missing(conn, name, result,
                                              config.sources.close_after_missed_runs)
                sources_core.record_run(
                    conn, name, status=result.status, error=result.error,
                    count=len(result.jobs) if result.status in ("ok", "partial") else None,
                )
            tallies[name] = tally
            say(f"{name}: {tally.new} new, {tally.merged} merged, {tally.unrouted} unrouted")

        with transaction(conn):
            expired_research = _close_expired_research(conn)
            rerouted = _reroute_stored(conn, config.tracks.tracks)
            rejudged = rejudge(conn, config.tracks.tracks)
        say(f"re-judged stored jobs with the current rules: {rejudged} changed")
        say(f"new track links from stored active postings: {rerouted}")
        if expired_research:
            say(f"research programs closed at their published deadline: {expired_research}")
        tiered = apply_rules_tiers(conn, config)
        say(f"rules tiers set or updated: {tiered}")
        if likeness.rescore(conn):
            say("like scores updated from your favorites")
        sources_core.finish_run(conn, run_id, stats={
            "sources": {name: tally.as_dict() for name, tally in tallies.items()},
            "rejudged": rejudged,
            "rerouted": rerouted,
            "rules_tiered": tiered,
            "expired_research": expired_research,
        })
        return DiscoverResult(run_id, started, tallies, rejudged)
    finally:
        for client in clients.values():
            client.close()
        conn.close()

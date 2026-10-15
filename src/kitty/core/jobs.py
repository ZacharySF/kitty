"""Jobs: creating them, filtering them, and the small edits I make by hand.

`upsert_job` is the single write path. Stage 1 calls it with source='manual' from
the paste-a-URL modal and the CSV importer; Stage 2's adapters call the same
function, which is what makes cross-source dedupe and provenance automatic.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Sequence

from kitty.config import ViewFilters
from kitty.core import companies as companies_core
from kitty.core import sources as sources_core
from kitty.core.evidence import merge_facts
from kitty.dates import now, now_iso, to_iso
from kitty.models import Job, JobSource, QueueState, Tier
from kitty.normalize import canonical_url, dedupe_key, sha256_text

#: eligibility_reason of a job I marked not eligible by hand. Discover's re-judge
#: leaves these alone, so the rules filter never undoes my call.
HAND_NOT_ELIGIBLE = "marked not eligible by hand"

_JOB_SELECT = """
SELECT j.*,
       c.display_name       AS company_name,
       r.label              AS resume_label,
       a.id                 AS application_id,
       a.status             AS status,
       a.next_action_at     AS next_action_at,
       a.next_action_note   AS next_action_note
  FROM jobs j
  JOIN companies c     ON c.id = j.company_id
  LEFT JOIN resumes r  ON r.id = j.resume_id
  LEFT JOIN applications a ON a.job_id = j.id
"""

#: Eligibility values that passed the rules filter (everything except 'ineligible').
NOT_FILTERED_OUT: tuple[str, ...] = ("boosted", "eligible", "ambiguous", "unknown")


@dataclass
class JobFilter:
    """How the list screens and CLI narrow the job list.

    Every field is optional; a None or empty field is simply not applied.
    Built either by hand or from a saved view with `from_view_filters`.
    """

    tracks: Sequence[str] | None = None
    tiers: Sequence[str] | None = None
    statuses: Sequence[str] | None = None
    queue_states: Sequence[str] | None = None
    sources: Sequence[str] | None = None
    eligibility: Sequence[str] | None = None
    school_selective: bool | None = None
    posted_within_days: int | None = None
    has_deadline: bool | None = None
    next_action_due: bool | None = None
    not_applied: bool | None = None
    applied_only: bool | None = None
    include_closed: bool = False
    favorites_only: bool | None = None
    text: str | None = None
    limit: int | None = None
    order: str = "newest"                     # newest | company | deadline | best
    #: Used by order="best": jobs in these cities (or remote, with prefer_remote) rank above the rest.
    prefer_cities: Sequence[str] = ()
    prefer_remote: bool = False
    company_text: str | None = None           # literal, case-insensitive company-name substring

    @classmethod
    def from_view_filters(cls, filters: ViewFilters) -> "JobFilter":
        """Translate a saved view from views.yaml into a JobFilter."""
        return cls(
            tracks=filters.track,
            tiers=filters.tier,
            statuses=filters.status,
            queue_states=filters.queue_state,
            sources=filters.source,
            eligibility=filters.eligibility,
            school_selective=filters.school_selective,
            posted_within_days=filters.posted_within_days,
            has_deadline=filters.has_deadline,
            next_action_due=filters.next_action_due,
            not_applied=filters.not_applied,
            include_closed=bool(filters.include_closed),
            text=filters.text,
        )


def _in_clause(column: str, values: Sequence[str], params: list[object]) -> str:
    """Build 'column IN (?, ?)' and push the values onto `params`."""
    params.extend(values)
    placeholders = ",".join("?" for _ in values)
    return f"{column} IN ({placeholders})"


def _where(filt: JobFilter) -> tuple[str, list[object]]:
    """Build the WHERE clause and parameters for a JobFilter."""
    clauses: list[str] = []
    params: list[object] = []

    if not filt.include_closed:
        clauses.append("j.is_active = 1")
    if filt.tracks:
        placeholders = ",".join("?" for _ in filt.tracks)
        clauses.append(
            f"EXISTS (SELECT 1 FROM job_tracks jt WHERE jt.job_id = j.id "
            f"AND jt.track IN ({placeholders}))"
        )
        params.extend(filt.tracks)
    if filt.sources:
        placeholders = ",".join("?" for _ in filt.sources)
        clauses.append(
            f"EXISTS (SELECT 1 FROM job_sources js WHERE js.job_id = j.id "
            f"AND js.source IN ({placeholders}))"
        )
        params.extend(filt.sources)
    if filt.tiers:
        clauses.append(_in_clause("j.tier", filt.tiers, params))
    if filt.queue_states:
        clauses.append(_in_clause("j.queue_state", filt.queue_states, params))
    if filt.eligibility:
        clauses.append(_in_clause("j.eligibility", filt.eligibility, params))
    if filt.statuses:
        clauses.append(_in_clause("a.status", filt.statuses, params))
    if filt.school_selective is not None:
        clauses.append("j.school_selective = ?")
        params.append(int(filt.school_selective))
    if filt.posted_within_days is not None:
        cutoff = to_iso(now() - timedelta(days=filt.posted_within_days))
        clauses.append("COALESCE(j.posted_at, j.first_seen_at) >= ?")
        params.append(cutoff)
    if filt.has_deadline is not None:
        clauses.append(
            "COALESCE(j.deadline_at, j.source_deadline_at) IS NOT NULL" if filt.has_deadline
            else "COALESCE(j.deadline_at, j.source_deadline_at) IS NULL"
        )
    if filt.next_action_due:
        clauses.append("a.next_action_at IS NOT NULL AND a.next_action_at <= ?")
        params.append(now_iso())
    if filt.not_applied:
        clauses.append("a.id IS NULL")
    if filt.applied_only:
        clauses.append("a.id IS NOT NULL")
    if filt.favorites_only:
        clauses.append("j.favorited_at IS NOT NULL")
    if filt.company_text and filt.company_text.strip():
        clauses.append("instr(lower(c.display_name), lower(?)) > 0")
        params.append(filt.company_text.strip())
    if filt.text:
        like = f"%{filt.text.strip()}%"
        clauses.append("(j.title LIKE ? OR c.display_name LIKE ? OR j.location LIKE ?)")
        params.extend([like, like, like])

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


_ORDER_SQL = {
    "newest": " ORDER BY COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC",
    "company": " ORDER BY c.display_name COLLATE NOCASE, j.title COLLATE NOCASE",
    "deadline": " ORDER BY COALESCE(j.deadline_at, j.source_deadline_at) IS NULL, "
                "COALESCE(j.deadline_at, j.source_deadline_at), j.id DESC",
}


def _attach(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[Job]:
    """Turn job rows into Jobs, loading their tracks and sources in two queries."""
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in ids)

    tracks: dict[int, list[str]] = {job_id: [] for job_id in ids}
    for row in conn.execute(
        f"SELECT job_id, track FROM job_tracks WHERE job_id IN ({placeholders}) ORDER BY track",
        ids,
    ):
        tracks[row["job_id"]].append(row["track"])

    sources: dict[int, list[JobSource]] = {job_id: [] for job_id in ids}
    for row in conn.execute(
        f"SELECT * FROM job_sources WHERE job_id IN ({placeholders}) ORDER BY source", ids
    ):
        sources[row["job_id"]].append(JobSource.from_row(row))

    return [
        Job.from_row(row, tracks=tracks[row["id"]], sources=sources[row["id"]])
        for row in rows
    ]


#: Width of one step of the like score in best-first order. Scores are compared in
#: steps, so place and age still sort jobs that look about equally like my favorites.
LIKE_STEP = 0.05


def _best_order(prefer_cities: Sequence[str], prefer_remote: bool, params: list[object]) -> str:
    """ORDER BY for 'best first': who I can apply to, then fit, then place, then age.

    Freshman-friendly (boosted) beats eligible beats ambiguous; tier A beats B,
    and untriaged sits between B and C; watchlist priority next; then how much
    the job looks like my favorites (core/likeness.py, in LIKE_STEP steps; NULL
    without favorites, so it changes nothing then); then jobs in a preferred
    city (or remote, when preferred); newest last.
    """
    places = ["j.location LIKE ?" for _ in prefer_cities]
    params.extend(f"%{name}%" for name in prefer_cities)
    if prefer_remote:
        places += ["j.is_remote = 1", "j.location LIKE '%remote%'", "j.location LIKE '%virtual%'"]
    place = f"CASE WHEN {' OR '.join(places)} THEN 0 ELSE 1 END, " if places else ""
    # Scores lie in [-1, 1]; +1 keeps the value positive so CAST truncation is a floor.
    like = f"CAST((COALESCE(j.like_score, 0) + 1) / {LIKE_STEP} AS INTEGER) DESC"
    return (
        " ORDER BY CASE j.eligibility WHEN 'boosted' THEN 0 WHEN 'eligible' THEN 1"
        " WHEN 'ambiguous' THEN 2 WHEN 'unknown' THEN 3 ELSE 4 END,"
        " CASE j.tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 3 ELSE 2 END,"
        f" c.priority DESC, {like}, {place}COALESCE(j.posted_at, j.first_seen_at) DESC, j.id DESC"
    )


def list_jobs(conn: sqlite3.Connection, filt: JobFilter | None = None) -> list[Job]:
    """Jobs matching a filter, with company, tracks, sources and application state."""
    active = filt or JobFilter()
    where, params = _where(active)
    if active.order == "best":
        order = _best_order(active.prefer_cities, active.prefer_remote, params)
    else:
        order = _ORDER_SQL.get(active.order, _ORDER_SQL["newest"])
    sql = _JOB_SELECT + where + order
    if active.limit:
        sql += " LIMIT ?"
        params.append(active.limit)
    return _attach(conn, conn.execute(sql, params).fetchall())


def count_jobs(conn: sqlite3.Connection, filt: JobFilter | None = None) -> int:
    """How many jobs match a filter."""
    where, params = _where(filt or JobFilter())
    sql = (
        "SELECT COUNT(*) AS n FROM jobs j JOIN companies c ON c.id = j.company_id "
        "LEFT JOIN applications a ON a.job_id = j.id" + where
    )
    return int(conn.execute(sql, params).fetchone()["n"])


def get_job(conn: sqlite3.Connection, job_id: int) -> Job | None:
    """One job with everything attached."""
    rows = conn.execute(_JOB_SELECT + " WHERE j.id = ?", (job_id,)).fetchall()
    found = _attach(conn, rows)
    return found[0] if found else None


def find_by_url(conn: sqlite3.Connection, url: str) -> Job | None:
    """The job whose canonical URL matches, if any (used before a manual add)."""
    canonical = canonical_url(url)
    row = conn.execute(
        """
        SELECT j.id FROM jobs j
         WHERE j.url = ?
            OR EXISTS (SELECT 1 FROM job_sources js
                        WHERE js.job_id = j.id AND js.source_url = ?)
         LIMIT 1
        """,
        (canonical, canonical),
    ).fetchone()
    return get_job(conn, row["id"]) if row else None


def link_track(
    conn: sqlite3.Connection, job_id: int, track: str, reason: str | None = None
) -> None:
    """Attach a job to a track (a job may be in several)."""
    conn.execute(
        """
        INSERT INTO job_tracks (job_id, track, match_reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(job_id, track) DO UPDATE SET
            match_reason = COALESCE(excluded.match_reason, job_tracks.match_reason)
        """,
        (job_id, track, reason, now_iso()),
    )


def unlink_track(conn: sqlite3.Connection, job_id: int, track: str) -> None:
    """Detach a job from a track."""
    conn.execute("DELETE FROM job_tracks WHERE job_id = ? AND track = ?", (job_id, track))


def add_job_source(
    conn: sqlite3.Connection,
    job_id: int,
    source: str,
    *,
    source_job_id: str | None = None,
    source_url: str | None = None,
    raw_json: str | None = None,
) -> None:
    """Record that `source` has seen this job, updating last_seen_at on repeats."""
    sources_core.ensure_source(conn, source)
    timestamp = now_iso()
    existing = conn.execute(
        "SELECT id FROM job_sources WHERE source = ? AND source_job_id IS ?",
        (source, source_job_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE job_sources SET last_seen_at = ?, source_url = COALESCE(?, source_url), "
            "raw_json = COALESCE(?, raw_json) WHERE id = ?",
            (timestamp, source_url, raw_json, existing["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO job_sources (job_id, source, source_job_id, source_url,
                                 first_seen_at, last_seen_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, source, source_job_id, source_url, timestamp, timestamp, raw_json),
    )


def upsert_job(
    conn: sqlite3.Connection,
    *,
    company: str,
    title: str,
    url: str,
    location: str | None = None,
    source: str = "manual",
    source_job_id: str | None = None,
    source_url: str | None = None,
    raw_json: str | None = None,
    is_remote: bool | None = None,
    apply_url: str | None = None,
    season: str | None = None,
    jd_text: str | None = None,
    posted_at: str | None = None,
    deadline_at: str | None = None,
    opens_at: str | None = None,
    tracks: Iterable[str] = (),
    match_reason: str | None = None,
    eligibility: str | None = None,
    eligibility_reason: str | None = None,
    school_selective: bool | None = None,
    school_selective_note: str | None = None,
    country: str | None = None,
    degrees: Sequence[str] | None = None,
    ats_kind: str | None = None,
    ats_slug: str | None = None,
    company_notes: str | None = None,
    application_cycle: str | None = None,
    observed_at: str | None = None,
) -> tuple[Job, bool]:
    """Create or update a job; return (job, created).

    Identity is the dedupe_key (normalized company + title + location), so the
    same posting seen on Greenhouse and Indeed becomes one job with two sources.
    Explicit program cycles extend that identity because normalization removes years.
    Source-owned facts are selected by authority and observation time. Personal
    workflow and reminders are untouched; sightings alone do not reopen a posting.
    """
    canonical = canonical_url(url)
    key = dedupe_key(company, title, location)
    if application_cycle is not None:
        key = sha256_text(f"{key}|cycle:{application_cycle}")
    timestamp = now_iso()
    degrees_json = json.dumps(list(degrees)) if degrees else None

    company_id = companies_core.upsert_company(
        conn,
        company,
        ats_kind=ats_kind,
        ats_slug=ats_slug,
        notes=company_notes,
        school_selective=school_selective,
    )

    existing = conn.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO jobs (company_id, title, location, is_remote, url, apply_url,
                              season, jd_text, jd_hash, dedupe_key, posted_at, deadline_at,
                              opens_at, first_seen_at, last_seen_at, is_active,
                              eligibility, eligibility_reason, school_selective,
                              school_selective_note, country, degrees, queue_state,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            """,
            (
                company_id, title, location,
                None if is_remote is None else int(is_remote),
                canonical, apply_url, season, jd_text,
                sha256_text(jd_text) if jd_text else None,
                key, posted_at, deadline_at if source == "manual" else None,
                opens_at if source == "manual" else None, timestamp, timestamp,
                eligibility or "unknown", eligibility_reason,
                int(bool(school_selective)), school_selective_note,
                country, degrees_json,
                timestamp, timestamp,
            ),
        )
        job_id = int(cursor.lastrowid)
        created = True
    else:
        job_id = int(existing["id"])
        created = False
        conn.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (timestamp, job_id))

    merge_source_facts(
        conn, job_id, source=source, source_url=source_url or canonical,
        observed_at=observed_at, title=title, location=location, is_remote=is_remote,
        apply_url=apply_url, season=season, jd_text=jd_text, posted_at=posted_at,
        source_deadline_at=deadline_at if source != "manual" else None,
        source_opens_at=opens_at if source != "manual" else None,
        school_selective_note=school_selective_note, country=country,
        degrees=list(degrees) if degrees else None,
    )

    add_job_source(
        conn, job_id, source,
        source_job_id=source_job_id or canonical,
        source_url=source_url or canonical,
        raw_json=raw_json,
    )
    for track in tracks:
        link_track(conn, job_id, track, match_reason)

    job = get_job(conn, job_id)
    assert job is not None               # just written
    return job, created


def set_queue_state(conn: sqlite3.Connection, job_id: int, state: QueueState) -> None:
    """Set my workflow state for a job (queued, later, skipped, ...).

    This is deliberately separate from application status: skipping a job never
    creates an application row.
    """
    conn.execute(
        "UPDATE jobs SET queue_state = ?, updated_at = ? WHERE id = ?",
        (state, now_iso(), job_id),
    )


def set_notes(conn: sqlite3.Connection, job_id: int, notes: str | None) -> None:
    """Replace my free-text notes on a job."""
    conn.execute(
        "UPDATE jobs SET my_notes = ?, updated_at = ? WHERE id = ?",
        (notes, now_iso(), job_id),
    )


def set_tier(
    conn: sqlite3.Connection,
    job_id: int,
    tier: Tier | None,
    *,
    reason: str | None = None,
    tier_source: str = "manual",
) -> None:
    """Set a job's tier. Stage 3 passes tier_source='llm'; I pass 'manual'."""
    conn.execute(
        "UPDATE jobs SET tier = ?, tier_reason = ?, tier_source = ?, updated_at = ? "
        "WHERE id = ?",
        (tier, reason, None if tier is None else tier_source, now_iso(), job_id),
    )


def set_resume(conn: sqlite3.Connection, job_id: int, resume_id: int | None) -> None:
    """Record which resume this job should use."""
    conn.execute(
        "UPDATE jobs SET resume_id = ?, updated_at = ? WHERE id = ?",
        (resume_id, now_iso(), job_id),
    )


def mark_triaged(conn: sqlite3.Connection, job_id: int) -> None:
    """Record when the job was last triaged (rules or LLM)."""
    timestamp = now_iso()
    conn.execute(
        "UPDATE jobs SET triaged_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, job_id)
    )




def set_edit_notes(conn: sqlite3.Connection, job_id: int, notes: Sequence[str]) -> None:
    """Store short edit suggestions. These point at JD phrases - never resume text."""
    conn.execute(
        "UPDATE jobs SET edit_notes = ?, updated_at = ? WHERE id = ?",
        (json.dumps(list(notes)), now_iso(), job_id),
    )


def set_deadline(conn: sqlite3.Connection, job_id: int, deadline_at: str | None) -> None:
    """Set or clear a job's application deadline."""
    conn.execute(
        "UPDATE jobs SET deadline_at = ?, updated_at = ? WHERE id = ?",
        (deadline_at, now_iso(), job_id),
    )


def set_eligibility(
    conn: sqlite3.Connection, job_id: int, eligibility: str, reason: str | None = None
) -> None:
    """Record the rules-filter verdict and the phrase that caused it."""
    conn.execute(
        "UPDATE jobs SET eligibility = ?, eligibility_reason = ?, updated_at = ? WHERE id = ?",
        (eligibility, reason, now_iso(), job_id),
    )


def mark_not_eligible(conn: sqlite3.Connection, job_id: int) -> None:
    """Record that I checked a posting and cannot apply (the 'x' key in the apply session).

    Sets eligibility 'ineligible' with HAND_NOT_ELIGIBLE as the reason, moves the job
    to queue_state 'not_eligible', and sets tier C by hand with the same reason, so
    neither the rules tiers nor a late triage answer moves it back up.
    """
    set_eligibility(conn, job_id, "ineligible", HAND_NOT_ELIGIBLE)
    set_queue_state(conn, job_id, "not_eligible")
    set_tier(conn, job_id, "C", reason=HAND_NOT_ELIGIBLE, tier_source="manual")


def merge_source_facts(
    conn: sqlite3.Connection, job_id: int, *, source: str,
    source_url: str | None = None, observed_at: str | None = None, **facts,
) -> set[str]:
    """Record source-owned observations and return the fields whose value changed."""
    return merge_facts(conn, job_id, source=source, source_url=source_url,
                       observed_at=observed_at, **facts)


def close_job(conn: sqlite3.Connection, job_id: int, *, reason: str | None = None) -> None:
    """Mark a posting closed: it leaves the queues but stays in 'All jobs'."""
    timestamp = now_iso()
    conn.execute(
        "UPDATE jobs SET is_active = 0, closed_at = COALESCE(closed_at, ?), "
        "verification_status = 'closed', verification_checked_at = ?, "
        "verification_reason = ?, updated_at = ? WHERE id = ?",
        (timestamp, timestamp, reason, timestamp, job_id),
    )


def search_jobs(conn: sqlite3.Connection, text: str, *, limit: int = 30) -> list[Job]:
    """Loose company/title search, used by the command palette."""
    return list_jobs(
        conn, JobFilter(text=text, include_closed=True, limit=limit, order="company")
    )

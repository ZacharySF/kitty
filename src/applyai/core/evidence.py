"""Source-owned facts and the evidence behind the selected version of each field.

Personal notes, workflow, resume choices and reminder dates never enter this merge.
Every nonempty observation is retained, including evidence that loses arbitration.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from applyai.dates import now_iso, parse, to_iso
from applyai.normalize import sha256_text

DIRECT_SOURCES = frozenset({
    "greenhouse", "lever", "ashby", "smartrecruiters", "workable", "workday",
    "employers", "local_employers", "starbucks", "usajobs", "research", "refresh",
})
FACT_FIELDS = frozenset({
    "title", "location", "is_remote", "apply_url", "season", "jd_text", "posted_at",
    "source_deadline_at", "source_opens_at", "school_selective_note", "country", "degrees",
})


def source_authority(source: str) -> int:
    """Known direct adapters beat verified public pages, which beat aggregators."""
    if source in DIRECT_SOURCES:
        return 30
    if source in {"websearch", "yc_internships"}:
        return 20
    return 10


def chosen_facts(conn: sqlite3.Connection, job_id: int) -> dict[str, dict[str, Any]]:
    """Selected value, source, URL and observation time per published fact."""
    rows = conn.execute(
        "SELECT f.* FROM source_facts f WHERE f.job_id = ? AND f.accepted = 1 "
        "AND f.id = (SELECT MAX(s.id) FROM source_facts s "
        "WHERE s.job_id = f.job_id AND s.field = f.field AND s.accepted = 1)", (job_id,),
    )
    return {row["field"]: {**dict(row), "value": json.loads(row["value_json"])} for row in rows}


def merge_facts(
    conn: sqlite3.Connection, job_id: int, *, source: str,
    source_url: str | None = None, observed_at: str | None = None,
    authority: int | None = None, **facts: Any,
) -> set[str]:
    """Accept higher-authority or newer same-authority evidence; never erase facts.

    Legacy values retain a conservative highest-prior-source authority, explicitly
    labeled legacy: their actual originating source and observation time are unknown.
    Equal-time descriptions prefer the richer copy. Invalid supplied dates are
    rejected before any writes rather than being promoted to current evidence.
    """
    unknown = facts.keys() - FACT_FIELDS
    if unknown:
        raise ValueError(f"Not source-owned facts: {sorted(unknown)}")
    parsed = parse(observed_at) if observed_at else None
    if observed_at and parsed is None:
        raise ValueError(f"Invalid source observation timestamp: {observed_at!r}")
    timestamp = to_iso(parsed) if parsed else now_iso()
    rank = source_authority(source) if authority is None else authority
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown job {job_id}")
    selected = chosen_facts(conn, job_id)
    previous_sources = conn.execute(
        "SELECT source, source_url, last_seen_at FROM job_sources WHERE job_id = ?", (job_id,),
    ).fetchall()
    legacy = max(previous_sources, key=lambda s: source_authority(s["source"]), default=None)
    changed: set[str] = set()
    for field, value in facts.items():
        if value is None or value == "" or value == [] or (isinstance(value, str) and not value.strip()):
            continue
        old = selected.get(field)
        current = row[field]
        if field == "degrees" and current:
            current = json.loads(current)
        populated = current not in (None, "", []) and not (isinstance(current, str) and not current.strip())
        if old is None and populated and legacy:
            old = {"source": "legacy", "authority": source_authority(legacy["source"]),
                   "observed_at": legacy["last_seen_at"], "value": current}
            conn.execute(
                "INSERT INTO source_facts(job_id,field,source,source_url,observed_at,authority,value_json,accepted) "
                "VALUES (?,?,?,?,?,?,?,1)",
                (job_id, field, "legacy", None, old["observed_at"],
                 old["authority"], json.dumps(current)),
            )
        accepted = old is None or rank > old["authority"] or (
            rank == old["authority"] and (old["source"] == "legacy" or timestamp >= old["observed_at"])
        )
        if (accepted and old and rank == old["authority"] and timestamp == old["observed_at"]
                and field == "jd_text" and len(str(value)) < len(str(old["value"]))):
            accepted = False
        conn.execute(
            "INSERT INTO source_facts(job_id,field,source,source_url,observed_at,authority,value_json,accepted) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, field, source, source_url, timestamp, rank, json.dumps(value), int(accepted)),
        )
        if not accepted or value == current:
            continue
        stored = json.dumps(value) if field == "degrees" else value
        conn.execute(f"UPDATE jobs SET {field} = ?, updated_at = ? WHERE id = ?", (stored, now_iso(), job_id))
        changed.add(field)
        if field == "jd_text":
            # Hash-keyed triage caches no longer match. A model tier on the old JD
            # is no longer evidence; hand decisions and resume choices survive.
            conn.execute(
                "UPDATE jobs SET jd_hash = ?, triaged_at = NULL, "
                "tier = CASE WHEN tier_source = 'llm' THEN NULL ELSE tier END, "
                "tier_reason = CASE WHEN tier_source = 'llm' THEN NULL ELSE tier_reason END, "
                "tier_source = CASE WHEN tier_source = 'llm' THEN NULL ELSE tier_source END WHERE id = ?",
                (sha256_text(value), job_id),
            )
    return changed

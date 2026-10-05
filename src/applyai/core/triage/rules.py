"""Free tiers from the rules, so every kept job has a tier before (or without) the model.

  * filtered out (ineligible)          -> C, with the filter's reason
  * open to underclassmen (boosted)    -> A
  * eligible, or no standing/degree bar -> B
Part-time (triage_depth 'rules') only ever gets these. On model tracks they are
provisional: `applyai triage` replaces them with the model's tier. A tier I set
by hand is never touched, and a model tier only gives way when the job has since
been filtered out. Runs at the end of every discover, so it costs nothing.
"""

from __future__ import annotations

import sqlite3

from applyai.config import Config
from applyai.core import jobs as jobs_core
from applyai.db.connection import transaction
from applyai.models import Job


def rules_tier(job: Job, *, uses_model: bool) -> tuple[str, str]:
    """(tier, reason) from the eligibility verdict alone."""
    later = "; applyai triage gives the model's tier" if uses_model else ""
    if job.eligibility == "ineligible":
        return "C", f"filtered out: {job.eligibility_reason or 'not eligible'}"
    if job.eligibility == "boosted":
        return "A", f"rules: open to underclassmen ({job.eligibility_reason or 'boosted'}){later}"
    if job.eligibility == "eligible":
        return "B", f"rules: eligible ({job.eligibility_reason or 'no bar found'}){later}"
    return "B", f"rules: no standing or degree bar found{later}"


def rules_changes(conn: sqlite3.Connection, config: Config) -> list[tuple[Job, str, str]]:
    """(job, tier, reason) for every candidate whose stored tier should change."""
    depth = {track.name: track.triage_depth for track in config.tracks.tracks}
    jobs = jobs_core.list_jobs(conn, jobs_core.JobFilter(
        queue_states=["new", "queued", "not_eligible"], not_applied=True))
    changes = []
    for job in jobs:
        if job.tier_source == "manual" or not job.tracks:
            continue
        if all(depth.get(name) == "none" for name in job.tracks):
            continue
        if job.tier_source == "llm" and job.eligibility != "ineligible":
            continue
        uses_model = any(depth.get(name) == "llm" for name in job.tracks)
        tier, reason = rules_tier(job, uses_model=uses_model)
        if (job.tier, job.tier_reason, job.tier_source) != (tier, reason, "rules"):
            changes.append((job, tier, reason))
    return changes


def apply_rules_tiers(conn: sqlite3.Connection, config: Config) -> int:
    """Store the rules tiers that changed; return how many."""
    changes = rules_changes(conn, config)
    with transaction(conn):
        for job, tier, reason in changes:
            jobs_core.set_tier(conn, job.id, tier, reason=reason, tier_source="rules")
    return len(changes)

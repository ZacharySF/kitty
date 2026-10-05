"""Favorites, and the hand judgements that steer "more like these".

I star the jobs I like (favorite) and turn down the ones I do not (skip, or
not eligible by hand). Both feed the like score in core/likeness.py, so every
write here recomputes it once, after the whole batch. Discovery's own
queue-state changes (re-judging eligibility) go through jobs.set_queue_state
directly: they are rules, not my taste.

A favorite is independent of queue_state and of any application: starring a
job never queues, skips or applies it.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from applyai.config import Config
from applyai.core import jobs as jobs_core
from applyai.core import likeness
from applyai.dates import now_iso
from applyai.db.connection import transaction
from applyai.models import Job, QueueState


def _ids_where(conn: sqlite3.Connection, job_ids: Iterable[int], condition: str = "1") -> list[int]:
    """The given ids that are jobs matching `condition`, in the order given, without repeats."""
    wanted = list(dict.fromkeys(job_ids))
    if not wanted:
        return []
    placeholders = ",".join("?" for _ in wanted)
    found = {row["id"] for row in conn.execute(
        f"SELECT id FROM jobs WHERE id IN ({placeholders}) AND {condition}", wanted)}
    return [job_id for job_id in wanted if job_id in found]


def favorite(conn: sqlite3.Connection, job_ids: Iterable[int]) -> list[int]:
    """Star these jobs; return the ids that were not favorites before."""
    changed = _ids_where(conn, job_ids, "favorited_at IS NULL")
    if changed:
        with transaction(conn):
            conn.executemany("UPDATE jobs SET favorited_at = ? WHERE id = ?",
                             [(now_iso(), job_id) for job_id in changed])
            likeness.rescore(conn)
    return changed


def unfavorite(conn: sqlite3.Connection, job_ids: Iterable[int]) -> list[int]:
    """Unstar these jobs; return the ids that were favorites."""
    changed = _ids_where(conn, job_ids, "favorited_at IS NOT NULL")
    if changed:
        with transaction(conn):
            conn.executemany("UPDATE jobs SET favorited_at = NULL WHERE id = ?",
                             [(job_id,) for job_id in changed])
            likeness.rescore(conn)
    return changed


def toggle(conn: sqlite3.Connection, job_id: int) -> bool:
    """Star the job if it is not a favorite, else unstar it; return True when it is one now."""
    if favorite(conn, [job_id]):
        return True
    unfavorite(conn, [job_id])
    return False


def list_favorites(conn: sqlite3.Connection, config: Config, *, include_closed: bool = False) -> list[Job]:
    """My favorites, best first (the same order as the Inbox and Jobs tabs)."""
    return jobs_core.list_jobs(conn, jobs_core.JobFilter(
        favorites_only=True, include_closed=include_closed, order="best",
        prefer_cities=config.prefer_cities, prefer_remote=config.profile.open_to_remote,
    ))


def set_state(conn: sqlite3.Connection, job_ids: Iterable[int], state: QueueState) -> None:
    """Set my queue state on these jobs by hand (never creates an application).

    Rescores only when a job enters or leaves 'skipped', the one queue state
    the like score reads (rules also set 'not_eligible', so that one counts
    only through mark_not_eligible's hand reason).
    """
    ids = _ids_where(conn, job_ids)
    if not ids:
        return
    was_skipped = bool(_ids_where(conn, ids, "queue_state = 'skipped'"))
    with transaction(conn):
        for job_id in ids:
            jobs_core.set_queue_state(conn, job_id, state)
        if state == "skipped" or was_skipped:
            likeness.rescore(conn)


def skip(conn: sqlite3.Connection, job_ids: Iterable[int]) -> None:
    """Skip these jobs by hand, then rescore."""
    set_state(conn, job_ids, "skipped")


def mark_not_eligible(conn: sqlite3.Connection, job_ids: Iterable[int]) -> None:
    """jobs.mark_not_eligible on each job (my call, which discover keeps), then rescore."""
    ids = _ids_where(conn, job_ids)
    if not ids:
        return
    with transaction(conn):
        for job_id in ids:
            jobs_core.mark_not_eligible(conn, job_id)
        likeness.rescore(conn)

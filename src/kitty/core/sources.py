"""The sources table: which adapters exist and how their last run went.

Stage 1 only syncs config/sources.yaml and reads health back. Stage 2's adapters
call `record_run` so a rate-limited source can never be mistaken for "0 jobs".
"""

from __future__ import annotations

import json
import os
import re
import sqlite3

from kitty.config import SourcesFile
from kitty.dates import now_iso
from kitty.models import SourceHealth


PROBLEM_STATUSES = ("partial", "error", "rate_limited")


def safe_error(detail: str | None) -> str:
    """Keep diagnostic counts and hosts, not credentials or URL query values."""
    text = detail or "no detail"
    for key, value in os.environ.items():
        if len(value) >= 4 and any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1?[redacted]", text)
    text = re.sub(
        r"""(?ix)(\b(?:[\w-]*(?:token|secret|password|api[_-]?key)|authorization)\b
        ["']?\s*[:=]\s*["']?)(?:bearer\s+|basic\s+)?[^\s,;}"']+""",
        r"\1[redacted]", text,
    )
    text = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [redacted]", text)
    return " ".join(text.split())


def recovery_actions(status: str | None, detail: str | None) -> dict[str, str]:
    """Classify all known blockers; never recommend rerunning a restricted source."""
    error = (detail or "").lower()
    actions: dict[str, str] = {}
    if "robots" in error:
        actions["robots"] = "Respect robots restrictions; use a permitted direct source or manual review."
    if any(term in error for term in (
        "dns", "nameresolution", "name resolution", "getaddrinfo",
        "failed to resolve", "name or service not known", "nodename nor servname",
    )):
        actions["dns"] = "Check resolver, network and provider endpoint; use an existing direct source, not full reruns."
    if any(term in error for term in ("cap reached", "page cap", "query cap", "result cap", "truncat", "pagination limit", "query limit")):
        actions["coverage"] = "Coverage is incomplete; narrow targeted queries or refresh individual postings."
    if status == "rate_limited" or any(term in error for term in ("429", "rate limit", "rate_limit", "too many requests")):
        actions["cooldown"] = "Respect Retry-After/cooldown before a targeted later attempt."
    if any(term in error for term in ("401", "authentication", "authenticated", "unauthorized", "api key", "api_key", "sign in", "login required", "credentials")):
        actions["setup"] = "Setup required: configure authorized access or keys; use permitted manual access meanwhile."
    if not actions:
        actions["retry"] = "Retry only this source once later; investigate if the error persists."
    return actions


def source_warning(source: SourceHealth, *, detail_limit: int | None = None) -> str:
    """One safe reason-plus-action line shared by Today, push and source screens."""
    detail = safe_error(source.last_error)
    if detail_limit is not None and len(detail) > detail_limit:
        detail = detail[:detail_limit - 3] + "..."
    actions = " ".join(recovery_actions(source.last_status, source.last_error).values())
    return f"{source.name}: {source.last_status} - {detail} Action: {actions}"


def sync_sources(conn: sqlite3.Connection, sources: SourcesFile) -> int:
    """Mirror config/sources.yaml into the sources table; return the count."""
    timestamp = now_iso()
    for name, config in sources.sources.items():
        conn.execute(
            """
            INSERT INTO sources (name, kind, enabled, fragile, config_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                kind        = excluded.kind,
                enabled     = excluded.enabled,
                fragile     = excluded.fragile,
                config_json = excluded.config_json,
                updated_at  = excluded.updated_at
            """,
            (
                name, config.kind, int(config.enabled), int(config.fragile),
                config.model_dump_json(), timestamp,
            ),
        )
    return len(sources.sources)


def record_run(
    conn: sqlite3.Connection,
    name: str,
    *,
    status: str,
    count: int | None = None,
    error: str | None = None,
) -> None:
    """Record one adapter run. `status` is ok | partial | error | rate_limited."""
    timestamp = now_iso()
    conn.execute(
        """
        UPDATE sources
           SET last_run_at     = ?,
               last_success_at = CASE WHEN ? IN ('ok', 'partial') THEN ? ELSE last_success_at END,
               last_status     = ?,
               last_error      = ?,
               last_count      = ?,
               updated_at      = ?
         WHERE name = ?
        """,
        (timestamp, status, timestamp, status, error, count, timestamp, name),
    )


def list_sources(conn: sqlite3.Connection) -> list[SourceHealth]:
    """Health of every known source, enabled ones first."""
    return [
        SourceHealth.from_row(row)
        for row in conn.execute(
            "SELECT * FROM sources ORDER BY enabled DESC, name"
        )
    ]


def start_run(conn: sqlite3.Connection, kind: str) -> int:
    """Open a row in `runs` and return its id."""
    cursor = conn.execute(
        "INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, now_iso())
    )
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    stats: dict | None = None,
    error: str | None = None,
) -> None:
    """Close a `runs` row with its stats (counts, cost, cap hits)."""
    conn.execute(
        "UPDATE runs SET finished_at = ?, stats_json = ?, error = ? WHERE id = ?",
        (now_iso(), json.dumps(stats) if stats else None, error, run_id),
    )


def ensure_source(conn: sqlite3.Connection, name: str, kind: str = "other") -> None:
    """Make sure a source row exists, so job_sources can reference it.

    Called before writing a job source; a hand-added job needs the 'manual' row
    even on a database whose sources.yaml has not been synced yet.
    """
    conn.execute(
        """
        INSERT INTO sources (name, kind, enabled, fragile, updated_at)
        VALUES (?, ?, 1, 0, ?)
        ON CONFLICT(name) DO NOTHING
        """,
        (name, kind, now_iso()),
    )

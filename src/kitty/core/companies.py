"""Company rows, and syncing them from config/watchlist.yaml."""

from __future__ import annotations

import json
import sqlite3

from kitty.config import WatchlistFile
from kitty.dates import now_iso
from kitty.models import Company
from kitty.normalize import norm_company


def upsert_company(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    ats_kind: str | None = None,
    ats_slug: str | None = None,
    website: str | None = None,
    notes: str | None = None,
    school_selective: bool | None = None,
    from_watchlist: bool | None = None,
    priority: int | None = None,
    expected_open_months: list[int] | None = None,
) -> int:
    """Insert or update a company by normalized name; return its id.

    Only arguments that are not None overwrite existing values, so a discovery
    run cannot wipe notes I typed by hand.
    """
    norm = norm_company(display_name)
    timestamp = now_iso()
    row = conn.execute("SELECT id FROM companies WHERE norm_name = ?", (norm,)).fetchone()

    if row is None:
        cursor = conn.execute(
            """
            INSERT INTO companies (norm_name, display_name, website, ats_kind, ats_slug,
                                   notes, school_selective, from_watchlist, priority,
                                   expected_open_months, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                norm, display_name, website, ats_kind, ats_slug, notes,
                int(bool(school_selective)), int(bool(from_watchlist)), priority or 0,
                json.dumps(expected_open_months) if expected_open_months else None,
                timestamp, timestamp,
            ),
        )
        return int(cursor.lastrowid)

    updates: dict[str, object] = {}
    if ats_kind is not None:
        updates["ats_kind"] = ats_kind
    if ats_slug is not None:
        updates["ats_slug"] = ats_slug
    if website is not None:
        updates["website"] = website
    if notes is not None:
        updates["notes"] = notes
    if school_selective is not None:
        updates["school_selective"] = int(school_selective)
    if from_watchlist is not None:
        updates["from_watchlist"] = int(from_watchlist)
    if priority is not None:
        updates["priority"] = priority
    if expected_open_months is not None:
        updates["expected_open_months"] = json.dumps(expected_open_months)

    if updates:
        updates["updated_at"] = timestamp
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE companies SET {assignments} WHERE id = ?",
            (*updates.values(), row["id"]),
        )
    return int(row["id"])


def get_company(conn: sqlite3.Connection, company_id: int) -> Company | None:
    """One company by id."""
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return Company.from_row(row) if row else None


def find_company(conn: sqlite3.Connection, name: str) -> Company | None:
    """One company by name, matched after normalization."""
    row = conn.execute(
        "SELECT * FROM companies WHERE norm_name = ?", (norm_company(name),)
    ).fetchone()
    return Company.from_row(row) if row else None


def list_companies(
    conn: sqlite3.Connection, *, watchlist_only: bool = False
) -> list[Company]:
    """All companies, optionally only the watchlist ones."""
    sql = "SELECT * FROM companies"
    if watchlist_only:
        sql += " WHERE from_watchlist = 1"
    sql += " ORDER BY priority DESC, display_name COLLATE NOCASE"
    return [Company.from_row(row) for row in conn.execute(sql)]


def sync_watchlist(conn: sqlite3.Connection, watchlist: WatchlistFile) -> int:
    """Push config/watchlist.yaml into the companies table; return rows touched.

    The yaml stays the source of truth: entries removed from it keep their
    company row (jobs reference it) but lose the watchlist flag.
    """
    seen: set[int] = set()
    for entry in watchlist.companies:
        company_id = upsert_company(
            conn,
            entry.name,
            # "none" is stored as 'other': a real company with no board to poll.
            ats_kind="other" if entry.ats == "none" else entry.ats,
            ats_slug=entry.slug or None,
            notes=entry.notes or None,
            school_selective=entry.school_selective,
            from_watchlist=True,
            priority=entry.priority,
            expected_open_months=entry.expected_open_months,
        )
        seen.add(company_id)

    if seen:
        placeholders = ",".join("?" for _ in seen)
        conn.execute(
            f"UPDATE companies SET from_watchlist = 0, updated_at = ? "
            f"WHERE from_watchlist = 1 AND id NOT IN ({placeholders})",
            (now_iso(), *seen),
        )
    return len(seen)

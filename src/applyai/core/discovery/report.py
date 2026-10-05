"""What a discover run found, in the shape the first-sync summary prints.

Read from the run's stats plus the jobs it created, so the same report can be
rebuilt later for any past run.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from applyai.config import WatchlistFile
from applyai.normalize import norm_company


@dataclass(frozen=True)
class FilteredReason:
    rule: str
    count: int
    example_company: str
    example_title: str
    example_url: str
    example_reason: str


@dataclass(frozen=True)
class FreshmanCompany:
    """A watchlist company tagged [F] that has at least one open posting."""

    company: str
    open_postings: int
    not_filtered: int                 # eligible, boosted or ambiguous
    examples: list[str]


@dataclass
class DiscoverReport:
    run_id: int
    started_at: str
    finished_at: str | None
    sources: dict[str, dict]
    new_by_track: Counter = field(default_factory=Counter)
    new_by_eligibility: Counter = field(default_factory=Counter)
    top_filtered: list[FilteredReason] = field(default_factory=list)
    freshman_open: list[FreshmanCompany] = field(default_factory=list)
    rejudged: int = 0            # stored jobs whose eligibility changed under the current rules


def last_discover_run(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT id FROM runs WHERE kind = 'discover' AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def top_filtered_reasons(conn: sqlite3.Connection, since: str, limit: int = 10) -> list[FilteredReason]:
    """The most common ineligible rules among jobs first seen since `since`, one example each."""
    rows = conn.execute(
        """
        SELECT j.eligibility_reason AS reason, j.title, j.url, c.display_name AS company
          FROM jobs j JOIN companies c ON c.id = j.company_id
         WHERE j.eligibility = 'ineligible' AND j.first_seen_at >= ?
         ORDER BY j.id
        """,
        (since,),
    ).fetchall()
    counts: Counter = Counter()
    example: dict[str, sqlite3.Row] = {}
    for row in rows:
        rule = (row["reason"] or "no reason").split(" — ")[0]
        counts[rule] += 1
        example.setdefault(rule, row)
    return [
        FilteredReason(rule, count, example[rule]["company"], example[rule]["title"],
                       example[rule]["url"], example[rule]["reason"])
        for rule, count in counts.most_common(limit)
    ]


def freshman_companies_open(conn: sqlite3.Connection, watchlist: WatchlistFile) -> list[FreshmanCompany]:
    """[F]-tagged watchlist companies with an active posting, most postings first."""
    found = []
    for company in watchlist.companies:
        if "[F]" not in company.notes:
            continue
        rows = conn.execute(
            """
            SELECT j.title, j.eligibility FROM jobs j JOIN companies c ON c.id = j.company_id
             WHERE c.norm_name = ? AND j.is_active = 1
             ORDER BY (j.eligibility = 'boosted') DESC, (j.eligibility = 'ineligible'), j.id
            """,
            (norm_company(company.name),),
        ).fetchall()
        if rows:
            found.append(FreshmanCompany(
                company.name, len(rows),
                sum(row["eligibility"] != "ineligible" for row in rows),
                [f"{row['title']} [{row['eligibility']}]" for row in rows[:3]],
            ))
    return sorted(found, key=lambda f: (-f.not_filtered, -f.open_postings, f.company))


def build_report(conn: sqlite3.Connection, run_id: int, watchlist: WatchlistFile) -> DiscoverReport:
    """The full summary for one discover run."""
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    stats = json.loads(run["stats_json"] or "{}")
    sources = stats.get("sources") or {}
    report = DiscoverReport(run_id, run["started_at"], run["finished_at"], sources,
                            rejudged=stats.get("rejudged", 0))
    for source in sources.values():
        report.new_by_track.update(source.get("new_by_track") or {})
        report.new_by_eligibility.update(source.get("new_by_eligibility") or {})
    report.top_filtered = top_filtered_reasons(conn, run["started_at"])
    report.freshman_open = freshman_companies_open(conn, watchlist)
    return report

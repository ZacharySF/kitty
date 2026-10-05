"""Opens soon: watchlist companies with nothing open yet whose postings are due.

A recruiting cycle runs July 1 to June 30 (Summer 2027 postings mostly appear
from July 2026; quant opens in July). For each watchlist company the expected
opening this cycle is:
  * the 1st of each month in its `expected_open_months` override, when set;
  * otherwise the day its first posting appeared last cycle, one year on.
A company shows as "opens soon" when it has no active posting and that date is
between OVERDUE_DAYS ago and SOON_DAYS ahead. Nothing is stored: the estimate
is recomputed from the jobs history each time, so it cannot go stale.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from applyai.dates import now, parse

CYCLE_START_MONTH = 7
SOON_DAYS = 30
OVERDUE_DAYS = 30


@dataclass(frozen=True)
class OpeningSoon:
    company: str
    opens_on: date
    basis: str          # "watchlist months" or "last cycle"


def cycle_start(day: date) -> date:
    """July 1 of the recruiting cycle `day` falls in."""
    year = day.year if day.month >= CYCLE_START_MONTH else day.year - 1
    return date(year, CYCLE_START_MONTH, 1)


def _one_year_later(day: date) -> date:
    """The same day next year; Feb 29 becomes Feb 28."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def estimate_opening(
    expected_months: list[int], last_cycle_first_seen: date | None, today: date
) -> tuple[date, str] | None:
    """This cycle's expected opening date and what it is based on, or None when unknown.

    The watchlist override wins over history. With several override months the
    earliest one not yet more than OVERDUE_DAYS past is used.
    """
    if expected_months:
        start = cycle_start(today)
        candidates = sorted(
            date(start.year if month >= CYCLE_START_MONTH else start.year + 1, month, 1)
            for month in expected_months
        )
        floor = today - timedelta(days=OVERDUE_DAYS)
        upcoming = [day for day in candidates if day >= floor]
        return (upcoming[0], "watchlist months") if upcoming else None
    if last_cycle_first_seen is not None:
        return _one_year_later(last_cycle_first_seen), "last cycle"
    return None


def opening_soon(conn: sqlite3.Connection, today: date | None = None) -> list[OpeningSoon]:
    """Watchlist companies with no active posting whose opening is near, soonest first."""
    today = today or now().date()
    this_cycle = cycle_start(today)
    last_cycle = this_cycle.replace(year=this_cycle.year - 1)
    rows = conn.execute(
        """
        SELECT c.display_name, c.expected_open_months,
               MIN(CASE WHEN j.first_seen_at >= ? AND j.first_seen_at < ?
                        THEN j.first_seen_at END) AS last_cycle_first_seen,
               COALESCE(MAX(j.is_active), 0) AS has_open
        FROM companies c LEFT JOIN jobs j ON j.company_id = c.id
        WHERE c.from_watchlist = 1
        GROUP BY c.id
        """,
        (last_cycle.isoformat(), this_cycle.isoformat()),
    ).fetchall()

    found = []
    for row in rows:
        if row["has_open"]:
            continue
        first_seen = parse(row["last_cycle_first_seen"])
        estimate = estimate_opening(
            json.loads(row["expected_open_months"] or "[]"),
            first_seen.date() if first_seen else None,
            today,
        )
        if estimate is None:
            continue
        opens_on, basis = estimate
        if -OVERDUE_DAYS <= (opens_on - today).days <= SOON_DAYS:
            found.append(OpeningSoon(row["display_name"], opens_on, basis))
    return sorted(found, key=lambda o: (o.opens_on, o.company))

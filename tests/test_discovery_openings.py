"""'Opens soon' estimates for watchlist companies with nothing open yet."""

from datetime import date

from kitty.core import companies as companies_core
from kitty.core import jobs as jobs_core
from kitty.core.discovery.openings import opening_soon


def _job(workspace, company, first_seen, *, active):
    job, _ = jobs_core.upsert_job(workspace.conn, company=company, title=f"Quant Trader Intern {first_seen[:10]}",
                                  url=f"https://example.com/{company}/{first_seen}")
    workspace.conn.execute("UPDATE jobs SET first_seen_at = ? WHERE id = ?", (first_seen, job.id))
    if not active:
        jobs_core.close_job(workspace.conn, job.id)


def test_opens_soon_uses_the_override_then_last_cycle_and_skips_open_companies(workspace):
    for name, months in (("Example Override", [7, 8]), ("Example History", []), ("Example Open", [])):
        companies_core.upsert_company(workspace.conn, name, from_watchlist=True, expected_open_months=months)
    _job(workspace, "Example Override", "2025-09-15T00:00:00Z", active=False)
    _job(workspace, "Example History", "2025-08-20T00:00:00Z", active=False)
    _job(workspace, "Example History", "2025-11-02T00:00:00Z", active=False)
    _job(workspace, "Example Open", "2025-08-15T00:00:00Z", active=True)

    soon = opening_soon(workspace.conn, today=date(2026, 8, 10))

    assert [(o.company, o.opens_on, o.basis) for o in soon] == [
        ("Example Override", date(2026, 8, 1), "watchlist months"),
        ("Example History", date(2026, 8, 20), "last cycle"),
    ]

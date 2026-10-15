"""Syncing config/watchlist.yaml into the companies table."""

from kitty.config import WatchlistCompany, WatchlistFile
from kitty.core import companies as companies_core


def test_a_company_with_no_board_is_kept_without_a_slug(workspace):
    watchlist = WatchlistFile(companies=[
        WatchlistCompany(name="Stripe", ats="greenhouse", slug="stripe", tracks=["software"]),
        WatchlistCompany(name="Lockheed Martin", ats="none", notes="[TX] uses BrassRing"),
    ])
    companies_core.sync_watchlist(workspace.conn, watchlist)

    rows = {
        row["display_name"]: row
        for row in workspace.conn.execute("SELECT * FROM companies WHERE from_watchlist = 1")
    }
    assert (rows["Stripe"]["ats_kind"], rows["Stripe"]["ats_slug"]) == ("greenhouse", "stripe")
    # Still on the watchlist, but with nothing for a discovery adapter to poll.
    assert (rows["Lockheed Martin"]["ats_kind"], rows["Lockheed Martin"]["ats_slug"]) == ("other", None)
    assert rows["Lockheed Martin"]["notes"] == "[TX] uses BrassRing"

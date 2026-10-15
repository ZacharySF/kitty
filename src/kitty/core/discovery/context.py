"""What an adapter is given: config, a polite HTTP client, and last run's numbers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from kitty.config import Config, WatchlistCompany
from kitty.core.discovery.http import PoliteHttp


@dataclass
class DiscoverContext:
    """Shared inputs for one discover run.

    `http_for(source)` returns that source's client (its own pacing and TTL).
    `previous_board_counts[source][board]` is how many postings the board had
    on the last run, so a board that suddenly returns 0 can be flagged.
    """

    config: Config
    http_for: Callable[[str], PoliteHttp]
    previous_board_counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def boards(self, ats: str) -> list[WatchlistCompany]:
        """Watchlist companies whose board lives on `ats`."""
        return [c for c in self.config.watchlist.companies if c.ats == ats]

    def dropped_to_zero(self, source: str, board: str, count: int) -> bool:
        """True when a board that had postings last run now has none."""
        return count == 0 and self.previous_board_counts.get(source, {}).get(board, 0) > 0

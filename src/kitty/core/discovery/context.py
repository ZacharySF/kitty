"""What an adapter is given: config, a polite HTTP client, last run's numbers and paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from kitty.config import Config, WatchlistCompany
from kitty.core.discovery.http import PoliteHttp
from kitty.settings import Settings


@dataclass
class DiscoverContext:
    """Shared inputs for one discover run.

    `http_for(source)` returns that source's client (its own pacing and TTL).
    `previous_board_counts[source][board]` is how many postings the board had
    on the last run, so a board that suddenly returns 0 can be flagged.
    `settings` gives workspace paths (a browser-driven source keeps its profile
    under data/); tests that never start a browser may leave it unset.
    """

    config: Config
    http_for: Callable[[str], PoliteHttp]
    previous_board_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    settings: Settings | None = None

    def boards(self, ats: str) -> list[WatchlistCompany]:
        """Watchlist companies whose board lives on `ats`."""
        return [c for c in self.config.watchlist.companies if c.ats == ats]

    def dropped_to_zero(self, source: str, board: str, count: int) -> bool:
        """True when a board that had postings last run now has none."""
        return count == 0 and self.previous_board_counts.get(source, {}).get(board, 0) > 0

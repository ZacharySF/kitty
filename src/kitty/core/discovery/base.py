"""The shapes every discovery adapter shares.

An adapter turns one source (a repo, an ATS) into NormalizedJobs plus an honest
status. The pipeline never has to know which source it is talking to, and a
failing or rate-limited source can never be mistaken for "0 jobs".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

AdapterStatus = Literal["ok", "partial", "error", "rate_limited"]


@dataclass
class NormalizedJob:
    """One posting as an adapter saw it, before routing and eligibility.

    `company` is the watchlist display name when the adapter knows it, so jobs
    from a board land on the same company row as the watchlist entry.
    """

    source: str
    source_job_id: str
    company: str
    title: str
    url: str
    location: str | None = None
    is_remote: bool | None = None
    country: str | None = None          # when the source states it, e.g. "United States"
    apply_url: str | None = None
    description: str | None = None      # plain text
    posted_at: str | None = None        # ISO UTC
    season: str | None = None
    degrees: list[str] | None = None    # Simplify only
    closed: bool = False                # the source says the posting is closed
    school_selective_note: str | None = None
    raw: dict | None = None             # the source record, stored in job_sources.raw_json
    deadline_at: str | None = None      # explicit application deadline, ISO UTC
    opens_at: str | None = None         # explicit application opening date, ISO UTC
    observed_at: str | None = None      # original response retrieval time, not rediscovery time


@dataclass
class BoardOutcome:
    """How one board (one company on one ATS) went, for health reporting."""

    board: str
    status: AdapterStatus
    count: int = 0
    error: str | None = None


@dataclass
class AdapterResult:
    """Everything one adapter run produced.

    `status` is the worst honest summary: 'partial' when some boards failed,
    'rate_limited' when the source told us to back off.
    """

    jobs: list[NormalizedJob]
    status: AdapterStatus
    error: str | None = None
    stats: dict = field(default_factory=dict)
    boards: list[BoardOutcome] = field(default_factory=list)


class Adapter(Protocol):
    """What the pipeline needs from a source module."""

    name: str

    def fetch(self, since: datetime | None) -> AdapterResult:
        """Fetch postings. `since` is the last successful run, or None."""
        ...


def summarize_boards(boards: list[BoardOutcome]) -> tuple[AdapterStatus, str | None]:
    """Collapse per-board outcomes into one adapter status and error line.

    All boards failing is an error (or rate_limited); some failing is partial.
    A board that answered with 0 jobs after previously having jobs is marked
    'partial' by the adapter itself, so it shows up here as a warning.
    """
    if not boards:
        return "ok", None
    bad = [b for b in boards if b.status != "ok"]
    if not bad:
        return "ok", None
    detail = "; ".join(f"{b.board}: {b.error or b.status}" for b in bad[:5])
    if len(bad) > 5:
        detail += f"; and {len(bad) - 5} more"
    if len(bad) == len(boards):
        limited = all(b.status == "rate_limited" for b in bad)
        return ("rate_limited" if limited else "error"), detail
    return "partial", f"{len(bad)} of {len(boards)} boards had problems: {detail}"

"""The shapes every finder adapter shares.

A finder adapter turns one place (YC's company list, a Hacker News thread, a
conference sponsor page, GitHub search) into RawCandidates plus an honest
status, exactly like the discovery adapters: a source that failed or was rate
limited says so, and is never shown as "0 found".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from kitty.config import Config
from kitty.core.discovery.base import AdapterStatus
from kitty.core.discovery.http import PoliteHttp
from kitty.normalize import norm_company


@dataclass
class SourceText:
    """One piece of text about a firm, and where it came from (for the reason line)."""

    source: str          # adapter name, e.g. "yc-oss"
    label: str           # what the text is, e.g. "description", "tags", "repo topics"
    text: str


@dataclass
class RawCandidate:
    """A firm one or more adapters found, before scoring.

    `hint_areas` are skills.yaml areas the source itself vouches for (a
    conference lists which areas its sponsors belong to). `slug_hints` are
    identifiers the source already knows (YC's company slug, a GitHub login),
    tried first when looking for a job board.
    """

    name: str
    texts: list[SourceText] = field(default_factory=list)
    website: str | None = None
    evidence: list[str] = field(default_factory=list)      # "yc-oss: https://..." lines
    hint_areas: list[str] = field(default_factory=list)
    slug_hints: list[str] = field(default_factory=list)

    @property
    def norm_name(self) -> str:
        return norm_company(self.name)

    @property
    def sources(self) -> list[str]:
        """Adapters that found this firm, once each, in order."""
        return list(dict.fromkeys(text.source for text in self.texts))

    def merge(self, other: "RawCandidate") -> None:
        """Fold another adapter's sighting of the same firm into this one."""
        self.texts.extend(other.texts)
        self.website = self.website or other.website
        self.evidence.extend(line for line in other.evidence if line not in self.evidence)
        self.hint_areas.extend(a for a in other.hint_areas if a not in self.hint_areas)
        self.slug_hints.extend(s for s in other.slug_hints if s not in self.slug_hints)


@dataclass
class CandidateBatch:
    """Everything one finder adapter produced, with the same status words as discovery."""

    candidates: list[RawCandidate]
    status: AdapterStatus
    error: str | None = None
    stats: dict = field(default_factory=dict)


@dataclass
class FinderContext:
    """What an adapter is given: the config, a polite HTTP client per adapter, and
    the normalized names already known (watchlist or proposed before), so an
    adapter can skip spending requests on firms that would be dropped anyway."""

    config: Config
    http_for: Callable[[str], PoliteHttp]
    known: set[str] = field(default_factory=set)


class CandidateAdapter(Protocol):
    """What the finder needs from a candidate source."""

    name: str

    def fetch(self) -> CandidateBatch:
        """Return candidate firms and how the fetch went."""
        ...


def merge_by_name(candidates: list[RawCandidate]) -> list[RawCandidate]:
    """One RawCandidate per normalized name; later sightings are merged into the first."""
    merged: dict[str, RawCandidate] = {}
    for candidate in candidates:
        key = candidate.norm_name
        if not key:
            continue
        if key in merged:
            merged[key].merge(candidate)
        else:
            merged[key] = candidate
    return list(merged.values())

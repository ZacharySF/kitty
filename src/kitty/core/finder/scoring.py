"""Scoring candidate firms against config/skills.yaml, and picking this run's proposals.

The rules are plain and explainable:
  * a keyword matches when it appears as a whole word or phrase (any case) in
    something a source said about the firm: a description, tags, repo topics,
    a hiring post;
  * an area's score is how many of its distinct keywords matched, plus one when
    a source vouches for the area (a conference lists it for its sponsors);
  * the firm's area is its best-scoring area (ties go to the area listed first);
    areas with per_run_quota 0 are switched off and never score;
  * a firm needs MIN_SCORE: two different keywords of one area, or one keyword
    plus a conference vouching for the area. One keyword alone too often means
    something else ("embedded" microcredit, "audio" in a list of AI media).
Proposals go best score first; each area stops at its per_run_quota and the
whole run stops at the cap, so neither can ever be exceeded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kitty.config import SkillArea
from kitty.core.finder.base import RawCandidate

REASON_MAX = 220
MIN_SCORE = 2.0
SNIPPET_RADIUS = 50


@dataclass
class ScoredCandidate:
    """A candidate with its best area, score and one-line reason."""

    raw: RawCandidate
    area: str
    score: float
    matched: list[str]
    reason: str


@dataclass
class Selection:
    """What one run proposes, and why the rest were left out."""

    proposed: list[ScoredCandidate] = field(default_factory=list)
    proposed_by_area: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive match; spaces in a phrase match any whitespace or hyphen."""
    parts = [re.escape(part) for part in keyword.lower().split()]
    return re.compile(r"(?<![a-z0-9])" + r"[\s\-]+".join(parts) + r"(?![a-z0-9])", re.I)


def _matches(raw: RawCandidate, area: SkillArea) -> list[tuple[str, int, int]]:
    """(keyword, text index, match start) for each distinct keyword of `area` found."""
    found = []
    for keyword in area.keywords:
        pattern = keyword_pattern(keyword)
        for index, text in enumerate(raw.texts):
            hit = pattern.search(text.text)
            if hit:
                found.append((keyword, index, hit.start()))
                break
    return found


def _snippet(text: str, start: int) -> str:
    """About a hundred characters of `text` around position `start`, on one line."""
    begin = max(0, start - SNIPPET_RADIUS)
    end = min(len(text), start + SNIPPET_RADIUS)
    piece = re.sub(r"\s+", " ", text[begin:end]).strip()
    return ("..." if begin else "") + piece + ("..." if end < len(text) else "")


def reason_line(raw: RawCandidate, area: str, hits: list[tuple[str, int, int]], vouched: bool) -> str:
    """One line: the area, the keywords that matched, and a quote of the first match."""
    parts = [area + ":"]
    if hits:
        parts.append("matched " + ", ".join(f'"{keyword}"' for keyword, _, _ in hits))
        _, index, start = hits[0]
        text = raw.texts[index]
        parts.append(f'in {text.source} {text.label} "{_snippet(text.text, start)}"')
    if vouched:
        conference = next((t.text for t in raw.texts if t.label == "sponsor of"), "a conference")
        parts.append(f"({conference} lists it for {area})")
    line = " ".join(parts)
    return line if len(line) <= REASON_MAX else line[: REASON_MAX - 3] + "..."


def score_candidate(raw: RawCandidate, areas: list[SkillArea]) -> ScoredCandidate | None:
    """The firm's best area and score, or None when no keyword of any live area matched."""
    best: ScoredCandidate | None = None
    for area in areas:
        if area.per_run_quota <= 0:
            continue
        hits = _matches(raw, area)
        if not hits:
            continue
        vouched = area.name in raw.hint_areas
        score = float(len(hits) + (1 if vouched else 0))
        if best is None or score > best.score:
            best = ScoredCandidate(raw, area.name, score, [k for k, _, _ in hits],
                                   reason_line(raw, area.name, hits, vouched))
    return best


def select(
    candidates: list[RawCandidate], areas: list[SkillArea], known: set[str], cap: int
) -> Selection:
    """Score every candidate and pick at most `cap`, within each area's quota.

    `known` holds normalized names to leave out: the watchlist, and every firm
    ever proposed (pending, approved or rejected), so a rejection sticks.
    """
    selection = Selection()
    quota = {area.name: area.per_run_quota for area in areas}
    scored: list[ScoredCandidate] = []
    for raw in candidates:
        if raw.norm_name in known:
            selection.skip("already known (watchlist or proposed before)")
            continue
        result = score_candidate(raw, areas)
        if result is None:
            selection.skip("no skills.yaml keyword matched")
        elif result.score < MIN_SCORE:
            selection.skip("only one keyword matched")
        else:
            scored.append(result)

    scored.sort(key=lambda s: (-s.score, -len(s.raw.sources), s.raw.name.lower()))
    for result in scored:
        if len(selection.proposed) >= cap:
            selection.skip("over the run cap")
            continue
        if selection.proposed_by_area.get(result.area, 0) >= quota.get(result.area, 0):
            selection.skip("over its area's per_run_quota")
            continue
        selection.proposed.append(result)
        selection.proposed_by_area[result.area] = selection.proposed_by_area.get(result.area, 0) + 1
    return selection

"""Track routing: which tracks a posting belongs to.

Per the spec addendum, routing is decided by the job's title and description
against each track's search_terms. Company-level tracks in watchlist.yaml are
only hints and are not used here.

A term matches a title when every word of the term (minus "intern") appears in
the title, after light stemming, so "software engineer intern" matches
"Backend Software Engineering Intern". Descriptions can add further tracks,
but only for multi-word terms as an exact phrase, because single words like
"trading" appear in almost every description.
Sentences about degrees and majors are ignored there. A track only receives
jobs from the sources listed in its `sources`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from applyai.config import Track

#: Words dropped from search terms and titles before matching.
_FILLER = {"intern", "internship", "interns", "coop", "co", "op", "and", "the", "of", "a"}
_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "er", "ed", "es", "s", "ive", "al")


def stem(word: str) -> str:
    """Strip common English endings until none apply: 'engineering' -> 'engine'."""
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[: -len(suffix)]
                changed = True
                break
    return word


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#]+", text.lower())


def title_stems(title: str) -> set[str]:
    """Stems of a title's words, plus hyphenated pairs joined ('back-end' -> 'backend')."""
    words = _tokens(title)
    stems = {stem(w) for w in words if w not in _FILLER}
    for match in re.finditer(r"([a-z0-9]+)-([a-z0-9]+)", title.lower()):
        stems.add(stem(match.group(1) + match.group(2)))
    return stems


def term_stems(term: str) -> list[str]:
    """Stems of a search term, without filler words."""
    return [stem(w) for w in _tokens(term) if w not in _FILLER]


#: Sentences listing degrees or majors ("a degree in CS, Electrical Engineering, or
#: similar") say nothing about what the job is, so description routing skips them.
_REQUIREMENT_SENTENCE = re.compile(r"[^.\n]*\b(degree|major|majoring|pursuing|studying|coursework)\b[^.\n]*", re.I)


def _phrase_in(stems: list[str], text_stems: list[str]) -> bool:
    """True when `stems` occurs as a consecutive run inside `text_stems`."""
    size = len(stems)
    return any(text_stems[i:i + size] == stems for i in range(len(text_stems) - size + 1))


@dataclass(frozen=True)
class TrackMatch:
    track: str
    reason: str


def route(
    title: str, description: str | None, source: str, tracks: list[Track]
) -> list[TrackMatch]:
    """Every track this posting belongs to, each with the term that matched."""
    eligible_tracks = [t for t in tracks if source in t.sources]
    in_title = title_stems(title)

    matches: list[TrackMatch] = []
    for track in eligible_tracks:
        for term in track.search_terms:
            needed = term_stems(term)
            if needed and set(needed) <= in_title:
                matches.append(TrackMatch(track.name, f"title matches '{term}'"))
                break
    if not description:
        return matches

    text_stems = [stem(w) for w in _tokens(_REQUIREMENT_SENTENCE.sub(" ", description))]
    title_tracks = {match.track for match in matches}
    for track in eligible_tracks:
        if track.name in title_tracks:
            continue
        for term in track.search_terms:
            needed = term_stems(term)
            if len(needed) >= 2 and _phrase_in(needed, text_stems):
                phrase = " ".join(w for w in _tokens(term) if w not in _FILLER)
                matches.append(TrackMatch(track.name, f"description mentions '{phrase}'"))
                break
    return matches


def location_allowed(
    track: Track, location: str | None, is_remote: bool | None, company: str | None = None,
) -> bool:
    """False when a track only takes jobs in certain places and this one isn't there.

    A track with `allow_cities` (part-time: Springfield) accepts a posting only if
    its location names one of them, or names an `allow_city_employers` city and
    the company is one listed for it (Chatham: coffee chains only). Remote
    postings pass only when the track allows remote. Tracks without
    `allow_cities` take any location (eligibility still drops non-US jobs).
    """
    rules = track.location_rules
    def in_city(city: str) -> bool:
        # Whole city names avoid "Waco County"/"Lancaster Hills". For
        # Texas-local searches, require the state too: Lancaster, PA is not local.
        suffix = r"\s*,\s*(?:TX|Texas)\b" if rules.near and rules.near.state.upper() == "TX" else r"(?=\s*(?:,|;|$))"
        return bool(re.search(rf"\b{re.escape(city)}{suffix}", location or "", re.I))

    if not rules.allow_cities:
        return True
    if is_remote:
        return rules.allow_remote
    if any(in_city(city) for city in rules.allow_cities):
        return True
    employer = f" {_words(company or '')} "
    return any(
        in_city(city) and any(f" {_words(name)} " in employer for name in names)
        for city, names in rules.allow_city_employers.items()
    )


def _words(text: str) -> str:
    """Lower-case words without punctuation: "Dunkin'" -> "dunkin", "Scooter's" -> "scooters"."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("\u2019", "")))

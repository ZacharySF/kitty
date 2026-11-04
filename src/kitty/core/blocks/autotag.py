"""Keyword matching for auto-tags (platform design Q13): one Aho-Corasick pass per block.

A ``Vocabulary`` holds every tag's terms (its name, aliases and keywords), casefolded
with runs of whitespace collapsed. ``scan`` walks a block's casefolded text once through
the automaton and keeps only whole-word occurrences outside the spans that are not
prose: inline code (backticks or a ``c`` mark), fenced code, URLs and e-mail addresses,
and the tokens that already link something (``#tag``, ``[[page]]``, ``((ref))``,
``{{embed:ref}}``). There is no stemming. Terms shorter than ``MIN_TERM`` characters
are ignored unless their tag opts in (``short``).

Offsets are code points of the block text (Python's); ``utf16`` converts them to the
UTF-16 offsets the editor's DOM and inline marks use. Pure: no database, no I/O.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from kitty.core.blocks.tokens import REF, TAG, WIKI

MIN_TERM = 3
MAX_TERM = 100
#: Spans of block text that are not prose and never match.
_SKIP = re.compile(
    r"```[\s\S]*?(?:```|\Z)"                       # fenced code (to the end when unclosed)
    r"|`[^`\n]+`"                                  # inline code
    r"|\b[a-z][a-z0-9+.-]*://[^\s<>\"']+"          # URLs with a scheme
    r"|\bwww\.[^\s<>\"']+"                         # www. URLs
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+",              # e-mail addresses
    re.IGNORECASE,
)


def normalize_term(value: str) -> str:
    """How a term is compared: casefolded, whitespace runs collapsed to one space."""
    return " ".join(value.casefold().split())


def _wordy(char: str) -> bool:
    return char.isalnum() or char == "_"


@dataclass(frozen=True)
class Tag:
    """One tag as the matcher sees it. ``mode`` is the tag's own override or None."""
    id: str
    name: str
    terms: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    mode: str | None = None
    short: bool = False


@dataclass(frozen=True)
class Match:
    tag: str
    start: int
    end: int
    term: str


class _Automaton:
    """A plain Aho-Corasick automaton over strings (goto dicts, failure links, outputs)."""

    def __init__(self, terms: Iterable[str]):
        self.goto: list[dict[str, int]] = [{}]
        self.fail: list[int] = [0]
        self.out: list[tuple[str, ...]] = [()]
        for term in terms:
            state = 0
            for char in term:
                nxt = self.goto[state].get(char)
                if nxt is None:
                    nxt = len(self.goto)
                    self.goto[state][char] = nxt
                    self.goto.append({})
                    self.fail.append(0)
                    self.out.append(())
                state = nxt
            self.out[state] = (*self.out[state], term)
        queue = list(self.goto[0].values())
        for state in queue:  # breadth first: a state's failure target is always shallower
            for char, nxt in self.goto[state].items():
                queue.append(nxt)
                back = self.fail[state]
                while back and char not in self.goto[back]:
                    back = self.fail[back]
                target = self.goto[back].get(char, 0)
                self.fail[nxt] = target if target != nxt else 0
                self.out[nxt] = (*self.out[nxt], *self.out[self.fail[nxt]])

    def find(self, text: str) -> Iterator[tuple[int, str]]:
        """(end index, term) for every occurrence of every term in `text`."""
        goto, fail, out = self.goto, self.fail, self.out
        state = 0
        for index, char in enumerate(text):
            while state and char not in goto[state]:
                state = fail[state]
            state = goto[state].get(char, 0)
            if out[state]:
                for term in out[state]:
                    yield index + 1, term


def _fold(text: str) -> tuple[str, list[int] | None]:
    """Casefolded text and, when folding changed lengths, each folded char's source index."""
    folded = text.casefold()
    if len(folded) == len(text):  # casefold never shrinks a character, so this is 1:1
        return folded, None
    source: list[int] = []
    for index, char in enumerate(text):
        source.extend([index] * len(char.casefold()))
    return folded, source


def skip_spans(text: str, code_marks: Iterable[tuple[int, int]] = ()) -> list[tuple[int, int]]:
    """Sorted (start, end) code-point spans of `text` that never match."""
    spans = [match.span() for pattern in (_SKIP, TAG, WIKI, REF) for match in pattern.finditer(text)]
    spans.extend(code_marks)
    return sorted(spans)


def utf16(text: str, offsets: Iterable[int]) -> list[int]:
    """Code-point offsets of `text` as UTF-16 offsets (what the editor's marks and DOM use)."""
    values = list(offsets)
    if len(text.encode("utf-16-le")) == 2 * len(text):
        return values
    prefix = [0]
    for char in text:
        prefix.append(prefix[-1] + (2 if ord(char) > 0xFFFF else 1))
    return [prefix[value] for value in values]


def code_points(text: str, offsets: Iterable[int]) -> list[int]:
    """UTF-16 offsets into `text` as code-point offsets (clamped to the text)."""
    values = list(offsets)
    if len(text.encode("utf-16-le")) == 2 * len(text):
        return [max(0, min(len(text), value)) for value in values]
    units: dict[int, int] = {}
    total = 0
    for index, char in enumerate(text):
        units.setdefault(total, index)
        total += 2 if ord(char) > 0xFFFF else 1
    units[total] = len(text)
    result = []
    for value in values:
        value = max(0, min(total, value))
        while value not in units:  # inside a surrogate pair: round down
            value -= 1
        result.append(units[value])
    return result


def code_marks(text: str, marks) -> list[tuple[int, int]]:
    """Inline-code mark ranges of a block (its ``marks`` property) as code-point spans."""
    if not isinstance(marks, list):
        return []
    found = [(mark["s"], mark["e"]) for mark in marks if isinstance(mark, dict) and mark.get("t") == "c"
             and type(mark.get("s")) is int and type(mark.get("e")) is int and mark["e"] > mark["s"]]
    if not found:
        return []
    flat = code_points(text, [value for span in found for value in span])
    return list(zip(flat[::2], flat[1::2]))


@dataclass
class Vocabulary:
    """Every tag's matchable terms in one automaton, plus name/alias lookup for ``#tag`` tokens."""
    tags: dict[str, Tag]
    _automaton: _Automaton = field(init=False, repr=False)
    _owners: dict[str, tuple[str, ...]] = field(init=False, repr=False)
    names: dict[str, tuple[str, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        owners: dict[str, list[str]] = {}
        names: dict[str, list[str]] = {}
        for tag in self.tags.values():
            for value in (tag.name, *tag.aliases):
                name = normalize_term(value)
                if name and tag.id not in names.setdefault(name, []):
                    names[name].append(tag.id)
            for value in (tag.name, *tag.aliases, *tag.terms):
                term = normalize_term(value)
                if not term or len(term) > MAX_TERM or (len(term) < MIN_TERM and not tag.short):
                    continue
                if tag.id not in owners.setdefault(term, []):
                    owners[term].append(tag.id)
        self._owners = {term: tuple(ids) for term, ids in owners.items()}
        self.names = {name: tuple(ids) for name, ids in names.items()}
        self._automaton = _Automaton(self._owners)

    @classmethod
    def of(cls, tags: Iterable[Tag]) -> Vocabulary:
        return cls({tag.id: tag for tag in tags})

    @property
    def empty(self) -> bool:
        return not self._owners

    def inline(self, text: str) -> set[str]:
        """Tag IDs named by ``#name`` tokens in `text` (by tag name or alias)."""
        found: set[str] = set()
        for match in TAG.finditer(text):
            found.update(self.names.get(normalize_term(match[1]), ()))
        return found

    def scan(self, text: str, *, marks=None) -> list[Match]:
        """Whole-word term occurrences outside skipped spans; one per tag per place, leftmost longest."""
        if not self._owners or not text:
            return []
        folded, source = _fold(text)
        hits: list[tuple[int, int, str]] = []
        for end, term in self._automaton.find(folded):
            start = end - len(term)
            if source is not None:
                if start and source[start - 1] == source[start] or end < len(folded) and source[end] == source[end - 1]:
                    continue  # the match starts or ends inside one character's folding
                start, end = source[start], source[end - 1] + 1
            if _wordy(term[0]) and start and _wordy(text[start - 1]):
                continue
            if _wordy(term[-1]) and end < len(text) and _wordy(text[end]):
                continue
            hits.append((start, end, term))
        if not hits:
            return []
        skipped = skip_spans(text, code_marks(text, marks))
        hits.sort(key=lambda hit: (hit[0], hit[0] - hit[1]))
        taken: dict[str, int] = {}
        result: list[Match] = []
        for start, end, term in hits:
            if any(s < end and start < e for s, e in skipped):
                continue
            for tag in self._owners[term]:
                if taken.get(tag, -1) > start:
                    continue  # overlaps this tag's previous, longer-or-earlier match
                taken[tag] = end
                result.append(Match(tag, start, end, term))
        return result

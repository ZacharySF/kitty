"""Resume coach: what a track's postings ask for, and whether its resume mentions it.

Pure, deterministic counting over the vocabulary in config/resume_terms.yaml. For
each track it counts how many of the track's postings mention each term, then
looks the term up in the track's default resume. The output is statistics plus
pointers: a JD phrase (with the posting it came from) and, when the term is on
the resume, the resume line it is on. It never writes or suggests resume text;
whether a missing term is true of me, and how to say it, is my call.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from kitty.config import Config, ResumeTermsFile
from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.models import Job
from kitty.settings import Settings

#: Longest JD snippet shown for a term, not counting the "…" markers.
SNIPPET_CHARS = 120

#: Unicode hyphens PDFs and job boards use where a plain "-" is meant.
_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u00ad": ""})


@dataclass(frozen=True)
class TermStat:
    """How often one vocabulary term shows up in a track's postings."""

    term: str                    # display name from the vocabulary, e.g. "SPI / I2C / UART"
    group: str                   # vocabulary group, e.g. "embedded"
    postings: int                # how many of the track's postings mention it
    share: float                 # postings / track postings, 0..1
    on_resume: bool
    resume_line: str | None      # the resume line that mentions it (when on_resume)
    example_job_id: int | None   # the newest posting that mentions it
    example_company: str | None
    example_title: str | None
    example_snippet: str | None  # the JD phrase around the match, one line


@dataclass(frozen=True)
class TrackReport:
    """The coach's findings for one track."""

    track: str
    resume_label: str | None     # the track's default_resume
    resume_file: str | None
    postings: int                # postings counted for this track
    missing: list[TermStat]      # asked for but not on the resume, most common first
    covered: list[TermStat]      # on the resume, most common first
    error: str | None = None     # why the report is empty, and what to do about it


# ------------------------------------------------------------------ matching


def normalize_text(text: str) -> str:
    """NFKC-normalise text so PDF quirks ("I²C", ligatures, odd hyphens) match plain aliases."""
    return unicodedata.normalize("NFKC", text).translate(_HYPHENS)


#: Left boundary for aliases that start with a letter or digit: not mid-word and
#: not right after a "." (file names and URLs: "page.html").
_WORD_START = r"(?<![\w.])"

#: A one-letter alias ("C", "R") only counts in a list of skills: right after a
#: list separator or "in/or/and/with", or right before one or a word like
#: "programming". That keeps "Level C clearance" and "Plan B" out. "&" is not a
#: separator here because of "R&D".
_LIST_BEFORE = (
    r"(?:(?<=[,(/•;:])|(?<=[,(/•;:]\s)|(?<=\bin\s)|(?<=\bor\s)|(?<=\band\s)|(?<=\bwith\s))"
)
_LIST_AFTER = (
    r"(?=\s*[,/•;)]|\s+(?:or|and|programming|language|languages|code|development)\b)"
)


def _alias_body(alias: str) -> str:
    """Regex source for one alias, without its left boundary (see `_compile`).

    A space or hyphen in the alias matches any run of spaces, hyphens or line
    breaks; aliases of 3+ characters ending in a letter also match a trailing
    "s". An alias ending in a letter or digit never matches right before another
    word character, "+" or "#" (so "C" skips "C++" and "C#"), or before
    ".letter" (so "C" skips "C.F.R."). A one-letter alias also never touches a
    hyphen or "&" ("C-suite", "Frama-C", "R&D", "P&R"), never sits in a letter
    pair like "R/C" or "A/B", and must sit in a list (see _LIST_BEFORE).
    """
    body = r"[\s-]+".join(re.escape(part) for part in re.split(r"[\s-]+", alias))
    if len(alias) >= 3 and alias[-1].isalpha():
        body += "s?"
    if len(alias) == 1 and alias.isalnum():
        body = r"(?<!\b\w/)(?<![&-])" + body + r"(?![\w+#&-]|\.\w|/\w(?![\w+#]))"
        return f"(?:{_LIST_BEFORE}{body}|{body}{_LIST_AFTER})"
    if alias[-1].isalnum():
        body += r"(?![\w+#]|\.\w)"
    return body


def _compile(aliases: list[str], flags: int) -> re.Pattern[str]:
    """One regex over `aliases`, longest first so the fullest phrase wins.

    Aliases starting with a word character share a single left-boundary check in
    front of their alternation; repeating it inside every alternative makes the
    regex several times slower to scan a posting.
    """
    cleaned = sorted((normalize_text(a).strip() for a in aliases), key=len, reverse=True)
    words = [_alias_body(a) for a in cleaned if a[0].isalnum()]
    others = [_alias_body(a) for a in cleaned if not a[0].isalnum()]
    parts = [f"{_WORD_START}(?:{'|'.join(words)})"] if words else []
    parts += others
    return re.compile("|".join(parts), flags)


def _needle(alias: str) -> str:
    """The alias's longest word, lowercased: if it is absent, the alias can't match.

    The longest word is usually the rarest, so it rules out the most postings.
    """
    return max(re.split(r"[\s-]+", normalize_text(alias).strip().lower()), key=len)


@dataclass(frozen=True)
class TermMatcher:
    """One vocabulary term compiled into regexes (any-case aliases, exact-case aliases)."""

    term: str
    group: str
    patterns: tuple[re.Pattern[str], ...]
    #: Plain substrings checked first; regex scanning is the slow part, and most
    #: postings mention none of a term's aliases.
    needles: tuple[str, ...]

    def find(self, text: str, lowered: str | None = None) -> re.Match[str] | None:
        """The earliest match of any alias in normalised `text`, or None.

        Pass `lowered` (text.lower()) when checking many terms against one text.
        """
        haystack = text.lower() if lowered is None else lowered
        if not any(needle in haystack for needle in self.needles):
            return None
        found = [m for pattern in self.patterns if (m := pattern.search(text))]
        return min(found, key=lambda m: m.start(), default=None)


def compile_vocabulary(vocabulary: ResumeTermsFile) -> list[TermMatcher]:
    """Turn config/resume_terms.yaml into matchers, in file order."""
    matchers: list[TermMatcher] = []
    for group in vocabulary.groups:
        for entry in group.terms:
            patterns: list[re.Pattern[str]] = []
            if entry.aliases:
                patterns.append(_compile(entry.aliases, re.IGNORECASE))
            if entry.cased:
                patterns.append(_compile(entry.cased, 0))
            needles = tuple(dict.fromkeys(_needle(alias) for alias in entry.aliases + entry.cased))
            matchers.append(TermMatcher(entry.term, group.name, tuple(patterns), needles))
    return matchers


def snippet(text: str, start: int, end: int, width: int = SNIPPET_CHARS) -> str:
    """One line of `text` around text[start:end], about `width` characters long.

    Whitespace (including line breaks) collapses to single spaces; partial words
    at the cut edges are dropped and marked with "…".
    """
    hit = re.sub(r"\s+", " ", text[start:end])
    before = re.sub(r"\s+", " ", text[max(0, start - 2 * width):start])
    after = re.sub(r"\s+", " ", text[end:end + 2 * width])
    room = max(width - len(hit), 0)
    keep_before = min(len(before), room // 2)
    keep_after = room - keep_before

    if len(before) > keep_before:
        cut = before[len(before) - keep_before:]
        before = "…" + (cut.split(" ", 1)[1] if " " in cut else "")
    if len(after) > keep_after:
        cut = after[:keep_after]
        after = (cut.rsplit(" ", 1)[0] if " " in cut else "") + "…"
    return (before + hit + after).strip()


# -------------------------------------------------------------------- resume


def read_resume_lines(path: Path) -> list[str]:
    """The resume's text as non-empty, whitespace-collapsed lines.

    PDFs go through pypdf; .txt and .md are read as text. Lines are kept so a
    covered term can point at the line it is on. Raises ValueError for any other
    file type.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"can't read {suffix or 'extension-less'} resumes; use a PDF, .txt or .md file")
    lines = (" ".join(normalize_text(_unspace(line)).split()) for line in text.splitlines())
    return [line for line in lines if line]


def _unspace(line: str) -> str:
    """Undo letter-spaced headings ("E D U C A T I O N" -> "EDUCATION").

    Designed resumes space out heading letters; left alone, every single letter
    would match one-letter aliases like "C". Words in such headings are
    separated by two or more spaces, letters by one.
    """
    tokens = line.split()
    if len(tokens) < 3 or any(len(token) > 1 for token in tokens):
        return line
    return " ".join(word.replace(" ", "") for word in re.split(r"\s{2,}", line.strip()))


def _load_resume(
    conn: sqlite3.Connection, settings: Settings, track: str, label: str | None
) -> tuple[str | None, list[str], str | None]:
    """Return (stored file path, lines, error) for the resume labelled `label`."""
    if label is None:
        return None, [], f"track {track} has no default_resume; set one in config/tracks.yaml"
    resume = resumes_core.get_resume_by_label(conn, label)
    if resume is None:
        return None, [], (
            f"no resume registered with label '{label}': "
            f"kitty resumes add <file> --label {label}"
        )
    path = settings.root / resume.file_path
    if not path.exists():
        return resume.file_path, [], (
            f"resume file {resume.file_path} not found; restore it or re-register it: "
            f"kitty resumes add <file> --label {label}"
        )
    try:
        lines = read_resume_lines(path)
    except (ValueError, OSError, PdfReadError) as exc:
        return resume.file_path, [], f"could not read {resume.file_path}: {exc}"
    if not lines:
        return resume.file_path, [], f"no text could be read from {resume.file_path} (scanned image?)"
    return resume.file_path, lines, None


def _resume_line(lines: list[str], text: str, match: re.Match[str]) -> str:
    """The resume line(s) a match sits on; two lines joined when it wraps."""
    first = text.count("\n", 0, match.start())
    last = text.count("\n", 0, match.end())
    return " ".join(lines[first:last + 1])


# ------------------------------------------------------------------ postings


def track_postings(conn: sqlite3.Connection, track: str) -> list[Job]:
    """Active postings on `track` with a description that are not ineligible, newest first."""
    jobs = jobs_core.list_jobs(conn, jobs_core.JobFilter(tracks=[track], order="newest"))
    return [job for job in jobs if job.eligibility != "ineligible" and (job.jd_text or "").strip()]


@dataclass(frozen=True)
class _Scan:
    """One posting's normalised text and the first match span of each term it mentions."""

    text: str
    hits: dict[int, tuple[int, int]]


def _scan(job: Job, matchers: list[TermMatcher]) -> _Scan:
    """Find every vocabulary term in one posting (each counted once).

    The company's own name is blanked out before matching, so "Monolithic Power
    Systems" or "Analog Devices" in the boilerplate doesn't count as the posting
    asking for power systems or analog design. Blanking keeps offsets, so the
    snippet still comes from the original text.
    """
    text = normalize_text(job.jd_text or "")
    masked = _mask(text, job.company_name)
    lowered = masked.lower()
    hits: dict[int, tuple[int, int]] = {}
    for index, matcher in enumerate(matchers):
        match = matcher.find(masked, lowered)
        if match:
            hits[index] = match.span()
    return _Scan(text, hits)


def _mask(text: str, company: str) -> str:
    """`text` with every case-insensitive occurrence of `company` replaced by spaces."""
    name = normalize_text(company).strip()
    if not name:
        return text
    return re.sub(re.escape(name), lambda m: " " * len(m.group()), text, flags=re.IGNORECASE)


# -------------------------------------------------------------------- report


def coach(
    conn: sqlite3.Connection,
    config: Config,
    settings: Settings,
    *,
    track: str | None = None,
    min_share: float = 0.05,
    top: int = 15,
) -> list[TrackReport]:
    """One report per track in config order (or just `track`).

    A term is listed when at least `min_share` of the track's postings mention
    it; `missing` and `covered` each keep the `top` most common. Problems (no
    resume, unreadable file, no postings, unknown track) come back as
    TrackReport.error rather than exceptions so a screen can show them inline.
    """
    matchers = compile_vocabulary(config.resume_terms)
    names = [track] if track is not None else config.track_names
    scans: dict[int, _Scan] = {}          # shared across tracks: a job can be on several
    return [
        _track_report(conn, config, settings, name, matchers, scans, min_share, top)
        for name in names
    ]


def _track_report(
    conn: sqlite3.Connection,
    config: Config,
    settings: Settings,
    name: str,
    matchers: list[TermMatcher],
    scans: dict[int, _Scan],
    min_share: float,
    top: int,
) -> TrackReport:
    """Count one track's postings against its resume."""
    track_config = config.track(name)
    if track_config is None:
        known = ", ".join(config.track_names)
        return TrackReport(name, None, None, 0, [], [], error=f"unknown track '{name}'; tracks are: {known}")
    from kitty.core.resume_versions import default_resume

    selected = default_resume(conn, config, name)
    label = selected.label if selected else track_config.default_resume
    if not matchers:
        return TrackReport(name, label, None, 0, [], [], error=(
            "config/resume_terms.yaml is missing or has no terms; restore it from git"
        ))

    postings = track_postings(conn, name)
    resume_file, lines, problem = _load_resume(conn, settings, name, label)
    if problem is None and not postings:
        problem = (
            f"no active postings with a description on track {name} yet; "
            "run `kitty discover` to collect some"
        )
    if problem:
        return TrackReport(name, label, resume_file, len(postings), [], [], error=problem)

    for job in postings:
        if job.id not in scans:
            scans[job.id] = _scan(job, matchers)

    resume_text = "\n".join(lines)
    stats = [
        stat
        for index, matcher in enumerate(matchers)
        if (stat := _term_stat(index, matcher, postings, scans, lines, resume_text, min_share))
    ]
    stats.sort(key=lambda s: (-s.postings, s.term.lower()))
    return TrackReport(
        track=name,
        resume_label=label,
        resume_file=resume_file,
        postings=len(postings),
        missing=[s for s in stats if not s.on_resume][:top],
        covered=[s for s in stats if s.on_resume][:top],
    )


def _term_stat(
    index: int,
    matcher: TermMatcher,
    postings: list[Job],
    scans: dict[int, _Scan],
    lines: list[str],
    resume_text: str,
    min_share: float,
) -> TermStat | None:
    """The stat for one term on one track, or None if too few postings mention it."""
    mentioning = [job for job in postings if index in scans[job.id].hits]
    share = len(mentioning) / len(postings)
    if not mentioning or share < min_share:
        return None

    example = mentioning[0]                # postings are newest first
    scan = scans[example.id]
    start, end = scan.hits[index]
    on_resume = matcher.find(resume_text)
    return TermStat(
        term=matcher.term,
        group=matcher.group,
        postings=len(mentioning),
        share=share,
        on_resume=on_resume is not None,
        resume_line=_resume_line(lines, resume_text, on_resume) if on_resume else None,
        example_job_id=example.id,
        example_company=example.company_name,
        example_title=example.title,
        example_snippet=snippet(scan.text, start, end),
    )

"""Text normalization used for dedupe keys and lookups.

These functions decide when two postings are "the same job", so they are kept
deliberately boring and are covered by unit tests.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Company legal suffixes that carry no identity ("Stripe, Inc." == "Stripe").
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "l l c", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "lp", "llp", "gmbh", "sa", "ag", "bv", "nv", "pty",
    "holdings", "group", "technologies", "technology",
}

# Words in job titles that vary between boards without changing the role.
_TITLE_NOISE = {
    "intern", "internship", "interns", "co-op", "coop", "student",
    "summer", "fall", "spring", "winter",
    "2026", "2027", "2028", "2029", "2030",
    "us", "usa", "remote", "hybrid", "onsite",
    "f", "m", "d", "w",   # leftovers from "(f/m/d)" style tags
}

_STATE_ABBREV = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne",
    "nevada": "nv", "new hampshire": "nh", "new jersey": "nj",
    "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
    "district of columbia": "dc",
}

# Query parameters boards add for tracking; dropping them makes URLs comparable.
# gh_jid is not tracking: it is the Greenhouse job id on a company's own careers
# page (pinterestcareers.com/jobs/?gh_jid=...), so it stays.
TRACKING_PARAMS = re.compile(
    r"^(utm_|gh_(?!jid$)|ref$|source$|src$|trk|ss$|from$|lever-|_gl$|fbclid$|gclid$|_ccid$)", re.I
)


def _words(text: str) -> list[str]:
    """Lowercase `text` and split it into alphanumeric words."""
    return re.findall(r"[a-z0-9]+", text.lower())


def norm_company(name: str) -> str:
    """Normalize a company name: lowercase, drop punctuation and legal suffixes."""
    words = [w for w in _words(name) if w not in _LEGAL_SUFFIXES]
    if not words:                      # a name made only of suffixes: keep it as-is
        words = _words(name)
    return " ".join(words)


def norm_title(title: str) -> str:
    """Normalize a job title: drop parentheticals, seasons, years and filler words."""
    without_parens = re.sub(r"\(.*?\)|\[.*?\]", " ", title)
    words = [w for w in _words(without_parens) if w not in _TITLE_NOISE]
    if not words:
        words = _words(without_parens)
    return " ".join(words)


def norm_location(location: str | None) -> str:
    """Normalize a location to 'city st' form; empty string when unknown.

    'Remote' in any spelling collapses to 'remote' so remote duplicates match.
    """
    if not location:
        return ""
    text = location.lower()
    text = re.sub(r"\b(united states|usa|u\.s\.a?\.?|us)\b", " ", text)
    if "remote" in text and not re.search(r"\b(hybrid|onsite|on-site)\b", text):
        return "remote"
    for full, abbrev in _STATE_ABBREV.items():
        text = re.sub(rf"\b{re.escape(full)}\b", abbrev, text)
    words = _words(text)
    return " ".join(words)


def dedupe_key(company: str, title: str, location: str | None) -> str:
    """Build the cross-source identity key for a posting.

    Two postings with the same key are treated as one job with several sources.
    """
    return f"{norm_company(company)}|{norm_title(title)}|{norm_location(location)}"


def canonical_url(url: str) -> str:
    """Strip tracking parameters and trailing slashes so URLs compare equal."""
    parts = urlsplit(url.strip())
    kept = [(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept), ""))


def company_from_url(url: str) -> str | None:
    """Guess a company name from a job URL's host, or None when it is a job board.

    Only ever used to pre-fill a form the user then edits - never stored silently.
    """
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if not host:
        return None

    # ATS hosts carry the company in the path, not the host.
    ats_paths = {
        "boards.greenhouse.io": 1, "job-boards.greenhouse.io": 1,
        "jobs.lever.co": 1, "jobs.ashbyhq.com": 1, "careers.smartrecruiters.com": 1,
        "apply.workable.com": 1,
    }
    if host in ats_paths:
        parts = [p for p in urlsplit(url).path.split("/") if p]
        if parts:
            return parts[ats_paths[host] - 1].replace("-", " ").title()
        return None
    if host.endswith(".myworkdayjobs.com"):
        return host.split(".")[0].title()

    generic = {"linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
               "google.com", "simplify.jobs", "handshake.com", "joinhandshake.com"}
    bare = ".".join(host.split(".")[-2:])
    if bare in generic:
        return None

    label = host.split(".")[-2] if host.count(".") >= 1 else host
    if label in {"careers", "jobs", "boards", "apply", "work", "join"}:
        parts = host.split(".")
        label = parts[-2] if len(parts) >= 2 else host
    return label.replace("-", " ").title()


def sha256_text(text: str) -> str:
    """Hex sha256 of a string (used for jd_hash and question hashes)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm_question(text: str) -> str:
    """Normalize a screening question so the same question is only asked once."""
    return " ".join(_words(text))


#: Spellings of the United States in any case: "U.S.", "U.S.A.", "USA", "United States of America".
_US_SPELLED = re.compile(r"\bu\.s\.(?:a\.?)?|\bu\.s\b|\busa\b|\bunited states of america\b", re.IGNORECASE)
#: "US" in capitals only: lower-case "us" is the pronoun ("tell us about yourself").
_US_CAPITALS = re.compile(r"\bUS\b")
#: Programming-language punctuation is meaningful in screening questions.
_C_PLUSPLUS = re.compile(r"\bc\s*\+\s*\+", re.IGNORECASE)
_C_SHARP = re.compile(r"\bc\s*#|\bc\s*sharp\b", re.IGNORECASE)




def norm_screening(text: str) -> str:
    """norm_question, with every spelling of the United States written 'united states'.

    Used to match a form's question to answers.yaml, so "eligible to work in the U.S."
    meets a phrase written "eligible to work in the united states". Question hashes
    keep plain norm_question so already-logged questions keep their identity.
    """
    text = _US_SPELLED.sub(" united states ", text or "")
    if not text.isupper():               # an all-capitals label's "US" may be the pronoun
        text = _US_CAPITALS.sub(" united states ", text)
    text = _C_PLUSPLUS.sub(" cpp ", text)
    text = _C_SHARP.sub(" csharp ", text)
    return norm_question(text)


def question_hash(text: str) -> str:
    """Stable hash of a normalized screening question."""
    return sha256_text(norm_question(text))

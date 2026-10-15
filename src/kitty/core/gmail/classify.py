"""Rules that read an email and say which status it reports, if any.

No model: a fixed phrase list per status, and every hit carries the phrase that
matched so the proposal shows why. Two rules keep it conservative:
  * Precedence rejection > offer > interview > oa. A rejection often mentions
    the interview or assessment that came before it.
  * A phrase in a hedged sentence ("if selected, we will contact you to
    schedule an interview") does not count. That is what keeps plain
    "we received your application" mail at None.
"""

from __future__ import annotations

import re

from kitty.models import Status

REJECTION_PHRASES = (
    "not moving forward", "not be moving forward", "will not be moving",
    "won't be moving forward", "decided to move forward with other candidates",
    "move forward with other candidates", "moving forward with other candidates",
    "pursue other candidates", "decided not to proceed", "not to proceed with your",
    "no longer under consideration", "position has been filled", "role has been filled",
    "regret to inform", "not been selected", "were not selected", "unable to offer you",
    "not able to offer you", "unfortunately",
)
OFFER_PHRASES = (
    "pleased to offer", "happy to offer", "delighted to offer", "excited to offer you",
    "offer letter", "extend you an offer", "extend an offer",
)
INTERVIEW_PHRASES = (
    "invite you to interview", "invite you to an interview", "invite you for an interview",
    "interview invitation", "invitation to interview", "schedule an interview",
    "schedule your interview", "schedule a time to interview", "phone screen",
    "availability for a call", "your availability", "superday",
)
OA_PHRASES = (
    "online assessment", "coding assessment", "technical assessment", "assessment invitation",
    "invited to complete an assessment", "coding challenge", "hackerrank", "codesignal",
    "codility", "hirevue",
)

#: Checked in this order; the first status with an unhedged phrase wins.
_RULES: tuple[tuple[Status, tuple[str, ...]], ...] = (
    ("rejected", REJECTION_PHRASES),
    ("offer", OFFER_PHRASES),
    ("interview", INTERVIEW_PHRASES),
    ("oa", OA_PHRASES),
)

#: Words that make a sentence conditional ("if selected, we will reach out").
_HEDGES = re.compile(
    r"\b(if|whether|should you|in the event)\b"
    r"|(\bwill|\bmay|\bmight|\bwould|'ll) (contact|reach out|be in touch|follow up)"
)
_SENTENCE_END = re.compile(r"[.!?\n]")


def _clean(text: str) -> str:
    """Lowercase, straighten quotes, unwrap lines; a blank line stays a sentence end."""
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("\xa0", " ")
    paragraphs = re.split(r"\n\s*\n", text)
    return "\n".join(re.sub(r"\s+", " ", paragraph).strip() for paragraph in paragraphs)


def _hedged(text: str, start: int) -> bool:
    """True when the sentence holding position `start` is conditional before that point."""
    sentence_start = max((m.end() for m in _SENTENCE_END.finditer(text, 0, start)), default=0)
    return bool(_HEDGES.search(text, sentence_start, start))


def _first_unhedged(text: str, phrases: tuple[str, ...]) -> str | None:
    """The first phrase that appears at least once outside a hedged sentence."""
    for phrase in phrases:
        for match in re.finditer(r"\b" + re.escape(phrase) + r"\b", text):
            if not _hedged(text, match.start()):
                return phrase
    return None


def classify(subject: str, body: str) -> tuple[Status, str] | None:
    """(status, matched phrase) for a status email, or None for anything else."""
    text = _clean(f"{subject}\n\n{body}")
    for status, phrases in _RULES:
        phrase = _first_unhedged(text, phrases)
        if phrase:
            return status, phrase
    return None

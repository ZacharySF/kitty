"""The rules-only eligibility filter. It runs before any LLM call.

Verdicts, checked in this order (the first that applies wins):
  ineligible - outside the US; Simplify says grad degrees only; the title asks
               for a grad degree; a required graduation year I cannot meet; a
               required standing (junior/senior) or grad degree in the text;
               listed hourly pay below the track's min_hourly_pay.
  boosted    - affirmative freshman/sophomore-friendly evidence, without conflicts.
  eligible   - lists hourly pay at or above min_hourly_pay, or is explicitly
               open to undergraduates, with nothing disqualifying.
  ambiguous  - nothing either way; passes through.

Every verdict carries a reason written as "<rule> — <what matched>", so the
report can group by the rule and still show the exact phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from applyai.config import EligibilityRules
from applyai.core.discovery.text import us_location

#: Simplify degree values that are graduate or professional only.
GRAD_DEGREES = {"Master's", "PhD", "MBA", "JD", "MD", "PharmD", "DO", "DDS", "DVM"}

#: "Graduate Intern" / "Intern, Graduate" mean a grad student; \b keeps "Undergraduate" out.
_TITLE_GRAD = re.compile(
    r"\b(ph\.?\s?d|doctoral|master'?s|mba|postdoc(toral)?|(?<!new )(?<!recent )graduate)\b", re.I
)
_UNDERGRAD = re.compile(r"\b(bachelor['’]?s?|undergrad(uate)?s?|b\.?s\.? (degree|in)|bs/ms)\b", re.I)
_UNDERCLASS = re.compile(r"\b(freshm[ae]n|sophomores?|first[- ]years?|second[- ]years?)\b", re.I)
_GRAD_WINDOW = re.compile(
    r"\b(graduat\w*|class of|degree completion|complet\w* (your |their |a |the )?(degree|program)|"
    r"expected to graduate)[^.\n;]{0,80}",
    re.I,
)
_YEAR = re.compile(r"\b(20[2-3]\d)\b")
#: "graduating in December 2026 or later" is a floor, not a requirement.
_OPEN_ENDED = re.compile(r"\b(or later|or after|and later|and beyond|or beyond|onwards?|or more recently)\b|\+", re.I)
#: "if you are graduating prior to May 2027, apply to entry level" turns away
#: earlier graduates; a year after "before" is not something regex can call a
#: requirement, so it passes through instead of dropping the posting.
_BEFORE = re.compile(r"\b(prior to|before)\b", re.I)
#: Boost phrases that are too common in descriptions to trust outside the title.
TITLE_ONLY_BOOSTS = {"explore", "discovery program"}
_RATE = r"(\d{1,3}(?:\.\d{1,2})?)"
#: "$15/hr", "$15.00 - $18.00 per hour", "$12 to $14 an hour", "$15.50 hourly".
_HOURLY = re.compile(
    rf"\$\s?{_RATE}(?:\s*(?:-|–|to)\s*\$?\s?{_RATE})?"
    r"(?:\s*(?:/\s*|per\s+|an?\s+)(?:hour|hr)\b|\s+hourly\b)",
    re.I,
)

# Preserve alternatives, but separate assertions so one cannot cancel another.
_CLAUSE_BREAK = re.compile(
    r"[;\n!?]+|(?<!\b[A-Za-z])\.(?:\s+|$)|"
    r"\b(?:but|however|whereas)\b|"
    r"(?:,\s*|\band\s+)(?!\s*or\b)(?=(?:must|requires?|required)\b|"
    r"[^,;.\n]{0,70}\b(?:required|preferred|not eligible)\b)",
    re.I,
)
_GRAD = re.compile(r"\b(?:master['’]?s?(?:\s+degree)?|ph\.?\s?d\.?|doctoral|mba)\b", re.I)
_STANDING = re.compile(r"\b(?:(?:rising\s+)?(?:juniors?|seniors?)(?:\s+standing)?)\b", re.I)
_REQUIRED = re.compile(
    r"\b(?:must|requires?|required|minimum|at least|only|need to|"
    r"pursuing|enrolled|working towards?|candidates? (?:are|should)|"
    r"applicants? (?:are|should))\b",
    re.I,
)
_OPTIONAL = re.compile(
    r"\b(?:preferred|preferably|optional|not (?:strictly )?(?:required|necessary)|"
    r"not a requirement|no requirement|need not|do not (?:need|require)|"
    r"does not require|don't (?:need|require))\b",
    re.I,
)
_NEGATIVE = re.compile(
    r"\b(?:not|never|no|cannot|can't|ineligible|ineligibility|exclude[ds]?|"
    r"excluding|except|inaccessible|unavailable|without|neither|nor)\b",
    re.I,
)
_EXCLUSION_AFTER = re.compile(
    r"^\s*(?:(?:and|or|,)\s*(?:freshm[ae]n|sophomores?|undergraduates?)\s*)*"
    r"(?:(?:students?|applicants?|candidates?)\s+)?"
    r"(?:(?:are|is|will be)\s+)?(?:not\s+(?:eligible|accepted|allowed|welcome|"
    r"considered|supported)|ineligible|excluded|cannot apply|can't apply|"
    r"may not apply|need not apply)\b",
    re.I,
)
_EXCLUSION_BEFORE = re.compile(
    r"\b(?:no|excluding|excludes?|except|not (?:open|available|accessible) to|"
    r"not (?:intended|suitable) for|do not accept|does not accept)\s+"
    r"(?:(?:applications? from|first[- ]year|freshm[ae]n|sophomores?|"
    r"undergraduates?|and|or)\s+)*$",
    re.I,
)
_AFFIRMATIVE = re.compile(
    r"\b(?:open to|welcome|eligible|accept(?:s|ing|ed)?|program for|"
    r"opportunit(?:y|ies) for|designed for|aimed at|intended for|friendly|"
    r"encouraged to apply|pursuing|enrolled|working towards?|seeking|must|requires?|required)\b",
    re.I,
)
_ALTERNATIVE = re.compile(
    r"^\s*(?:(?:degree|students?|candidates?|standing)\s*)?"
    r"(?:,?\s*or|/|through|to|[-–])\s*(?:a\s+|an\s+|rising\s+)?$",
    re.I,
)
_CONDITIONAL = re.compile(r"\b(?:if|unless|provided|subject to|depending on)\b", re.I)
_ASSERTION_BOUNDARY = re.compile(r",|\b(?:and|with|while)\b", re.I)


def _clauses(text: str) -> list[str]:
    return [clause.strip(" ,:") for clause in _CLAUSE_BREAK.split(text) if clause.strip(" ,:")]


def _has_negation(clause: str) -> bool:
    # "Not only freshmen but also sophomores" is an affirmative inclusion.
    return bool(_NEGATIVE.search(re.sub(r"\bnot only\b", "", clause, flags=re.I)))


def _alternative(clause: str, match: re.Match[str], allowed: re.Pattern[str]) -> bool:
    """Accept a local, explicit alternative, never an unrelated mention."""
    if _has_negation(clause):
        return False
    restricted = _GRAD if allowed is _UNDERGRAD else _STANDING
    options = sorted(
        [*allowed.finditer(clause), *restricted.finditer(clause)],
        key=lambda option: option.start(),
    )
    groups: list[list[re.Match[str]]] = []
    for option in options:
        if groups:
            connector = clause[groups[-1][-1].end():option.start()]
            if connector.strip() == "," or _ALTERNATIVE.fullmatch(connector):
                groups[-1].append(option)
                continue
        groups.append([option])
    for group in groups:
        if not any(option.start() <= match.start() < option.end() for option in group):
            continue
        if not any(allowed.fullmatch(option.group(0)) for option in group):
            continue
        if any(
            _ALTERNATIVE.fullmatch(clause[left.end():right.start()])
            for left, right in zip(group, group[1:])
        ):
            return True
    return False


def _excluded(clause: str, match: re.Match[str]) -> bool:
    return bool(
        _EXCLUSION_BEFORE.search(clause[:match.start()])
        or _EXCLUSION_AFTER.search(clause[match.end():])
    )


def _quoted(clause: str) -> str:
    return "'" + re.sub(r"\s+", " ", clause).strip() + "'"


def _qualification_context(clause: str, match: re.Match[str]) -> str:
    """Keep modifiers local to this qualification, not a neighboring requirement."""
    start, end = 0, len(clause)
    for boundary in _ASSERTION_BOUNDARY.finditer(clause):
        if boundary.end() <= match.start():
            start = boundary.end()
        elif boundary.start() >= match.end():
            end = boundary.start()
            break
    for pattern in (_UNDERGRAD, _UNDERCLASS, _GRAD, _STANDING):
        for other in pattern.finditer(clause):
            if other.end() <= match.start():
                start = max(start, other.end())
            elif other.start() >= match.end():
                end = min(end, other.start())
    return clause[start:end]


def _qualification_verdict(
    title: str, description: str, rules: EligibilityRules,
) -> tuple[Verdict | None, Verdict | None, Verdict | None]:
    """Return restriction, uncertainty and affirmative evidence in priority order.

    This is deliberately not a general language parser. Unresolved negations or
    relationships between qualifications abstain instead of inventing eligibility.
    """
    uncertain = None
    positive = None
    requirements = [(phrase, _phrase(phrase)) for phrase in rules.drop_if_requires]
    if any(re.search(r"master|ph\.?d|doctor", phrase, re.I) for phrase in rules.drop_if_requires):
        requirements.append(("graduate degree", _GRAD))
    if any(re.search(r"junior|senior", phrase, re.I) for phrase in rules.drop_if_requires):
        requirements.append(("junior/senior standing", _STANDING))

    for text, is_title in ((title, True), (description, False)):
        for clause in _clauses(text):
            evidence = _quoted(clause)
            relevant = bool(_UNDERCLASS.search(clause) or _UNDERGRAD.search(clause))
            for pattern in (_UNDERCLASS, _UNDERGRAD):
                for match in pattern.finditer(clause):
                    if _excluded(clause, match):
                        return Verdict("ineligible", f"excludes undergraduates/underclassmen — {evidence}"), None, None
            if relevant and _CONDITIONAL.search(clause):
                uncertain = Verdict("ambiguous", f"conditional eligibility — {evidence}")
                continue

            candidates = requirements + ([("graduate degree in title", _TITLE_GRAD)] if is_title else [])
            for phrase, pattern in candidates:
                for match in pattern.finditer(clause):
                    relevant = True
                    context = _qualification_context(clause, match)
                    if _OPTIONAL.search(context):
                        continue
                    is_degree = bool(re.search(r"master|ph\.?d|doctor|graduate degree", phrase, re.I))
                    allowed = _UNDERGRAD if is_degree else _UNDERCLASS
                    if _alternative(clause, match, allowed):
                        continue
                    if _has_negation(context):
                        uncertain = Verdict("ambiguous", f"unclear qualification — {evidence}")
                        continue
                    # A phrase alone is a qualification heading; contextual mentions
                    # (e.g. working with senior engineers) are not requirements.
                    if (
                        (is_title and pattern is not _STANDING) or _REQUIRED.search(context)
                        or clause.lower() == match.group(0).lower()
                    ):
                        return Verdict("ineligible", f"requires {phrase} — {evidence}"), None, None
                    uncertain = Verdict("ambiguous", f"unclear qualification — {evidence}")

            if relevant and _has_negation(clause) and not _OPTIONAL.search(clause):
                uncertain = Verdict("ambiguous", f"unclear qualification — {evidence}")
                continue

            for phrase in rules.boost_phrases:
                if phrase.lower() in TITLE_ONLY_BOOSTS and not is_title:
                    continue
                match = _phrase(phrase).search(clause)
                if not match:
                    continue
                # "No experience" is itself affirmative, unlike "not no experience".
                remainder = clause[:match.start()] + clause[match.end():]
                if _has_negation(remainder):
                    uncertain = Verdict("ambiguous", f"unclear eligibility evidence — {evidence}")
                    continue
                if _OPTIONAL.search(clause):
                    continue
                if (
                    is_title or _AFFIRMATIVE.search(clause)
                    or not _UNDERCLASS.search(match.group(0))
                    or clause.lower() == match.group(0).lower()
                ):
                    positive = Verdict("boosted", f"mentions {phrase} — {evidence}")
                else:
                    uncertain = Verdict("ambiguous", f"unclear eligibility evidence — {evidence}")
            if (
                not is_title and _UNDERGRAD.search(clause) and _AFFIRMATIVE.search(clause)
                and not _has_negation(clause) and not _OPTIONAL.search(clause)
                and (positive is None or positive.eligibility != "boosted")
            ):
                positive = Verdict("eligible", f"open to undergraduates — {evidence}")
    return None, uncertain, positive


@dataclass(frozen=True)
class Verdict:
    eligibility: str
    reason: str

    @property
    def rule(self) -> str:
        """The part of the reason before the dash, used for grouping."""
        return self.reason.split(" — ")[0]


def merge_rules(rules: list[EligibilityRules]) -> EligibilityRules:
    """Union the rules of every track a job was routed to."""
    def union(lists):
        seen: list = []
        for items in lists:
            for item in items:
                if item not in seen:
                    seen.append(item)
        return seen

    return EligibilityRules(
        drop_if_requires=union(r.drop_if_requires for r in rules),
        drop_if_grad_year_required=union(r.drop_if_grad_year_required for r in rules),
        boost_phrases=union(r.boost_phrases for r in rules),
        min_hourly_pay=max((r.min_hourly_pay for r in rules if r.min_hourly_pay is not None), default=None),
    )


def hourly_pay(text: str) -> tuple[float, str] | None:
    """The highest hourly rate a posting lists (top of a range) and the text that says it.

    The highest, so "$11-$14/hr" or "$10/hr in training, then $14" still counts
    as reaching $14: a posting is only dropped when every rate it names is lower.
    """
    best: tuple[float, str] | None = None
    for match in _HOURLY.finditer(text):
        top = float(match.group(2) or match.group(1))
        if best is None or top > best[0]:
            best = (top, _snippet(text, match.start(), match.end(), 20))
    return best


def _snippet(text: str, start: int, end: int, pad: int = 50) -> str:
    """The matched text with some context, on one line."""
    piece = text[max(0, start - pad): end + pad]
    return "'" + re.sub(r"\s+", " ", piece).strip() + "'"


def _phrase(phrase: str) -> re.Pattern[str]:
    """A whole-word, case-insensitive pattern for a config phrase.

    Tolerates apostrophes, hyphens-or-spaces and plurals, so 'freshman' also
    finds 'freshmen' and 'sophomore' finds 'sophomores'.
    """
    escaped = re.escape(phrase.lower()).replace("'", "['’]?").replace(r"\-", "[- ]?").replace(r"\ ", r"[\s-]+")
    if escaped.endswith("man"):
        escaped = escaped[:-3] + "m[ae]n"
    return re.compile(rf"\b{escaped}s?\b", re.I)


def _grad_year_verdict(text: str, years: list[int]) -> Verdict | None:
    """Ineligible when a graduation window names only years I cannot meet."""
    if not years:
        return None
    blocked = set(years)
    for match in _GRAD_WINDOW.finditer(text):
        window = match.group(0)
        found = {int(y) for y in _YEAR.findall(window)}
        if _OPEN_ENDED.search(window):
            continue
        first_year = _YEAR.search(window)
        if first_year and _BEFORE.search(window[:first_year.start()]):
            continue
        if found and found <= blocked:
            listed = ", ".join(str(y) for y in sorted(found))
            return Verdict("ineligible", f"graduation in {listed} required — {_snippet(text, match.start(), match.end(), 10)}")
    return None


def evaluate(
    *,
    title: str,
    description: str | None,
    location: str | None,
    country: str | None,
    degrees: list[str] | None,
    rules: EligibilityRules,
) -> Verdict:
    """The eligibility verdict for one posting."""
    if us_location(location, country) is False:
        return Verdict("ineligible", f"outside the US — {location or country}")

    if degrees and set(degrees) <= GRAD_DEGREES:
        return Verdict("ineligible", f"Simplify degrees are graduate-only — {', '.join(degrees)}")

    text = description or ""
    both = f"{title}\n{text}"
    restriction, uncertain, positive = _qualification_verdict(title, text, rules)

    year_verdict = _grad_year_verdict(text, rules.drop_if_grad_year_required)
    if year_verdict:
        return year_verdict

    if restriction:
        return restriction

    floor = rules.min_hourly_pay
    pay = hourly_pay(both) if floor is not None else None
    if pay and pay[0] < floor:
        return Verdict("ineligible", f"pays under ${floor:g}/hr — {pay[1]}")

    if uncertain:
        return uncertain
    if positive and positive.eligibility == "boosted":
        return positive

    if pay:
        return Verdict("eligible", f"pays ${floor:g}+/hr — {pay[1]}")
    if positive:
        return positive
    if not text:
        return Verdict("ambiguous", "no description to check — title and location only")
    return Verdict("ambiguous", "no standing, graduation or degree requirement found")

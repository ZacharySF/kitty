"""Deciding what goes into one form field. Pure functions, no browser.

The filler reads each field's label (and name / id / autocomplete), then asks
`plan_for` what to put there. Values only ever come from two places:

* config/answers.yaml, matched on the normalized question text, and
* config/profile.yaml, for the handful of contact and education fields every
  form asks for (name, email, phone, links, school, major, graduation, city).

When neither gives a confident match the answer is None and the field is left
blank. Nothing is guessed, and demographic (EEO) questions are never filled
from the profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Sequence

from applyai.config import AnswerEntry, Config, Profile
from applyai.normalize import norm_location, norm_question

#: Kinds of field the page reader reports.
TEXT_KINDS = ("text", "textarea")

#: Demographic / EEO questions: only answers.yaml may fill these.
_DEMOGRAPHIC = re.compile(
    r"\b(gender|sex|race|racial|ethnicity|ethnic|hispanic|latino|latinx|veteran|"
    r"disability|disabled|sexual orientation|transgender|pronoun|pronouns|lgbt\w*)\b"
)

#: Trailing markers forms add to labels that are not part of the question.
_LABEL_NOISE = re.compile(r"(\s*[*✱]\s*|\s*\((required|optional)\)\s*)+$", re.IGNORECASE)

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")

#: The term a month's graduation falls in: January-May Spring, June-August Summer,
#: September-December Fall (forms list graduation choices like "2032 - Spring").
_SEASON_OF_MONTH = ("Spring",) * 5 + ("Summer",) * 3 + ("Fall",) * 4
_SEASONS = ("Spring", "Summer", "Fall", "Winter")


@dataclass(frozen=True)
class FormField:
    """One question on a form, as read from the page.

    Radio buttons and checkboxes that answer the same question are one
    FormField whose `options` are the choices and `option_keys` the elements.
    """

    key: str                          # data-applyai-id of the element (first option for groups)
    kind: str                         # text | textarea | select | combobox | radio | checkbox | file
    label: str                        # the question as the form shows it
    frame: int = 0                    # index of the frame the field lives in
    input_type: str = ""              # the HTML type attribute (email, tel, month, ...)
    name: str = ""
    element_id: str = ""
    autocomplete: str = ""
    required: bool = False
    options: tuple[str, ...] = ()     # choice texts for select / radio / checkbox
    option_keys: tuple[str, ...] = () # element keys for radio / checkbox choices
    blank: bool = True                # nothing entered or chosen yet
    in_autofill: bool = False         # inside a "fill from resume" widget
    invalid: bool = False             # browser / ATS validation did not accept the value


@dataclass(frozen=True)
class Plan:
    """What to put in a field and where the value came from."""

    field_name: str                   # human-readable name for the report, e.g. "email"
    candidates: tuple[str, ...]       # values to try, best first
    search_text: str | None = None    # what to type into a search-as-you-type box
    location: bool = False            # compare options as places ("Austin, Texas" == "Austin, TX")
    from_answers: bool = False
    exact_choices: bool = False       # majors must not match a different program by prefix


# ------------------------------------------------------------------ labels


def clean_label(text: str) -> str:
    """Strip whitespace and required markers ('*', '✱', '(required)') from a label."""
    collapsed = " ".join((text or "").split())
    return _LABEL_NOISE.sub("", collapsed).strip()


def is_demographic(label: str) -> bool:
    """True for EEO / self-identification questions (gender, race, veteran...)."""
    return bool(_DEMOGRAPHIC.search(norm_question(label)))


def is_resume_input(field: FormField) -> bool:
    """True for the file input that takes the resume / CV (not the cover letter).

    "Autofill from resume" widgets are excluded: they would overwrite what I
    typed with whatever the site parsed out of the PDF.
    """
    if field.kind != "file" or field.in_autofill:
        return False
    text = norm_question(" ".join((field.label, field.name, field.element_id)))
    if "autofill" in text or "cover" in text:
        return False
    return bool(re.search(r"\b(resume|cv|resume cv)\b", text))


# ----------------------------------------------------------------- answers


def lookup_answer(
    config: Config, label: str, *, company: str | None = None, tracks: Sequence[str] = (),
) -> AnswerEntry | None:
    """The answers.yaml entry for this question, or None.

    Config.answer_for already ignores case and punctuation; this also drops
    '(required)' style markers. No fuzzy matching: a near miss is a miss.
    """
    for text in dict.fromkeys((label, clean_label(label))):
        if text and (entry := config.answer_for(text, company, tracks)) is not None:
            return entry
    return None


def _undecorated_option(text: str) -> str:
    """Drop only an acronym that spells the initials of the complete option name."""
    leading = re.fullmatch(r"\s*([A-Z]{2,10})\s+[-–—:]\s+(.+?)\s*", text)
    trailing = re.fullmatch(r"\s*(.+?)\s+\(([A-Z]{2,10})\)\s*", text)
    if leading:
        acronym, name = leading.groups()
    elif trailing:
        name, acronym = trailing.groups()
    else:
        return norm_question(text)
    words = norm_question(name).split()
    initials = "".join(word[0] for word in words if word not in {"and", "of", "the"})
    return norm_question(name) if initials == acronym.lower() else norm_question(text)


def match_option(
    candidates: tuple[str, ...] | list[str],
    options: tuple[str, ...] | list[str],
    *,
    location: bool = False,
    prefix: bool = True,
) -> int | None:
    """Index of the option that matches a candidate value, or None.

    Each candidate's exact match precedes its acronym-decorated equivalents,
    before trying alternate candidates. Unique whole-word prefixes come last.
    Decoration must spell the full name's initials, e.g. "ECE - Electrical and
    Computer Engineering". Duplicate
    matches are ambiguous, not a reason to choose the first row. `prefix=False`
    excludes extensions such as a different engineering technology program.
    With `location=True`, places compare by city and state.
    """
    normalize: Callable[[str], str] = norm_location if location else norm_question
    normalized = [normalize(option) for option in options]
    wanted_values = [wanted for candidate in candidates if (wanted := normalize(candidate))]
    undecorated = [] if location else [_undecorated_option(option) for option in options]
    for candidate in candidates:
        wanted = normalize(candidate)
        if not wanted:
            continue
        exact = [i for i, text in enumerate(normalized) if text == wanted]
        if exact:
            return exact[0] if len(exact) == 1 else None
        if not location:
            wanted = _undecorated_option(candidate)
            equivalent = [i for i, text in enumerate(undecorated) if wanted and text == wanted]
            if equivalent:
                return equivalent[0] if len(equivalent) == 1 else None
    if prefix:
        for wanted in wanted_values:
            prefixed = [i for i, text in enumerate(normalized) if text.startswith(wanted + " ")]
            if prefixed:
                return prefixed[0] if len(prefixed) == 1 else None
    return None


def is_affirmative(answer: str) -> bool:
    """True when an answers.yaml answer means 'tick the box'."""
    return norm_question(answer) in {"yes", "true", "i agree", "agree", "checked", "i accept"}


# ----------------------------------------------------------------- profile


def is_graduation_question(words: str) -> bool:
    """True when normalized label `words` asks for my graduation date or year.

    A high-school graduation is not it: profile.graduation is the college date.
    """
    return bool(re.search(r"\b(graduation|grad date|grad year)\b", words)) and not re.search(
        r"\bhigh school\b", words
    )


def _term_spellings(year: str, season: str) -> tuple[str, ...]:
    """A graduation term as choice lists write it: 'Spring 2032' and '2032 - Spring'."""
    return (f"{season} {year}", f"{year} - {season}")


def graduation_answer_spellings(answer: str) -> tuple[str, ...]:
    """The term spellings of a graduation answer like 'May 2032' or 'Spring 2032'.

    The answer is only re-worded, never changed: May 2032 is the Spring 2032 term.
    Anything that is not '<month or season> <year>' gives nothing.
    """
    words = norm_question(answer).split()
    if len(words) != 2 or not re.fullmatch(r"\d{4}", words[1]):
        return ()
    name, year = words
    months = [month.lower() for month in _MONTHS]
    if name in months:
        return _term_spellings(year, _SEASON_OF_MONTH[months.index(name)])
    if name.title() in _SEASONS:
        return _term_spellings(year, name.title())
    return ()


def _graduation(profile: Profile, field: FormField, words: str) -> tuple[str, ...]:
    """The graduation date (profile: YYYY-MM) in the format the field wants."""
    year, _, month = profile.graduation.partition("-")
    month_name = _MONTHS[int(month) - 1] if month.isdigit() and 1 <= int(month) <= 12 else ""
    if field.input_type == "month":
        return (profile.graduation,)
    if "year" in words and "month" not in words:
        return (year,)
    if "month" in words and "year" not in words:
        return tuple(v for v in (month_name, month, str(int(month)) if month.isdigit() else "") if v)
    if not month_name:
        return (year,)
    term = _term_spellings(year, _SEASON_OF_MONTH[int(month) - 1])
    return (f"{month_name} {year}", f"{month}/{year}", profile.graduation, *term)


def _full(pattern: str, text: str) -> bool:
    return re.fullmatch(pattern, text) is not None


def _school_names(profile: Profile) -> tuple[str, ...]:
    """The school as written, then with its campus city (how school pickers list campuses)."""
    names = [profile.school]
    if profile.school_city:
        names += [f"{profile.school} - {profile.school_city}", f"{profile.school} {profile.school_city}"]
    return tuple(names)


#: Spellings forms use for the same degree. Only the degree I wrote is ever chosen.
_DEGREE_SPELLINGS = {
    "bs": ("Bachelor of Science", "Bachelor's", "Bachelor's Degree", "Bachelors", "BS", "B.S."),
    "ba": ("Bachelor of Arts", "Bachelor's", "Bachelor's Degree", "Bachelors", "BA", "B.A."),
    "bachelor": ("Bachelor's", "Bachelor's Degree", "Bachelors"),
}


def _degree_names(degree: str | None) -> tuple[str, ...]:
    """The degree as written, then the usual spellings of its level ('B.S.' -> 'Bachelor of Science')."""
    if not degree:
        return ()
    first = norm_question(degree).split()
    key = "bs" if first[:2] == ["b", "s"] or first[:1] == ["bs"] else (
        "ba" if first[:2] == ["b", "a"] or first[:1] == ["ba"] else (
            "bachelor" if first and first[0].startswith("bachelor") else None))
    return (degree, *_DEGREE_SPELLINGS.get(key, ()))


def _country_names(country: str | None) -> tuple[str, ...]:
    """The country as written, plus the usual spellings of the United States."""
    if not country:
        return ()
    if norm_question(country) in ("united states", "united states of america", "usa", "us"):
        return (country, "United States", "United States of America", "USA", "US")
    return (country,)


#: Lists that only offer the combined program file both of its halves under it.
_ECE_HALVES = {"electrical engineering", "computer engineering"}
_ECE_NAMES = ("Electrical and Computer Engineering", "Electrical & Computer Engineering")


def _major_names(major: str) -> tuple[str, ...]:
    """The major as written, without its '(...)' note, and how discipline lists write it.

    Discipline lists often file a general major under its field ('Engineering
    (General)'), and some only have the combined 'Electrical and Computer
    Engineering' for either half. The exact name always comes first.
    """
    base = major.split("(")[0].strip()
    names = [major, base]
    if base.lower().startswith("general ") and len(base.split()) > 1:
        field_name = base.split(" ", 1)[1]
        names += [f"{field_name} (General)", f"{field_name}, General"]
    if base.lower() in _ECE_HALVES or base.lower() in {name.lower() for name in _ECE_NAMES}:
        names += _ECE_NAMES
    return tuple(dict.fromkeys(names))


def profile_plan(field: FormField, profile: Profile, *, tracks: Sequence[str] = ()) -> Plan | None:
    """Map a contact / education field to a profile.yaml value, or None.

    Labels must match one of a few short, specific shapes ("First Name",
    "LinkedIn Profile URL", "College or University"); long questions that just
    mention a word like 'email' or 'graduate' do not match. A few longer shapes
    are allowed where the field offers choices, since a value only lands there
    when it equals one of them.
    """
    words = norm_question(field.label)
    if not words:                       # no label: fall back to name / id (Lever: name="email")
        words = norm_question(f"{field.name} {field.element_id}")
    auto = field.autocomplete.lower()
    short = len(words.split()) <= 6
    choice = field.kind not in TEXT_KINDS

    def plan(name: str, *values: str | None) -> Plan | None:
        kept = tuple(v for v in values if v)
        return Plan(name, kept) if kept else None

    if auto == "given-name" or (_full(r"(legal )?(first|given) name", words)):
        return plan("first name", profile.given_name)
    if auto == "family-name" or _full(r"(legal )?(last|family) name|surname", words):
        return plan("last name", profile.family_name)
    if auto == "name" or _full(r"(your |full |legal |full legal |candidate |applicant )?name", words):
        return plan("full name", profile.name)
    if auto == "email" or _full(
        r"(your |confirm |confirm your )?e ?mail( address)?", words
    ):
        return plan("email", profile.email)
    if auto in ("tel", "tel-national") or _full(
        r"(your )?(mobile |cell |primary )?(phone|telephone|mobile)( number)?", words
    ):
        return plan("phone", profile.phone)
    if short and re.search(r"\blinkedin\b", words):
        return plan("LinkedIn", profile.linkedin)
    if short and re.search(r"\bgithub\b", words):
        return plan("GitHub", profile.github)
    if _full(r"(personal |other )?(website|web site|portfolio|personal site)( url| link)?", words):
        return plan("website", profile.website)
    if _full(
        r"(name of )?(your )?(current )?(school|university|college|college or university|"
        r"university or college|institution|educational institution|school university)( name)?",
        words,
    ) or re.match(
        r"(which|what) (school|university|college|college or university) "
        r"(are you (currently )?attending|do you (currently )?attend)\b", words
    ):
        return plan("school", *_school_names(profile))
    if _full(r"(current |highest )?degree( type| level)?( currently pursuing)?", words) or (
        choice and _full(r"(current )?(academic |degree )?program type", words)
    ):
        return plan("degree", *_degree_names(profile.degree))
    if _full(
        r"(your )?(intended )?(major|discipline|field of study|area of study|"
        r"major or field of study|field of study or major|major field of study|concentration)",
        words,
    ):
        return Plan("major", _major_names(profile.discipline_for(tracks)), exact_choices=True)
    if _full(r"(your )?(current )?country( of residence)?", words):
        return plan("country", *_country_names(profile.country))
    if (short or choice) and is_graduation_question(words):
        return plan("graduation", *_graduation(profile, field, words))
    if _full(
        r"(current |your current |home )?(location|city|location city|city state|"
        r"city and state|city of residence)",
        words,
    ):
        city = profile.city_for(tracks)
        return Plan("location", (f"{city}, {profile.home_state}",), search_text=city, location=True)
    return None


# -------------------------------------------------------------------- plan


def _answer_plan(field: FormField, entry: AnswerEntry) -> Plan:
    """The plan for an answers.yaml entry: the answer, then my `choices` wordings of it.

    A graduation answer ('May 2032') also offers its term ('2032 - Spring').
    """
    candidates = [entry.answer, *entry.choices]
    if is_graduation_question(norm_question(field.label)):
        candidates += graduation_answer_spellings(entry.answer)
    name = clean_label(field.label) or clean_label(field.options[0] if field.options else "")
    return Plan(name[:60], tuple(dict.fromkeys(candidates)), search_text=entry.answer,
                from_answers=True)


def plan_for(
    field: FormField, config: Config, *, company: str | None = None, tracks: Sequence[str] = (),
) -> Plan | None:
    """What to put in `field`: answers.yaml first, then profile.yaml, else None.

    answers.yaml wins because I wrote that exact question's answer myself.
    `tracks` are the job's tracks; they pick the discipline (profile.discipline_by_track).
    File inputs are not planned here; the filler handles the resume.
    """
    if field.kind == "file" or not field.label and not field.name:
        return None
    entry = lookup_answer(config, field.label, company=company, tracks=tracks)
    if entry is None and field.kind == "checkbox" and len(field.options) == 1:
        entry = lookup_answer(
            config, field.options[0], company=company, tracks=tracks,
        )                                                       # "I agree to ..." boxes
    if entry is not None:
        return _answer_plan(field, entry)
    if is_demographic(field.label):
        return None
    return profile_plan(field, config.profile, tracks=tracks)

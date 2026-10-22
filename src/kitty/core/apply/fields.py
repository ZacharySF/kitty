"""Deciding what goes into one form field. Pure functions, no browser.

The filler reads each field's label (and name / id / autocomplete), then asks
`plan_for` what to put there. Values come from, in order:

* config/answers.yaml, matched on the normalized question text,
* config/profile.yaml, for the handful of contact and education fields every
  form asks for (name, email, phone, links, school, major, graduation, city), and
* a plain offered source choice for "how did you hear about us".

When none gives a confident match the answer is None and the field is left
blank. Demographic (EEO) questions are only ever filled from answers.yaml.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from kitty.config import AnswerEntry, Config, Profile
from kitty.normalize import norm_location, norm_question, norm_screening

#: Kinds of field the page reader reports.
TEXT_KINDS = ("text", "textarea")

MANUAL_CONSENT = re.compile(
    r"\b(?:agree|consent|certify|attest|signature|terms(?: and conditions| of use| of service)?|"
    r"privacy (?:policy|notice)|accept.{0,80}(?:privacy|data processing)|"
    r"einverstanden|einwilligung|datenschutzerklärung)\b", re.I,
)

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

    key: str                          # data-kitty-id of the element (first option for groups)
    kind: str                         # text | textarea | select | combobox | radio | checkbox | file | unsupported
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
    attachment_only: bool = False    # observed separate attachment, not the profile-import widget


@dataclass(frozen=True)
class Plan:
    """What to put in a field and where the value came from."""

    field_name: str                   # human-readable name for the report, e.g. "email"
    candidates: tuple[str, ...]       # values to try, best first
    search_text: str | None = None    # what to type into a search-as-you-type box
    location: bool = False            # compare options as places ("Austin, Texas" == "Austin, TX")
    from_answers: bool = False
    exact_choices: bool = False       # majors must not match a different program by prefix
    graduation: str | None = None     # saved exact YYYY-MM; enables offered date-range buckets only


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
    jurisdiction: str | None = None, options: Sequence[str] = (),
) -> AnswerEntry | None:
    """The answers.yaml entry for this question, or None.

    Config.answer_for already ignores case and punctuation; this also drops
    '(required)' markers. A named employer's exact "ever worked for" question
    may use the saved generic company-history wording, with normal scope/tie
    rules. `options` preserves explicit field-country evidence; `jurisdiction`
    is the strict job jurisdiction, never the applicant's residence/citizenship.
    Other near misses are not answers.
    """
    # "Postal Code Extension" (ZIP+4) is a different fact from "Postal Code";
    # a parent answer never fills its extension sub-field.
    extension = re.search(r"\bextension\b", label, re.I)
    for text in dict.fromkeys((label, clean_label(label))):
        if text and (entry := config.answer_for(
            text, company, tracks, jurisdiction=jurisdiction, options=options,
        )) is not None:
            if extension and not any(
                re.search(r"\bextension\b", phrase, re.I) for phrase in (entry.question, *entry.match)
            ):
                return None
            return entry
    if company:
        original = f" {norm_screening(clean_label(label))} "
        if any(
            norm_screening(entry.question) == original.strip() or any(
                f" {norm_screening(phrase)} " in original for phrase in entry.match
                if norm_screening(phrase)
            ) for entry in config.answers.answers
        ):
            return None                # do not bypass an explicit match's scope or tie
        # Only the actual employer (or its explicitly parenthesized alias), not
        # another company, future employment, or an arbitrary history question.
        employers = {
            norm_question(company),
            norm_question(re.sub(r"\([^)]*\)", "", company)),
            *(norm_question(alias) for alias in re.findall(r"\(([^)]+)\)", company)),
        }
        words = norm_question(clean_label(label))
        for employer in employers - {""}:
            if re.fullmatch(
                r"have you ever worked for " + re.escape(employer) +
                r"(?: one of our current brands or any of our predecessor firms)?", words,
            ):
                return config.answer_for(
                    "Have you worked for this company?", company, tracks,
                    jurisdiction=jurisdiction, options=options,
                )
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


def _graduation_period(text: str) -> tuple[int, int] | None:
    """Closed month interval under the same term convention as graduation dates."""
    iso = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", text.strip())
    if iso:
        year, month = map(int, iso.groups())
        value = year * 12 + month - 1
        return value, value
    words = norm_question(text).split()
    if len(words) == 1 and re.fullmatch(r"\d{4}", words[0]):
        year = int(words[0])
        return year * 12, year * 12 + 11
    if len(words) != 2:
        return None
    if re.fullmatch(r"\d{4}", words[0]):
        words.reverse()
    name, year = words
    if not re.fullmatch(r"\d{4}", year):
        return None
    base = int(year) * 12
    months = [month.lower() for month in _MONTHS]
    if name in months:
        value = base + months.index(name)
        return value, value
    # Winter spans calendar years and has no unambiguous year ownership.
    term_months = [i for i, season in enumerate(_SEASON_OF_MONTH) if season.lower() == name]
    return (base + min(term_months), base + max(term_months)) if term_months else None


def _graduation_bucket_index(date: str, options: Sequence[str]) -> int | None:
    """Only a unique offered interval may contain the saved month; never nearest."""
    saved = _graduation_period(date)
    if saved is None or saved[0] != saved[1]:
        return None
    wanted = saved[0]
    matches = []
    for index, option in enumerate(options):
        words = norm_question(option)
        bound = re.fullmatch(r"(earlier|later) than (.+)", words)
        period = _graduation_period(bound[2] if bound else option)
        if period is None:
            continue
        if bound:
            contained = wanted < period[0] if bound[1] == "earlier" else wanted > period[1]
        else:
            contained = period[0] <= wanted <= period[1]
        if contained:
            matches.append(index)
    return matches[0] if len(matches) == 1 else None


def match_option(
    candidates: tuple[str, ...] | list[str],
    options: tuple[str, ...] | list[str],
    *,
    location: bool = False,
    prefix: bool = True,
    graduation: str | None = None,
) -> int | None:
    """Index of the option that matches a candidate value, or None.

    Each candidate's exact match precedes its acronym-decorated equivalents,
    before trying alternate candidates. Unique whole-word prefixes come last.
    Decoration must spell the full name's initials, e.g. "ECE - Electrical and
    Computer Engineering". Duplicate
    matches are ambiguous, not a reason to choose the first row. `prefix=False`
    excludes extensions such as a different engineering technology program.
    With `location=True`, places compare by city and state.
    `graduation` is an exact saved month, authorized by the question's plan:
    offered range buckets must contain it uniquely, without nearest-year guesses.
    """
    if graduation and any(re.fullmatch(r"(earlier|later) than .+", norm_question(option))
                          for option in options):
        return _graduation_bucket_index(graduation, options)
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
        if not location and re.fullmatch(r"\+\d{1,3}", candidate):
            calling_code = [
                i for i, text in enumerate(options)
                if re.search(r"(?:^|\s|\()\+" + re.escape(candidate[1:]) + r"\)?\s*$", text)
                or re.fullmatch(re.escape(candidate) + r"\s+\([^()]+\)", text.strip())
            ]
            if calling_code:
                return calling_code[0] if len(calling_code) == 1 else None
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


# ITU E.164 assigned country calling codes (not area codes or nationality).
# Parsing uses the explicit '+' prefix; only a saved US default region permits
# the conventional ten-digit NANP national spelling below.
_CALLING_CODES = frozenset("""
1 7 20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49
51 52 53 54 55 56 57 58 60 61 62 63 64 65 66 81 82 84 86
90 91 92 93 94 95 98 211 212 213 216 218 220 221 222 223 224
225 226 227 228 229 230 231 232 233 234 235 236 237 238 239
240 241 242 243 244 245 246 247 248 249 250 251 252 253 254
255 256 257 258 260 261 262 263 264 265 266 267 268 269 290
291 297 298 299 350 351 352 353 354 355 356 357 358 359 370
371 372 373 374 375 376 377 378 379 380 381 382 383 385 386
387 389 420 421 423 500 501 502 503 504 505 506 507 508 509
590 591 592 593 594 595 596 597 598 599 670 672 673 674 675
676 677 678 679 680 681 682 683 685 686 687 688 689 690 691
692 850 852 853 855 856 880 886 960 961 962 963 964 965 966
967 968 970 971 972 973 974 975 976 977 992 993 994 995 996 998
""".split())


def _phone_country_code(phone: str | None, default_region: str | None) -> tuple[str, ...]:
    """Normalize a saved international or validated US national number, if present."""
    if not phone or not re.fullmatch(r"\s*\+?[\d\s().-]+\s*", phone):
        return ()
    digits = re.sub(r"\D", "", phone)
    if phone.lstrip().startswith("+"):
        if not 7 <= len(digits) <= 15:
            return ()
        code = next((digits[:size] for size in (1, 2, 3)
                     if digits[:size] in _CALLING_CODES), "")
        if not code or len(digits) - len(code) < 4:
            return ()
        if code == "1" and not re.fullmatch(r"1[2-9]\d{2}[2-9]\d{6}", digits):
            return ()
    elif norm_question(default_region or "") in {
        "united states", "united states of america", "us", "usa",
    } and re.fullmatch(r"[2-9]\d{2}[2-9]\d{6}", digits):
        code = "1"
    else:
        return ()
    region = norm_question(default_region or "")
    if code == "1" and region in {"united states", "united states of america", "us", "usa"}:
        # "+1" alone is shared by Canada and the Caribbean; lists that name the
        # region ("🇺🇸 (+1) United States") need the saved country with its code.
        return (f"+{code}", code, "(+1) United States", "(+1) United States of America",
                "United States (+1)", "United States of America (+1)", "+1 United States")
    return (f"+{code}", code)


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
    words = re.sub(r"^(?:what is your|please (?:enter|provide) your) ", "", words)
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
    if auto == "tel-country-code" or _full(
        r"(phone )?country( territory)?( phone)? code|country calling code", words
    ):
        codes = _phone_country_code(profile.phone, profile.country)
        if "(+1) United States" in codes:
            # Long virtualized lists ("254 matches") render the saved region only
            # after searching for it; "+1" alone would surface Canada first.
            return Plan("phone country code", codes, search_text="United States")
        return plan("phone country code", *codes)
    if auto in ("tel", "tel-national") or _full(
        r"(your )?(mobile |cell |primary )?(phone|telephone|mobile)( number)?|telefonnummer", words
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
    if _full(r"(your )?(current )?country( territory| of residence)?", words):
        return plan("country", *_country_names(profile.country))
    if (short or choice) and is_graduation_question(words):
        return Plan("graduation", _graduation(profile, field, words), graduation=profile.graduation)
    if (_full(r"(your |current |your current |home |contact |residential )?state( of residence)?|state province|state or province", words) or
            auto == "address-level1" and not re.search(
                r"\b(birth|born|nationality|citizenship|employment|employer|work|job|school|education|street|zip|postal|phone|device)\b", words)):
        if profile.home_state:
            return Plan("state", (profile.home_state,), location=True, exact_choices=True)
    if auto == "address-level2" or _full(
        r"(current |your current |home )?(location|city|location city|city state|"
        r"city and state|city of residence)|stadt",
        words,
    ):
        city = profile.city_for(tracks)
        if field.kind in TEXT_KINDS and (
                auto == "address-level2" or _full(r"(current |your current |home )?city( of residence)?|stadt", words)):
            return plan("city", city)
        return Plan("location", (f"{city}, {profile.home_state}",), search_text=city, location=True)
    return None


# -------------------------------------------------------------------- plan


def _answer_plan(field: FormField, entry: AnswerEntry) -> Plan:
    """The plan for an answers.yaml entry: the answer, then my `choices` wordings of it.

    A graduation answer ('May 2032') also offers its term ('2032 - Spring').
    """
    candidates = [entry.answer, *entry.choices]
    graduation = None
    if is_graduation_question(norm_question(field.label)):
        candidates += graduation_answer_spellings(entry.answer)
        period = _graduation_period(entry.answer)
        if period and period[0] == period[1]:
            year, month = divmod(period[0], 12)
            graduation = f"{year:04d}-{month + 1:02d}"
    search_text: str | None = entry.answer
    # Nationality lists may name people ("American") rather than the country.
    if (re.fullmatch(r"(your |current |primary )?(nationality|nationalities)", norm_question(clean_label(field.label)))
            and len(_country_names(entry.answer)) > 1):
        candidates += ["American", *_country_names(entry.answer)[1:]]
        search_text = None                 # search each spelling in turn
    name = clean_label(field.label) or clean_label(field.options[0] if field.options else "")
    return Plan(name[:60], tuple(dict.fromkeys(candidates)), search_text=search_text,
                from_answers=True, graduation=graduation)


# ----------------------------------------------------------- source choices

_SOURCE = re.compile(
    r"\bhow (did|do) you (first )?(hear|learn|find out|come to know|come across|find)\b|"
    r"\bwhere did you (first )?(hear|find|learn|see)\b|"
    r"^(referral |recruitment |applicant |candidate |job |application )?source\b"
)
_SOURCE_SKIP = re.compile(
    r"\b(referr\w*|employee|recruiter|agency|staffing|other|specify|select|choose|please)\b"
)
_SOURCE_PREFERENCE = (
    re.compile(r"\b(company|career|careers|corporate) (web ?site|site|page|portal)\b|\bcompany website\b"),
    re.compile(r"\blinkedin\b"),
    re.compile(r"\b(job board|indeed|handshake|glassdoor|ziprecruiter|internet|online|website|web)\b"),
    re.compile(r"\b(university|college|campus|career fair)\b"),
)


def is_source_question(label: str) -> bool:
    """True for recruitment-source questions, not learning or source-code questions."""
    words = norm_question(clean_label(label))
    return bool(_SOURCE.search(words)) and bool(
        re.search(r"\b(us|company|position|role|job|opportunity|vacancy|opening|recruitment|careers)\b", words)
        or re.fullmatch(r"(referral |recruitment |applicant |candidate |job |application )?source", words)
    )


def pick_source_option(options: Sequence[str], rng: random.Random | None = None) -> str | None:
    """A plain online source from the offered list, else any plain option, else None.

    Referral, recruiter, agency and "Other" choices are skipped: they ask for a
    name or explanation that has no saved answer.
    """
    rng = rng or random.Random()
    usable = [o for o in options if norm_question(o) and not _SOURCE_SKIP.search(norm_question(o))]
    for pattern in _SOURCE_PREFERENCE:
        preferred = [o for o in usable if pattern.search(norm_question(o))]
        if preferred:
            return rng.choice(preferred)
    return rng.choice(usable) if usable else None


def _source_plan(field: FormField, saved: Plan | None) -> Plan | None:
    """Keep a saved source that is offered; otherwise pick an offered option."""
    if saved is not None and (not field.options or match_option(saved.candidates, field.options) is not None):
        return saved
    picked = pick_source_option(field.options) if field.options else None
    if picked is None:
        return saved
    return Plan(clean_label(field.label)[:60] or "source", (picked,), exact_choices=True)


def plan_for(
    field: FormField, config: Config, *, company: str | None = None, tracks: Sequence[str] = (),
    jurisdiction: str | None = None,
) -> Plan | None:
    """What to put in `field`: answers.yaml, then profile.yaml, else None.

    answers.yaml wins because I wrote that exact question's answer myself, except
    that a saved source the field does not offer falls back to an offered option.
    `tracks` are the job's tracks; they pick the discipline (profile.discipline_by_track).
    File inputs are not planned here; the filler handles the resume.
    Country-specific permission requires explicit field evidence or the strict
    job `jurisdiction`; neither residence nor citizenship supplies that context.
    """
    if field.kind in ("file", "unsupported") or not field.label and not field.name:
        return None
    entry = lookup_answer(
        config, field.label, company=company, tracks=tracks,
        jurisdiction=jurisdiction, options=field.options,
    )
    if entry is None and field.kind == "checkbox" and len(field.options) == 1:
        entry = lookup_answer(
            config, field.options[0], company=company, tracks=tracks,
            jurisdiction=jurisdiction, options=field.options,
        )                                                       # "I agree to ..." boxes
    if entry is not None:
        plan = _answer_plan(field, entry)
        return _source_plan(field, plan) if is_source_question(field.label) else plan
    if is_demographic(field.label):
        return None
    return profile_plan(field, config.profile, tracks=tracks) or (
        _source_plan(field, None) if is_source_question(field.label) else None
    )

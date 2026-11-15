"""Spoken dates: what "next Friday at 4" can mean, as local dates and times.

Pure: no database, no clock of its own. :func:`readings` reads the user's words for a
date or time against a fixed ``now`` and zone and returns every reading they allow, so
the caller can tell a clear date (one reading) from one it must ask about:

* "next Friday" while the coming Friday is within 6 days: the coming one or the one after;
* "tomorrow" said between 00:00 and 04:00: today's date or the next;
* a bare hour from 1 to 11 with no am or pm ("at 4"): the morning and the afternoon
  reading, as long as both are still ahead ("at 12" is noon);
* a day and month already past this year: next year's, or the past one;
* 4/5: the 4th of May or April the 5th;
* an event with a day but no time (``need_time``): Morning, Afternoon and All day.

A reading may lie in the past ("yesterday", "last Friday"); the caller asks about those.
:func:`split` finds the date words at the end of a typed phrase ("Read chapter 3
tomorrow" is the title "Read chapter 3" and the words "tomorrow").
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

#: What the parts of a day mean when no clock time is said.
MORNING, AFTERNOON, EVENING, NIGHT = time(9), time(14), time(19), time(20)
#: Before this hour "tomorrow" may still mean today's date.
SMALL_HOURS = 4
MAX_READINGS = 3
SAID_MAX = 200

_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "weds": 2,
             "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
             "sunday": 6, "weekend": 5}
_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
           "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
           "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
_COUNTS = {"a": 1, "an": 1, "one": 1, "two": 2, "a couple of": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_PERIODS = {"morning": MORNING, "afternoon": AFTERNOON, "evening": EVENING, "tonight": NIGHT, "night": NIGHT}
#: Words that carry no date of their own; anything else left over means the words were not all read.
_FILLER = frozenset("on at by the this in of for due to and around until till before".split())

_WD = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_MO = "|".join(sorted(_MONTHS, key=len, reverse=True))
_CT = "|".join(sorted(_COUNTS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)"


@dataclass(frozen=True)
class Reading:
    """One thing the words can mean: a local day, a time unless it is the whole day, and a short label."""
    day: date
    time: time | None
    label: str


@dataclass(frozen=True)
class Resolution:
    readings: list[Reading]
    #: The words name a day but no time, and a time was needed; the readings are parts of that day.
    missing_time: bool = False


def label(day: date, at: time | None, today: date) -> str:
    """Fri 20 Nov, with the time and (in another year) the year."""
    text = f"{day:%a} {day.day} {day:%b}" + (f" {day.year}" if day.year != today.year else "")
    return text + (f", {at:%H:%M}" if at is not None else "")


# Reading the words ---------------------------------------------------------------

class _Words:
    """The words being read; a pattern that matches is taken out so nothing is read twice."""

    def __init__(self, said: str):
        text = said.casefold().replace("’", "'").replace("a.m.", "am").replace("p.m.", "pm").replace(",", " ")
        self.text = " " + " ".join(text.split()) + " "

    def take(self, pattern: str) -> re.Match | None:
        match = re.search(pattern, self.text)
        if match is not None:
            self.text = self.text[:match.start()] + " " + self.text[match.end():]
        return match

    def has(self, pattern: str) -> bool:
        return re.search(pattern, self.text) is not None

    @property
    def rest(self) -> list[str]:
        return [word for word in re.findall(r"[^\s]+", self.text) if word.strip(".!?;:") not in _FILLER and word.strip(".!?;:")]


def _clock(hour: int, minute: int, half: str | None) -> time | None:
    if minute > 59:
        return None
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    return time(hour, minute) if hour <= 23 else None


def _times(words: _Words) -> tuple[list[time], bool, bool]:
    """The clock times the words allow, whether they say "all day", and whether a time was said but is impossible."""
    if words.take(r"\ball[ -]day\b"):
        return [], True, False
    period = next((name for name in _PERIODS if words.has(rf"\b{name}\b")), None)
    exact: time | None = None
    bare: tuple[int, int] | None = None
    bad = False
    if match := words.take(r"(?<=\d{4}-\d{2}-\d{2})t(\d{2}):(\d{2})(?::\d{2})?"):
        exact = _clock(int(match[1]), int(match[2]), None)
    elif match := (words.take(r"\b(\d{1,2}):(\d{2}) ?(am|pm)?\b") or words.take(r"\b(\d{1,2})\.(\d{2}) ?(am|pm)\b")):
        exact = _clock(int(match[1]), int(match[2]), match[3])
        bad = exact is None
    elif match := words.take(r"\b(\d{1,2}) ?(am|pm)\b"):
        exact = _clock(int(match[1]), 0, match[2])
        bad = exact is None
    elif words.take(r"\b(?:noon|midday)\b"):
        exact = time(12)
    elif words.take(r"\bmidnight\b"):
        exact = time(0)
    elif match := words.take(r"\b(half|quarter) (past|to) (\d{1,2})\b"):
        hour, minute = int(match[3]), 30 if match[1] == "half" else 15
        if match[2] == "to":
            hour, minute = (hour - 1) or 12, 60 - minute
        bare = (hour, minute)
    elif match := (words.take(rf"\b(?:at|around|by) ?(\d{{1,2}})\b(?! ?(?:{_ORD}|of|{_MO}|days?|weeks?|months?)\b)(?![/.:-]\d)")
                   or words.take(r"\b(\d{1,2}) ?o'?clock\b")):
        bare = (int(match[1]), 0)
    if bare is not None:
        hour, minute = bare
        if 1 <= hour <= 11 and period is None:
            found = [time(hour, minute), time(hour + 12, minute)]
        elif 1 <= hour <= 12 and period is not None:
            found = [_clock(hour, minute, "am" if period == "morning" else "pm")]
        else:
            found = [_clock(hour, minute, None)]
        bad = found == [None]
        times = [] if bad else found
    else:
        times = [exact] if exact is not None else []
    if period is not None:
        taken = words.take(rf"\b(?:in the |this |at )?{period}\b")
        if period == "tonight" or taken[0].startswith("this"):
            words.text += "today "  # "tonight" and "this evening" are also the day
        if not times and not bad:
            times = [_PERIODS[period]]
    return times, False, bad


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _year(text: str | None) -> int | None:
    if text is None:
        return None
    return int(text) + (2000 if len(text) == 2 else 0)


def _yearly(month: int, day: int, year: int | None, today: date) -> list[date]:
    """A day and month: in the said year, else this year's, or when that has passed next year's and the past one."""
    if year is not None:
        found = _valid(year, month, day)
        return [found] if found else []
    this = _valid(today.year, month, day)
    following = _valid(today.year + 1, month, day)
    if this is not None and this >= today:
        return [this]
    return [found for found in (following, this) if found is not None]


def _nth(number: int, today: date) -> date | None:
    """The next day-of-month `number` on or after today."""
    year, month = today.year, today.month
    for _ in range(12):
        found = _valid(year, month, number)
        if found is not None and found >= today:
            return found
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def _months_on(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _weekday(words: _Words, today: date) -> list[date] | None:
    match = words.take(rf"\b(?:(this coming|this|next|last|coming) )?({_WD})\b")
    if match is None:
        return None
    how, target = match[1], _WEEKDAYS[match[2]]
    ahead = (target - today.weekday()) % 7
    monday = today - timedelta(days=today.weekday())
    if words.take(r"\bnext week\b"):
        return [monday + timedelta(days=7 + target)]
    if words.take(r"\bthis week\b"):
        return [monday + timedelta(days=target)]
    number = words.take(rf"\b(?:the )?(\d{{1,2}}){_ORD}\b")
    if number is not None:
        # "Friday the 20th": the 20th decides; a 20th that is no Friday is a second reading.
        dated = _nth(int(number[1]), today)
        coming = today + timedelta(days=ahead)
        if dated is None:
            return [coming]
        return [dated] if dated.weekday() == target else [dated, coming]
    if how == "last":
        return [today - timedelta(days=(today.weekday() - target) % 7 or 7)]
    if how == "next":
        coming = today + timedelta(days=ahead or 7)
        return [coming, coming + timedelta(days=7)] if ahead else [coming]
    if how in ("coming", "this coming"):
        return [today + timedelta(days=ahead or 7)]
    if how == "this" or ahead:
        return [today + timedelta(days=ahead)]
    return [today, today + timedelta(days=7)]  # "Friday", said on a Friday


def _days(words: _Words, today: date, hour: int) -> list[date]:
    """The days the words allow, most likely first; empty when they name none."""
    found = _dated(words, today, hour)
    if found:
        words.take(rf"\b({_WD})\b")  # "Fri 20 Nov": the date decides
    return found


def _dated(words: _Words, today: date, hour: int) -> list[date]:
    if match := words.take(r"\b(\d{4})-(\d{2})-(\d{2})\b"):
        found = _valid(int(match[1]), int(match[2]), int(match[3]))
        return [found] if found else []
    if match := words.take(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b"):
        first, second, year = int(match[1]), int(match[2]), _year(match[3])
        found = []
        for month, day in ((second, first), (first, second)):  # day first, then month first
            if 1 <= month <= 12:
                options = _yearly(month, day, year, today)
                found += [option for option in options[:1] if option not in found]
        return found
    if match := words.take(rf"\b(?:the )?(\d{{1,2}}){_ORD}?(?: of)? ({_MO})\b(?: (\d{{4}})\b)?"):
        return _yearly(_MONTHS[match[2]], int(match[1]), _year(match[3]), today)
    if match := words.take(rf"\b({_MO}) (?:the )?(\d{{1,2}}){_ORD}?\b(?: (\d{{4}})\b)?"):
        return _yearly(_MONTHS[match[1]], int(match[2]), _year(match[3]), today)
    if match := words.take(r"\b(?:(?:the )?end of (?:the |this )?(next )?(week|month)|eo([wm]))\b"):
        unit = match[2] or {"w": "week", "m": "month"}[match[3]]
        if unit == "month":
            return [_month_end(_months_on(today.replace(day=1), 1) if match[1] else today)]
        friday = today - timedelta(days=today.weekday()) + timedelta(days=4)
        if match[1]:
            return [friday + timedelta(days=7)]
        return [friday if friday >= today else friday + timedelta(days=2)]  # at the weekend, its Sunday
    if match := (words.take(rf"\bin ({_CT}|\d{{1,3}}) (day|week|month|year)s?(?: time)?\b")
                 or words.take(rf"\b({_CT}|\d{{1,3}}) (day|week|month|year)s? (?:from now|from today|later)\b")):
        count = _COUNTS[match[1]] if match[1] in _COUNTS else int(match[1])
        if match[2] in ("month", "year"):
            return [_months_on(today, count * (12 if match[2] == "year" else 1))]
        return [today + timedelta(days=count * (7 if match[2] == "week" else 1))]
    if words.take(r"\b(?:in )?a fortnight\b"):
        return [today + timedelta(days=14)]
    if words.take(r"\b(?:the )?day after (?:tomorrow|tmrw|tmr)\b"):
        return [today + timedelta(days=2)]
    if words.take(r"\b(?:tomorrow|tmrw|tmr)\b"):
        return [today, today + timedelta(days=1)] if hour < SMALL_HOURS else [today + timedelta(days=1)]
    if words.take(r"\byesterday\b"):
        return [today - timedelta(days=1)]
    if words.take(r"\b(?:today|now)\b"):
        return [today]
    found = _weekday(words, today)
    if found is not None:
        return found
    if match := (words.take(rf"\bthe (\d{{1,2}}){_ORD}?(?= |$)(?! ?(?:am|pm|o'?clock))")
                 or words.take(rf"\b(\d{{1,2}}){_ORD}\b")):
        dated = _nth(int(match[1]), today) if 1 <= int(match[1]) <= 31 else None
        return [dated] if dated else []
    if match := words.take(r"\bnext (week|month)\b"):
        if match[1] == "month":
            return [_months_on(today.replace(day=1), 1)]
        return [today - timedelta(days=today.weekday()) + timedelta(days=7)]
    return []


@dataclass(frozen=True)
class _Read:
    days: list[date]
    times: list[time]
    all_day: bool
    #: Every word was understood (and no impossible time was said).
    whole: bool


def _read(said: str, local: datetime) -> _Read:
    words = _Words(said[:SAID_MAX])
    times, all_day, bad = _times(words)
    days = _days(words, local.date(), local.hour)
    return _Read(days, times, all_day, not bad and not words.rest)


def readings(said: str, *, now: datetime, zone: ZoneInfo, need_time: bool = False) -> Resolution:
    """Every reading of `said` at `now` in `zone`, most likely first; none when it names no date or time."""
    local = now.astimezone(zone).replace(tzinfo=None)
    today = local.date()
    read = _read(said, local)
    days, times = read.days, read.times
    if not days and not times:
        return Resolution([])
    if not days:
        ahead = [at for at in times if datetime.combine(today, at) > local]
        pairs = [(today, at) for at in ahead] or [(today + timedelta(days=1), at) for at in times]
    elif times:
        pairs = []
        for day in days:
            ahead = [at for at in times if datetime.combine(day, at) > local]
            # A time already gone today leaves the other reading; when both are gone they stay, and the caller asks.
            pairs += [(day, at) for at in (ahead if day == today and ahead else times)]
    elif need_time and not read.all_day and len(days) == 1:
        day = days[0]
        parts = [(MORNING, "Morning"), (AFTERNOON, "Afternoon"), (EVENING, "Evening")]
        open_parts = [part for part in parts if day != today or datetime.combine(day, part[0]) > local][:2]
        return Resolution([*(Reading(day, at, name) for at, name in open_parts), Reading(day, None, "All day")],
                          missing_time=True)
    else:
        return Resolution([Reading(day, None, label(day, None, today)) for day in days[:MAX_READINGS]],
                          missing_time=need_time and not read.all_day)
    return Resolution([Reading(day, at, label(day, at, today)) for day, at in pairs[:MAX_READINGS]])


def split(text: str, *, now: datetime, zone: ZoneInfo) -> tuple[str, str | None]:
    """A phrase and the date words it ends with: ("Read chapter 3", "tomorrow"), or (text, None)."""
    local = now.astimezone(zone).replace(tzinfo=None)
    words = text.split()
    for start in range(1, len(words)):
        said = " ".join(words[start:])
        read = _read(said, local)
        if read.whole and (read.days or read.times or read.all_day):
            return " ".join(words[:start]).rstrip(" ,;:"), said
    return text, None

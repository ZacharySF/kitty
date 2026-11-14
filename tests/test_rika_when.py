"""Spoken dates (kitty.core.rika.when): every reading of the words, against a fixed clock and zone."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from kitty.core.rika import when

ZONE = ZoneInfo("America/New_York")
#: Saturday 14 November 2026, 10:00 in New York.
NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
#: The same Saturday at 02:00 in New York.
SMALL_HOURS = datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc)


def read(said, *, now=NOW, need_time=False):
    found = when.readings(said, now=now, zone=ZONE, need_time=need_time)
    return [(reading.day.isoformat(), reading.time.strftime("%H:%M") if reading.time else None) for reading in found.readings]


def days(*values):
    return [(value, None) for value in values]


@pytest.mark.parametrize(("said", "expected"), [
    ("today", days("2026-11-14")),
    ("tomorrow", days("2026-11-15")),
    ("Tomorrow.", days("2026-11-15")),
    ("the day after tomorrow", days("2026-11-16")),
    ("yesterday", days("2026-11-13")),
    ("friday", days("2026-11-20")),
    ("on Friday", days("2026-11-20")),
    ("this fri", days("2026-11-20")),
    ("coming monday", days("2026-11-16")),
    ("last friday", days("2026-11-13")),
    ("sunday", days("2026-11-15")),
    ("the weekend", days("2026-11-14", "2026-11-21")),
    ("next week", days("2026-11-16")),
    ("next month", days("2026-12-01")),
    ("friday next week", days("2026-11-20")),
    ("next week on wednesday", days("2026-11-18")),
    ("wednesday this week", days("2026-11-11")),
    ("in 3 days", days("2026-11-17")),
    ("in a week", days("2026-11-21")),
    ("in two weeks", days("2026-11-28")),
    ("in a fortnight", days("2026-11-28")),
    ("3 days from now", days("2026-11-17")),
    ("in a month", days("2026-12-14")),
    ("in 1 year", days("2027-11-14")),
    ("the 20th", days("2026-11-20")),
    ("on the 14th", days("2026-11-14")),
    ("the 4th", days("2026-12-04")),
    ("the 31st", days("2026-12-31")),
    ("20 nov", days("2026-11-20")),
    ("Nov 20", days("2026-11-20")),
    ("November 20th", days("2026-11-20")),
    ("the 20th of November", days("2026-11-20")),
    ("Fri 20 Nov", days("2026-11-20")),
    ("3 nov 2026", days("2026-11-03")),
    ("Dec 1, 2027", days("2027-12-01")),
    ("20/11", days("2026-11-20")),
    ("20/11/2027", days("2027-11-20")),
    ("2026-11-20", days("2026-11-20")),
    ("end of the week", days("2026-11-15")),
    ("end of next week", days("2026-11-20")),
    ("end of the month", days("2026-11-30")),
    ("end of next month", days("2026-12-31")),
    ("eom", days("2026-11-30")),
])
def test_a_clear_day_has_one_reading(said, expected):
    assert read(said) == expected


@pytest.mark.parametrize(("said", "expected"), [
    # The coming Friday is within 6 days: it, or the one after.
    ("next friday", days("2026-11-20", "2026-11-27")),
    ("next sunday", days("2026-11-15", "2026-11-22")),
    # Said on a Saturday, "next Saturday" can only be the one a week away, but "Saturday" can be today.
    ("next saturday", days("2026-11-21")),
    ("saturday", days("2026-11-14", "2026-11-21")),
    # A day and month already gone: next year's, or the past one.
    ("3 nov", days("2027-11-03", "2026-11-03")),
    ("October 31st", days("2027-10-31", "2026-10-31")),
    # Day first, or month first.
    ("4/12", days("2026-12-04", "2027-04-12")),
    # The 20th is a Friday, not a Monday: the date, or the coming Monday.
    ("monday the 20th", days("2026-11-20", "2026-11-16")),
    ("friday the 20th", days("2026-11-20")),
])
def test_an_ambiguous_day_has_each_reading(said, expected):
    assert read(said) == expected


@pytest.mark.parametrize(("said", "expected"), [
    ("noon", [("2026-11-14", "12:00")]),
    ("4pm", [("2026-11-14", "16:00")]),
    ("4 p.m.", [("2026-11-14", "16:00")]),
    ("16:30", [("2026-11-14", "16:30")]),
    ("4.30pm", [("2026-11-14", "16:30")]),
    ("12am tomorrow", [("2026-11-15", "00:00")]),
    # 9am has gone today, so it is tomorrow's.
    ("9am", [("2026-11-15", "09:00")]),
    ("tonight", [("2026-11-14", "20:00")]),
    ("tonight at 9", [("2026-11-14", "21:00")]),
    ("tomorrow morning", [("2026-11-15", "09:00")]),
    ("friday afternoon", [("2026-11-20", "14:00")]),
    ("tomorrow evening", [("2026-11-15", "19:00")]),
    ("tomorrow at 4pm", [("2026-11-15", "16:00")]),
    ("tomorrow at 4 in the afternoon", [("2026-11-15", "16:00")]),
    ("tomorrow at 7 in the morning", [("2026-11-15", "07:00")]),
    ("friday at 15", [("2026-11-20", "15:00")]),
    ("friday at 12", [("2026-11-20", "12:00")]),
    ("friday at midnight", [("2026-11-20", "00:00")]),
    ("5 o'clock friday evening", [("2026-11-20", "17:00")]),
    ("2026-11-20T16:30", [("2026-11-20", "16:30")]),
    ("2026-11-20 16:30:00", [("2026-11-20", "16:30")]),
    ("20 Nov at 16:30", [("2026-11-20", "16:30")]),
    ("by 20 Nov", [("2026-11-20", None)]),
    ("in 2 days at 8am", [("2026-11-16", "08:00")]),
    # A bare hour whose morning has gone today is the afternoon's.
    ("at 4", [("2026-11-14", "16:00")]),
    ("half past 4", [("2026-11-14", "16:30")]),
    ("quarter to 1", [("2026-11-14", "12:45")]),
    # A time already gone on a named day stays, for the caller to ask about.
    ("today at 8am", [("2026-11-14", "08:00")]),
    ("this morning", [("2026-11-14", "09:00")]),
])
def test_a_time_joins_its_day(said, expected):
    assert read(said) == expected


@pytest.mark.parametrize(("said", "expected"), [
    ("tomorrow at 4", [("2026-11-15", "04:00"), ("2026-11-15", "16:00")]),
    ("at 11", [("2026-11-14", "11:00"), ("2026-11-14", "23:00")]),
    ("friday at 9", [("2026-11-20", "09:00"), ("2026-11-20", "21:00")]),
    ("friday half past 4", [("2026-11-20", "04:30"), ("2026-11-20", "16:30")]),
    ("next friday at 4pm", [("2026-11-20", "16:00"), ("2026-11-27", "16:00")]),
])
def test_a_bare_hour_is_the_morning_or_the_afternoon(said, expected):
    assert read(said) == expected


def test_tomorrow_in_the_small_hours_is_today_or_the_next_day():
    assert read("tomorrow", now=SMALL_HOURS) == days("2026-11-14", "2026-11-15")
    assert read("tomorrow at 4pm", now=SMALL_HOURS) == [("2026-11-14", "16:00"), ("2026-11-15", "16:00")]
    assert read("today", now=SMALL_HOURS) == days("2026-11-14")


def test_the_zone_decides_the_day():
    late = datetime(2026, 11, 15, 4, 30, tzinfo=timezone.utc)  # still Saturday evening in New York
    assert read("tomorrow", now=late) == days("2026-11-15")
    assert when.readings("tomorrow", now=late, zone=ZoneInfo("UTC")).readings[0].day == date(2026, 11, 16)


@pytest.mark.parametrize("said", ["", "sometime soon", "chapter 3", "at 25", "the 40th", "31/31", "at 4:75", "13pm"])
def test_words_with_no_date_have_no_reading(said):
    assert read(said) == []


def test_an_event_without_a_time_gets_the_parts_of_the_day():
    found = when.readings("friday", now=NOW, zone=ZONE, need_time=True)
    assert found.missing_time
    assert [(reading.label, reading.time) for reading in found.readings] == [
        ("Morning", time(9)), ("Afternoon", time(14)), ("All day", None)]
    assert {reading.day for reading in found.readings} == {date(2026, 11, 20)}
    # Today's morning has gone.
    today = when.readings("today", now=NOW, zone=ZONE, need_time=True)
    assert [reading.label for reading in today.readings] == ["Afternoon", "Evening", "All day"]
    # A said time, or "all day", needs no question.
    timed = when.readings("friday at 4pm", now=NOW, zone=ZONE, need_time=True)
    assert not timed.missing_time and len(timed.readings) == 1
    whole = when.readings("all day friday", now=NOW, zone=ZONE, need_time=True)
    assert not whole.missing_time and read("all day friday", need_time=True) == days("2026-11-20")
    # Two possible days keep the day question; the time is still missing.
    both = when.readings("next friday", now=NOW, zone=ZONE, need_time=True)
    assert both.missing_time and [reading.time for reading in both.readings] == [None, None]
    assert not when.readings("friday", now=NOW, zone=ZONE).missing_time


def test_labels_are_short_and_name_another_year():
    labels = lambda said: [reading.label for reading in when.readings(said, now=NOW, zone=ZONE).readings]
    assert labels("next friday") == ["Fri 20 Nov", "Fri 27 Nov"]
    assert labels("friday at 4pm") == ["Fri 20 Nov, 16:00"]
    assert labels("3 nov") == ["Wed 3 Nov 2027", "Tue 3 Nov"]


def test_at_most_three_readings():
    assert len(read("next friday at 4")) == when.MAX_READINGS


@pytest.mark.parametrize(("text", "title", "said"), [
    ("Read chapter 3 tomorrow", "Read chapter 3", "tomorrow"),
    ("Study group tomorrow at 4pm", "Study group", "tomorrow at 4pm"),
    ("Lab report next Friday", "Lab report", "next Friday"),
    ("Call Sam on the 20th at 16:30", "Call Sam", "on the 20th at 16:30"),
    ("Submit essay Fri 20 Nov", "Submit essay", "Fri 20 Nov"),
    ("Dentist in 2 weeks", "Dentist", "in 2 weeks"),
    ("Pay rent by end of the month", "Pay rent", "by end of the month"),
    ("Read chapter 3", "Read chapter 3", None),
    ("Sign in", "Sign in", None),
    ("Sit in the sun", "Sit in the sun", None),
    ("Study group tomorrow at 25", "Study group tomorrow at 25", None),
    ("tomorrow", "tomorrow", None),
])
def test_the_date_words_end_a_phrase(text, title, said):
    assert when.split(text, now=NOW, zone=ZONE) == (title, said)

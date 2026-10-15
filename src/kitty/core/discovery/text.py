"""Small text helpers the adapters share: HTML to text, "is this an internship",
seasons, and "is this location in the US".

All of these are plain rules with tests, not guesses from a model.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from kitty.dates import to_iso

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h\d|tr)\b[^>]*>", re.I)


def html_to_text(markup: str | None) -> str | None:
    """Strip tags from a job description. Handles Greenhouse's double-escaped HTML."""
    if not markup:
        return None
    text = html.unescape(markup)
    if "&lt;" in text or "&amp;" in text:        # escaped twice
        text = html.unescape(text)
    text = _BLOCK.sub("\n", text)
    text = html.unescape(_TAG.sub(" ", text))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


# Internship, co-op, and student program titles. Company boards list every job
# they have; only these are kept.
_INTERN_TITLE = re.compile(
    r"\b(intern|interns|internship|internships|co[-\s]?op|cooperative education|student|apprentice(ship)?|"
    r"summer analyst|summer associate|placement|werkstudent|trainee)\b",
    re.I,
)
# Titles that contain an intern word but are not internships.
_NOT_INTERN = re.compile(r"\b(internal|international|intern(al)? audit|full[- ]time)\b", re.I)


def is_internship_title(title: str) -> bool:
    """True when a title reads as an internship, co-op or student program."""
    cleaned = _NOT_INTERN.sub(" ", title)
    return bool(_INTERN_TITLE.search(cleaned))


_SEASON = re.compile(r"\b(summer|fall|autumn|winter|spring)\s*'?(20)?(2[6-9]|3[0-2])\b", re.I)


def season_from(*texts: str | None) -> str | None:
    """'Summer 2027' from a title or terms list, when one is stated."""
    for text in texts:
        if not text:
            continue
        match = _SEASON.search(text)
        if match:
            name = match.group(1).capitalize().replace("Autumn", "Fall")
            return f"{name} 20{match.group(3)}"
    return None


def unix_to_iso(value: float | int | None, *, millis: bool = False) -> str | None:
    """Unix seconds (or milliseconds) to a stored timestamp."""
    if not value:
        return None
    seconds = value / 1000 if millis else value
    return to_iso(datetime.fromtimestamp(seconds, tz=timezone.utc))


def iso_to_utc(value: str | None) -> str | None:
    """Any ISO-8601 string with an offset to a stored UTC timestamp."""
    if not value:
        return None
    try:
        return to_iso(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


# ------------------------------------------------------------------ location

_STATE_CODES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
_STATE_NAMES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|"
    "georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|"
    "massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
    "new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|"
    "oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|"
    "vermont|virginia|west virginia|wisconsin|wyoming|district of columbia"
)
_US_WORDS = re.compile(
    rf"\b(united states|usa|u\.s\.a?\.?|us|{_STATE_NAMES}|nyc|new york city|"
    r"chicago|austin|houston|dallas|san antonio|san francisco|san jose|seattle|boston|"
    r"los angeles|san diego|denver|atlanta|miami|philadelphia|pittsburgh|bala cynwyd|"
    r"greenwich|stamford|santa clara|sunnyvale|mountain view|palo alto|redmond|galveston|"
    r"washington,? d\.?c\.?)\b",
    re.I,
)
_US_STATE_CODE = re.compile(rf"(,|\s-)\s*({'|'.join(_STATE_CODES)})\b")
_FOREIGN_WORDS = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ottawa|mexico|brazil|argentina|chile|colombia|"
    r"united kingdom|uk|england|scotland|ireland|dublin|london|cambridge, uk|edinburgh|"
    r"france|paris|germany|berlin|munich|frankfurt|netherlands|amsterdam|switzerland|zurich|"
    r"geneva|spain|madrid|barcelona|italy|milan|poland|warsaw|warszawa|gdansk|gdańsk|"
    r"sweden|stockholm|finland|norway|denmark|belgium|austria|czech|hungary|romania|"
    r"portugal|greece|israel|tel aviv|india|bengaluru|bangalore|hyderabad|pune|chennai|"
    r"mumbai|delhi|noida|gurgaon|gurugram|china|shanghai|beijing|shenzhen|hong kong|"
    r"taiwan|taipei|hsinchu|japan|tokyo|osaka|korea|seoul|singapore|malaysia|penang|"
    r"thailand|vietnam|philippines|indonesia|australia|sydney|melbourne|new zealand|"
    r"south africa|uae|dubai|saudi|egypt|turkey|istanbul|emea|apac|latam)\b",
    re.I,
)


def _us_part(part: str) -> bool | None:
    """US verdict for one location in a list (see `us_location`)."""
    if _US_WORDS.search(part):
        return True
    if _FOREIGN_WORDS.search(part):
        return False
    if _US_STATE_CODE.search(part):
        return True
    return None


def us_location(location: str | None, country: str | None = None) -> bool | None:
    """True when the location is in the US, False when clearly abroad, None when unclear.

    A multi-location string counts as US if any part is, and as abroad if no part
    is US and at least one is clearly foreign. 'Remote' alone and
    '3 Locations' are unclear, so they pass through rather than being dropped.
    State codes are checked last because some clash with country codes
    ('Bengaluru, IN').
    """
    if country:
        lowered = country.lower()
        if lowered in {"us", "usa", "united states", "united states of america"}:
            return True
        if location is None:
            return False
    if not location:
        return None
    verdicts = [_us_part(part) for part in re.split(r";|\||\bor\b|/", location) if part.strip()]
    if any(v is True for v in verdicts):
        return True
    if any(v is False for v in verdicts):
        return False
    if country:
        return False
    return None

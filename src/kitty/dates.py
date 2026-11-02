"""Date helpers. All stored timestamps are ISO-8601 UTC with a 'Z' suffix."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


def now_iso() -> str:
    """Current UTC time as a storable string."""
    return now().strftime(ISO_FORMAT)


def to_iso(value: datetime | date) -> str:
    """Render a date or datetime as a storable UTC string."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).strftime(ISO_FORMAT)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).strftime(ISO_FORMAT)


def parse(value: str | None) -> datetime | None:
    """Parse a stored timestamp (or a bare 'YYYY-MM-DD'); None when unparseable."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_user_date(value: str) -> str | None:
    """Parse what I might type into a date field.

    Accepts 'today', 'tomorrow', '+7' / '7d' (days from now), 'YYYY-MM-DD',
    or a full timestamp. Returns a storable string, or None when it makes no sense.
    """
    text = value.strip().lower()
    if not text:
        return None
    if text == "today":
        return to_iso(now())
    if text == "tomorrow":
        return to_iso(now() + timedelta(days=1))
    offset = text.removeprefix("+").removesuffix("d")
    if offset.isdigit():
        return to_iso(now() + timedelta(days=int(offset)))
    parsed = parse(value)
    return to_iso(parsed) if parsed else None


def days_since(value: str | None, *, reference: datetime | None = None) -> int:
    """Whole days between a stored timestamp and now (0 when unknown)."""
    parsed = parse(value)
    if parsed is None:
        return 0
    delta = (reference or now()) - parsed
    return max(0, delta.days)


def days_until(value: str | None, *, reference: datetime | None = None) -> int | None:
    """Whole days from now until a stored timestamp; negative when overdue."""
    parsed = parse(value)
    if parsed is None:
        return None
    return (parsed - (reference or now())).days


def week_start(reference: datetime | None = None) -> datetime:
    """Midnight UTC on the Monday of the reference week.

    Weekly targets are counted from Monday, which matches how I plan the week.
    """
    base = reference or now()
    monday = base - timedelta(days=base.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start_iso(reference: datetime | None = None) -> str:
    """`week_start` as a storable string."""
    return week_start(reference).strftime(ISO_FORMAT)

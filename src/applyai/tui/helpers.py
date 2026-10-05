"""Small formatting helpers shared by the screens."""

from __future__ import annotations

from textual.containers import Vertical

from applyai.dates import days_since, days_until
from applyai.models import Job, Status
from applyai.tui.theme import ASH, BONE, CRIMSON, DUSK, GOLD, MOSS, STORM

#: Colour per pipeline status, used in tables and on the board.
STATUS_COLOUR: dict[str, str] = {
    "applied": STORM,
    "oa": GOLD,
    "interview": DUSK,
    "offer": f"bold {MOSS}",
    "rejected": CRIMSON,
    "ghosted": ASH,
    "withdrawn": ASH,
}

TIER_COLOUR: dict[str, str] = {"A": f"bold {GOLD}", "B": STORM, "C": ASH}

ELIGIBILITY_COLOUR: dict[str, str] = {
    "boosted": f"bold {MOSS}",
    "eligible": MOSS,
    "ambiguous": GOLD,
    "ineligible": CRIMSON,
    "unknown": ASH,
}


def panel(title: str) -> Vertical:
    """A bordered box with its title set into the top border."""
    box = Vertical(classes="panel")
    box.border_title = f"✦ {title}"
    return box


def muted(text: str) -> str:
    """Rich markup for text that matters less (closed, never run, '-')."""
    return f"[{ASH}]{text}[/]"


def short_date(value: str | None) -> str:
    """'2026-09-27T12:00:00Z' -> '2026-09-27'; '-' when unknown."""
    return value[:10] if value else "-"


def age(value: str | None) -> str:
    """How long ago a timestamp was, as '3d'."""
    return "-" if not value else f"{days_since(value)}d"


def due_label(value: str | None) -> str:
    """A due date as 'in 3d', 'today' or 'overdue 2d'."""
    if not value:
        return "-"
    days = days_until(value)
    if days is None:
        return "-"
    if days < 0:
        return f"overdue {abs(days)}d"
    return "today" if days == 0 else f"in {days}d"


def bar(fraction: float, width: int = 12) -> str:
    """A text progress bar, e.g. '████░░░░'."""
    filled = max(0, min(width, round(fraction * width)))
    return "█" * filled + "░" * (width - filled)


def tier_markup(tier: str | None) -> str:
    """Tier letter with its colour, or '-'."""
    if not tier:
        return muted("-")
    return f"[{TIER_COLOUR.get(tier, BONE)}]{tier}[/]"


def status_markup(status: Status | str | None) -> str:
    """Status name with its colour."""
    if not status:
        return muted("-")
    return f"[{STATUS_COLOUR.get(status, BONE)}]{status}[/]"


def eligibility_markup(value: str | None) -> str:
    """Eligibility verdict with its colour."""
    if not value:
        return muted("-")
    return f"[{ELIGIBILITY_COLOUR.get(value, BONE)}]{value}[/]"


def job_label(job: Job) -> str:
    """'Company - Title' for titles and palette hits."""
    return f"{job.company_name} - {job.title}"


def favorite_mark(job: Job) -> str:
    """A gold ★ on favorites (gold = look closer), a space otherwise."""
    return f"[{GOLD}]★[/]" if job.is_favorite else " "


def like_line(job: Job) -> str | None:
    """'like your favorites: fpga, embedded, ...' for a job that resembles them, else None."""
    if job.like_score is None or job.like_score <= 0 or not job.like_terms:
        return None
    return f"like your favorites ({job.like_score:+.2f}): {', '.join(job.like_terms)}"

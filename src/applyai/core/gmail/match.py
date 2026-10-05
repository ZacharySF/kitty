"""Tie an email to exactly one open application, or to none.

Matching is by company name (normalized the same way as the companies table)
in the sender's name or domain and the subject first, then the body. Anything
unclear is skipped and counted, never guessed:
  * no open application's company appears        -> 'no_company'
  * two or more different companies appear        -> 'ambiguous'
  * one company, several applications, and the job
    title words do not single one out              -> 'several_applications'
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from applyai.core.gmail.fetch import GmailMessage
from applyai.normalize import norm_title

#: Statuses an email can still move. Ghosted counts: a late reply proves it was not.
MATCHABLE_STATUSES = ("applied", "oa", "interview", "offer", "ghosted")


@dataclass(frozen=True)
class OpenApplication:
    """An application an email might be about."""

    id: int
    status: str
    company_name: str
    company_norm: str
    title: str


@dataclass(frozen=True)
class MatchResult:
    """The one application matched, or the reason there is none."""

    application: OpenApplication | None
    reason: str | None = None


def open_applications(conn: sqlite3.Connection) -> list[OpenApplication]:
    """Every application whose status an email could still change."""
    marks = ", ".join("?" for _ in MATCHABLE_STATUSES)
    rows = conn.execute(
        f"""
        SELECT a.id, a.status, c.display_name, c.norm_name, j.title
          FROM applications a
          JOIN jobs j      ON j.id = a.job_id
          JOIN companies c ON c.id = j.company_id
         WHERE a.status IN ({marks})
         ORDER BY a.id
        """,
        MATCHABLE_STATUSES,
    )
    return [OpenApplication(row[0], row[1], row[2], row[3], row[4]) for row in rows]


def _words(text: str) -> str:
    """Lowercase alphanumeric words joined by single spaces, padded for whole-word search."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def _companies_in(text: str, domain_labels: set[str], norms: set[str]) -> set[str]:
    """Company norms that appear as whole words in `text` or as a domain label.

    When one found name is part of another ("citadel" in "citadel securities"),
    only the longer one is kept.
    """
    words = _words(text)
    found = {norm for norm in norms
             if f" {norm} " in words or norm.replace(" ", "") in domain_labels}
    return {norm for norm in found
            if not any(norm != other and f" {norm} " in f" {other} " for other in found)}


def _pick_by_title(candidates: list[OpenApplication], text: str) -> OpenApplication | None:
    """The single application whose title words appear most often in the email."""
    words = set(_words(text).split())
    scores = [(len(set(norm_title(app.title).split()) & words), app) for app in candidates]
    best = max(score for score, _ in scores)
    winners = [app for score, app in scores if score == best]
    return winners[0] if best > 0 and len(winners) == 1 else None


def match_application(message: GmailMessage, applications: list[OpenApplication]) -> MatchResult:
    """Find the one open application this message is about."""
    norms = {app.company_norm for app in applications if app.company_norm}
    domain_labels = set(message.sender_domain.split("."))
    found = _companies_in(f"{message.sender}\n{message.subject}", domain_labels, norms)
    if not found:
        found = _companies_in(message.body, set(), norms)
    if not found:
        return MatchResult(None, "no_company")
    if len(found) > 1:
        return MatchResult(None, "ambiguous")

    company = found.pop()
    candidates = [app for app in applications if app.company_norm == company]
    if len(candidates) == 1:
        return MatchResult(candidates[0])
    chosen = _pick_by_title(candidates, f"{message.subject}\n{message.body}")
    return MatchResult(chosen) if chosen else MatchResult(None, "several_applications")

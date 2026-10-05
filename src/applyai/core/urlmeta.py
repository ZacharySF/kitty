"""Fetching a page title to pre-fill the add-a-job form.

This is the only network call in Stage 1 and it is opt-in: the modal fetches only
when I press the fetch key. Whatever comes back is shown in editable fields -
the tool never stores a guess without my seeing it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx

from applyai.normalize import company_from_url

USER_AGENT = "applyai/0.1 (personal job tracker; +local use)"
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# Boards put "Company - Title" or "Title at Company" in <title>; these split it.
_SEPARATORS = (" - ", " – ", " | ", " — ", " at ")


@dataclass(frozen=True)
class PageMeta:
    """What a quick GET could tell us about a job URL. All fields are guesses."""

    url: str
    page_title: str | None = None
    company_guess: str | None = None
    title_guess: str | None = None
    error: str | None = None


def _split_title(page_title: str, company_hint: str | None) -> tuple[str | None, str | None]:
    """Split a page <title> into (job title, company), using the host as a hint."""
    text = " ".join(page_title.split())
    for separator in _SEPARATORS:
        if separator not in text:
            continue
        left, _, right = text.partition(separator)
        left, right = left.strip(), right.strip()
        if company_hint and company_hint.lower() in left.lower():
            return right or None, left or None
        if company_hint and company_hint.lower() in right.lower():
            return left or None, right or None
        # No hint matched: boards usually write "Job Title - Company".
        return left or None, right or None
    return text or None, company_hint


def fetch_page_meta(url: str, *, timeout: float = 10.0) -> PageMeta:
    """GET a job URL and guess its title and company.

    Never raises: a failure comes back as PageMeta(error=...) so the modal can say
    "could not fetch, type it in" instead of crashing.
    """
    company_hint = company_from_url(url)
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return PageMeta(url=url, company_guess=company_hint, error=str(exc))

    match = _TITLE.search(response.text)
    if not match:
        return PageMeta(
            url=url, company_guess=company_hint, error="no <title> in the page"
        )

    page_title = html.unescape(match.group(1)).strip()
    title_guess, company_guess = _split_title(page_title, company_hint)
    return PageMeta(
        url=url,
        page_title=page_title,
        company_guess=company_guess or company_hint,
        title_guess=title_guess,
    )

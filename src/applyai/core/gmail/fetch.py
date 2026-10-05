"""Read recruiting-looking messages through the Gmail REST API.

Polite on purpose: small pages, one request at a time with a short pause, and
the caller skips message ids it has already looked at so they are never fetched
again. Errors are honest: a 401 asks for re-auth, a 429 (or a 403 quota error)
stops the run as rate limited instead of retrying.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any, Protocol

import requests

from applyai.core.discovery.text import html_to_text, unix_to_iso

API = "https://gmail.googleapis.com/gmail/v1/users/me"
PAGE_SIZE = 25
#: Pause between requests, in seconds.
REQUEST_DELAY = 0.25
TIMEOUT = 30
#: Classification only needs the top of a message; quoted threads add noise.
MAX_BODY_CHARS = 20_000

#: Senders of application-status mail: ATS platforms and assessment tools.
ATS_SENDERS = (
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co", "ashbyhq.com",
    "myworkday.com", "workday.com", "smartrecruiters.com", "icims.com", "jobvite.com",
    "taleo.net", "successfactors.com", "eightfold.ai", "avature.net", "brassring.com",
    "hackerrank.com", "hackerrankforwork.com", "codesignal.com", "codility.com",
    "hirevue.com", "modernhire.com", "pymetrics.ai", "gem.com", "rippling.com",
)
#: Subject words that status mail tends to carry.
SUBJECT_WORDS = (
    "application", "applying", "candidacy", "interview", "assessment", "offer",
    '"next steps"', "internship", "intern",
)


class GmailSession(Protocol):
    """What fetch needs from an HTTP session (google-auth's AuthorizedSession fits)."""

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> Any: ...


class GmailFetchError(Exception):
    """A request failed. `kind` is 'auth', 'rate_limited' or 'error'."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class GmailMessage:
    """One message, reduced to what classification and matching read."""

    id: str
    sender: str            # display name, e.g. "Jane Street Recruiting"
    sender_email: str      # e.g. "no-reply@janestreet.com"
    subject: str
    received_at: str       # ISO UTC
    body: str

    @property
    def sender_domain(self) -> str:
        return self.sender_email.rpartition("@")[2].lower()


def build_query(days: int) -> str:
    """The Gmail search: recent, not chat, from an ATS or with a recruiting subject."""
    senders = " OR ".join(ATS_SENDERS)
    subjects = " OR ".join(SUBJECT_WORDS)
    return (f"newer_than:{days}d -in:chats -in:sent -category:promotions -category:social "
            f"(from:({senders}) OR subject:({subjects}))")


def _get(session: GmailSession, path: str, params: dict) -> dict:
    """GET one API path and return its JSON, turning failures into GmailFetchError."""
    try:
        response = session.get(f"{API}/{path}", params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise GmailFetchError("error", f"Gmail request failed: {type(exc).__name__}: {exc}") from exc
    status = response.status_code
    if status == 401:
        raise GmailFetchError("auth", "Gmail answered 401 (token not accepted): run `applyai gmail auth` again.")
    if status == 429 or (status == 403 and "rateLimitExceeded" in response.text):
        raise GmailFetchError("rate_limited", f"Gmail answered {status} (rate limited); stopped, try again later.")
    if status >= 400:
        raise GmailFetchError("error", f"Gmail answered {status}: {response.text[:200]}")
    return response.json()


def list_message_ids(
    session: GmailSession, query: str, *, limit: int, delay: float = REQUEST_DELAY
) -> list[str]:
    """Message ids matching `query`, newest first, at most `limit`."""
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < limit:
        params: dict = {"q": query, "maxResults": min(PAGE_SIZE, limit - len(ids))}
        if page_token:
            params["pageToken"] = page_token
        page = _get(session, "messages", params)
        ids.extend(item["id"] for item in page.get("messages", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
        time.sleep(delay)
    return ids[:limit]


def get_message(session: GmailSession, message_id: str) -> GmailMessage:
    """Fetch and decode one message."""
    return parse_message(_get(session, f"messages/{message_id}", {"format": "full"}))


def parse_message(payload: dict) -> GmailMessage:
    """Turn a users.messages.get (format=full) response into a GmailMessage."""
    part = payload.get("payload") or {}
    headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}
    name, address = parseaddr(headers.get("from", ""))
    body = _body_text(part) or payload.get("snippet", "")
    return GmailMessage(
        id=payload["id"],
        sender=name,
        sender_email=address,
        subject=headers.get("subject", ""),
        received_at=unix_to_iso(int(payload.get("internalDate", 0)), millis=True) or "",
        body=body[:MAX_BODY_CHARS],
    )


def _decode(data: str) -> str:
    """Gmail bodies are base64url without padding."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _collect(part: dict, mime: str) -> list[str]:
    """Every decoded body of one MIME type in a part tree, attachments excluded."""
    found: list[str] = []
    data = (part.get("body") or {}).get("data")
    if part.get("mimeType") == mime and data and not part.get("filename"):
        found.append(_decode(data))
    for child in part.get("parts", []) or []:
        found.extend(_collect(child, mime))
    return found


def _body_text(part: dict) -> str:
    """The plain-text body, falling back to the HTML body as text."""
    plain = _collect(part, "text/plain")
    if plain:
        return "\n".join(plain).strip()
    return "\n".join(html_to_text(markup) or "" for markup in _collect(part, "text/html")).strip()

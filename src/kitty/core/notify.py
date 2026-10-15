"""Optional push of the daily digest to my phone: ntfy or a Telegram bot.

Off by default. It turns on only when .env sets NTFY_TOPIC, or both
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (ntfy wins when both are set). Only the
digest summary is ever sent - no job descriptions, resumes or answers. One
request per digest, no retries: a failed push is reported, never repeated.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx

NTFY_URL = "https://ntfy.sh/{topic}"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 15.0
#: ntfy and Telegram both cap a message near 4096 characters.
MAX_CHARS = 3900


def _env(name: str) -> str:
    """An environment variable, stripped; '' when unset."""
    return os.environ.get(name, "").strip()


def _trim(text: str) -> str:
    """Cut the text to fit one push message."""
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 4] + "\n..."


def _send_ntfy(topic: str, text: str, title: str) -> str:
    """POST the text to an ntfy.sh topic; return what happened."""
    try:
        response = httpx.post(
            NTFY_URL.format(topic=quote(topic, safe="")),
            content=_trim(text).encode("utf-8"),
            headers={"Title": title},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return f"ntfy failed: {type(exc).__name__}: {exc}"
    if response.is_success:
        return f"sent to ntfy topic {topic}"
    return f"ntfy failed: HTTP {response.status_code} {response.text[:200]}"


def _send_telegram(token: str, chat_id: str, text: str) -> str:
    """Send the text through a Telegram bot; return what happened.

    Errors never include the request URL, because it carries the bot token.
    """
    try:
        response = httpx.post(
            TELEGRAM_URL.format(token=token),
            json={"chat_id": chat_id, "text": _trim(text), "disable_web_page_preview": True},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return f"telegram failed: {type(exc).__name__}"
    if response.is_success:
        return f"sent to telegram chat {chat_id}"
    try:
        detail = str(response.json().get("description", ""))
    except ValueError:
        detail = ""
    return f"telegram failed: HTTP {response.status_code} {detail}".rstrip()


def notifier_configured() -> str | None:
    """'ntfy' or 'telegram' when .env sets one up, else None."""
    if _env("NTFY_TOPIC"):
        return "ntfy"
    if _env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID"):
        return "telegram"
    return None


def send_digest(text: str, *, title: str = "kitty today") -> str | None:
    """Push the digest text; return a one-line result, or None when no notifier is set up.

    `title` is the ntfy notification title (Telegram messages have none).
    Never raises for a network or HTTP problem: the message says what failed.
    """
    kind = notifier_configured()
    if kind == "ntfy":
        return _send_ntfy(_env("NTFY_TOPIC"), text, title)
    if kind == "telegram":
        return _send_telegram(_env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID"), text)
    return None

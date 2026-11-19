"""A Microsoft account through Microsoft Graph: Outlook calendars and inbox, read only.

The app registration is the owner's own public client (no secret). Scopes are
delegated and read-only: ``User.Read offline_access Calendars.Read Mail.Read``.

Calendars: every Outlook calendar becomes a local calendar listed in
``calendar_remote`` (read-only in rika!). Each sync reads the calendar view of a
rolling window, which Graph returns as expanded instances, and reconciles it:
new events are added, changed ones updated, and ones no longer there removed.

Mail: recent inbox messages are stored in Rika's mail table with
``provider='outlook'``, so the classifier, the brief and Rika's mail tools see them.
Rika drafts no replies to them: nothing is written to the mailbox.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode
from uuid import uuid4

import requests

from kitty.core import calendar as local
from kitty.core import connect
from kitty.core.connect import ConnectError
from kitty.db.connection import transaction

PROVIDER = "microsoft"
#: `common` signs in work, school and personal accounts of a multitenant registration.
AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("User.Read", "offline_access", "Calendars.Read", "Mail.Read")
CLIENT_ID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
#: The calendar window that is kept in step, around today.
PAST, AHEAD = timedelta(days=30), timedelta(days=180)
MAX_PAGES = 50
MAIL_PAGE = 25
EVENT_FIELDS = "id,subject,start,end,isAllDay,location,bodyPreview,webLink,showAs,isCancelled,isReminderOn,reminderMinutesBeforeStart"
MAIL_FIELDS = "id,conversationId,internetMessageId,from,replyTo,subject,body,receivedDateTime,internetMessageHeaders"
EXPIRED = "Microsoft sign-in expired; connect again"


# Authorization -------------------------------------------------------------------

def authorize_url(client_id: str, redirect: str, state: str, challenge: str) -> str:
    return AUTHORITY + "/authorize?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": redirect, "response_mode": "query",
        "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "prompt": "select_account"})


def _token(client_id: str, payload: dict) -> dict:
    if not isinstance(payload.get("access_token"), str) or not isinstance(payload.get("refresh_token"), str):
        raise ConnectError(502, "Microsoft did not return a usable sign-in")
    return {"client_id": client_id, "access_token": payload["access_token"], "refresh_token": payload["refresh_token"],
            "expires_at": connect.expires_at(payload), "scopes": str(payload.get("scope", "")).split()}


def exchange(settings, client_id: str, code: str, verifier: str, redirect: str) -> dict:
    """Trade the code for tokens (PKCE, no secret), save them and return ``{account, scopes}``."""
    response = connect.post_form(AUTHORITY + "/token", {
        "client_id": client_id, "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
        "code_verifier": verifier, "scope": " ".join(SCOPES)}, "Microsoft")
    if response.status_code != 200:
        raise ConnectError(502, "Microsoft refused the sign-in. Check the app registration and try again.")
    token = _token(client_id, response.json())
    granted = {scope.lower() for scope in token["scopes"]}
    if not {"calendars.read", "mail.read"} <= granted:
        raise ConnectError(403, "Microsoft did not grant calendar and mail reading; connect again and accept both")
    connect.save_token(settings, PROVIDER, token)
    try:
        me = _get(settings, "/me?$select=mail,userPrincipalName")
    except ConnectError:
        connect.drop_token(settings, PROVIDER)
        raise
    return {"account": me.get("mail") or me.get("userPrincipalName") or "Microsoft account",
            "scopes": " ".join(token["scopes"])}


def _refresh(saved: dict) -> dict:
    response = connect.post_form(AUTHORITY + "/token", {
        "client_id": saved["client_id"], "grant_type": "refresh_token", "refresh_token": saved["refresh_token"],
        "scope": " ".join(SCOPES)}, "Microsoft")
    if response.status_code in {400, 401}:
        raise ConnectError(401, EXPIRED)
    if response.status_code != 200:
        raise ConnectError(502, f"Microsoft answered {response.status_code} to a token refresh; try again later")
    return _token(saved["client_id"], response.json())


# Graph ---------------------------------------------------------------------------

def _get(settings, url: str, prefer: str = "") -> dict:
    """One GET; `url` is a Graph path or a next-page link Graph returned."""
    if url.startswith("/"):
        url = GRAPH + url
    elif not url.startswith(GRAPH + "/"):
        raise ConnectError(502, "Microsoft returned a next page outside Microsoft Graph; stopped")
    headers = {"Authorization": "Bearer " + connect.access_token(settings, PROVIDER, _refresh)}
    if prefer:
        headers["Prefer"] = prefer
    try:
        response = requests.get(url, headers=headers, timeout=connect.TIMEOUT)
    except requests.RequestException as exc:
        raise ConnectError(502, f"Microsoft could not be reached: {type(exc).__name__}") from None
    status = response.status_code
    if status == 401:
        raise ConnectError(401, EXPIRED)
    if status == 429:
        raise ConnectError(429, "Microsoft answered 429 (rate limited); stopped, try again later")
    if status >= 400:
        # Never surface a provider body, which may contain private details.
        raise ConnectError(502, f"Microsoft answered {status}")
    return response.json()


def _all(settings, url: str, prefer: str = "") -> list[dict]:
    items: list[dict] = []
    for _ in range(MAX_PAGES):
        page = _get(settings, url, prefer)
        items.extend(page.get("value", []))
        url = page.get("@odata.nextLink")
        if not url:
            return items
    raise ConnectError(502, "Microsoft returned more pages than rika! reads in one sync; stopped")


# Calendars -----------------------------------------------------------------------

def _event(conn: sqlite3.Connection, calendar_id: str, remote: dict) -> dict:
    """A Graph event (times in UTC) as local event data; APIError for one rika! cannot hold."""
    all_day = bool(remote.get("isAllDay"))
    start, end = (str((remote.get(key) or {}).get("dateTime", "")) for key in ("start", "end"))
    if all_day:
        start, end = start[:10], end[:10]
    else:
        start, end = start[:19] + "+00:00", end[:19] + "+00:00"
        if end == start:  # Outlook allows events with no length
            end = (datetime.fromisoformat(start) + timedelta(minutes=1)).isoformat()
    minutes = remote.get("reminderMinutesBeforeStart")
    remind = remote.get("isReminderOn") and type(minutes) is int and 0 <= minutes <= 40320
    return local.normalize_event(conn, {
        "calendar_id": calendar_id, "title": (remote.get("subject") or "").strip()[:500] or "(No title)",
        "start": start, "end": end, "all_day": all_day, "timezone": "UTC",
        "description": remote.get("bodyPreview") or "", "location": (remote.get("location") or {}).get("displayName") or "",
        "url": remote.get("webLink") or "", "busy": remote.get("showAs") not in {"free", "workingElsewhere"},
        "reminders": [minutes] if remind else []})


def _retire(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute("UPDATE calendar_events SET deleted=1,revision=revision+1,updated_at=? WHERE id=?", (local.now(), event_id))


def _drop_calendar(conn: sqlite3.Connection, calendar_id: str) -> None:
    for row in conn.execute("SELECT event_id FROM calendar_remote_events WHERE calendar_id=?", (calendar_id,)).fetchall():
        _retire(conn, row["event_id"])
    conn.execute("DELETE FROM calendar_remote WHERE calendar_id=?", (calendar_id,))
    conn.execute("UPDATE calendar_calendars SET deleted=1,revision=revision+1,updated_at=? WHERE id=?",
                 (local.now(), calendar_id))


def sync_calendars(settings, conn: sqlite3.Connection) -> dict[str, int]:
    """Bring every Outlook calendar's window in step; returns counts of what changed."""
    from kitty.web_server import APIError

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    window = urlencode({"startDateTime": (today - PAST).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "endDateTime": (today + AHEAD).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "$select": EVENT_FIELDS, "$top": 100})
    # Everything is read before anything is written, so a failed read changes nothing.
    remote = [(item["id"], item.get("name") or "Calendar",
               _all(settings, f"/me/calendars/{quote(item['id'], safe='')}/calendarView?{window}",
                    'outlook.timezone="UTC"'))
              for item in _all(settings, "/me/calendars?$select=id,name")]
    result = {"calendars": len(remote), "added": 0, "updated": 0, "removed": 0, "skipped": 0}
    with transaction(conn, immediate=True):
        known = {row["remote_id"]: row["calendar_id"]
                 for row in conn.execute("SELECT * FROM calendar_remote WHERE provider=?", (PROVIDER,))}
        for remote_id in set(known) - {ident for ident, _name, _events in remote}:
            _drop_calendar(conn, known[remote_id])
        for remote_id, name, events in remote:
            calendar_id = known.get(remote_id)
            if calendar_id is None:
                calendar_id = uuid4().hex
                conn.execute("INSERT INTO calendar_calendars(id,name,color,timezone,updated_at) VALUES(?,?,?,'UTC',?)",
                             (calendar_id, f"{name} (Outlook)"[:200], local.next_color(conn), local.now()))
                conn.execute("INSERT INTO calendar_remote(calendar_id,provider,remote_id) VALUES(?,?,?)",
                             (calendar_id, PROVIDER, remote_id))
            mapped = {row["remote_id"]: row for row in conn.execute(
                "SELECT * FROM calendar_remote_events WHERE calendar_id=?", (calendar_id,))}
            seen = set()
            for item in events:
                if item.get("isCancelled") or not isinstance(item.get("id"), str):
                    continue
                try:
                    data = _event(conn, calendar_id, item)
                except APIError:
                    result["skipped"] += 1
                    continue
                seen.add(item["id"])
                fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
                row = mapped.get(item["id"])
                if row is None:
                    event_id = local._insert(conn, data)
                    conn.execute("INSERT INTO calendar_remote_events(calendar_id,remote_id,event_id,fingerprint) "
                                 "VALUES(?,?,?,?)", (calendar_id, item["id"], event_id, fingerprint))
                    result["added"] += 1
                elif row["fingerprint"] != fingerprint:
                    local._update(conn, row["event_id"], data, manual=False)
                    conn.execute("UPDATE calendar_remote_events SET fingerprint=? WHERE event_id=?",
                                 (fingerprint, row["event_id"]))
                    result["updated"] += 1
            for remote_event, row in mapped.items():
                if remote_event not in seen:
                    _retire(conn, row["event_id"])
                    conn.execute("DELETE FROM calendar_remote_events WHERE event_id=?", (row["event_id"],))
                    result["removed"] += 1
    return result


# Mail ----------------------------------------------------------------------------

def _message(item: dict):
    from kitty.core.rika import mail

    sender = (item.get("from") or {}).get("emailAddress") or {}
    address = sender.get("address")
    if not isinstance(item.get("id"), str) or not address or not item.get("receivedDateTime"):
        return None
    reply_to = next((entry["emailAddress"]["address"] for entry in item.get("replyTo") or []
                     if (entry.get("emailAddress") or {}).get("address")), "")
    headers = {entry.get("name", "").lower(): entry.get("value", "") for entry in item.get("internetMessageHeaders") or []}
    bulk = ("list-unsubscribe" in headers or headers.get("precedence", "").lower() in {"bulk", "list", "junk"}
            or headers.get("auto-submitted", "no").lower() != "no")
    return mail.MailMessage(
        id="outlook:" + item["id"], thread_id=item.get("conversationId") or item["id"],
        rfc_id=item.get("internetMessageId") or "", sender=sender.get("name") or "", sender_email=reply_to or address,
        subject=item.get("subject") or "", body=((item.get("body") or {}).get("content") or "")[:mail.MAX_BODY],
        received_at=item["receivedDateTime"], bulk=bulk, from_email=address)


def sync_mail(settings, conn: sqlite3.Connection) -> dict[str, int]:
    """Store inbox mail of the last days that Rika has not seen; returns ``{"new": n}``."""
    from kitty.core.rika import mail

    since = (datetime.now(UTC) - mail.LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urlencode({"$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime desc",
                       "$top": MAIL_PAGE, "$select": MAIL_FIELDS})
    # Plain-text bodies, and ids that survive a move between folders.
    page = _get(settings, "/me/mailFolders/inbox/messages?" + query,
                'outlook.body-content-type="text", IdType="ImmutableId"')
    messages = [message for message in map(_message, page.get("value", [])) if message is not None]
    new = sum(mail._store(conn, message, provider="outlook") is not None
              for message in sorted(messages, key=lambda item: item.received_at))
    return {"new": new}


def sync(settings, conn: sqlite3.Connection) -> dict:
    return {"calendar": sync_calendars(settings, conn), "mail": sync_mail(settings, conn)}


def disconnect(settings, conn: sqlite3.Connection) -> None:
    """Remove the tokens, the account and the imported calendars. Mail Rika already read stays."""
    connect.drop_token(settings, PROVIDER)
    with transaction(conn, immediate=True):
        for row in conn.execute("SELECT calendar_id FROM calendar_remote WHERE provider=?", (PROVIDER,)).fetchall():
            _drop_calendar(conn, row["calendar_id"])
        conn.execute("DELETE FROM connect_accounts WHERE provider=?", (PROVIDER,))

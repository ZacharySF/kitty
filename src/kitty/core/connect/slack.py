"""Slack, read only: a reaction you add to a message becomes a task in rika!.

Each user creates their own Slack app from :func:`manifest` (an internal app of their
workspace: PKCE on, token rotation on, one fixed loopback redirect, no secret used).
A loopback redirect of a PKCE app is a desktop redirect, so rika! gets a user token
only, with one scope: ``reactions:read``. rika! posts nothing.

Each sync lists the messages you reacted to; one carrying the chosen reaction becomes
a calendar task (title: the message's first line; description: its permalink) exactly
once, recorded in ``slack_captures`` with a short excerpt and nothing else. Reactions
that were already there when Slack was connected, or when the reaction was changed,
are recorded without a task.
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import urlencode

import requests

from kitty.core import calendar as local
from kitty.core import connect
from kitty.core.connect import ConnectError
from kitty.db.connection import transaction

PROVIDER = "slack"
AUTHORIZE = "https://slack.com/oauth/v2/authorize"
API = "https://slack.com/api"
SCOPES = ("reactions:read",)
#: Slack matches the redirect exactly, port included, so the port is fixed.
PORT = 47813
CALLBACK = "/slack/callback"
REDIRECT = f"http://localhost:{PORT}{CALLBACK}"
CLIENT_ID = re.compile(r"[0-9]{1,20}\.[0-9]{1,20}")
REACTION = re.compile(r"[a-z0-9_+'-]{1,100}")
DEFAULT_REACTION = "pushpin"
MAX_PAGES = 5
EXPIRED = "Slack sign-in expired; connect again"


def manifest() -> str:
    """The app manifest to paste at api.slack.com/apps → Create New App → From a manifest."""
    return json.dumps({
        "display_information": {"name": "rika!", "description": "Turns a reaction into a task in rika! on my computer."},
        "oauth_config": {"redirect_urls": [REDIRECT], "scopes": {"user": list(SCOPES)}, "pkce_enabled": True},
        "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": True},
    }, indent=2)


def reaction(conn: sqlite3.Connection) -> str:
    return connect.config(conn, "slack_reaction", DEFAULT_REACTION)


# Authorization -------------------------------------------------------------------

def authorize_url(client_id: str, redirect: str, state: str, challenge: str) -> str:
    return AUTHORIZE + "?" + urlencode({
        "client_id": client_id, "scope": "", "user_scope": ",".join(SCOPES), "redirect_uri": redirect,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def _answer(response: requests.Response) -> dict:
    if response.status_code == 429:
        raise ConnectError(429, "Slack answered 429 (rate limited); stopped, try again later")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code >= 400 or not isinstance(payload, dict):
        raise ConnectError(502, f"Slack answered {response.status_code}")
    return payload


def exchange(settings, client_id: str, code: str, verifier: str, redirect: str) -> dict:
    """Trade the code for a user token (PKCE, no secret), save it and return ``{account, scopes}``."""
    payload = _answer(connect.post_form(API + "/oauth.v2.access", {
        "client_id": client_id, "code": code, "code_verifier": verifier, "redirect_uri": redirect}, "Slack"))
    user = payload.get("authed_user") or {}
    team = payload.get("team") or {}
    if not payload.get("ok") or not isinstance(user.get("access_token"), str) or not user.get("id") or not team.get("id"):
        raise ConnectError(502, "Slack refused the sign-in. Check the app's client ID and try again.")
    scopes = str(user.get("scope", ""))
    if "reactions:read" not in scopes.split(","):
        raise ConnectError(403, "Slack did not grant reading your reactions; connect again and allow it")
    connect.save_token(settings, PROVIDER, {
        "client_id": client_id, "access_token": user["access_token"], "refresh_token": user.get("refresh_token"),
        "expires_at": connect.expires_at(user), "user": user["id"], "team": team["id"]})
    return {"account": team.get("name") or team["id"], "scopes": scopes}


def _refresh(saved: dict) -> dict:
    payload = _answer(connect.post_form(API + "/oauth.v2.access", {
        "client_id": saved["client_id"], "grant_type": "refresh_token", "refresh_token": saved["refresh_token"]}, "Slack"))
    # A refreshed user token is answered at the top level, or under authed_user like the first one.
    token = payload if isinstance(payload.get("access_token"), str) else payload.get("authed_user") or {}
    if not payload.get("ok") or not isinstance(token.get("access_token"), str):
        raise ConnectError(401, EXPIRED)
    return {**saved, "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token") or saved["refresh_token"], "expires_at": connect.expires_at(token)}


def _call(settings, method: str, params: dict) -> dict:
    headers = {"Authorization": "Bearer " + connect.access_token(settings, PROVIDER, _refresh)}
    try:
        response = requests.get(f"{API}/{method}", params=params, headers=headers, timeout=connect.TIMEOUT)
    except requests.RequestException as exc:
        raise ConnectError(502, f"Slack could not be reached: {type(exc).__name__}") from None
    payload = _answer(response)
    if not payload.get("ok"):
        if payload.get("error") in {"invalid_auth", "token_expired", "token_revoked", "account_inactive", "not_authed"}:
            raise ConnectError(401, EXPIRED)
        if payload.get("error") == "ratelimited":
            raise ConnectError(429, "Slack answered rate limited; stopped, try again later")
        raise ConnectError(502, f"Slack refused {method}: {str(payload.get('error'))[:80]}")
    return payload


# Reaction → task -----------------------------------------------------------------

def _reacted(settings, name: str) -> list[dict]:
    """Messages carrying your `name` reaction: ``{channel, ts, text, permalink}``."""
    saved = connect.load_token(settings, PROVIDER) or {}
    found, cursor = [], ""
    for _ in range(MAX_PAGES):
        page = _call(settings, "reactions.list", {"full": "true", "limit": 100, **({"cursor": cursor} if cursor else {})})
        for item in page.get("items", []):
            message = item.get("message") or {}
            if item.get("type") != "message" or not item.get("channel") or not message.get("ts"):
                continue
            if not any(entry.get("name") == name and saved.get("user") in (entry.get("users") or [])
                       for entry in message.get("reactions") or []):
                continue
            found.append({"channel": item["channel"], "ts": message["ts"], "text": str(message.get("text") or ""),
                          "permalink": str(message.get("permalink") or "")})
        cursor = (page.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            break
    return found


def sync(settings, conn: sqlite3.Connection) -> dict[str, int]:
    """Turn each newly reacted message into one task; returns ``{"tasks": n}``."""
    name = reaction(conn)
    team = (connect.load_token(settings, PROVIDER) or {}).get("team", "")
    seen = {(row["channel"], row["ts"]) for row in conn.execute("SELECT channel,ts FROM slack_captures WHERE team=?", (team,))}
    fresh = [item for item in _reacted(settings, name) if (item["channel"], item["ts"]) not in seen]
    # The first look after connecting (or changing the reaction) only notes what is already there.
    baseline = connect.config(conn, "slack_baseline") != name
    made = 0
    for item in fresh:
        if not baseline and not item["permalink"]:
            item["permalink"] = str(_call(settings, "chat.getPermalink",
                                          {"channel": item["channel"], "message_ts": item["ts"]}).get("permalink") or "")
        lines = [line.strip() for line in item["text"].splitlines() if line.strip()]
        with transaction(conn, immediate=True):
            inserted = conn.execute(
                "INSERT OR IGNORE INTO slack_captures(team,channel,ts,permalink,excerpt,created_at) VALUES(?,?,?,?,?,?)",
                (team, item["channel"], item["ts"], item["permalink"], " ".join(lines)[:200], connect.now())).rowcount
        if baseline or not inserted:
            continue
        task = local.save_task(conn, {"title": (lines[0] if lines else "Slack message")[:200],
                                      "description": item["permalink"], "tags": ["slack"]})
        with transaction(conn):
            conn.execute("UPDATE slack_captures SET task_id=? WHERE team=? AND channel=? AND ts=?",
                         (task["id"], team, item["channel"], item["ts"]))
        made += 1
    if baseline:
        connect.set_config(conn, "slack_baseline", name)
    return {"tasks": made}


def disconnect(settings, conn: sqlite3.Connection) -> None:
    """Remove the token and the account. Tasks already made stay."""
    connect.drop_token(settings, PROVIDER)
    with transaction(conn):
        conn.execute("DELETE FROM connect_accounts WHERE provider=?", (PROVIDER,))
        conn.execute("DELETE FROM connect_config WHERE key='slack_baseline'")

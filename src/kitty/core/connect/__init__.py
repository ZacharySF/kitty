"""Connections to Microsoft (Outlook) and Slack: public OAuth clients, read-first.

Both providers are used as public clients: authorization code with PKCE (S256) and a
loopback redirect, no client secret anywhere. The client ID is the owner's own app
registration, saved in ``connect_config``. Tokens are kept the way the Google Calendar
token is (:mod:`kitty.core.calendar_google`): one owner-only JSON file per provider
under ``.private/``, never in SQLite or logs. Nothing here writes to either service.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from kitty.db.connection import transaction

PROVIDERS = ("microsoft", "slack")
LABELS = {"microsoft": "Microsoft", "slack": "Slack"}
TIMEOUT = 30
#: An access token this close to its expiry is refreshed before use.
EXPIRY_MARGIN = 60
#: One refresh at a time: both providers replace the refresh token on every use.
_refreshing = threading.Lock()


class ConnectError(Exception):
    """A refused or failed connection step; the message is safe to show. `status` is the HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Config and account rows ---------------------------------------------------------

def config(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM connect_config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    with transaction(conn):
        conn.execute("INSERT INTO connect_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))


def account(conn: sqlite3.Connection, provider: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM connect_accounts WHERE provider=?", (provider,)).fetchone()


def save_account(conn: sqlite3.Connection, provider: str, name: str, scopes: str) -> None:
    with transaction(conn):
        conn.execute("INSERT INTO connect_accounts(provider,account,scopes,created_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(provider) DO UPDATE SET account=excluded.account,scopes=excluded.scopes,"
                     "created_at=excluded.created_at,last_sync=NULL,error=NULL", (provider, name, scopes, now()))


def record_sync(conn: sqlite3.Connection, provider: str, error: str | None) -> None:
    """After a sync: the time of a good one, or why the last one failed."""
    with transaction(conn):
        if error is None:
            conn.execute("UPDATE connect_accounts SET last_sync=?,error=NULL WHERE provider=?", (now(), provider))
        else:
            conn.execute("UPDATE connect_accounts SET error=? WHERE provider=?", (error, provider))


# Token files -----------------------------------------------------------------------

def token_path(settings, provider: str) -> Path:
    return settings.root / ".private" / f"{provider}_token.json"


def save_token(settings, provider: str, token: dict) -> None:
    path = token_path(settings, provider)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".connect-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(token))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_token(settings, provider: str) -> dict | None:
    try:
        saved = json.loads(token_path(settings, provider).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and isinstance(saved.get("access_token"), str) else None


def drop_token(settings, provider: str) -> None:
    token_path(settings, provider).unlink(missing_ok=True)


def expires_at(payload: dict) -> float | None:
    """When a token response's access token ends, or None for one that does not expire."""
    seconds = payload.get("expires_in")
    return time.time() + seconds if isinstance(seconds, (int, float)) and seconds > 0 else None


def post_form(url: str, data: dict, label: str) -> requests.Response:
    try:
        return requests.post(url, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ConnectError(502, f"{label} could not be reached: {type(exc).__name__}") from None


def access_token(settings, provider: str, refresh: Callable[[dict], dict]) -> str:
    """A usable access token, refreshed first when it is about to expire.

    `refresh(saved)` asks the provider for the next token file contents; it raises
    ConnectError(401) when the refresh token is no longer accepted.
    """
    with _refreshing:
        saved = load_token(settings, provider)
        if saved is None:
            raise ConnectError(409, f"Connect {LABELS[provider]} first")
        ends = saved.get("expires_at")
        if ends is None or ends > time.time() + EXPIRY_MARGIN:
            return saved["access_token"]
        if not saved.get("refresh_token"):
            raise ConnectError(401, f"{LABELS[provider]} sign-in expired; connect again")
        renewed = refresh(saved)
        save_token(settings, provider, renewed)
        return renewed["access_token"]


# Loopback authorization ----------------------------------------------------------

def challenge(verifier: str) -> str:
    """The S256 code challenge of a PKCE verifier (RFC 7636)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class Loopback:
    """At most one bounded, cancellable authorization attempt (the model of
    :class:`kitty.google_connection.GoogleConnection`, without a secret).

    The listener binds 127.0.0.1 only and answers one path. It accepts the redirect
    only with this attempt's `state`; the code is exchanged with this attempt's PKCE
    verifier. Only the page that started the attempt holds the ticket to poll it.
    """

    def __init__(self, label: str, *, lifetime: float = 300) -> None:
        self.label = label
        self.lifetime = lifetime
        self._lock = threading.Lock()
        self._attempt: dict | None = None

    def start(self, authorize: Callable[[str, str, str], str], exchange: Callable[[str, str, str], None], *,
              port: int = 0, path: str = "/") -> dict:
        """Listen on 127.0.0.1:`port` (0: any free port) and return ``{url, ticket}``.

        `authorize(redirect, state, challenge)` builds the provider's sign-in address;
        `exchange(code, verifier, redirect)` finishes the connection or raises ConnectError.
        """
        self.close()
        state, ticket = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        attempt = {"ticket": ticket, "expires": time.monotonic() + self.lifetime,
                   "result": {"status": "pending"}, "stop": threading.Event()}
        owner, label = self, self.label

        class Callback(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_args):
                pass

            def do_GET(self):
                accepted = False
                try:
                    target = urlsplit(self.path)
                    query = parse_qs(target.query, keep_blank_values=True, max_num_fields=10)
                    hosts = {f"localhost:{server.server_port}", f"127.0.0.1:{server.server_port}"}
                    valid = (target.path == path and query.get("state") == [state]
                             and len(self.headers.get_all("Host") or []) == 1 and self.headers["Host"] in hosts
                             and all(len(values) == 1 for values in query.values())
                             and ((bool(query.get("code", [""])[0]) and "error" not in query)
                                  or ("error" in query and "code" not in query)))
                    with owner._lock:
                        accepted = bool(valid and owner._attempt is attempt
                                        and time.monotonic() < attempt["expires"]
                                        and attempt["result"]["status"] == "pending")
                        if accepted:
                            attempt["result"] = {"status": "exchanging"}
                    if accepted:
                        if "error" in query:
                            result = {"status": "error", "error": f"{label} sign-in was cancelled."}
                        else:
                            try:
                                exchange(query["code"][0], verifier, redirect)
                                result = {"status": "connected"}
                            except ConnectError as exc:
                                result = {"status": "error", "error": str(exc)}
                            except Exception:  # noqa: BLE001 - never a provider body or a token in the answer
                                result = {"status": "error", "error": f"{label} sign-in could not complete. Try again."}
                        with owner._lock:
                            if owner._attempt is attempt:
                                attempt["result"] = result
                        attempt["stop"].set()
                except (ValueError, KeyError):
                    accepted = False
                text = (f"{label} sign-in finished. Return to rika! You may close this tab." if accepted
                        else "This sign-in link is invalid or expired. Start again in rika!.")
                body = ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        "<title>Return to rika!</title><h1>rika!</h1><p>" + text + "</p></html>").encode()
                self.send_response(200 if accepted else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

        try:
            server = HTTPServer(("127.0.0.1", port), Callback)
        except OSError:
            raise ConnectError(409, f"Port {port} on this computer is in use; close what uses it and try again") from None
        server.timeout = min(0.1, self.lifetime)
        redirect = f"http://localhost:{server.server_port}{'' if path == '/' else path}"
        with self._lock:
            self._attempt = attempt

        def listen():
            try:
                while not attempt["stop"].is_set() and time.monotonic() < attempt["expires"]:
                    server.handle_request()
            finally:
                server.server_close()

        worker = threading.Thread(target=listen, daemon=True, name=f"kitty-{label.lower()}-callback")
        attempt["worker"] = worker
        worker.start()
        return {"url": authorize(redirect, state, challenge(verifier)), "ticket": ticket}

    def _require(self, ticket: str) -> dict:
        attempt = self._attempt
        if not attempt or not secrets.compare_digest(attempt["ticket"], ticket):
            raise ConnectError(400, "This sign-in attempt is no longer available. Start again.")
        return attempt

    def poll(self, ticket: str) -> dict:
        with self._lock:
            attempt = self._require(ticket)
            if attempt["result"]["status"] in {"pending", "exchanging"}:
                if time.monotonic() < attempt["expires"]:
                    return {"status": "pending"}
                result = {"status": "error", "error": f"{self.label} sign-in expired. Try again."}
            else:
                result = attempt["result"]
            self._attempt = None
            attempt["stop"].set()
            return result

    def cancel(self, ticket: str) -> None:
        with self._lock:
            self._require(ticket)["stop"].set()
            self._attempt = None

    def close(self) -> None:
        with self._lock:
            attempt, self._attempt = self._attempt, None
            if attempt:
                attempt["stop"].set()
        if attempt:
            attempt["worker"].join(timeout=0.5)

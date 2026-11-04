"""Paired outside programs and their scoped tokens (platform design Q7).

The kitty Tern plugin and the browser extension reach rika! through the client
ingress, not through the page API. A client asks to pair (name, kind, scopes); rika!
shows the request with a short code the client also displays, and the user approves
or denies it in rika!. An approved client collects one random 256-bit token on its
next poll; only the token's SHA-256 is stored. Every later call presents the token and
reaches only the routes of its granted scopes. Revoking a client ends its token.

Pending requests live in memory (`Pairing`): they expire after `REQUEST_TTL`, at most
`MAX_WAITING` wait at once and at most `RATE_LIMIT` are made per `RATE_WINDOW`.
A browser extension pairs from its own origin (``chrome-extension://<id>`` or
``moz-extension://<uuid>``), and its token works only from that origin.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: What a client may be granted: `capture` writes quick notes, inbox blocks and page
#: mirror snapshots; `read` lists recent page and task titles.
SCOPES = ("capture", "read")
KINDS = ("tern-plugin", "browser-extension")
REQUEST_TTL = 300
MAX_WAITING = 3
RATE_LIMIT = 10
RATE_WINDOW = 600
MAX_NAME = 80
#: `last_used_at` is written at most this often per client.
USE_RESOLUTION = timedelta(seconds=60)
TOKEN_PREFIX = "kitty_"
_TOKEN = re.compile(r"kitty_[A-Za-z0-9_-]{43}")
_SECRET = re.compile(r"[A-Za-z0-9_-]{43}")
_CLIENT_ID = re.compile(r"[0-9a-f]{32}")
_EXTENSION = re.compile(r"chrome-extension://[a-p]{32}|moz-extension://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: Unambiguous when read aloud or copied by eye: no 0/O, 1/I/L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class ClientError(ValueError):
    def __init__(self, message: str, status: int = 400, *, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def extension_origin(value: Any) -> bool:
    """True for a Chrome-family or Firefox extension's own origin; never a web page's."""
    return isinstance(value, str) and _EXTENSION.fullmatch(value) is not None


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
        raise ClientError(f"Name the client in 1–{MAX_NAME} characters")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ClientError("Client names cannot contain control or formatting characters")
    return value.strip()


def _scopes(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(set(map(str, value))) != len(value) \
            or any(item not in SCOPES for item in value):
        raise ClientError(f"Request one or more distinct scopes from: {', '.join(SCOPES)}")
    return [scope for scope in SCOPES if scope in value]


def _client(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "scopes": json.loads(row["scopes"]),
            "origin": row["origin"], "created_at": row["created_at"], "last_used_at": row["last_used_at"]}


def create(conn: sqlite3.Connection, name: str, kind: str, scopes: list[str], origin: str | None) -> tuple[dict, str]:
    """Record an approved client; returns it and its token, which is never stored."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    client_id = uuid.uuid4().hex
    conn.execute("INSERT INTO clients(id,name,kind,scopes,origin,token_hash,created_at) VALUES (?,?,?,?,?,?,?)",
                 (client_id, name, kind, json.dumps(scopes), origin, _digest(token), now()))
    return _client(conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()), token


def authenticate(conn: sqlite3.Connection, token: Any) -> dict | None:
    """The live client holding `token`, or None. Records when it was last used."""
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    digest = _digest(token)
    row = conn.execute("SELECT * FROM clients WHERE token_hash=? AND revoked_at IS NULL", (digest,)).fetchone()
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        return None
    stamp = datetime.now(UTC)
    last = row["last_used_at"]
    if last is None or stamp - datetime.fromisoformat(last) >= USE_RESOLUTION:
        conn.execute("UPDATE clients SET last_used_at=? WHERE id=?", (stamp.isoformat(timespec="seconds"), row["id"]))
        row = conn.execute("SELECT * FROM clients WHERE id=?", (row["id"],)).fetchone()
    return _client(row)


def listing(conn: sqlite3.Connection) -> list[dict]:
    return [_client(row) for row in conn.execute("SELECT * FROM clients WHERE revoked_at IS NULL ORDER BY created_at, id")]


def revoke(conn: sqlite3.Connection, client_id: Any) -> None:
    if not isinstance(client_id, str) or not _CLIENT_ID.fullmatch(client_id):
        raise ClientError("Expected a client ID")
    if conn.execute("UPDATE clients SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now(), client_id)).rowcount != 1:
        raise ClientError("Client not found or already revoked", 404)


def allowed_origin(conn: sqlite3.Connection, origin: Any) -> bool:
    """True when `origin` is the recorded origin of a live paired extension."""
    return extension_origin(origin) and conn.execute(
        "SELECT 1 FROM clients WHERE origin=? AND revoked_at IS NULL LIMIT 1", (origin,)).fetchone() is not None


@dataclass
class _Request:
    id: str
    name: str
    kind: str
    scopes: list[str]
    origin: str | None
    code: str
    requested_at: str
    expires: float
    state: str = "pending"


class Pairing:
    """Pending pairing requests, keyed by the hash of the client's poll secret."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, _Request] = {}
        self._made: deque[float] = deque()

    def _purge(self, moment: float) -> None:
        for key in [key for key, item in self._requests.items() if item.expires <= moment]:
            del self._requests[key]
        while self._made and self._made[0] <= moment - RATE_WINDOW:
            self._made.popleft()

    def request(self, body: Any, origin: str | None) -> dict:
        """Open a request; the returned ``request`` secret is the client's to poll with."""
        if not isinstance(body, dict) or set(body) != {"name", "kind", "scopes"}:
            raise ClientError("Expected {\"name\", \"kind\", \"scopes\"}")
        name, scopes, kind = _name(body["name"]), _scopes(body["scopes"]), body["kind"]
        if kind not in KINDS:
            raise ClientError(f"Kind must be one of: {', '.join(KINDS)}")
        if kind == "browser-extension" and not extension_origin(origin):
            raise ClientError("A browser extension pairs from its extension page or background, with its own Origin")
        if kind != "browser-extension" and origin is not None:
            raise ClientError("Only a browser extension pairs from a browser origin")
        with self._lock:
            moment = self._clock()
            self._purge(moment)
            if sum(item.state == "pending" for item in self._requests.values()) >= MAX_WAITING:
                raise ClientError("Too many pairing requests are waiting; approve or deny them in rika! first", 429,
                                  retry_after=60)
            if len(self._made) >= RATE_LIMIT:
                raise ClientError("Too many pairing requests; try again later", 429,
                                  retry_after=int(self._made[0] + RATE_WINDOW - moment) + 1)
            secret = secrets.token_urlsafe(32)
            letters = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            code = f"{letters[:3]}-{letters[3:]}"
            self._requests[_digest(secret)] = _Request(uuid.uuid4().hex, name, kind, scopes, origin, code, now(),
                                                       moment + REQUEST_TTL)
            self._made.append(moment)
        return {"status": "pending", "request": secret, "code": code, "expires_in": REQUEST_TTL}

    def waiting(self) -> list[dict]:
        """Requests awaiting the user's decision, oldest first (no secrets)."""
        with self._lock:
            moment = self._clock()
            self._purge(moment)
            return [{"id": item.id, "name": item.name, "kind": item.kind, "scopes": list(item.scopes), "origin": item.origin,
                     "code": item.code, "requested_at": item.requested_at, "expires_in": max(0, int(item.expires - moment))}
                    for item in self._requests.values() if item.state == "pending"]

    def decide(self, request_id: Any, approve: bool) -> None:
        with self._lock:
            self._purge(self._clock())
            match = next((item for item in self._requests.values()
                          if isinstance(request_id, str) and item.id == request_id and item.state == "pending"), None)
            if match is None:
                raise ClientError("Pairing request not found or expired", 404)
            match.state = "approved" if approve else "denied"

    def status(self, conn: sqlite3.Connection, secret: Any, origin: str | None) -> dict:
        """The request's state; an approved request creates the client and returns its token, once."""
        if not isinstance(secret, str) or not _SECRET.fullmatch(secret):
            raise ClientError("Expected the pairing request secret")
        key = _digest(secret)
        with self._lock:
            self._purge(self._clock())
            item = self._requests.get(key)
            if item is None or item.origin != origin:
                return {"status": "expired"}
            if item.state == "pending":
                return {"status": "pending"}
            if item.state == "denied":
                del self._requests[key]
                return {"status": "denied"}
            client, token = create(conn, item.name, item.kind, item.scopes, item.origin)
            del self._requests[key]
        return {"status": "approved", "token": token, "client": client}

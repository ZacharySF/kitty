"""The phone surface: Siri Shortcuts and widgets on an iPhone reach rika! without an app.

A second HTTP listener, off until the owner turns it on, bound to this machine's Wi-Fi
address or its Tailscale address on port 8771. It serves only::

    POST /phone/v1/capture {text?, url?, title?}   scope capture: a line in the Inbox page
    POST /phone/v1/rika    {text}                  scope rika: one Rika request
    GET  /phone/v1/today                           scope read: next events, open tasks
    GET  /phone/v1/setup/<code>                    the one-time setup page (no token)

and answers 404 to anything else: no page, no /api, no static files. Every answer that
Siri may speak carries ``say``.

Checks, in order: the Host is the bound address; a request that carries ``Origin`` or
``Sec-Fetch-*`` is refused (no web page can drive this surface); the bearer token is a
live phone's; the token has the route's scope; the body is JSON of at most 64 KiB; and
each token gets `RATE_LIMIT` requests a minute, each address `AUTH_FAILURES` wrong tokens
before it waits. Tokens and bodies are never logged.

A phone is added at the desktop (kitty.web_phone): rika! creates its token and shows a
short setup code for five minutes; the phone opens ``/phone/v1/setup/<code>`` in Safari
once, and that page shows the token to copy into the shortcut. On plain Wi-Fi the token
travels as cleartext HTTP, so the default scope is capture only; Tailscale is the
recommended network.
"""
from __future__ import annotations

import hashlib
import html
import hmac
import ipaddress
import json
import re
import secrets
import socket
import sqlite3
import struct
import threading
import time
import unicodedata
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 8771
#: What a phone may be granted: `capture` adds to the Inbox, `rika` asks Rika, `read`
#: shows today's events and open tasks (for a widget).
SCOPES = ("capture", "rika", "read")
NETWORKS = ("off", "wifi", "tailscale")
TOKEN_PREFIX = "kittyphone_"
_TOKEN = re.compile(r"kittyphone_[A-Za-z0-9_-]{43}")
_PHONE_ID = re.compile(r"[0-9a-f]{32}")
MAX_NAME = 80
MAX_BODY = 64 * 1024
MAX_TEXT = 4000
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
CODE_TTL = 300.0
CODE_TRIES = 5
RATE_LIMIT = 30
RATE_WINDOW = 60.0
AUTH_FAILURES = 5
AUTH_BACKOFF = 60.0
#: How long a phone waits for Rika's reply before it is told the reply is in rika!.
RIKA_WAIT = 8.0
USE_RESOLUTION = timedelta(seconds=60)
_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_SIOCGIFADDR = 0x8915


class PhoneError(ValueError):
    def __init__(self, message: str, status: int = 400, *, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _phone(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "scopes": json.loads(row["scopes"]),
            "created_at": row["created_at"], "last_used_at": row["last_used_at"]}


def name_of(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
        raise PhoneError(f"Name the phone in 1–{MAX_NAME} characters")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise PhoneError("Phone names cannot contain control or formatting characters")
    return value.strip()


def scopes_of(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(set(map(str, value))) != len(value) \
            or any(item not in SCOPES for item in value):
        raise PhoneError(f"Grant one or more of: {', '.join(SCOPES)}")
    return [scope for scope in SCOPES if scope in value]


def create(conn: sqlite3.Connection, name: Any, scopes: Any) -> tuple[dict, str]:
    """Record a phone; returns it and its token, which is never stored."""
    name, scopes = name_of(name), scopes_of(scopes)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    phone_id = uuid.uuid4().hex
    conn.execute("INSERT INTO phones(id,name,scopes,token_hash,created_at) VALUES (?,?,?,?,?)",
                 (phone_id, name, json.dumps(scopes), _digest(token), _now()))
    return _phone(conn.execute("SELECT * FROM phones WHERE id=?", (phone_id,)).fetchone()), token


def authenticate(conn: sqlite3.Connection, token: Any) -> dict | None:
    """The live phone holding `token`, or None. Records when it was last used."""
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    digest = _digest(token)
    row = conn.execute("SELECT * FROM phones WHERE token_hash=? AND revoked_at IS NULL", (digest,)).fetchone()
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        return None
    stamp = datetime.now(UTC)
    last = row["last_used_at"]
    if last is None or stamp - datetime.fromisoformat(last) >= USE_RESOLUTION:
        conn.execute("UPDATE phones SET last_used_at=? WHERE id=?", (stamp.isoformat(timespec="seconds"), row["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM phones WHERE id=?", (row["id"],)).fetchone()
    return _phone(row)


def listing(conn: sqlite3.Connection) -> list[dict]:
    return [_phone(row) for row in conn.execute("SELECT * FROM phones WHERE revoked_at IS NULL ORDER BY created_at, id")]


def revoke(conn: sqlite3.Connection, phone_id: Any) -> None:
    if not isinstance(phone_id, str) or not _PHONE_ID.fullmatch(phone_id):
        raise PhoneError("Expected a phone ID")
    if conn.execute("UPDATE phones SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (_now(), phone_id)).rowcount != 1:
        raise PhoneError("Phone not found or already removed", 404)


def network(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT network FROM phone_settings WHERE id=1").fetchone()
    return row["network"] if row else "off"


def set_network(conn: sqlite3.Connection, value: Any) -> str:
    if value not in NETWORKS:
        raise PhoneError(f"Network is one of: {', '.join(NETWORKS)}")
    conn.execute("INSERT INTO phone_settings(id,network) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET network=excluded.network", (value,))
    return value


def _interface_addresses() -> list[str]:
    """Every interface's IPv4 address (Linux ioctl; no dependency). Empty where there is no ioctl."""
    try:
        import fcntl
    except ImportError:
        return []
    found = []
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as probe:
        for _, name in socket.if_nameindex():
            try:
                packed = fcntl.ioctl(probe.fileno(), _SIOCGIFADDR, struct.pack("256s", name[:15].encode()))
            except OSError:
                continue
            found.append(socket.inet_ntoa(packed[20:24]))
    return found


def addresses() -> dict[str, str | None]:
    """This machine's address on the Wi-Fi (the default route's) and on Tailscale, each or None."""
    wifi = None
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as probe:
        try:
            # No packet is sent: connecting a UDP socket only picks the outgoing address.
            probe.connect(("192.0.2.1", 9))
            # Whatever address the default route uses (some networks number themselves outside
            # the private ranges), unless that route is Tailscale itself.
            candidate = ipaddress.ip_address(probe.getsockname()[0])
            if not (candidate.is_loopback or candidate.is_link_local or candidate.is_unspecified or candidate in _TAILNET):
                wifi = str(candidate)
        except OSError:
            pass
    tailscale = next((address for address in _interface_addresses() if ipaddress.ip_address(address) in _TAILNET), None)
    return {"wifi": wifi, "tailscale": tailscale}


class Setup:
    """Short one-time codes that show a new phone its token: five minutes, one use, few tries."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._codes: dict[str, tuple[float, str, str]] = {}
        self._misses = 0

    def issue(self, token: str, name: str) -> dict:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        with self._lock:
            self._prune()
            self._codes[code] = (self._clock() + CODE_TTL, token, name)
            self._misses = 0
        return {"code": code, "expires_in": int(CODE_TTL)}

    def redeem(self, code: str) -> tuple[str, str] | None:
        """The token and phone name for `code`, once; too many wrong codes retire them all."""
        with self._lock:
            self._prune()
            found = self._codes.pop(code.upper(), None)
            if found is None:
                self._misses += 1
                if self._misses >= CODE_TRIES:
                    self._codes.clear()
                return None
            return found[1], found[2]

    def _prune(self) -> None:
        moment = self._clock()
        for code in [code for code, (expires, _, _) in self._codes.items() if expires <= moment]:
            del self._codes[code]


class _Limits:
    """Requests per token and wrong tokens per address, both in memory."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = {}
        self._failures: dict[str, tuple[int, float]] = {}

    def blocked(self, address: str) -> int:
        with self._lock:
            count, until = self._failures.get(address, (0, 0.0))
            left = until - self._clock()
            return int(left) + 1 if count >= AUTH_FAILURES and left > 0 else 0

    def failed(self, address: str) -> None:
        with self._lock:
            count, until = self._failures.get(address, (0, 0.0))
            if until <= self._clock():
                count = 0
            self._failures[address] = (count + 1, self._clock() + AUTH_BACKOFF)

    def allow(self, phone_id: str) -> bool:
        with self._lock:
            calls = self._calls.setdefault(phone_id, deque())
            moment = self._clock()
            while calls and calls[0] <= moment - RATE_WINDOW:
                calls.popleft()
            if len(calls) >= RATE_LIMIT:
                return False
            calls.append(moment)
            return True


_SETUP_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>rika! on this phone</title>
<style>body{{font:17px -apple-system,system-ui,sans-serif;margin:0;padding:28px 22px;background:#000;color:#f5f5f7}}
h1{{font-size:26px;font-weight:600;margin:0 0 18px}}p{{margin:0 0 14px;color:#a1a1a6;line-height:1.45}}
code{{display:block;padding:14px;border:1px solid #2c2c2e;border-radius:12px;color:#f5f5f7;font:15px ui-monospace,monospace;word-break:break-all;user-select:all;-webkit-user-select:all}}
button{{font:inherit;margin:14px 0 22px;padding:12px 18px;border:0;border-radius:999px;background:#f5f5f7;color:#000}}</style>
<h1>{name}</h1>
<p>Address</p><code id="address">{address}</code>
<p style="margin-top:18px">Token (shown once)</p><code id="token">{token}</code>
<button type="button" onclick="navigator.clipboard.writeText(document.getElementById('token').textContent).then(()=>this.textContent='Copied')">Copy token</button>
<p>Paste both into the “Add to Rika” shortcut when it asks. You can remove this phone in rika! under Settings → Connected apps.</p>
</html>"""


class Services:
    """What the listener calls into the running app for: each takes the request's own connection."""

    def __init__(self, *, connect: Callable[[], sqlite3.Connection], capture: Callable[[sqlite3.Connection, dict], str],
                 ask: Callable[[sqlite3.Connection, str], str], today: Callable[[sqlite3.Connection], dict]):
        self.connect, self.capture, self.ask, self.today = connect, capture, ask, today


class PhoneListener:
    """The second listener. `host` is the one address it binds and the only Host it answers."""

    def __init__(self, host: str, services: Services, setup: Setup, *, port: int = PORT):
        self.host, self.services, self.setup = host, services, setup
        self.limits = _Limits()
        listener = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "rika-phone"
            sys_version = ""

            def log_message(self, *_args) -> None:  # tokens and bodies never reach a log
                return

            def do_GET(self) -> None:
                listener.handle(self)

            def do_POST(self) -> None:
                listener.handle(self)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, name="rika-phone", daemon=True)

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)

    # -- one request -------------------------------------------------------------------

    def handle(self, request: BaseHTTPRequestHandler) -> None:
        try:
            status, body, kind = self._answer(request)
        except PhoneError as error:
            headers = {"Retry-After": str(error.retry_after)} if error.retry_after else {}
            if error.status == 401:
                headers["WWW-Authenticate"] = 'Bearer realm="rika-phone"'
            self._send(request, error.status, json.dumps({"error": str(error), "say": str(error)}).encode(), "application/json", headers)
            return
        except Exception:  # noqa: BLE001 - one bad request must not take the listener down
            self._send(request, 500, json.dumps({"error": "rika! could not do that", "say": "rika! could not do that"}).encode(), "application/json", {})
            return
        self._send(request, status, body, kind, {})

    @staticmethod
    def _send(request: BaseHTTPRequestHandler, status: int, body: bytes, kind: str, headers: dict[str, str]) -> None:
        request.send_response(status)
        request.send_header("Content-Type", f"{kind}; charset=utf-8")
        request.send_header("Content-Length", str(len(body)))
        request.send_header("Cache-Control", "no-store")
        request.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers.items():
            request.send_header(name, value)
        request.end_headers()
        request.wfile.write(body)

    def _answer(self, request: BaseHTTPRequestHandler) -> tuple[int, bytes, str]:
        # 1. The Host is the bound address (a rebound DNS name never matches).
        if request.headers.get("Host", "") != self.authority:
            raise PhoneError("Not found", 404)
        # 2. No web page drives this surface.
        if request.headers.get("Origin") is not None or any(name.lower().startswith("sec-fetch-") for name in request.headers):
            if not (request.command == "GET" and request.path.startswith("/phone/v1/setup/")):
                raise PhoneError("Browsers cannot use the phone surface", 403)
        path = request.path
        if request.command == "GET" and path.startswith("/phone/v1/setup/"):
            return self._setup(path.removeprefix("/phone/v1/setup/"))
        route = {("POST", "/phone/v1/capture"): "capture", ("POST", "/phone/v1/rika"): "rika", ("GET", "/phone/v1/today"): "read"}.get((request.command, path))
        if route is None:
            raise PhoneError("Not found", 404)
        # 3. A live phone's token.
        address = request.client_address[0]
        wait = self.limits.blocked(address)
        if wait:
            raise PhoneError("Too many wrong tokens; wait a minute", 429, retry_after=wait)
        values = request.headers.get_all("Authorization", [])
        token = values[0].removeprefix("Bearer ").strip() if len(values) == 1 and values[0].startswith("Bearer ") else None
        with closing(self.services.connect()) as conn:
            phone = authenticate(conn, token)
            if phone is None:
                self.limits.failed(address)
                raise PhoneError("This phone's token is not valid; add the phone again in rika!", 401)
            # 4. Its scope.
            if route not in phone["scopes"]:
                raise PhoneError(f"This phone was not allowed to {'ask Rika' if route == 'rika' else 'read today' if route == 'read' else 'add to the Inbox'}", 403)
            # 6. Its rate.
            if not self.limits.allow(phone["id"]):
                raise PhoneError("Too many requests; wait a minute", 429, retry_after=int(RATE_WINDOW))
            if route == "read":
                return 200, json.dumps(self.services.today(conn)).encode(), "application/json"
            # 5. A small JSON body.
            body = self._body(request)
            if route == "capture":
                return 200, json.dumps({"say": self.services.capture(conn, body)}).encode(), "application/json"
            if set(body) != {"text"} or not isinstance(body["text"], str) or not body["text"].strip() or len(body["text"]) > MAX_TEXT:
                raise PhoneError(f"Send {{text}} of at most {MAX_TEXT} characters")
            return 200, json.dumps({"say": self.services.ask(conn, body["text"].strip())}).encode(), "application/json"

    @staticmethod
    def _body(request: BaseHTTPRequestHandler) -> dict:
        if request.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise PhoneError("Send JSON", 415)
        try:
            length = int(request.headers.get("Content-Length", ""))
        except ValueError:
            raise PhoneError("Send a Content-Length", 411) from None
        if not 0 < length <= MAX_BODY:
            raise PhoneError("Send a JSON body of at most 64 KiB", 413)
        try:
            body = json.loads(request.rfile.read(length))
        except ValueError:
            raise PhoneError("Send valid JSON") from None
        if not isinstance(body, dict):
            raise PhoneError("Send a JSON object")
        return body

    def _setup(self, code: str) -> tuple[int, bytes, str]:
        found = self.setup.redeem(code) if re.fullmatch(rf"[A-Za-z0-9]{{{CODE_LENGTH}}}", code) else None
        if found is None:
            raise PhoneError("That setup code is not valid any more. Add the phone again in rika!.", 404)
        token, name = found
        page = _SETUP_PAGE.format(name=html.escape(name), address=html.escape(f"http://{self.authority}"), token=html.escape(token))
        return 200, page.encode(), "text/html"


def capture_line(body: dict) -> str:
    """The Inbox line for a capture: the text, or a link with its title."""
    if not isinstance(body, dict) or not set(body) <= {"text", "url", "title"}:
        raise PhoneError("Send {text} or {url, title?}")
    text, url, title = body.get("text"), body.get("url"), body.get("title")
    for value in (text, url, title):
        if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT or "\x00" in value):
            raise PhoneError(f"Each field is text of at most {MAX_TEXT} characters")
    if url:
        if not re.fullmatch(r"https?://[^\s]+", url.strip()):
            raise PhoneError("The link must start with http:// or https://")
        label = (title or "").strip()
        line = f"[{label}]({url.strip()})" if label else url.strip()
        return f"{text.strip()} {line}" if text and text.strip() else line
    if not text or not text.strip():
        raise PhoneError("Say or share something to add")
    return text.strip()

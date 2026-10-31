"""Terminal module: kitty!'s herdr session streamed over a same-origin WebSocket.

GET  /api/terminal/activity  ->  {"running", "workspaces": [{id, label, status, focused, agents}]}
    herdr's own agent states (idle/working/blocked/done/unknown) per workspace,
    read with herdr's CLI. Never starts the session: ``running`` is false while
    kitty!'s session is not running.
POST /api/terminal/session  {}  ->  {"token", "socket"}
    Header-protected like every mutation (exact Host/Origin, X-Kitty-Request,
    application/json), so only kitty!'s own page can obtain a token.
GET  /api/terminal/socket?token=<token>[&cols=<n>&rows=<n>]   (WebSocket upgrade)
    Browsers cannot add X-Kitty-Request to an upgrade. The equivalent same-origin
    proof is the exact Host and Origin (checked by the server) plus a token that
    is single-use and expires after TOKEN_TTL seconds. A refused proof never
    reaches herdr; a valid one attaches a new herdr client on a pty.

Binary messages carry terminal bytes both ways. A text message resizes the pty:
``{"type": "resize", "cols": <1-1000>, "rows": <1-1000>}``.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
import select
import struct
import threading
import time

from kitty.core.terminal import CODING_AGENTS, HerdrRuntime, TerminalClient, TerminalNotRunning, TerminalUnavailable

SOCKET_PATH = "/api/terminal/socket"
TOKEN_TTL = 30.0
WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1024 * 1024
DEFAULT_SIZE = (80, 24)


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + WEBSOCKET_GUID).digest()).decode("ascii")


class Terminal:
    """Per-process token store plus kitty!'s herdr runtime."""

    def __init__(self, runtime: HerdrRuntime):
        self.runtime = runtime
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        token = secrets.token_hex(32)
        now = time.monotonic()
        with self._lock:
            self._tokens = {key: expiry for key, expiry in self._tokens.items() if expiry > now}
            self._tokens[token] = now + TOKEN_TTL
        return token

    def consume(self, token: str) -> bool:
        with self._lock:
            expiry = self._tokens.pop(token, None)
        return expiry is not None and expiry > time.monotonic()


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    settings = app.settings
    app.terminal = Terminal(HerdrRuntime(settings.herdr_path, settings.herdr_session,
                                         settings.root / ".private" / "herdr"))


def stop(app) -> None:
    app.terminal.runtime.stop()


def get(app, conn, path, query):
    if path == SOCKET_PATH:
        raise _error(426, "Open the terminal with a WebSocket upgrade")
    if path == "/api/terminal/agents":
        runtime = app.terminal.runtime
        return {"binary": runtime.binary, "session": runtime.session,
                "cwd": str(app.settings.root), "default": "omp", "kinds": list(CODING_AGENTS)}
    if path == "/api/terminal/activity":
        try:
            return app.terminal.runtime.activity()
        except TerminalUnavailable as exc:
            raise _error(503, str(exc)) from None


def post(app, conn, path, body):
    if path == "/api/terminal/agents":
        kind = body.get("kind", "omp")
        if set(body) - {"kind"} or not isinstance(kind, str) or kind not in CODING_AGENTS:
            raise _error(400, "Choose a supported coding agent")
        try:
            return app.terminal.runtime.start_agent(kind, app.settings.root)
        except TerminalNotRunning as exc:
            raise _error(409, str(exc)) from None
        except TerminalUnavailable as exc:
            raise _error(503, str(exc)) from None
    if path != "/api/terminal/session":
        return None
    if body:
        raise _error(400, "A terminal session request expects an empty object")
    return {"token": app.terminal.issue(), "socket": SOCKET_PATH}


def _size(query: dict[str, str]) -> tuple[int, int]:
    size = []
    for name, default in zip(("cols", "rows"), DEFAULT_SIZE):
        value = query.get(name, str(default))
        if not value.isdigit() or not 1 <= int(value) <= 1000:
            raise _error(400, f"{name} must be between 1 and 1000")
        size.append(int(value))
    return size[0], size[1]


def upgrade(app, handler, path, query) -> bool:
    """Authorise, attach and stream one terminal; False when `path` is not ours."""
    if path != SOCKET_PATH:
        return False
    headers = handler.headers
    if set(query) - {"token", "cols", "rows"}:
        raise _error(400, "Unexpected terminal parameters")
    if (headers.get("Upgrade", "").lower() != "websocket"
            or "upgrade" not in [part.strip().lower() for part in headers.get("Connection", "").split(",")]
            or headers.get_all("Sec-WebSocket-Version", []) != ["13"]):
        raise _error(400, "Expected a WebSocket version 13 upgrade")
    key = headers.get_all("Sec-WebSocket-Key", [])
    try:
        if len(key) != 1 or len(base64.b64decode(key[0], validate=True)) != 16:
            raise ValueError
    except (ValueError, binascii.Error):
        raise _error(400, "Invalid Sec-WebSocket-Key") from None
    cols, rows = _size(query)
    if not app.terminal.consume(query.get("token", "")):
        raise _error(403, "Terminal token is missing, expired or already used")
    try:
        client = app.terminal.runtime.attach(cols, rows)
    except TerminalUnavailable as exc:
        raise _error(503, str(exc)) from None
    except OSError:
        raise _error(503, "kitty!'s herdr session could not start") from None
    try:
        # RFC 6455 needs an HTTP/1.1 response; WebKit ignores this server's HTTP/1.0 default.
        handler.protocol_version = "HTTP/1.1"
        handler.send_response(101)
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept_key(key[0]))
        handler.end_headers()
        handler.wfile.flush()
        handler.close_connection = True
        _pump(handler.connection, client, app.streams_closed)
    finally:
        client.close()
    return True


class _Closed(Exception):
    def __init__(self, code: int):
        self.code = code


def _frame(opcode: int, payload: bytes) -> bytes:
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    return header + payload


class _Reader:
    """Incremental RFC 6455 parser for masked client frames."""

    def __init__(self):
        self.buffer = bytearray()
        self.fragments: list[bytes] = []
        self.kind = 0

    def feed(self, data: bytes):
        self.buffer += data
        while True:
            if len(self.buffer) < 2:
                return
            first, second = self.buffer[0], self.buffer[1]
            opcode, length, offset = first & 0x0F, second & 0x7F, 2
            if first & 0x70 or not second & 0x80:
                raise _Closed(1002)
            if length == 126:
                if len(self.buffer) < 4:
                    return
                length, offset = struct.unpack_from("!H", self.buffer, 2)[0], 4
            elif length == 127:
                if len(self.buffer) < 10:
                    return
                length, offset = struct.unpack_from("!Q", self.buffer, 2)[0], 10
            if length > MAX_MESSAGE:
                raise _Closed(1009)
            if len(self.buffer) < offset + 4 + length:
                return
            mask = self.buffer[offset:offset + 4]
            start = offset + 4
            payload = bytes(byte ^ mask[index % 4] for index, byte in
                            enumerate(self.buffer[start:start + length]))
            del self.buffer[:start + length]
            final = bool(first & 0x80)
            if opcode >= 8:
                if not final or length > 125 or opcode not in (8, 9, 10):
                    raise _Closed(1002)
                yield opcode, payload
                continue
            if opcode in (1, 2):
                if self.fragments:
                    raise _Closed(1002)
                self.kind = opcode
            elif opcode != 0 or not self.fragments and not self.kind:
                raise _Closed(1002)
            self.fragments.append(payload)
            if sum(map(len, self.fragments)) > MAX_MESSAGE:
                raise _Closed(1009)
            if final:
                message, self.fragments, kind = b"".join(self.fragments), [], self.kind
                self.kind = 0
                yield kind, message


def _resize(client: TerminalClient, payload: bytes) -> None:
    try:
        message = json.loads(payload)
    except ValueError:
        raise _Closed(1007) from None
    cols, rows = (message.get("cols"), message.get("rows")) if isinstance(message, dict) else (None, None)
    if (not isinstance(message, dict) or set(message) != {"type", "cols", "rows"}
            or message["type"] != "resize"
            or not all(type(value) is int and 1 <= value <= 1000 for value in (cols, rows))):
        raise _Closed(1008)
    client.resize(cols, rows)


def _pump(connection, client: TerminalClient, closing: threading.Event) -> None:
    """Relay until the page closes, the client exits, or the server shuts down."""
    connection.settimeout(None)
    reader = _Reader()
    code = 1000
    try:
        while not closing.is_set():
            ready, _, _ = select.select([connection, client.master], [], [], 0.25)
            if client.master in ready:
                try:
                    data = os.read(client.master, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                connection.sendall(_frame(2, data))
            if connection in ready:
                data = connection.recv(65536)
                if not data:
                    return
                for opcode, payload in reader.feed(data):
                    if opcode == 2:
                        view = memoryview(payload)
                        while view:
                            view = view[os.write(client.master, view):]
                    elif opcode == 1:
                        _resize(client, payload)
                    elif opcode == 9:
                        connection.sendall(_frame(10, payload))
                    elif opcode == 8:
                        code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1000
                        raise _Closed(code)
        else:
            code = 1001
    except _Closed as closed:
        code = closed.code
    except OSError:
        return
    try:
        connection.sendall(_frame(8, struct.pack("!H", code)))
    except OSError:
        pass

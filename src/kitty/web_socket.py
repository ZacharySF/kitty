"""Minimal RFC 6455 server side shared by rika!'s same-origin WebSocket streams.

The upgrade itself is guarded by ``RequestHandler._upgrade`` (exact Host and Origin);
each stream additionally demands a single-use token from a header-protected API call,
because browsers cannot add X-Kitty-Request to an upgrade (``Tokens``).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import struct
import threading
import time

WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1024 * 1024
TOKEN_TTL = 30.0


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + WEBSOCKET_GUID).digest()).decode("ascii")


class Tokens:
    """Single-use tokens that expire after TOKEN_TTL seconds."""

    def __init__(self):
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


def handshake_key(headers) -> str:
    """The client's Sec-WebSocket-Key after checking this is a version 13 upgrade."""
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
    return key[0]


def accept(handler, key: str) -> None:
    """Send the 101 response; the handler's connection is then a WebSocket."""
    # RFC 6455 needs an HTTP/1.1 response; WebKit ignores this server's HTTP/1.0 default.
    handler.protocol_version = "HTTP/1.1"
    handler.send_response(101)
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept_key(key))
    handler.end_headers()
    handler.wfile.flush()
    handler.close_connection = True


class Closed(Exception):
    def __init__(self, code: int):
        self.code = code


def frame(opcode: int, payload: bytes) -> bytes:
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    return header + payload


class Reader:
    """Incremental RFC 6455 parser for masked client frames."""

    def __init__(self, limit: int = MAX_MESSAGE):
        self.buffer = bytearray()
        self.fragments: list[bytes] = []
        self.kind = 0
        self.limit = limit

    def feed(self, data: bytes):
        self.buffer += data
        while True:
            if len(self.buffer) < 2:
                return
            first, second = self.buffer[0], self.buffer[1]
            opcode, length, offset = first & 0x0F, second & 0x7F, 2
            if first & 0x70 or not second & 0x80:
                raise Closed(1002)
            if length == 126:
                if len(self.buffer) < 4:
                    return
                length, offset = struct.unpack_from("!H", self.buffer, 2)[0], 4
            elif length == 127:
                if len(self.buffer) < 10:
                    return
                length, offset = struct.unpack_from("!Q", self.buffer, 2)[0], 10
            if length > self.limit:
                raise Closed(1009)
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
                    raise Closed(1002)
                yield opcode, payload
                continue
            if opcode in (1, 2):
                if self.fragments:
                    raise Closed(1002)
                self.kind = opcode
            elif opcode != 0 or not self.fragments and not self.kind:
                raise Closed(1002)
            self.fragments.append(payload)
            if sum(map(len, self.fragments)) > self.limit:
                raise Closed(1009)
            if final:
                message, self.fragments, kind = b"".join(self.fragments), [], self.kind
                self.kind = 0
                yield kind, message


def close_code(payload: bytes) -> int:
    return struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1000


def send_close(connection, code: int) -> None:
    try:
        connection.sendall(frame(8, struct.pack("!H", code)))
    except OSError:
        pass

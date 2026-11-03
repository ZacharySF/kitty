"""The RFC 6455 subset rika!'s same-origin upgrades use (the terminal and Rika streams).

The server checks the exact Host and Origin of every upgrade before an extension
sees it (``RequestHandler._upgrade``); this module validates the handshake, answers
it and frames messages. Client frames must be masked; messages are bounded.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import struct

WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1024 * 1024


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + WEBSOCKET_GUID).digest()).decode("ascii")


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def check_upgrade(headers) -> str:
    """The client's Sec-WebSocket-Key once the request is a valid version 13 upgrade."""
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
    """Answer the handshake; the connection then belongs to the stream."""
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
                raise Closed(1002)
            if length == 126:
                if len(self.buffer) < 4:
                    return
                length, offset = struct.unpack_from("!H", self.buffer, 2)[0], 4
            elif length == 127:
                if len(self.buffer) < 10:
                    return
                length, offset = struct.unpack_from("!Q", self.buffer, 2)[0], 10
            if length > MAX_MESSAGE:
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
            if sum(map(len, self.fragments)) > MAX_MESSAGE:
                raise Closed(1009)
            if final:
                message, self.fragments, kind = b"".join(self.fragments), [], self.kind
                self.kind = 0
                yield kind, message

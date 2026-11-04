"""kitty sync channel over socketpair(): IK sync, SPAKE2 + XXpsk3 pairing, framing, refusals."""
from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from kitty.core.sync import channel as channel_module
from kitty.core.sync.channel import (
    MAX_CHUNK,
    MAX_MESSAGE,
    PAIR_PREAMBLE,
    SYNC_PREAMBLE,
    WRONG_CODE,
    Channel,
    ChannelError,
    connect,
    pair_connect,
    serve,
)
from kitty.core.sync.noise import HandshakeState, generate_keypair

TIMEOUT = 10.0


class _Side(threading.Thread):
    """Runs one party; keeps its return value or the exception it raised."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self.fn = fn
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            self.error = exc


def _together(*fns: Callable[[], Any]) -> list[_Side]:
    sides = [_Side(fn) for fn in fns]
    for side in sides:
        side.start()
    for side in sides:
        side.join(TIMEOUT * 2)
        assert not side.is_alive(), "a side hung"
    return sides


@pytest.fixture
def pair() -> Iterator[tuple[socket.socket, socket.socket]]:
    client, server = socket.socketpair()
    client.settimeout(TIMEOUT)
    server.settimeout(TIMEOUT)
    yield client, server
    client.close()
    server.close()


def _ok(side: _Side) -> Any:
    if side.error is not None:
        raise side.error
    return side.result


def _reason(side: _Side) -> str:
    assert isinstance(side.error, ChannelError), side.error
    return side.error.reason


def _sync(pair: tuple[socket.socket, socket.socket]) -> tuple[Channel, Channel, bytes, bytes]:
    client_sock, server_sock = pair
    client_private, client_public = generate_keypair()
    server_private, server_public = generate_keypair()
    client, server = _together(
        lambda: connect(client_sock, private_key=client_private, peer_public_key=server_public),
        lambda: serve(server_sock, private_key=server_private,
                      is_known=lambda key: key == client_public, pairing_code=lambda: None),
    )
    mode, server_channel = _ok(server)
    assert mode == "sync"
    return _ok(client), server_channel, client_public, server_public


def _pairing(pair: tuple[socket.socket, socket.socket], client_code: str,
             server_code: str | None) -> tuple[_Side, _Side, bytes, bytes]:
    client_sock, server_sock = pair
    client_private, client_public = generate_keypair()
    server_private, server_public = generate_keypair()
    client, server = _together(
        lambda: pair_connect(client_sock, code=client_code, private_key=client_private),
        lambda: serve(server_sock, private_key=server_private, is_known=lambda key: False,
                      pairing_code=lambda: server_code),
    )
    return client, server, client_public, server_public


@pytest.mark.parametrize("size", [0, 1, MAX_CHUNK, MAX_CHUNK + 1, 5 * 1024 * 1024 + 3])
def test_sync_round_trip(pair: tuple[socket.socket, socket.socket], size: int) -> None:
    client, server, client_public, server_public = _sync(pair)
    assert client.remote_static == server_public
    assert server.remote_static == client_public
    assert client.handshake_hash == server.handshake_hash
    message = bytes(i % 251 for i in range(size))
    sender, receiver = _together(lambda: client.send(message), server.recv)
    _ok(sender)
    assert _ok(receiver) == message
    sender, receiver = _together(lambda: server.send(message[::-1]), client.recv)
    _ok(sender)
    assert _ok(receiver) == message[::-1]


def test_unknown_device_refused_before_any_reply(pair: tuple[socket.socket, socket.socket]) -> None:
    client_sock, server_sock = pair
    client_private, client_public = generate_keypair()
    server_private, server_public = generate_keypair()
    seen: list[bytes] = []

    def is_known(key: bytes) -> bool:
        seen.append(key)
        return False

    def raw_initiator() -> bytes:
        handshake = HandshakeState("IK", True, prologue=b"kitty-sync/1 IK", s=client_private,
                                   rs=server_public)
        first = handshake.write_message()
        client_sock.sendall(SYNC_PREAMBLE + len(first).to_bytes(2, "big") + first)
        return client_sock.recv(1)

    client, server = _together(
        raw_initiator,
        lambda: serve(server_sock, private_key=server_private, is_known=is_known,
                      pairing_code=lambda: "1234"),
    )
    assert _reason(server) == "unknown device"
    assert seen == [client_public]
    assert _ok(client) == b""  # EOF: not a single byte came back


def test_unknown_device_seen_by_connect_as_closed(pair: tuple[socket.socket, socket.socket]) -> None:
    client_sock, server_sock = pair
    client_private, _ = generate_keypair()
    server_private, server_public = generate_keypair()
    client, server = _together(
        lambda: connect(client_sock, private_key=client_private, peer_public_key=server_public),
        lambda: serve(server_sock, private_key=server_private, is_known=lambda key: False,
                      pairing_code=lambda: None),
    )
    assert _reason(server) == "unknown device"
    assert _reason(client) == "connection closed"


def test_wrong_server_key_fails_handshake(pair: tuple[socket.socket, socket.socket]) -> None:
    client_sock, server_sock = pair
    client_private, _ = generate_keypair()
    server_private, _ = generate_keypair()
    _, someone_else = generate_keypair()
    client, server = _together(
        lambda: connect(client_sock, private_key=client_private, peer_public_key=someone_else),
        lambda: serve(server_sock, private_key=server_private, is_known=lambda key: True,
                      pairing_code=lambda: None),
    )
    assert _reason(server) == "decryption failed"
    assert _reason(client) == "connection closed"


def test_tampered_frame_refused(pair: tuple[socket.socket, socket.socket]) -> None:
    client, server, _, _ = _sync(pair)
    frame = bytearray(client._send_cipher.encrypt_with_ad(b"", b"\x01hello"))
    frame[3] ^= 0x40
    pair[0].sendall(len(frame).to_bytes(2, "big") + bytes(frame))
    with pytest.raises(ChannelError) as caught:
        server.recv()
    assert caught.value.reason == "decryption failed"
    assert pair[1].fileno() == -1  # the failed channel is closed


def test_replayed_frame_refused(pair: tuple[socket.socket, socket.socket]) -> None:
    client, server, _, _ = _sync(pair)
    frame = client._send_cipher.encrypt_with_ad(b"", b"\x01once")
    wire = len(frame).to_bytes(2, "big") + frame
    pair[0].sendall(wire + wire)
    assert server.recv() == b"once"
    with pytest.raises(ChannelError) as caught:
        server.recv()
    assert caught.value.reason == "decryption failed"


def test_truncated_stream_is_connection_closed(pair: tuple[socket.socket, socket.socket]) -> None:
    client, server, _, _ = _sync(pair)
    frame = client._send_cipher.encrypt_with_ad(b"", b"\x01" + b"x" * 100)
    pair[0].sendall((len(frame)).to_bytes(2, "big") + frame[:20])
    pair[0].shutdown(socket.SHUT_WR)
    with pytest.raises(ChannelError) as caught:
        server.recv()
    assert caught.value.reason == "connection closed"


def test_truncated_handshake_is_connection_closed(pair: tuple[socket.socket, socket.socket]) -> None:
    client_sock, server_sock = pair
    server_private, _ = generate_keypair()
    client_sock.sendall(SYNC_PREAMBLE + b"\x00\x60" + b"\x00" * 10)
    client_sock.shutdown(socket.SHUT_WR)
    with pytest.raises(ChannelError) as caught:
        serve(server_sock, private_key=server_private, is_known=lambda key: True,
              pairing_code=lambda: None)
    assert caught.value.reason == "connection closed"


def test_pairing_with_right_code(pair: tuple[socket.socket, socket.socket]) -> None:
    client, server, client_public, server_public = _pairing(pair, "k7q2-x9 mp", "K7Q2X9MP")
    client_channel: Channel = _ok(client)
    mode, server_channel = _ok(server)
    assert mode == "pair"
    assert client_channel.remote_static == server_public
    assert server_channel.remote_static == client_public
    assert client_channel.handshake_hash == server_channel.handshake_hash
    sender, receiver = _together(lambda: client_channel.send(b"hello"), server_channel.recv)
    _ok(sender)
    assert _ok(receiver) == b"hello"
    sender, receiver = _together(lambda: server_channel.send(b"hi"), client_channel.recv)
    _ok(sender)
    assert _ok(receiver) == b"hi"


def test_pairing_with_wrong_code_fails_on_both_sides(pair: tuple[socket.socket, socket.socket]) -> None:
    client, server, _, _ = _pairing(pair, "K7Q2-X9MP", "K7Q2-X9MQ")
    assert _reason(client) == WRONG_CODE
    assert _reason(server) == WRONG_CODE


def test_serve_refuses_pairing_when_not_pairing(pair: tuple[socket.socket, socket.socket]) -> None:
    client, server, _, _ = _pairing(pair, "K7Q2-X9MP", None)
    assert _reason(server) == "not pairing"
    assert _reason(client) == WRONG_CODE


@pytest.mark.parametrize("garbage", [
    b"GET / HTTP/1.1\r\n",
    b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03" + b"\x00" * 40,
    b"KITTY-SYNC/2 IK\n",
    b"KITTY-SYNC/1 ik\n",
])
def test_preamble_garbage_refused(pair: tuple[socket.socket, socket.socket], garbage: bytes) -> None:
    client_sock, server_sock = pair
    server_private, _ = generate_keypair()
    client_sock.sendall(garbage)
    with pytest.raises(ChannelError) as caught:
        serve(server_sock, private_key=server_private, is_known=lambda key: True,
              pairing_code=lambda: "1234")
    assert caught.value.reason == "protocol error"
    assert server_sock.fileno() == -1


def test_pair_preamble_with_garbage_spake_message_refused(
    pair: tuple[socket.socket, socket.socket],
) -> None:
    client_sock, server_sock = pair
    server_private, _ = generate_keypair()
    client_sock.sendall(PAIR_PREAMBLE + b"\x00\x05hello")
    with pytest.raises(ChannelError) as caught:
        serve(server_sock, private_key=server_private, is_known=lambda key: True,
              pairing_code=lambda: "1234")
    assert caught.value.reason == "protocol error"


def test_oversize_message_refused_on_send(pair: tuple[socket.socket, socket.socket],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    assert MAX_MESSAGE == 64 * 1024 * 1024
    client, server, _, _ = _sync(pair)
    monkeypatch.setattr(channel_module, "MAX_MESSAGE", 1000)
    with pytest.raises(ChannelError) as caught:
        client.send(b"x" * 1001)
    assert caught.value.reason == "message too large"
    sender, receiver = _together(lambda: client.send(b"still open"), server.recv)
    _ok(sender)
    assert _ok(receiver) == b"still open"


def test_oversize_message_refused_on_recv(pair: tuple[socket.socket, socket.socket],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    client, server, _, _ = _sync(pair)
    monkeypatch.setattr(channel_module, "MAX_MESSAGE", 1000)
    client._send_chunk(False, b"a" * 600)
    client._send_chunk(False, b"b" * 600)
    with pytest.raises(ChannelError) as caught:
        server.recv()
    assert caught.value.reason == "message too large"

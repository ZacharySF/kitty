"""Encrypted, mutually authenticated byte channel between two kitty servers (Q16).

This layer turns a connected blocking socket into a :class:`Channel` that carries
whole application messages. It knows device keys, never device ids or names: the
sync protocol exchanges identity over the finished channel.

Two ways in, chosen by a cleartext preamble line the initiator sends first:

``KITTY-SYNC/1 IK\\n`` (sync between paired devices)
    ``Noise_IK_25519_ChaChaPoly_SHA256`` with prologue ``kitty-sync/1 IK``. The
    initiator knows the responder's static key from pairing. The responder learns
    the initiator's static key from message 1 and checks it against its paired
    devices *before* answering, so an unknown device gets no bytes at all.

``KITTY-SYNC/1 PAIR\\n`` (first contact, protected by a short pairing code)
    A short human code cannot be a Noise psk directly: anyone who records the
    handshake could brute-force it offline. SPAKE2 (symmetric mode, identity
    ``kitty-sync/1 pairing``) turns the code into a strong shared key and allows
    one online guess per attempt. Its two 33-byte messages travel as cleartext
    frames (initiator first), the psk is HKDF-SHA256(SPAKE2 key, info
    ``kitty-sync/1 pairing psk``), and ``Noise_XXpsk3`` then exchanges and
    authenticates both long-term keys. The prologue
    ``kitty-sync/1 PAIR || initiator SPAKE2 msg || responder SPAKE2 msg`` binds the
    SPAKE2 transcript into the handshake hash. With a wrong code only the
    responder can tell (the psk enters in message 3), so the responder sends one
    confirmation message before :func:`pair_connect` returns; an initiator that
    sees the connection close instead reports "wrong code or expired code".

Wire format after the preamble: frames of a 2-byte big-endian length then that
many bytes (at most 65535, the Noise message limit). Transport frames decrypt to
one flag byte (``0x00`` more chunks follow, ``0x01`` final chunk) and up to
65518 bytes of the application message, so a message of any size up to
:data:`MAX_MESSAGE` is a run of chunks; an empty message is one empty final chunk.

All failures raise :class:`ChannelError` with a short reason. Handshake functions
close the socket when they fail, so the peer sees the refusal as a closed
connection. :meth:`Channel.send` and :meth:`Channel.recv` close the channel on any
error except refusing an oversize message before sending it. Timeouts are the
caller's: set them on the socket. One thread uses a channel at a time.
"""
from __future__ import annotations

import socket
import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_Symmetric, SPAKEError
from spake2.ed25519_basic import NotOnCurve

from kitty.core.sync.noise import (
    TAGLEN,
    CipherState,
    DecryptError,
    HandshakeState,
    NoiseError,
)

PROTOCOL = b"kitty-sync/1"
MAX_MESSAGE = 64 * 1024 * 1024
SYNC_PREAMBLE = b"KITTY-SYNC/1 IK\n"
PAIR_PREAMBLE = b"KITTY-SYNC/1 PAIR\n"
MAX_FRAME = 65535
#: Application bytes per transport frame: the frame minus the AEAD tag and flag byte.
MAX_CHUNK = MAX_FRAME - TAGLEN - 1
PAIRING_ID = b"kitty-sync/1 pairing"
PSK_INFO = b"kitty-sync/1 pairing psk"
PAIR_CONFIRM = b"kitty-sync/1 paired"
WRONG_CODE = "wrong code or expired code"
_IK_PROLOGUE = PROTOCOL + b" IK"
_PAIR_PROLOGUE = PROTOCOL + b" PAIR"
_SPAKE2_MESSAGE_LEN = 33
_MORE = b"\x00"
_FINAL = b"\x01"
_LENGTH = struct.Struct(">H")


class ChannelError(Exception):
    """A channel could not be established or used; ``reason`` is short human text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Channel:
    """An established channel: whole messages in, whole messages out, encrypted."""

    def __init__(
        self,
        sock: socket.socket,
        send_cipher: CipherState,
        recv_cipher: CipherState,
        *,
        remote_static: bytes,
        handshake_hash: bytes,
    ) -> None:
        self._sock = sock
        self._send_cipher = send_cipher
        self._recv_cipher = recv_cipher
        self.remote_static = remote_static
        self.handshake_hash = handshake_hash

    def send(self, data: bytes) -> None:
        if len(data) > MAX_MESSAGE:
            raise ChannelError("message too large")
        view = memoryview(data)
        with _closing_on_error(self._sock):
            offset = 0
            while True:
                end = offset + MAX_CHUNK
                final = end >= len(data)
                self._send_chunk(final, view[offset:end])
                if final:
                    return
                offset = end

    def recv(self) -> bytes:
        """Return the next whole application message."""
        parts: list[bytes] = []
        total = 0
        with _closing_on_error(self._sock):
            while True:
                frame = _recv_frame(self._sock)
                with _noise_errors():
                    plaintext = self._recv_cipher.decrypt_with_ad(b"", frame)
                flag, chunk = plaintext[:1], plaintext[1:]
                if flag not in (_MORE, _FINAL):
                    raise ChannelError("protocol error")
                total += len(chunk)
                if total > MAX_MESSAGE:
                    raise ChannelError("message too large")
                parts.append(chunk)
                if flag == _FINAL:
                    return b"".join(parts)

    def close(self) -> None:
        self._sock.close()

    def _send_chunk(self, final: bool, chunk: bytes | memoryview) -> None:
        with _noise_errors():
            frame = self._send_cipher.encrypt_with_ad(b"", (_FINAL if final else _MORE) + chunk)
        _send_all(self._sock, _frame(frame))


def connect(sock: socket.socket, *, private_key: bytes, peer_public_key: bytes) -> Channel:
    """Open a sync channel to a paired device whose static key is ``peer_public_key``."""
    with _closing_on_error(sock):
        handshake = HandshakeState("IK", True, prologue=_IK_PROLOGUE, s=private_key,
                                   rs=peer_public_key)
        with _noise_errors():
            first = handshake.write_message()
        _send_all(sock, SYNC_PREAMBLE + _frame(first))
        reply = _recv_frame(sock)
        with _noise_errors():
            handshake.read_message(reply)
        return _established(sock, handshake)


def pair_connect(sock: socket.socket, *, code: str, private_key: bytes) -> Channel:
    """Pair with a device showing ``code``; the channel's ``remote_static`` is its key."""
    with _closing_on_error(sock):
        try:
            spake = SPAKE2_Symmetric(_code_bytes(code), idSymmetric=PAIRING_ID)
            mine = spake.start()
            _send_all(sock, PAIR_PREAMBLE + _frame(mine))
            theirs = _recv_frame(sock)
            handshake = HandshakeState(
                "XXpsk3", True, prologue=_PAIR_PROLOGUE + mine + theirs, s=private_key,
                psks=[_pairing_psk(spake, theirs)],
            )
            with _noise_errors():
                first = handshake.write_message()
            _send_frame(sock, first)
            reply = _recv_frame(sock)
            with _noise_errors():
                handshake.read_message(reply)
                third = handshake.write_message()
            _send_frame(sock, third)
            channel = _established(sock, handshake)
            if channel.recv() != PAIR_CONFIRM:
                raise ChannelError("protocol error")
            return channel
        except ChannelError as exc:
            if exc.reason == "connection closed":
                raise ChannelError(WRONG_CODE) from exc
            raise


def serve(
    sock: socket.socket,
    *,
    private_key: bytes,
    is_known: Callable[[bytes], bool],
    pairing_code: Callable[[], str | None],
) -> tuple[str, Channel]:
    """Answer one incoming connection: ``("sync", channel)`` or ``("pair", channel)``.

    ``is_known`` decides whether an IK initiator's static key belongs to a paired
    device; ``pairing_code`` returns the code currently offered, or None when this
    server is not pairing.
    """
    with _closing_on_error(sock):
        mode = _read_preamble(sock)
        if mode == "sync":
            return mode, _serve_sync(sock, private_key, is_known)
        return mode, _serve_pair(sock, private_key, pairing_code)


def _serve_sync(sock: socket.socket, private_key: bytes,
                is_known: Callable[[bytes], bool]) -> Channel:
    handshake = HandshakeState("IK", False, prologue=_IK_PROLOGUE, s=private_key)
    first = _recv_frame(sock)
    with _noise_errors():
        handshake.read_message(first)
    remote = handshake.remote_static
    if remote is None or not is_known(remote):
        raise ChannelError("unknown device")
    with _noise_errors():
        reply = handshake.write_message()
    _send_frame(sock, reply)
    return _established(sock, handshake)


def _serve_pair(sock: socket.socket, private_key: bytes,
                pairing_code: Callable[[], str | None]) -> Channel:
    code = pairing_code()
    if code is None:
        raise ChannelError("not pairing")
    spake = SPAKE2_Symmetric(_code_bytes(code), idSymmetric=PAIRING_ID)
    theirs = _recv_frame(sock)
    mine = spake.start()
    _send_frame(sock, mine)
    handshake = HandshakeState(
        "XXpsk3", False, prologue=_PAIR_PROLOGUE + theirs + mine, s=private_key,
        psks=[_pairing_psk(spake, theirs)],
    )
    first = _recv_frame(sock)
    with _noise_errors():
        handshake.read_message(first)
        reply = handshake.write_message()
    _send_frame(sock, reply)
    third = _recv_frame(sock)
    with _noise_errors(decrypt_reason=WRONG_CODE):
        handshake.read_message(third)
    channel = _established(sock, handshake)
    channel.send(PAIR_CONFIRM)
    return channel


def _code_bytes(code: str) -> bytes:
    """Normalize a pairing code: drop whitespace and dashes, upper-case."""
    normalized = "".join(ch for ch in code if not ch.isspace() and ch != "-").upper()
    if not normalized:
        raise ChannelError(WRONG_CODE)
    return normalized.encode("utf-8")


def _pairing_psk(spake: SPAKE2_Symmetric, theirs: bytes) -> bytes:
    if len(theirs) != _SPAKE2_MESSAGE_LEN:
        raise ChannelError("protocol error")
    try:
        key = spake.finish(theirs)
    except (SPAKEError, ValueError, NotOnCurve) as exc:
        raise ChannelError("protocol error") from exc
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=PSK_INFO).derive(key)


def _established(sock: socket.socket, handshake: HandshakeState) -> Channel:
    remote = handshake.remote_static
    if remote is None:
        raise ChannelError("protocol error")
    send_cipher, recv_cipher = handshake.split()
    return Channel(sock, send_cipher, recv_cipher, remote_static=remote,
                   handshake_hash=handshake.handshake_hash)


def _read_preamble(sock: socket.socket) -> str:
    """Read the preamble line byte by byte, never past the longest valid one."""
    line = b""
    while not line.endswith(b"\n") and len(line) < len(PAIR_PREAMBLE):
        line += _recv_exact(sock, 1)
    if line == SYNC_PREAMBLE:
        return "sync"
    if line == PAIR_PREAMBLE:
        return "pair"
    raise ChannelError("protocol error")


def _frame(payload: bytes) -> bytes:
    if len(payload) > MAX_FRAME:
        raise ChannelError("message too large")
    return _LENGTH.pack(len(payload)) + payload


def _send_frame(sock: socket.socket, payload: bytes) -> None:
    _send_all(sock, _frame(payload))


def _recv_frame(sock: socket.socket) -> bytes:
    (length,) = _LENGTH.unpack(_recv_exact(sock, _LENGTH.size))
    return _recv_exact(sock, length)


def _send_all(sock: socket.socket, data: bytes) -> None:
    try:
        sock.sendall(data)
    except TimeoutError as exc:
        raise ChannelError("timed out") from exc
    except OSError as exc:
        raise ChannelError("connection closed") from exc


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    buffer = bytearray(length)
    view = memoryview(buffer)
    received = 0
    while received < length:
        try:
            count = sock.recv_into(view[received:])
        except TimeoutError as exc:
            raise ChannelError("timed out") from exc
        except OSError as exc:
            raise ChannelError("connection closed") from exc
        if count == 0:
            raise ChannelError("connection closed")
        received += count
    return bytes(buffer)


@contextmanager
def _noise_errors(decrypt_reason: str = "decryption failed") -> Iterator[None]:
    try:
        yield
    except DecryptError as exc:
        raise ChannelError(decrypt_reason) from exc
    except NoiseError as exc:
        raise ChannelError("protocol error") from exc


@contextmanager
def _closing_on_error(sock: socket.socket) -> Iterator[None]:
    try:
        yield
    except BaseException:
        sock.close()
        raise

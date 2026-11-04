"""Noise handshakes for kitty peer sync (platform design Q16): IK and XXpsk3 only.

Two of one person's kitty servers talk over a LAN or a tailnet, neither of which we
trust. They need mutual authentication by long-term device keys, forward secrecy and
encryption, without certificates, a CA or a server in the middle. The Noise Protocol
Framework (revision 34, https://noiseprotocol.org/noise.html) gives exactly that from
a few primitives, and two of its patterns cover both situations sync has:

* ``IK``: the initiator already knows the responder's static key (a paired device),
  so one round trip authenticates both sides and the first message is already
  encrypted to the responder.
* ``XXpsk3``: during pairing neither side knows the other's key yet; both static
  keys are transmitted encrypted, and the pre-shared key (derived from the pairing
  code by SPAKE2 in :mod:`kitty.core.sync.channel`) is mixed in at the end of the
  third message, so only a party that knew the code can finish.

The suite is fixed to ``25519_ChaChaPoly_SHA256``. Everything is built on the
``cryptography`` package (X25519, ChaCha20-Poly1305, SHA-256 and HMAC for the
HKDF of spec §4.3); no other Noise library is used, so this file is the whole
implementation and is checked against the public cacophony/snow test vectors.

The structure follows the spec's objects one to one: :class:`CipherState` (§5.1),
:class:`SymmetricState` (§5.2) and :class:`HandshakeState` (§5.3), with the
``psk`` modifier rules of §9 (in a psk handshake the ``e`` token also calls
``MixKey(e.public_key)``). Keys are raw 32-byte X25519 values. Errors from bad
input on the wire raise :class:`NoiseError` (:class:`DecryptError` when an AEAD tag
does not verify); caller mistakes such as a wrong key length raise ``ValueError``.
A handshake state is unusable after any error.
"""
from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

SUITE = "25519_ChaChaPoly_SHA256"
DHLEN = 32
HASHLEN = 32
KEYLEN = 32
TAGLEN = 16
#: Every Noise message, handshake or transport, is at most this long (spec §3).
MAX_MESSAGE = 65535
#: Nonce 2^64-1 is reserved (spec §5.1); reaching it means the key is exhausted.
_RESERVED_NONCE = 2**64 - 1


class NoiseError(Exception):
    """A handshake or transport message could not be processed."""


class DecryptError(NoiseError):
    """An authentication tag did not verify (tampering, or the wrong key or psk)."""


def generate_keypair() -> tuple[bytes, bytes]:
    """Return a fresh X25519 ``(private, public)`` key pair as raw bytes."""
    key = X25519PrivateKey.generate()
    return key.private_bytes_raw(), key.public_key().public_bytes_raw()


def public_key(private: bytes) -> bytes:
    """Return the raw public key for a raw X25519 private key."""
    return _private_key(private).public_key().public_bytes_raw()


def _private_key(private: bytes) -> X25519PrivateKey:
    if len(private) != DHLEN:
        raise ValueError("an X25519 private key is 32 bytes")
    return X25519PrivateKey.from_private_bytes(private)


def _check_public(public: bytes | None) -> bytes | None:
    if public is not None and len(public) != DHLEN:
        raise ValueError("an X25519 public key is 32 bytes")
    return public


def _dh(private: X25519PrivateKey | None, public: bytes | None) -> bytes:
    if private is None or public is None:
        raise NoiseError("a key this pattern needs is missing")
    try:
        return private.exchange(X25519PublicKey.from_public_bytes(public))
    except ValueError as exc:  # low-order point: all-zero shared secret
        raise NoiseError("invalid public key") from exc


def _hash(data: bytes) -> bytes:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return digest.finalize()


def _hmac(key: bytes, data: bytes) -> bytes:
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(data)
    return mac.finalize()


def _hkdf(chaining_key: bytes, ikm: bytes, outputs: int) -> tuple[bytes, ...]:
    """Noise HKDF (spec §4.3): two or three HASHLEN outputs."""
    temp_key = _hmac(chaining_key, ikm)
    output1 = _hmac(temp_key, b"\x01")
    output2 = _hmac(temp_key, output1 + b"\x02")
    if outputs == 2:
        return output1, output2
    return output1, output2, _hmac(temp_key, output2 + b"\x03")


class CipherState:
    """Spec §5.1: a ChaCha20-Poly1305 key ``k`` (or none) and a 64-bit nonce ``n``.

    Without a key, encryption and decryption pass data through unchanged, as the
    spec requires for the start of a handshake.
    """

    def __init__(self, k: bytes | None = None) -> None:
        self.n = 0
        self._aead: ChaCha20Poly1305 | None = None
        self._k: bytes | None = None
        self.k = k

    @property
    def k(self) -> bytes | None:
        return self._k

    @k.setter
    def k(self, value: bytes | None) -> None:
        if value is not None and len(value) != KEYLEN:
            raise ValueError("a ChaCha20-Poly1305 key is 32 bytes")
        self._k = value
        self._aead = None if value is None else ChaCha20Poly1305(value)

    def has_key(self) -> bool:
        return self._aead is not None

    def encrypt_with_ad(self, ad: bytes, plaintext: bytes) -> bytes:
        if self._aead is None:
            return plaintext
        ciphertext = self._aead.encrypt(self._nonce(), plaintext, ad)
        self.n += 1
        return ciphertext

    def decrypt_with_ad(self, ad: bytes, ciphertext: bytes) -> bytes:
        """Decrypt; on a bad tag raise :class:`DecryptError` and keep ``n`` unchanged."""
        if self._aead is None:
            return ciphertext
        try:
            plaintext = self._aead.decrypt(self._nonce(), ciphertext, ad)
        except InvalidTag as exc:
            raise DecryptError("decryption failed") from exc
        self.n += 1
        return plaintext

    def _nonce(self) -> bytes:
        if self.n >= _RESERVED_NONCE:
            raise NoiseError("nonce exhausted")
        return b"\x00\x00\x00\x00" + self.n.to_bytes(8, "little")


class SymmetricState:
    """Spec §5.2: chaining key ``ck``, handshake hash ``h`` and the current cipher."""

    def __init__(self, protocol_name: bytes) -> None:
        if len(protocol_name) <= HASHLEN:
            self.h = protocol_name.ljust(HASHLEN, b"\x00")
        else:
            self.h = _hash(protocol_name)
        self.ck = self.h
        self.cipher = CipherState()

    def mix_key(self, ikm: bytes) -> None:
        self.ck, temp_k = _hkdf(self.ck, ikm, 2)
        self.cipher = CipherState(temp_k)

    def mix_hash(self, data: bytes) -> None:
        self.h = _hash(self.h + data)

    def mix_key_and_hash(self, ikm: bytes) -> None:
        self.ck, temp_h, temp_k = _hkdf(self.ck, ikm, 3)
        self.mix_hash(temp_h)
        self.cipher = CipherState(temp_k)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        ciphertext = self.cipher.encrypt_with_ad(self.h, plaintext)
        self.mix_hash(ciphertext)
        return ciphertext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        plaintext = self.cipher.decrypt_with_ad(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return plaintext

    def split(self) -> tuple[CipherState, CipherState]:
        """Return (initiator-to-responder, responder-to-initiator) ciphers."""
        temp_k1, temp_k2 = _hkdf(self.ck, b"", 2)
        return CipherState(temp_k1), CipherState(temp_k2)


@dataclass(frozen=True)
class _Pattern:
    #: Tokens of the responder's pre-message (both patterns here have no initiator one).
    responder_pre: tuple[str, ...]
    #: Message token lists; even indexes are written by the initiator.
    messages: tuple[tuple[str, ...], ...]


_PATTERNS: dict[str, _Pattern] = {
    "IK": _Pattern(("s",), (("e", "es", "s", "ss"), ("e", "ee", "se"))),
    "XXpsk3": _Pattern((), (("e",), ("e", "ee", "s", "es"), ("s", "se", "psk"))),
}


class HandshakeState:
    """Spec §5.3 for the ``IK`` and ``XXpsk3`` patterns.

    ``s``/``e`` are raw private keys (``e`` only so test vectors can fix the
    ephemeral; normally it is generated), ``rs``/``re`` raw public keys. Call
    :meth:`write_message` and :meth:`read_message` in pattern order, then
    :meth:`split` once :attr:`complete`.
    """

    def __init__(
        self,
        pattern: str,
        initiator: bool,
        *,
        prologue: bytes = b"",
        s: bytes | None = None,
        e: bytes | None = None,
        rs: bytes | None = None,
        re: bytes | None = None,
        psks: list[bytes] | None = None,
    ) -> None:
        spec = _PATTERNS.get(pattern)
        if spec is None:
            raise ValueError(f"unsupported Noise pattern {pattern!r}")
        if s is None:
            raise ValueError(f"Noise_{pattern} needs a local static key")
        psk_tokens = sum(tokens.count("psk") for tokens in spec.messages)
        self._psks = list(psks or [])
        if len(self._psks) != psk_tokens or any(len(psk) != KEYLEN for psk in self._psks):
            raise ValueError(f"Noise_{pattern} needs {psk_tokens} pre-shared key(s) of 32 bytes")
        self.protocol_name = f"Noise_{pattern}_{SUITE}"
        self.initiator = initiator
        self._messages = spec.messages
        self._psk_mode = psk_tokens > 0
        self._s = _private_key(s)
        self._s_public = self._s.public_key().public_bytes_raw()
        self._e = None if e is None else _private_key(e)
        self._rs = _check_public(rs)
        self._re = _check_public(re)
        self._index = 0
        self._symmetric = SymmetricState(self.protocol_name.encode("ascii"))
        self._symmetric.mix_hash(prologue)
        for token in spec.responder_pre:
            self._mix_pre_message(token, local=not initiator)

    @property
    def complete(self) -> bool:
        return self._index == len(self._messages)

    @property
    def handshake_hash(self) -> bytes:
        return self._symmetric.h

    @property
    def remote_static(self) -> bytes | None:
        return self._rs

    def write_message(self, payload: bytes = b"") -> bytes:
        tokens = self._next_tokens(writing=True)
        message = bytearray()
        for token in tokens:
            if token == "e":
                if self._e is None:
                    self._e = X25519PrivateKey.generate()
                ephemeral = self._e.public_key().public_bytes_raw()
                message += ephemeral
                self._mix_ephemeral(ephemeral)
            elif token == "s":
                message += self._symmetric.encrypt_and_hash(self._s_public)
            elif token == "psk":
                self._symmetric.mix_key_and_hash(self._psks.pop(0))
            else:
                self._symmetric.mix_key(self._dh_token(token))
        message += self._symmetric.encrypt_and_hash(payload)
        if len(message) > MAX_MESSAGE:
            raise NoiseError("handshake message too large")
        self._index += 1
        return bytes(message)

    def read_message(self, message: bytes) -> bytes:
        tokens = self._next_tokens(writing=False)
        if len(message) > MAX_MESSAGE:
            raise NoiseError("handshake message too large")
        offset = 0
        for token in tokens:
            if token == "e":
                self._re = _take(message, offset, DHLEN)
                offset += DHLEN
                self._mix_ephemeral(self._re)
            elif token == "s":
                length = DHLEN + (TAGLEN if self._symmetric.cipher.has_key() else 0)
                self._rs = self._symmetric.decrypt_and_hash(_take(message, offset, length))
                offset += length
            elif token == "psk":
                self._symmetric.mix_key_and_hash(self._psks.pop(0))
            else:
                self._symmetric.mix_key(self._dh_token(token))
        payload = self._symmetric.decrypt_and_hash(message[offset:])
        self._index += 1
        return payload

    def split(self) -> tuple[CipherState, CipherState]:
        """Return ``(send, recv)`` transport ciphers for this party's role."""
        if not self.complete:
            raise NoiseError("handshake not complete")
        initiator_to_responder, responder_to_initiator = self._symmetric.split()
        if self.initiator:
            return initiator_to_responder, responder_to_initiator
        return responder_to_initiator, initiator_to_responder

    def _next_tokens(self, *, writing: bool) -> tuple[str, ...]:
        if self.complete:
            raise NoiseError("handshake already complete")
        initiator_turn = self._index % 2 == 0
        if initiator_turn != (self.initiator == writing):
            raise NoiseError("handshake message out of order")
        return self._messages[self._index]

    def _mix_pre_message(self, token: str, *, local: bool) -> None:
        if token != "s":
            raise ValueError(f"unsupported pre-message token {token!r}")
        key = self._s_public if local else self._rs
        if key is None:
            raise ValueError(f"{self.protocol_name} needs the remote static key")
        self._symmetric.mix_hash(key)

    def _mix_ephemeral(self, public: bytes) -> None:
        self._symmetric.mix_hash(public)
        if self._psk_mode:
            self._symmetric.mix_key(public)

    def _dh_token(self, token: str) -> bytes:
        if token == "ee":
            return _dh(self._e, self._re)
        if token == "ss":
            return _dh(self._s, self._rs)
        if token == "es":
            return _dh(self._e, self._rs) if self.initiator else _dh(self._s, self._re)
        if token == "se":
            return _dh(self._s, self._re) if self.initiator else _dh(self._e, self._rs)
        raise ValueError(f"unknown Noise token {token!r}")


def _take(message: bytes, offset: int, length: int) -> bytes:
    if len(message) - offset < length:
        raise NoiseError("handshake message too short")
    return message[offset:offset + length]

"""Noise IK / XXpsk3 against the public cacophony and snow test vectors, plus failure rules."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kitty.core.sync.noise import (
    CipherState,
    DecryptError,
    HandshakeState,
    NoiseError,
    generate_keypair,
    public_key,
)

VECTORS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "noise_vectors.json").read_text(encoding="utf-8")
)["vectors"]


def _hex(value: str | None) -> bytes | None:
    return None if value is None else bytes.fromhex(value)


def _party(vector: dict[str, Any], prefix: str, initiator: bool) -> HandshakeState:
    pattern = vector["protocol_name"].split("_")[1]
    return HandshakeState(
        pattern,
        initiator,
        prologue=bytes.fromhex(vector[f"{prefix}_prologue"]),
        s=_hex(vector.get(f"{prefix}_static")),
        e=_hex(vector.get(f"{prefix}_ephemeral")),
        rs=_hex(vector.get(f"{prefix}_remote_static")),
        psks=[bytes.fromhex(psk) for psk in vector.get(f"{prefix}_psks", [])],
    )


def test_fixture_holds_only_the_two_patterns_kitty_uses() -> None:
    names = {vector["protocol_name"] for vector in VECTORS}
    assert names == {"Noise_IK_25519_ChaChaPoly_SHA256", "Noise_XXpsk3_25519_ChaChaPoly_SHA256"}


@pytest.mark.parametrize(
    "vector", VECTORS, ids=[f"{v['protocol_name']}@{v['source'].split('/')[4]}" for v in VECTORS]
)
def test_vector_replays_every_message(vector: dict[str, Any]) -> None:
    initiator = _party(vector, "init", True)
    responder = _party(vector, "resp", False)
    assert initiator.protocol_name == vector["protocol_name"]
    ciphers: dict[bool, tuple[CipherState, CipherState]] = {}
    for index, message in enumerate(vector["messages"]):
        payload = bytes.fromhex(message["payload"])
        expected = bytes.fromhex(message["ciphertext"])
        initiator_sends = index % 2 == 0
        sender, receiver = (initiator, responder) if initiator_sends else (responder, initiator)
        if not sender.complete:
            assert sender.write_message(payload) == expected
            assert receiver.read_message(expected) == payload
            continue
        if not ciphers:
            assert initiator.handshake_hash == responder.handshake_hash
            if "handshake_hash" in vector:
                assert initiator.handshake_hash.hex() == vector["handshake_hash"]
            ciphers = {True: initiator.split(), False: responder.split()}
        send, _ = ciphers[initiator_sends]
        _, recv = ciphers[not initiator_sends]
        assert send.encrypt_with_ad(b"", payload) == expected
        assert recv.decrypt_with_ad(b"", expected) == payload
    assert ciphers, "vector had no transport messages"
    assert initiator.remote_static == public_key(bytes.fromhex(vector["resp_static"]))
    assert responder.remote_static == public_key(bytes.fromhex(vector["init_static"]))


def _handshake(pattern: str, *, psk_i: bytes | None = None, psk_r: bytes | None = None,
               rs: bytes | None = None) -> tuple[HandshakeState, HandshakeState, bytes, bytes]:
    init_private, init_public = generate_keypair()
    resp_private, resp_public = generate_keypair()
    initiator = HandshakeState(pattern, True, prologue=b"p", s=init_private,
                               rs=rs if rs is not None else (resp_public if pattern == "IK" else None),
                               psks=None if psk_i is None else [psk_i])
    responder = HandshakeState(pattern, False, prologue=b"p", s=resp_private,
                               psks=None if psk_r is None else [psk_r])
    return initiator, responder, init_public, resp_public


def _run(initiator: HandshakeState, responder: HandshakeState) -> None:
    writer, reader = initiator, responder
    while not (initiator.complete and responder.complete):
        reader.read_message(writer.write_message(b"hello"))
        writer, reader = reader, writer


@pytest.mark.parametrize("pattern", ["IK", "XXpsk3"])
def test_generated_keys_complete_handshake_and_transport(pattern: str) -> None:
    psk = bytes(range(32)) if pattern == "XXpsk3" else None
    initiator, responder, init_public, resp_public = _handshake(pattern, psk_i=psk, psk_r=psk)
    _run(initiator, responder)
    assert initiator.remote_static == resp_public
    assert responder.remote_static == init_public
    assert initiator.handshake_hash == responder.handshake_hash
    i_send, i_recv = initiator.split()
    r_send, r_recv = responder.split()
    assert r_recv.decrypt_with_ad(b"", i_send.encrypt_with_ad(b"", b"ping")) == b"ping"
    assert i_recv.decrypt_with_ad(b"", r_send.encrypt_with_ad(b"", b"pong")) == b"pong"


def test_xxpsk3_wrong_psk_fails_at_third_message() -> None:
    initiator, responder, _, _ = _handshake("XXpsk3", psk_i=b"\x01" * 32, psk_r=b"\x02" * 32)
    responder.read_message(initiator.write_message())
    initiator.read_message(responder.write_message())
    with pytest.raises(DecryptError):
        responder.read_message(initiator.write_message())


def test_ik_wrong_responder_key_fails_first_message() -> None:
    _, other_public = generate_keypair()
    initiator, responder, _, _ = _handshake("IK", rs=other_public)
    with pytest.raises(DecryptError):
        responder.read_message(initiator.write_message())


def test_tampered_handshake_message_fails() -> None:
    initiator, responder, _, _ = _handshake("IK")
    message = bytearray(initiator.write_message(b"payload"))
    message[-1] ^= 1
    with pytest.raises(DecryptError):
        responder.read_message(bytes(message))


def test_truncated_handshake_message_fails() -> None:
    initiator, responder, _, _ = _handshake("IK")
    with pytest.raises(NoiseError):
        responder.read_message(initiator.write_message()[:40])


def test_out_of_order_and_premature_split_refused() -> None:
    initiator, responder, _, _ = _handshake("IK")
    with pytest.raises(NoiseError):
        responder.write_message()
    with pytest.raises(NoiseError):
        initiator.split()


def test_constructor_validates_pattern_keys_and_psks() -> None:
    private, public = generate_keypair()
    with pytest.raises(ValueError):
        HandshakeState("XX", True, s=private)
    with pytest.raises(ValueError):
        HandshakeState("IK", True, s=private)  # IK initiator needs rs
    with pytest.raises(ValueError):
        HandshakeState("IK", False)  # no static key
    with pytest.raises(ValueError):
        HandshakeState("XXpsk3", True, s=private)  # missing psk
    with pytest.raises(ValueError):
        HandshakeState("XXpsk3", True, s=private, psks=[b"short"])
    with pytest.raises(ValueError):
        HandshakeState("IK", True, s=private, rs=public[:31])


def test_cipher_state_nonce_rules() -> None:
    sender, receiver = CipherState(b"\x07" * 32), CipherState(b"\x07" * 32)
    ciphertext = sender.encrypt_with_ad(b"ad", b"data")
    assert sender.n == 1
    with pytest.raises(DecryptError):
        receiver.decrypt_with_ad(b"other ad", ciphertext)
    assert receiver.n == 0  # a failed decrypt does not consume the nonce
    assert receiver.decrypt_with_ad(b"ad", ciphertext) == b"data"
    sender.n = 2**64 - 1
    with pytest.raises(NoiseError):
        sender.encrypt_with_ad(b"", b"data")
    assert CipherState().encrypt_with_ad(b"ad", b"clear") == b"clear"


def test_keypair_helpers() -> None:
    private, public = generate_keypair()
    assert len(private) == len(public) == 32
    assert public_key(private) == public
    with pytest.raises(ValueError):
        public_key(b"\x00" * 31)

"""Pairing requests and scoped client tokens (platform design Q7), without HTTP."""
from __future__ import annotations

import hashlib
import re

import pytest

from kitty.core import clients
from kitty.db import connect, migrate

EXTENSION = "chrome-extension://" + "a" * 32


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "clients.db")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def pairing(clock):
    return clients.Pairing(clock=clock)


def ask(pairing, name="Tern", kind="tern-plugin", scopes=("capture",), origin=None):
    return pairing.request({"name": name, "kind": kind, "scopes": list(scopes)}, origin)


def test_a_request_shows_a_short_code_and_waits_for_the_user(pairing):
    ticket = ask(pairing, scopes=("capture", "read"))
    assert ticket["status"] == "pending" and re.fullmatch(r"[A-Z2-9]{3}-[A-Z2-9]{3}", ticket["code"])
    assert len(ticket["request"]) >= 40 and ticket["expires_in"] == clients.REQUEST_TTL
    [waiting] = pairing.waiting()
    assert waiting["code"] == ticket["code"] and waiting["name"] == "Tern" and waiting["scopes"] == ["capture", "read"]
    # The poll secret is the client's alone; kitty's UI addresses the request by a separate ID.
    assert ticket["request"] not in waiting.values() and waiting["id"] != ticket["request"]


def test_approval_hands_the_token_out_once_and_stores_only_its_hash(pairing, conn):
    ticket = ask(pairing, scopes=("read", "capture"))
    assert pairing.status(conn, ticket["request"], None) == {"status": "pending"}
    pairing.decide(pairing.waiting()[0]["id"], approve=True)
    result = pairing.status(conn, ticket["request"], None)
    assert result["status"] == "approved" and result["client"]["scopes"] == ["capture", "read"]
    token = result["token"]
    assert pairing.status(conn, ticket["request"], None) == {"status": "expired"}
    [row] = conn.execute("SELECT * FROM clients").fetchall()
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(tuple(row))
    client = clients.authenticate(conn, token)
    assert client is not None and client["name"] == "Tern" and client["scopes"] == ["capture", "read"]
    assert clients.authenticate(conn, token + "x") is None
    assert clients.authenticate(conn, "") is None


def test_denial_is_reported_once_and_creates_no_client(pairing, conn):
    ticket = ask(pairing)
    pairing.decide(pairing.waiting()[0]["id"], approve=False)
    assert pairing.status(conn, ticket["request"], None) == {"status": "denied"}
    assert pairing.status(conn, ticket["request"], None) == {"status": "expired"}
    assert pairing.waiting() == []
    assert conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0


def test_requests_expire_even_after_approval(pairing, conn, clock):
    ticket = ask(pairing)
    request_id = pairing.waiting()[0]["id"]
    clock.now += clients.REQUEST_TTL + 1
    assert pairing.waiting() == []
    with pytest.raises(clients.ClientError) as missing:
        pairing.decide(request_id, approve=True)
    assert missing.value.status == 404
    assert pairing.status(conn, ticket["request"], None) == {"status": "expired"}
    late = ask(pairing)
    pairing.decide(pairing.waiting()[0]["id"], approve=True)
    clock.now += clients.REQUEST_TTL + 1
    assert pairing.status(conn, late["request"], None) == {"status": "expired"}
    assert conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0


def test_waiting_requests_and_new_requests_are_rate_limited(pairing, clock):
    for _ in range(clients.MAX_WAITING):
        ask(pairing)
    with pytest.raises(clients.ClientError) as crowded:
        ask(pairing)
    assert crowded.value.status == 429
    made = clients.MAX_WAITING
    while made < clients.RATE_LIMIT:
        pairing.decide(pairing.waiting()[0]["id"], approve=False)
        ask(pairing)
        made += 1
    pairing.decide(pairing.waiting()[0]["id"], approve=False)
    with pytest.raises(clients.ClientError) as hurried:
        ask(pairing)
    assert hurried.value.status == 429
    clock.now += clients.RATE_WINDOW + 1
    assert ask(pairing)["status"] == "pending"


@pytest.mark.parametrize("body", [
    {"name": "", "kind": "tern-plugin", "scopes": ["capture"]},
    {"name": "x" * 81, "kind": "tern-plugin", "scopes": ["capture"]},
    {"name": "bad\nname", "kind": "tern-plugin", "scopes": ["capture"]},
    {"name": "Tern", "kind": "shell", "scopes": ["capture"]},
    {"name": "Tern", "kind": "tern-plugin", "scopes": []},
    {"name": "Tern", "kind": "tern-plugin", "scopes": ["admin"]},
    {"name": "Tern", "kind": "tern-plugin", "scopes": ["read", "read"]},
    {"name": "Tern", "kind": "tern-plugin", "scopes": "capture"},
    {"name": "Tern", "kind": "tern-plugin", "scopes": ["capture"], "token": "chosen"},
])
def test_malformed_requests_are_refused(pairing, body):
    with pytest.raises(clients.ClientError) as refused:
        pairing.request(body, None)
    assert refused.value.status == 400


def test_an_extension_pairs_from_its_own_origin_and_polls_from_it(pairing, conn):
    with pytest.raises(clients.ClientError):
        ask(pairing, kind="browser-extension")
    with pytest.raises(clients.ClientError):
        ask(pairing, kind="tern-plugin", origin=EXTENSION)
    ticket = ask(pairing, kind="browser-extension", origin=EXTENSION)
    assert pairing.waiting()[0]["origin"] == EXTENSION
    pairing.decide(pairing.waiting()[0]["id"], approve=True)
    other = "moz-extension://" + "0" * 8 + "-0000-4000-8000-" + "0" * 12
    assert pairing.status(conn, ticket["request"], other) == {"status": "expired"}
    result = pairing.status(conn, ticket["request"], EXTENSION)
    assert result["status"] == "approved" and result["client"]["origin"] == EXTENSION
    assert clients.allowed_origin(conn, EXTENSION)
    assert not clients.allowed_origin(conn, other)


@pytest.mark.parametrize("origin, extension", [
    ("chrome-extension://" + "a" * 32, True),
    ("moz-extension://3f2504e0-4f89-41d3-9a0c-0305e82c3301", True),
    ("chrome-extension://" + "z" * 32, False),
    ("https://example.com", False),
    ("http://127.0.0.1:8765", False),
    ("null", False),
    ("chrome-extension://" + "a" * 32 + "/", False),
])
def test_only_browser_extension_origins_qualify(origin, extension):
    assert clients.extension_origin(origin) is extension


def test_revoking_ends_the_token_and_hides_the_client(pairing, conn):
    client, token = clients.create(conn, "Tern", "tern-plugin", ["capture"], None)
    assert [item["id"] for item in clients.listing(conn)] == [client["id"]]
    clients.revoke(conn, client["id"])
    assert clients.authenticate(conn, token) is None
    assert clients.listing(conn) == []
    with pytest.raises(clients.ClientError) as missing:
        clients.revoke(conn, client["id"])
    assert missing.value.status == 404


def test_use_is_recorded_without_a_write_on_every_call(conn):
    _client, token = clients.create(conn, "Tern", "tern-plugin", ["read"], None)
    assert clients.listing(conn)[0]["last_used_at"] is None
    clients.authenticate(conn, token)
    first = clients.listing(conn)[0]["last_used_at"]
    assert first is not None
    clients.authenticate(conn, token)
    assert clients.listing(conn)[0]["last_used_at"] == first

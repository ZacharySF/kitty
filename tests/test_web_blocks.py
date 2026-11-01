"""Block transactions over the same-origin WebSocket, acknowledgements and live push."""
from __future__ import annotations

import json
import socket
import uuid

import pytest

from terminal_support import FrameReader, make_root, send_frame, site, websocket

SOCKET = "/api/blocks/socket"


class Client:
    """One window's blocks WebSocket."""

    def __init__(self, running):
        status, body = running.request("/api/blocks/session", {})
        assert status == 200 and body["socket"] == SOCKET, body
        status, _headers, self.connection, rest = websocket(running.port, f"{SOCKET}?token={body['token']}", {})
        assert status == 101
        self.reader = FrameReader(self.connection, rest)
        self.hello = self.receive()
        assert self.hello["type"] == "hello"

    def send(self, message: dict) -> None:
        send_frame(self.connection, 1, json.dumps(message).encode())

    def receive(self, timeout: float = 5) -> dict:
        while True:
            opcode, payload = self.reader.frame(timeout)
            if opcode == 1:
                return json.loads(payload)
            assert opcode in (9, 10), (opcode, payload)

    def expect(self, kind: str, timeout: float = 5) -> dict:
        while True:
            message = self.receive(timeout)
            if message["type"] == kind:
                return message

    def silent(self, timeout: float = .6) -> bool:
        try:
            self.receive(timeout)
        except (TimeoutError, socket.timeout):
            return True
        return False

    def close(self):
        self.connection.close()


def page_ops(page, block, text="first"):
    return [{"op": "insert", "id": page, "type": "page", "parent": None, "text": "Live page", "props": {"revision": 1}},
            {"op": "insert", "id": block, "type": "text", "parent": page, "text": text}]


@pytest.fixture
def running(tmp_path):
    with site(make_root(tmp_path, ("workspace",))) as server:
        yield server


def test_a_transaction_is_acknowledged_and_pushed_to_every_other_window(running):
    first, second = Client(running), Client(running)
    try:
        page, block, txn = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        first.send({"type": "txn", "txn_id": txn, "ops": page_ops(page, block)})
        ack = first.expect("ack")
        assert ack["txn_id"] == txn and set(ack["seq"]) == {page} and ack["revisions"] == {page: 1}
        pushed = second.expect("change")
        assert pushed["seq"] == ack["seq"][page] and pushed["txn_id"] == txn and pushed["page_id"] == page
        assert [op["op"] for op in pushed["ops"]] == ["insert", "insert"]
        assert pushed["root"] == {"type": "page", "number": None}
        echo = first.expect("change")
        assert echo["txn_id"] == txn
    finally:
        first.close()
        second.close()


def test_rejected_transactions_reach_only_their_sender(running):
    first, second = Client(running), Client(running)
    try:
        page, block = str(uuid.uuid4()), str(uuid.uuid4())
        first.send({"type": "txn", "ops": page_ops(page, block)})
        first.expect("ack")
        second.expect("change")
        first.send({"type": "txn", "txn_id": str(uuid.uuid4()), "base": {page: 7}, "ops": [{"op": "text", "id": block, "text": "stale"}]})
        reject = first.expect("reject")
        assert reject["status"] == 409
        first.send({"type": "txn", "ops": [{"op": "move", "id": block, "parent": block}]})
        assert first.expect("reject")["status"] == 400
        first.send({"type": "txn", "ops": "not a list"})
        assert first.expect("reject")["status"] == 400
        assert second.silent()
    finally:
        first.close()
        second.close()


def test_legacy_notes_saves_are_pushed_live(running):
    window = Client(running)
    try:
        status, notebook = running.request("/api/notes/notebooks", {"title": "Live"})
        assert status == 200
        status, page = running.request("/api/notes/pages", {"notebook_id": notebook["id"], "title": "Shared"})
        assert status == 200
        created = window.expect("change")
        assert created["root"] == {"type": "page", "number": page["id"]}
        status, saved = running.request(f"/api/notes/pages/{page['id']}/blocks", {"revision": page["revision"], "blocks": [
            {"id": str(uuid.uuid4()), "parent_id": None, "text": "typed in another window", "ordering": 0, "collapsed": False}]})
        assert status == 200
        change = window.expect("change")
        assert change["root"] == {"type": "page", "number": page["id"]}
        assert any(op.get("text") == "typed in another window" for op in change["ops"])
        status, note = running.request("/api/quick-notes/create", {"body": "pushed", "tint": "sky"})
        assert window.expect("change")["root"] == {"type": "quick_note", "number": note["id"]}
    finally:
        window.close()


def test_a_reconnecting_window_resumes_after_its_last_sequence(running):
    first = Client(running)
    page, block = str(uuid.uuid4()), str(uuid.uuid4())
    first.send({"type": "txn", "ops": page_ops(page, block)})
    seen = first.expect("ack")["seq"][page]
    first.close()
    writer = Client(running)
    writer.send({"type": "txn", "ops": [{"op": "text", "id": block, "text": "while away"}]})
    missed = writer.expect("ack")["seq"][page]
    writer.close()
    returning = Client(running)
    try:
        assert returning.hello["seq"] == missed
        returning.send({"type": "resume", "after": seen})
        change = returning.expect("change")
        assert change["seq"] == missed and change["ops"][0]["text"] == "while away"
    finally:
        returning.close()


def test_the_socket_needs_a_single_use_token_and_the_workspace_module(running, tmp_path):
    status, _headers, connection, _rest = websocket(running.port, SOCKET, {})
    connection.close()
    assert status == 403
    status, body = running.request("/api/blocks/session", {})
    token = body["token"]
    status, _headers, connection, _rest = websocket(running.port, f"{SOCKET}?token={token}", {})
    connection.close()
    assert status == 101
    status, _headers, connection, _rest = websocket(running.port, f"{SOCKET}?token={token}", {})
    connection.close()
    assert status == 403
    status, _headers, connection, _rest = websocket(running.port, f"{SOCKET}?token={token}", {"Origin": "http://evil.invalid"})
    connection.close()
    assert status == 403
    assert running.request("/api/blocks/session", {}, headers={"X-Kitty-Request": None})[0] == 403
    with site(make_root(tmp_path / "terminal-only", ("terminal",))) as other:
        assert other.request("/api/blocks/session", {})[0] == 403

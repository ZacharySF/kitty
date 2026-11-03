"""Rika over HTTP and its WebSocket stream, end to end in the demo (never a network call)."""
from __future__ import annotations

import dataclasses
import json
import time
from contextlib import closing
from urllib.parse import quote

import httpx
import pytest

from kitty.core.rika import store
from kitty.db import connect
from terminal_support import FrameReader, make_root, send_frame, site, websocket


@pytest.fixture
def no_network(monkeypatch):
    calls = []

    def refuse(self, request):
        calls.append(str(request.url))
        raise AssertionError(f"unexpected network request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    return calls


@pytest.fixture
def demo(tmp_path, no_network):
    settings = dataclasses.replace(make_root(tmp_path, ("workspace", "terminal")), demo=True)
    with site(settings) as running:
        yield running
    assert no_network == []


class Stream:
    def __init__(self, running):
        status, _headers, self.connection, rest = _connect(running)
        assert status == 101
        self.reader = FrameReader(self.connection, rest)

    def event(self, timeout=10) -> dict:
        opcode, payload = self.reader.frame(timeout)
        assert opcode == 1, (opcode, payload)
        return json.loads(payload)

    def until(self, done, timeout=10) -> list[dict]:
        seen, deadline = [], time.time() + timeout
        while True:
            event = self.event(max(0.01, deadline - time.time()))
            seen.append(event)
            if done(event):
                return seen

    def reply(self) -> list[dict]:
        return self.until(lambda event: event["type"] == "message" and event["message"]["role"] == "rika"
                          and event["message"]["status"] != "pending")


def _connect(running, headers=None):
    return websocket(running.port, "/api/rika/stream", headers or {})


@pytest.fixture
def stream(demo):
    opened = Stream(demo)
    yield opened
    opened.connection.close()


def kinds(events, kind):
    return [event for event in events if event["type"] == kind]


def test_a_demo_request_streams_tier_action_and_reply_and_undo_reverts_it(demo, stream):
    status, sent = demo.request("/api/rika/messages", {"text": "add a node Photosynthesis"})
    assert status == 200 and set(sent) == {"conversation_id", "message_id"}
    events = stream.reply()
    assert events[0] == {"type": "message", "message": {**events[0]["message"], "id": sent["message_id"], "role": "user"}}
    [tier] = kinds(events, "tier")
    assert (tier["tier"], tier["model"], tier["reply_to"]) == ("fast", "deterministic", sent["message_id"])
    [action] = kinds(events, "action")
    assert action["action"]["summary"] == "Added the node Photosynthesis to Canvas" and action["action"]["reversible"]
    status, history = demo.request("/api/rika/history")
    assert status == 200 and [item["kind"] for item in history["items"]] == ["message", "message", "action"]
    assert history["has_more"] is False
    status, body = demo.request(f"/api/rika/actions/{action['action']['id']}/undo", {})
    assert status == 200 and body["action"]["status"] == "undone"
    assert stream.until(lambda event: event["type"] == "action")[-1]["action"]["status"] == "undone"
    assert demo.request(f"/api/rika/actions/{action['action']['id']}/undo", {})[0] == 409
    assert demo.request("/api/notes")[1]["pages"] == []


def test_open_view_is_a_navigate_event(demo, stream):
    demo.request("/api/rika/messages", {"text": "Open calendar"})
    events = stream.reply()
    assert kinds(events, "navigate") == [{"type": "navigate", "view": "calendar"}]
    assert kinds(events, "action")[0]["action"]["reversible"] is False


def test_starting_an_agent_waits_for_approval_then_joins_the_scripted_roster(demo, stream):
    status, roster = demo.request("/api/rika/agents")
    assert status == 200 and len(roster["agents"]) == 5
    assert {agent["status"] for agent in roster["agents"]} == {"working", "blocked", "idle", "done"}
    assert all(set(agent) == {"id", "kind", "name", "status", "task", "line", "since", "where", "now"} for agent in roster["agents"])
    # Agent skills are off by default: no agent's now state is read.
    assert all(agent["now"] is None for agent in roster["agents"])
    demo.request("/api/rika/messages", {"text": "start omp to tidy the fixtures"})
    approval = stream.until(lambda event: event["type"] == "approval")[-1]["approval"]
    assert approval["tool"] == "start_worker" and "tidy the fixtures" in approval["detail"]
    assert demo.request(f"/api/rika/approvals/{approval['id']}", {"decision": "approve"})[1]["approval"]["status"] == "approved"
    events = stream.reply()
    assert kinds(events, "approval")[0]["approval"]["status"] == "approved"
    assert kinds(events, "action")[0]["action"]["summary"].startswith("Started omp")
    agents = demo.request("/api/rika/agents")[1]["agents"]
    assert len(agents) == 6 and agents[-1]["task"] == "tidy the fixtures"
    status, opened = demo.request(f"/api/rika/agents/{agents[-1]['id']}/open", {})
    assert status == 200 and opened == {"ok": True, "window": None, "view": None}
    assert demo.request("/api/rika/agents/herdr:nope/open", {})[0] == 404
    assert demo.request(f"/api/rika/approvals/{approval['id']}", {"decision": "reject"})[0] == 409


def test_unscripted_text_gets_the_demo_reply_as_tokens(demo, stream):
    demo.request("/api/rika/messages", {"text": "tell me a joke", "tier": "standard"})
    events = stream.reply()
    assert kinds(events, "tier")[0]["tier"] == "standard"
    assert len(kinds(events, "token")) > 5
    assert "".join(event["text"] for event in kinds(events, "token")) == events[-1]["message"]["text"]


def test_requests_are_validated_and_header_protected(demo):
    for body in ({}, {"text": ""}, {"text": "hi", "tier": "huge"}, {"text": "hi", "extra": 1}, {"text": "x" * 20001}):
        assert demo.request("/api/rika/messages", body)[0] == 400
    assert demo.request("/api/rika/messages", {"text": "hi"}, headers={"X-Kitty-Request": None})[0] == 403
    assert demo.request("/api/rika/messages", {"text": "hi", "conversation_id": "missing"})[0] == 404
    assert demo.request("/api/rika/approvals/" + "0" * 32, {"decision": "approve"})[0] == 404
    assert demo.request("/api/rika/history?before=;drop")[0] == 400
    assert demo.request("/api/rika/stream")[0] == 426


def test_the_stream_refuses_foreign_origins_and_closes_on_page_messages(demo):
    status, _headers, connection, _rest = _connect(demo, {"Origin": "http://evil.example"})
    connection.close()
    assert status == 403
    status, _headers, connection, rest = _connect(demo)
    assert status == 101
    send_frame(connection, 1, b"hello")
    assert FrameReader(connection, rest).closed()[:2] == (1003).to_bytes(2, "big")
    connection.close()


def test_history_pages_backwards(demo, stream):
    for index in range(3):
        demo.request("/api/rika/messages", {"text": f"new card Q{index} / A{index}"})
        stream.reply()
    with closing(connect(demo.app.settings.db_path)) as conn:
        page = store.history(conn, limit=4)
        older = store.history(conn, before=page["next_before"], limit=4)
    assert page["has_more"] and len(page["items"]) == 4
    assert older["items"][-1]["created_at"] < page["items"][0]["created_at"]
    everything = demo.request("/api/rika/history")[1]["items"]
    assert [item["kind"] for item in everything] == ["message", "message", "action"] * 3
    status, body = demo.request("/api/rika/history?before=" + quote(page["next_before"]))
    assert status == 200 and body["items"] == everything[:5]

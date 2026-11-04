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
    # Like the page, the upgrade offers this launch's UI secret as a subprotocol (kitty.web_auth).
    return websocket(running.port, "/api/rika/stream", headers or {}, secret=running.secret)


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


# Voice and brain dumps (phase 2a) ----------------------------------------------

from kitty.core.rika import voice as voice_engine  # noqa: E402
from kitty.core.rika.dumps import DEMO_DUMP, DEMO_LINES  # noqa: E402


def _audio(stream, seconds: float, chunk: float = 0.25) -> None:
    for _ in range(int(seconds / chunk)):
        send_frame(stream.connection, 2, b"\x00\x00" * int(voice_engine.SAMPLE_RATE * chunk))


def _control(stream, **message) -> None:
    send_frame(stream.connection, 1, json.dumps(message).encode())


def test_voice_streams_over_the_rika_socket_and_the_spoken_request_routes_fast(demo, stream):
    status, state = demo.request("/api/rika/voice")
    assert status == 200 and state["engine"] == "demo" and state["installed"] and state["speech"] is None
    assert {item["id"] for item in state["models"]} == {"tiny", "base", "small"} and state["model"] == "base"
    _control(stream, type="voice.start", session="hold-1", mode="command")
    _audio(stream, 1.5)
    partial = stream.until(lambda event: event["type"] == "voice.partial" and event["text"])[-1]
    assert partial["session"] == "hold-1" and voice_engine.DEMO_COMMAND.startswith(partial["text"])
    _control(stream, type="voice.stop")
    final = stream.until(lambda event: event["type"] == "voice.final")[-1]
    assert final["text"] == voice_engine.DEMO_COMMAND and final["mode"] == "command" and final["duration"] == 1.5
    assert demo.request("/api/rika/history")[1]["items"] == []  # hearing stores nothing by itself
    status, sent = demo.request("/api/rika/messages", {"text": final["text"], "spoken": True})
    events = stream.reply()
    assert kinds(events, "message")[0]["message"]["source"] == "voice"
    [tier] = kinds(events, "tier")
    assert tier["tier"] == "fast"
    reply = events[-1]["message"]
    assert reply["text"].startswith("Added the task Read chapter 3")
    status, spoken = demo.request(f"/api/rika/speech/{reply['id']}")
    assert status == 200 and spoken == {"text": reply["text"], "audio": None}  # the demo never starts a process


def test_only_replies_to_spoken_requests_are_spoken(demo, stream):
    demo.request("/api/rika/messages", {"text": "add a node Cells"})
    reply = stream.reply()[-1]["message"]
    assert demo.request(f"/api/rika/speech/{reply['id']}")[0] == 409
    assert demo.request("/api/rika/speech/" + "0" * 32)[0] == 404
    assert demo.request("/api/rika/messages", {"text": "hi", "spoken": "yes"})[0] == 400


def test_a_fake_engine_streams_chunks_segments_and_the_final_text_to_that_page_only(demo, stream):
    class Engine:
        hears_audio = False
        def transcribe(self, pcm, *, start, final, memory):
            seconds = len(pcm) // voice_engine.BYTES_PER_SECOND
            return f"{'final' if final else 'chunk'} {seconds}s" if seconds else ""  # like Whisper on no audio

    demo.app.rika.voice.factory = lambda mode: Engine()
    other = Stream(demo)
    try:
        _control(stream, type="voice.start", session="fake", mode="dump")
        _audio(stream, 1.0)
        assert stream.until(lambda event: event["type"] == "voice.partial")[-1]["text"] == "chunk 1s"
        _audio(stream, 1.0)
        _control(stream, type="voice.stop")
        events = stream.until(lambda event: event["type"] == "voice.final")
        # Silent frames may end the phrase at 2 s just before the stop arrives: then the
        # committed chunk is the whole text, and the empty rest adds nothing.
        assert events[-1]["text"] in ("final 2s", "chunk 2s") and events[-1]["mode"] == "dump"
        assert events[-1]["duration"] == 2.0 and events[-1]["segments"][-1]["end"] == 2.0
        assert [event["state"] for event in kinds(events, "voice.state")][-1] == "finishing"
        demo.request("/api/rika/messages", {"text": "add a node Seen"})
        assert all(not event["type"].startswith("voice.") for event in other.reply())
    finally:
        other.connection.close()


def test_the_socket_accepts_only_voice_messages_from_the_page(demo):
    for frame_opcode, payload in ((1, b'{"type": "voice.start", "session": "x", "mode": "sing"}'),
                                  (1, b'{"type": "token"}'), (2, b"\x00\x00\x00")):
        status, _headers, connection, rest = _connect(demo)
        assert status == 101
        send_frame(connection, frame_opcode, payload)
        assert FrameReader(connection, rest).closed()[:2] == (1003).to_bytes(2, "big")
        connection.close()


def test_voice_settings_choose_the_model_and_the_demo_downloads_nothing(demo):
    status, state = demo.request("/api/rika/voice", {"model": "tiny"})
    assert status == 200 and state["model"] == "tiny"
    assert demo.request("/api/rika/voice", {"model": "huge"})[0] == 400
    assert demo.request("/api/rika/voice/model", {})[0] == 409
    assert demo.request("/api/rika/voice", {"model": "base"}, headers={"X-Kitty-Request": None})[0] == 403


def _segments():
    clock, segments = 0.0, []
    for line in DEMO_LINES:
        length = len(line.split()) / voice_engine.DEMO_RATE
        segments.append({"start": round(clock, 2), "end": round(clock + length, 2), "text": line})
        clock += length
    return segments, round(clock, 2)


def _dump(demo, stream):
    segments, duration = _segments()
    status, sent = demo.request("/api/rika/dumps", {"transcript": DEMO_DUMP, "segments": segments, "duration": duration})
    assert status == 200, sent
    events = stream.reply()
    return sent, events


def test_a_brain_dump_in_the_demo_makes_one_plan_grouped_by_area_and_a_dump_note(demo, stream):
    sent, events = _dump(demo, stream)
    dump = kinds(events, "message")[0]["message"]
    assert dump["source"] == "dump" and dump["text"] == DEMO_DUMP
    [tier] = kinds(events, "tier")
    assert (tier["tier"], tier["model"]) == ("standard", "demo")
    [event] = kinds(events, "plan")
    plan = event["plan"]
    assert plan["status"] == "pending" and plan["request_id"] == sent["message_id"]
    assert [item["area"] for item in plan["items"]] == [
        "notes", "tasks", "tasks", "calendar", "calendar", "calendar", "cards", "jobs", "agents"]
    asks = [item for item in plan["items"] if item["question"]]
    assert len(asks) == 1 and asks[0]["question"]["text"] == "Which Tuesday?" and len(asks[0]["question"]["choices"]) == 2
    assert [item["outward"] for item in plan["items"]].count(True) == 1 and plan["items"][-1]["outward"]
    assert all(item["source"] in DEMO_LINES and "calls" not in item for item in plan["items"])
    assert events[-1]["message"]["text"].startswith("One plan from your brain dump: 9 items in notes, tasks, calendar")
    assert plan["note"]["title"].startswith("Brain dump · ")
    with closing(connect(demo.app.settings.db_path)) as conn:
        texts = [row["text"] for row in conn.execute("SELECT text FROM note_outline WHERE page_id=? ORDER BY path",
                                                     (plan["note"]["page_id"],))]
        notebook = conn.execute("SELECT n.title FROM note_page_index p JOIN note_notebooks n ON n.id=p.notebook_id"
                                " WHERE p.id=?", (plan["note"]["page_id"],)).fetchone()["title"]
    assert notebook == "Brain dumps"
    assert any(text.startswith("Transcript · ") for text in texts)
    assert "0:00 " + DEMO_LINES[0] in texts and any(DEMO_LINES[-1] in text for text in texts)
    history = demo.request("/api/rika/history")[1]["items"]
    assert [item["kind"] for item in history] == ["message", "message", "plan"]


def test_approving_ticked_items_runs_them_and_the_dump_note_links_everything_it_made(demo, stream):
    _sent, events = _dump(demo, stream)
    plan = kinds(events, "plan")[0]["plan"]
    ticked = [item["id"] for item in plan["items"] if item["area"] != "agents"]
    group = next(item for item in plan["items"] if item["question"])
    assert demo.request(f"/api/rika/plans/{plan['id']}", {"decision": "approve", "items": []})[0] == 400
    assert demo.request(f"/api/rika/plans/{plan['id']}", {"decision": "approve", "items": ["nope"]})[0] == 400
    status, body = demo.request(f"/api/rika/plans/{plan['id']}",
                                {"decision": "approve", "items": ticked, "choices": {group["id"]: 1}})
    assert status == 200 and body["plan"]["status"] == "running"
    events = stream.reply()
    done = kinds(events, "plan")[-1]["plan"]
    assert done["status"] == "applied"
    results = {item["area"] + ":" + item["title"]: item["result"] for item in done["items"]}
    assert results["agents:omp on the habit tracker"]["status"] == "skipped"
    assert all(result["status"] == "done" for key, result in results.items() if not key.startswith("agents"))
    actions = kinds(events, "action")
    assert len(actions) == 11 and all(event["action"]["reversible"] for event in actions)
    assert len(demo.request("/api/rika/agents")[1]["agents"]) == 5  # the unticked worker never started
    reply = events[-1]["message"]
    assert reply["text"].startswith("Done: 8 items in notes, tasks, calendar, cards and jobs.")
    assert done["note"]["title"] in reply["text"]
    with closing(connect(demo.app.settings.db_path)) as conn:
        texts = [row["text"] for row in conn.execute("SELECT text FROM note_outline WHERE page_id=?",
                                                     (done["note"]["page_id"],))]
        study = conn.execute("SELECT start FROM calendar_events WHERE title='Study group'").fetchone()["start"]
    assert "Created from this dump" in texts
    assert "[[CS 220 · Final project idea]]" in texts
    assert any(text.startswith("[Orgo midterm (") and text.endswith(")](#calendar)") for text in texts)
    assert "[SN1 or SN2: which wins?](#flashcards)" in texts and "[Apply to the Stripe internship](#calendar)" in texts
    assert {"Calendar", "Tasks", "Cards", "Notes", "Jobs"} <= set(texts) and "Agents" not in texts
    tuesday = group["question"]["choices"][1].removeprefix("Next ")
    from datetime import datetime
    assert datetime.fromisoformat(study).strftime("%a %-d %b") == tuesday
    assert demo.request(f"/api/rika/plans/{plan['id']}", {"decision": "dismiss"})[0] == 409


def test_dismissing_a_plan_keeps_the_transcript_and_makes_nothing(demo, stream):
    _sent, events = _dump(demo, stream)
    plan = kinds(events, "plan")[0]["plan"]
    assert demo.request(f"/api/rika/plans/{plan['id']}", {"decision": "dismiss", "items": []})[0] == 400
    status, body = demo.request(f"/api/rika/plans/{plan['id']}", {"decision": "dismiss"})
    assert status == 200 and body["plan"]["status"] == "dismissed"
    assert stream.until(lambda event: event["type"] == "plan")[-1]["plan"]["status"] == "dismissed"
    with closing(connect(demo.app.settings.db_path)) as conn:
        texts = [row["text"] for row in conn.execute("SELECT text FROM note_outline WHERE page_id=?", (plan["note"]["page_id"],))]
        assert conn.execute("SELECT count(*) FROM calendar_events").fetchone()[0] == 0
    assert "Nothing was created from this dump: the plan was dismissed" in texts
    assert any(text.startswith("Transcript · ") for text in texts)


def test_a_typed_dump_in_the_demo_is_planned_phrase_by_phrase(demo, stream):
    text = "Add a task Read chapter 3 tomorrow. New card Mitosis / Cell division. Okay that's all."
    status, _sent = demo.request("/api/rika/dumps", {"transcript": text, "segments": [], "duration": 12})
    assert status == 200
    plan = kinds(stream.reply(), "plan")[0]["plan"]
    assert [(item["area"], item["title"]) for item in plan["items"]] == [
        ("tasks", "Add a task Read chapter 3 tomorrow"), ("cards", "New card Mitosis / Cell division")]
    for body in ({"transcript": "", "segments": [], "duration": 1},
                 {"transcript": "hi", "segments": [{"start": 2, "end": 1, "text": "x"}], "duration": 1},
                 {"transcript": "hi", "segments": [], "duration": -1}, {"transcript": "hi", "segments": []}):
        assert demo.request("/api/rika/dumps", body)[0] == 400


def test_undo_all_reverts_everything_a_plan_made_newest_first(demo, stream):
    _sent, events = _dump(demo, stream)
    plan = kinds(events, "plan")[0]["plan"]
    ticked = [item["id"] for item in plan["items"] if item["area"] != "agents"]
    demo.request(f"/api/rika/plans/{plan['id']}", {"decision": "approve", "items": ticked})
    actions = [event["action"] for event in kinds(stream.reply(), "action")]
    results = [demo.request(f"/api/rika/actions/{action['id']}/undo", {}) for action in reversed(actions)]
    assert [status for status, _body in results] == [200] * len(actions), [body for _status, body in results]
    with closing(connect(demo.app.settings.db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM calendar_events WHERE deleted=0").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM note_page_index WHERE title='CS 220 · Final project idea'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM flash_notes WHERE archived=0").fetchone()[0] == 0

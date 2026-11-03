"""Rika's on-device speech to text: chunked streaming sessions with a fake engine, quiet
detection, the demo's canned speech and the pinned, checked model download."""
from __future__ import annotations

import hashlib
import math
import queue
import struct
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kitty.core.rika import voice
from kitty.core.rika.dumps import DEMO_DUMP

SECOND = voice.BYTES_PER_SECOND


def tone(seconds: float, amplitude: int = 8000) -> bytes:
    count = int(voice.SAMPLE_RATE * seconds)
    return struct.pack(f"<{count}h", *(int(amplitude * math.sin(2 * math.pi * 440 * n / voice.SAMPLE_RATE))
                                      for n in range(count)))


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(voice.SAMPLE_RATE * seconds)


class Fake:
    """Says how much audio it was given, so the test can see each chunk."""

    hears_audio = True

    def __init__(self):
        self.calls: list[tuple[float, float, bool]] = []

    def transcribe(self, pcm: bytes, *, start: float, final: bool, memory: dict) -> str:
        seconds = round(len(pcm) / SECOND, 1)
        self.calls.append((round(start, 1), seconds, final))
        return f"{'final' if final else 'heard'} {seconds}s at {round(start, 1)}s"


def make_voice(tmp_path: Path, *, demo: bool = False, engine=None) -> voice.Voice:
    app = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data", demo=demo, db_path=tmp_path / "x.db"))
    made = voice.Voice(app, lambda event: None)
    if engine is not None:
        made.factory = lambda mode: engine
    return made


def until(events: queue.Queue, kind: str, timeout: float = 10) -> list[dict]:
    seen = []
    while True:
        event = events.get(timeout=timeout)
        seen.append(event)
        if event["type"] == kind:
            return seen


def of(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event["type"] == kind]


def test_audio_streams_in_chunks_a_pause_commits_a_segment_and_stop_sends_the_whole_text(tmp_path):
    engine, events = Fake(), queue.Queue()
    session = make_voice(tmp_path, engine=engine).session("s1", "command", events.put)
    assert [event["state"] for event in until(events, "voice.state")] == ["loading"]
    assert until(events, "voice.state")[-1]["state"] == "listening"
    session.feed(tone(1.2))
    partial = until(events, "voice.partial")[-1]
    assert partial == {"type": "voice.partial", "session": "s1", "mode": "command", "text": "heard 1.2s at 0.0s"}
    session.feed(tone(1.0) + silence(0.8))  # a phrase, then a pause long enough to end it
    segment = until(events, "voice.segment")[-1]
    assert (segment["index"], segment["start"], segment["end"]) == (0, 0.0, 3.0)
    assert segment["text"] == "heard 3.0s at 0.0s"
    session.feed(tone(0.5))
    session.stop()
    seen = until(events, "voice.final")
    assert [event["state"] for event in of(seen, "voice.state")] == ["finishing"]
    final = seen[-1]
    assert final["text"] == "heard 3.0s at 0.0s final 0.5s at 3.0s"
    assert [(item["start"], item["end"]) for item in final["segments"]] == [(0.0, 3.0), (3.0, 3.5)]
    assert final["duration"] == 3.5 and final["mode"] == "command"
    assert engine.calls[-1] == (3.0, 0.5, True)
    session.join(5)
    assert not session.alive


def test_long_talk_is_committed_every_max_window_so_a_dump_transcribes_as_it_is_spoken(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "MAX_WINDOW", 3.0)
    engine, events = Fake(), queue.Queue()
    session = make_voice(tmp_path, engine=engine).session("d", "dump", events.put)
    for _ in range(4):
        session.feed(tone(1.0))
        until(events, "voice.partial")
    assert [segment["end"] for segment in of(list(events.queue), "voice.segment")] in ([], [3.0], [4.0])
    session.stop()
    final = until(events, "voice.final")[-1]
    assert final["duration"] == 4.0 and final["segments"][0]["start"] == 0.0
    assert final["segments"][-1]["end"] == 4.0 and final["mode"] == "dump"


def test_quiet_audio_never_reaches_the_engine(tmp_path):
    engine, events = Fake(), queue.Queue()
    session = make_voice(tmp_path, engine=engine).session("q", "command", events.put)
    session.feed(silence(2.5))
    session.stop()
    final = until(events, "voice.final")[-1]
    assert (final["text"], final["segments"], engine.calls) == ("", [], [])
    assert voice.quiet(silence(1)) and not voice.quiet(silence(1) + tone(0.1))
    assert voice.trailing_quiet(tone(1) + silence(0.9)) == pytest.approx(0.9, abs=0.05)


def test_cancel_drops_the_session_without_a_transcript(tmp_path):
    events = queue.Queue()
    session = make_voice(tmp_path, engine=Fake()).session("c", "command", events.put)
    session.feed(tone(0.4))
    session.cancel()
    seen = until(events, "voice.state")
    while seen[-1]["state"] != "cancelled":
        seen = until(events, "voice.state")
    session.join(5)
    assert not of(list(events.queue), "voice.final")


def test_a_command_ends_by_itself_at_its_limit(tmp_path, monkeypatch):
    monkeypatch.setitem(voice.LIMITS, "command", 1.5)
    events = queue.Queue()
    session = make_voice(tmp_path, engine=Fake()).session("l", "command", events.put)
    session.feed(tone(1.0))
    session.feed(tone(1.0))
    assert until(events, "voice.final")[-1]["duration"] == 1.5


def test_latching_a_command_into_a_dump_keeps_listening_in_dump_mode(tmp_path):
    modes, events = [], queue.Queue()
    made = make_voice(tmp_path)
    made.factory = lambda mode: modes.append(mode) or Fake()
    session = made.session("m", "command", events.put)
    session.feed(tone(1.0))
    until(events, "voice.partial")
    session.switch("dump")
    assert until(events, "voice.state")[-1] == {"type": "voice.state", "session": "m", "mode": "dump", "state": "listening"}
    session.feed(tone(0.2))
    session.stop()
    assert until(events, "voice.final")[-1]["mode"] == "dump"
    assert modes == ["command", "dump"]


def test_the_demo_hears_a_canned_transcript_without_a_model(tmp_path):
    made = make_voice(tmp_path, demo=True)
    for mode, expected in (("command", voice.DEMO_COMMAND), ("dump", DEMO_DUMP)):
        events = queue.Queue()
        session = made.session(mode, mode, events.put)
        session.feed(silence(1.2))  # even a silent microphone "speaks" in the demo
        partial = until(events, "voice.partial")[-1]["text"]
        assert expected.startswith(partial) and partial
        session.stop()
        assert until(events, "voice.final")[-1]["text"] == expected
    assert not (tmp_path / "data" / "voice").exists()


# The model download -----------------------------------------------------------

@pytest.fixture
def pinned(monkeypatch):
    """A tiny pinned model served by a mock Hugging Face."""
    body = {"config.json": b"{}", "model.bin": b"\x01" * 300_000, "tokenizer.json": b"[]", "vocabulary.txt": b"a\nb\n"}
    files = tuple(voice.File(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in body.items())
    model = voice.Model("test", "Test", "example/whisper-test", "0" * 40, files)
    monkeypatch.setitem(voice.MODELS, "test", model)
    served = SimpleNamespace(body=body, requests=[])

    def handle(request: httpx.Request) -> httpx.Response:
        served.requests.append(str(request.url))
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=served.body[name])

    monkeypatch.setattr(voice, "transport", httpx.MockTransport(handle))
    return served


def test_the_model_downloads_on_first_use_with_progress_into_the_data_folder(tmp_path, pinned):
    events = []
    models = voice.Models(tmp_path / "data" / "voice", events.append)
    assert not models.installed("test")
    folder = models.ensure("test")
    assert folder == tmp_path / "data" / "voice" / "test" and models.installed("test")
    assert sorted(path.name for path in folder.iterdir()) == [".complete", "config.json", "model.bin",
                                                               "tokenizer.json", "vocabulary.txt"]
    assert pinned.requests[1] == "https://huggingface.co/example/whisper-test/resolve/" + "0" * 40 + "/model.bin"
    states = [event["state"] for event in events]
    assert states[0] == "downloading" and states[-1] == "ready"
    assert events[-1]["received"] == events[-1]["total"] == sum(len(data) for data in pinned.body.values())
    before = len(pinned.requests)
    models.ensure("test")
    assert len(pinned.requests) == before


def test_a_download_that_does_not_match_the_pin_keeps_nothing(tmp_path, pinned):
    pinned.body["model.bin"] = b"\x02" * 300_000
    events = []
    models = voice.Models(tmp_path / "voice", events.append)
    with pytest.raises(voice.VoiceError, match="did not match"):
        models.ensure("test")
    assert not models.installed("test") and not (tmp_path / "voice" / "test" / "model.bin").exists()
    assert events[-1]["state"] == "failed" and "did not match" in events[-1]["message"]


def test_every_shipped_model_is_pinned_to_a_revision_and_hash():
    assert voice.DEFAULT_MODEL in voice.MODELS
    for model in voice.MODELS.values():
        assert len(model.revision) == 40 and model.repo.startswith("Systran/faster-whisper-")
        assert {item.name for item in model.files} == {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}
        assert all(len(item.sha256) == 64 for item in model.files)

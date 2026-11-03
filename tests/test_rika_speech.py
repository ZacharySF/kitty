"""Rika's spoken replies: a short local summary, spoken by a local engine or not at all."""
from __future__ import annotations

import base64
import os
import struct
from types import SimpleNamespace

from kitty.core.rika import speech


def test_the_summary_is_the_first_sentences_that_fit():
    assert speech.summary("Added the task Read chapter 3. It is due tomorrow. Anything else?", 40) == \
        "Added the task Read chapter 3."
    assert speech.summary("Found **3 matches** for `cells`: [Cells](#notes) and more.") == \
        "Found 3 matches for cells: Cells and more."
    long = "word " * 100
    assert len(speech.summary(long)) <= speech.SUMMARY_MAX and not speech.summary(long).endswith(" ")
    assert speech.summary("") == ""


def _app(tmp_path, *, demo=False):
    return SimpleNamespace(settings=SimpleNamespace(demo=demo, data_dir=tmp_path / "data"))


def _fake_espeak(tmp_path, monkeypatch):
    """An espeak-ng on PATH that writes a WAV with streamed (placeholder) lengths, like the real one."""
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "espeak-ng"
    header = b"RIFF" + struct.pack("<I", 0x7FFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
    wav = header + b"data" + struct.pack("<I", 0x7FFFFFFF) + b"\x00\x01" * 8
    (folder / "out.wav").write_bytes(wav)
    script.write_text(f"#!/bin/sh\ncat > {folder / 'heard.txt'}\necho \"$@\" > {folder / 'args.txt'}\n"
                      f"cat {folder / 'out.wav'}\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:{os.environ['PATH']}")
    return folder


def test_a_local_engine_speaks_the_summary_as_a_wav_data_url(tmp_path, monkeypatch):
    folder = _fake_espeak(tmp_path, monkeypatch)
    assert speech.engine(tmp_path)[0] == "espeak-ng"
    url = speech.synthesize(_app(tmp_path), "Added the task.")
    assert url.startswith("data:audio/wav;base64,")
    wav = base64.b64decode(url.split(",", 1)[1])
    assert struct.unpack("<I", wav[4:8])[0] == len(wav) - 8
    data = wav.find(b"data")
    assert struct.unpack("<I", wav[data + 4:data + 8])[0] == len(wav) - data - 8
    assert (folder / "heard.txt").read_text() == "Added the task."
    assert "--stdout" in (folder / "args.txt").read_text()


def test_without_an_engine_or_in_the_demo_rika_stays_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert speech.engine(tmp_path) is None
    assert speech.synthesize(_app(tmp_path), "Hello.") is None
    _fake_espeak(tmp_path, monkeypatch)
    assert speech.synthesize(_app(tmp_path, demo=True), "Hello.") is None


def test_piper_wins_when_it_has_a_voice_model(tmp_path, monkeypatch):
    folder = _fake_espeak(tmp_path, monkeypatch)
    piper = folder / "piper"
    piper.write_text("#!/bin/sh\nexit 0\n")
    piper.chmod(0o755)
    assert speech.engine(tmp_path / "data")[0] == "espeak-ng"  # no voice model yet
    voices = tmp_path / "data" / "voice" / "piper"
    voices.mkdir(parents=True)
    (voices / "en_US-amy-medium.onnx").write_bytes(b"onnx")
    name, argv = speech.engine(tmp_path / "data")
    assert name == "piper" and argv[1:3] == ["--model", str(voices / "en_US-amy-medium.onnx")]

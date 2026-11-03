"""Rika's spoken replies (design Q6 C): only to a spoken request, only a short summary.

The summary is local (the reply's first sentences, never a model call) and speech
runs on a local engine when one is installed: Piper with a voice model, else
espeak-ng (or espeak). Without one Rika stays silent; the reply is still in its toast.
The demo never speaks, because it never starts a process.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import struct
from pathlib import Path

#: Longest spoken summary, in characters.
SUMMARY_MAX = 180
TIMEOUT = 15
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_MARKUP = re.compile(r"[*_`#>|]+|\[([^\]]*)\]\([^)]*\)")


def summary(text: str, limit: int = SUMMARY_MAX) -> str:
    """The reply's first sentences that fit `limit`; a longer first sentence is cut at a word."""
    plain = " ".join(_MARKUP.sub(lambda match: match.group(1) or "", text).split())
    spoken = ""
    for sentence in _SENTENCE.split(plain):
        candidate = f"{spoken} {sentence}".strip()
        if len(candidate) > limit:
            break
        spoken = candidate
    if not spoken and plain:
        spoken = plain[:limit].rsplit(" ", 1)[0] if len(plain) > limit else plain
    return spoken


def _piper_voice(data_dir: Path) -> Path | None:
    named = os.environ.get("KITTY_PIPER_VOICE", "").strip()
    if named:
        path = Path(named).expanduser()
        return path if path.is_file() else None
    voices = sorted((data_dir / "voice" / "piper").glob("*.onnx"))
    return voices[0] if voices else None


def engine(data_dir: Path) -> tuple[str, list[str]] | None:
    """The local text-to-speech command (its name and argv, text on stdin, WAV on stdout)."""
    piper, voice = shutil.which("piper"), _piper_voice(data_dir)
    if piper and voice:
        return "piper", [piper, "--model", str(voice), "--output_file", "-"]
    for name in ("espeak-ng", "espeak"):
        found = shutil.which(name)
        if found:
            return name, [found, "--stdout", "-s", "170", "--stdin"]
    return None


def synthesize(app, text: str) -> str | None:
    """A data: URL of the spoken `text`, or None when no local engine can speak it."""
    if app.settings.demo or not text:
        return None
    found = engine(app.settings.data_dir)
    if found is None:
        return None
    try:
        done = subprocess.run(found[1], input=text.encode("utf-8"), capture_output=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not done.stdout.startswith(b"RIFF"):
        return None
    return "data:audio/wav;base64," + base64.b64encode(_sized(done.stdout)).decode("ascii")


def _sized(wav: bytes) -> bytes:
    """A WAV written to a pipe carries placeholder lengths; WebKit wants the real ones."""
    data = wav.find(b"data", 12)
    if data < 0:
        return wav
    fixed = bytearray(wav)
    fixed[4:8] = struct.pack("<I", len(wav) - 8)
    fixed[data + 4:data + 8] = struct.pack("<I", len(wav) - data - 8)
    return bytes(fixed)

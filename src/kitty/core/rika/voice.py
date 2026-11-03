"""On-device speech to text for Rika (design Q5 A): faster-whisper in the backend.

The page captures the microphone with ``getUserMedia`` and streams 16 kHz mono
little-endian 16-bit PCM over Rika's WebSocket while the user holds Space in the bar
(or for a whole brain dump). Each connection gets one :class:`Session` at a time,
which transcribes the audio in chunks on its own thread, so a ten-minute dump is
written out while it is spoken:

* every :data:`STEP` seconds of new audio, the open window is transcribed again and
  sent as ``voice.partial``;
* a pause (or :data:`MAX_WINDOW` seconds of talk) commits the window as a
  ``voice.segment`` with its start and end time, and the next window begins;
* stopping transcribes what is left and sends ``voice.final`` with the whole text.

Audio never leaves the machine. The Whisper model (a small multilingual one by
default, chosen in Settings → AI) downloads on first use into the workspace's
``data/voice/`` folder, pinned to a Hugging Face revision and checked against its
SHA-256; ``voice.model`` events report the progress to every page. The demo never
downloads or loads a model: it "hears" a canned transcript.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2
#: Seconds of new audio between two partial hypotheses of the open window.
STEP = 1.0
#: The longest window transcribed at once; longer talk is committed in pieces.
MAX_WINDOW = 20.0
#: A pause commits the window only once it holds at least this much audio.
MIN_COMMIT = 2.0
#: Trailing quiet, in seconds, that ends a phrase.
PAUSE = 0.6
#: 30 ms frames whose RMS is below this (of 32768) count as quiet.
QUIET_RMS = 300
FRAME = int(SAMPLE_RATE * 0.03) * 2
#: The longest a session listens: a spoken command, or a whole brain dump.
LIMITS = {"command": 60.0, "dump": 30 * 60.0}
MODES = tuple(LIMITS)
#: Largest audio frame the page may send (about two seconds).
FRAME_MAX = 64 * 1024
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=60.0)
#: Tests swap in an ``httpx.MockTransport``; None is the real network.
transport: httpx.BaseTransport | None = None


class VoiceError(RuntimeError):
    """Voice input cannot run; the message is safe to show."""


@dataclass(frozen=True)
class File:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    repo: str
    revision: str
    files: tuple[File, ...]

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)

    def url(self, name: str) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{name}"


_TOKENIZER = File("tokenizer.json", 2_203_239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab")
_VOCABULARY = File("vocabulary.txt", 459_861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913")
#: CTranslate2 conversions of OpenAI's multilingual Whisper models, pinned by revision.
MODELS: dict[str, Model] = {model.id: model for model in (
    Model("tiny", "Tiny · fastest", "Systran/faster-whisper-tiny", "d90ca5fe260221311c53c58e660288d3deb8d356", (
        File("config.json", 2_249, "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"),
        File("model.bin", 75_538_270, "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"),
        _TOKENIZER, _VOCABULARY)),
    Model("base", "Base · balanced", "Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66", (
        File("config.json", 2_309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
        File("model.bin", 145_217_532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
        _TOKENIZER, _VOCABULARY)),
    Model("small", "Small · most accurate", "Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120", (
        File("config.json", 2_370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
        File("model.bin", 483_546_902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        _TOKENIZER, _VOCABULARY)),
)}
DEFAULT_MODEL = "base"
_COMPLETE = ".complete"

#: What the demo "hears": a spoken command, and a short brain dump whose plan is scripted
#: in :mod:`kitty.core.rika.dumps`.
DEMO_COMMAND = "Add a task Read chapter 3 tomorrow"
#: Words per second the demo's canned speech arrives at.
DEMO_RATE = 2.6


# Models -------------------------------------------------------------------------

class Models:
    """Whisper models under ``<data>/voice/``; one download runs at a time."""

    def __init__(self, root: Path, publish: Callable[[dict], None]):
        self.root = root
        self._publish = publish
        self._lock = threading.Lock()
        self._done = threading.Condition(self._lock)
        self._downloading: dict[str, dict] = {}
        self._failed: dict[str, str] = {}

    def path(self, model_id: str) -> Path:
        return self.root / model_id

    def installed(self, model_id: str) -> bool:
        folder = self.path(model_id)
        return (folder / _COMPLETE).is_file() and all(
            (folder / item.name).is_file() and (folder / item.name).stat().st_size == item.size
            for item in MODELS[model_id].files)

    def progress(self, model_id: str) -> dict | None:
        with self._lock:
            current = self._downloading.get(model_id)
            return dict(current) if current else None

    def start(self, model_id: str) -> bool:
        """Download in the background; False when it is already here or on its way."""
        with self._lock:
            if model_id in self._downloading or self.installed(model_id):
                return False
            self._begin(model_id)
        threading.Thread(target=self._download_quietly, args=(model_id,), name="rika-voice-model", daemon=True).start()
        return True

    def ensure(self, model_id: str, cancelled: Callable[[], bool] = lambda: False) -> Path:
        """The model's folder, downloading it first when needed (waits for one in flight)."""
        with self._lock:
            if self.installed(model_id):
                return self.path(model_id)
            owner = model_id not in self._downloading
            if owner:
                self._begin(model_id)
        if owner:
            self._download(model_id)
            return self.path(model_id)
        with self._lock:
            while model_id in self._downloading:
                if cancelled():
                    raise VoiceError("Voice input was cancelled")
                self._done.wait(0.25)
            if not self.installed(model_id):
                raise VoiceError(self._failed.get(model_id) or "The voice model could not be downloaded")
        return self.path(model_id)

    def _begin(self, model_id: str) -> None:
        self._downloading[model_id] = {"received": 0, "total": MODELS[model_id].size}
        self._failed.pop(model_id, None)

    def _download_quietly(self, model_id: str) -> None:
        try:
            self._download(model_id)
        except VoiceError:
            pass  # reported as a voice.model event

    def _download(self, model_id: str) -> None:
        model = MODELS[model_id]
        folder = self.path(model_id)
        received, last = 0, 0.0
        self._event(model_id, "downloading", 0)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / _COMPLETE).unlink(missing_ok=True)
            with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True, transport=transport) as client:
                for item in model.files:
                    target = folder / item.name
                    if target.is_file() and target.stat().st_size == item.size and _sha256(target) == item.sha256:
                        received += item.size
                        continue
                    partial = folder / (item.name + ".part")
                    digest, size = hashlib.sha256(), 0
                    with client.stream("GET", model.url(item.name)) as response, partial.open("wb") as out:
                        response.raise_for_status()
                        for chunk in response.iter_bytes(1 << 16):
                            size += len(chunk)
                            if size > item.size:
                                raise VoiceError(f"{item.name} is larger than the pinned model")
                            digest.update(chunk)
                            out.write(chunk)
                            with self._lock:
                                self._downloading[model_id]["received"] = received + size
                            if time.monotonic() - last > 0.25:
                                last = time.monotonic()
                                self._event(model_id, "downloading", received + size)
                    if size != item.size or digest.hexdigest() != item.sha256:
                        partial.unlink(missing_ok=True)
                        raise VoiceError(f"{item.name} did not match the pinned model; nothing was kept")
                    partial.replace(target)
                    received += item.size
            (folder / _COMPLETE).write_text(model.revision + "\n")
        except Exception as exc:  # noqa: BLE001 - every failure becomes one readable message
            message = str(exc) if isinstance(exc, VoiceError) else "The voice model could not be downloaded; check the connection and try again"
            if not isinstance(exc, VoiceError):
                log.warning("Voice model download failed", exc_info=exc)
            with self._lock:
                self._downloading.pop(model_id, None)
                self._failed[model_id] = message
                self._done.notify_all()
            self._event(model_id, "failed", received, message=message)
            raise VoiceError(message) from None
        with self._lock:
            self._downloading.pop(model_id, None)
            self._done.notify_all()
        self._event(model_id, "ready", model.size)

    def _event(self, model_id: str, state: str, received: int, **extra: Any) -> None:
        self._publish({"type": "voice.model", "model": model_id, "state": state, "received": received,
                       "total": MODELS[model_id].size, **extra})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Engines ------------------------------------------------------------------------

class Engine(Protocol):
    #: Whether quiet audio is skipped without asking the engine (Whisper invents words in silence).
    hears_audio: bool

    def transcribe(self, pcm: bytes, *, start: float, final: bool, memory: dict) -> str:
        """The words in `pcm`, which begins `start` seconds into the session.

        `memory` is the session's own: what the engine learnt earlier in it (its language)."""


#: Audio Whisper needs before its language guess is trusted for the rest of a session;
#: on a one-second window it may well hear English as Swedish.
LANGUAGE_AFTER = 3.0


class Whisper:
    """faster-whisper on the CPU, int8, greedy while listening and a beam search for the
    final pass: about a second for a short command."""

    hears_audio = True

    def __init__(self, folder: Path):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # a build without the optional native libraries
            raise VoiceError("This build of rika! has no speech engine") from exc
        threads = max(1, min(4, os.cpu_count() or 1))
        self.model = WhisperModel(str(folder), device="cpu", compute_type="int8", cpu_threads=threads)

    def transcribe(self, pcm: bytes, *, start: float, final: bool, memory: dict) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, info = self.model.transcribe(audio, language=memory.get("language"), beam_size=5 if final else 1,
                                               condition_on_previous_text=False, vad_filter=False,
                                               without_timestamps=True)
        # Whisper hallucinates short phrases ("Thank you.") in noise; drop what it doubts.
        text = " ".join(segment.text.strip() for segment in segments
                        if not (segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0)).strip()
        if "language" not in memory and len(pcm) >= LANGUAGE_AFTER * BYTES_PER_SECOND and info.language_probability > 0.5:
            memory["language"] = info.language
        return text


class Canned:
    """The demo's engine: a scripted transcript arriving at a steady speaking rate."""

    hears_audio = False

    def __init__(self, text: str):
        self.words = text.split()

    def transcribe(self, pcm: bytes, *, start: float, final: bool, memory: dict) -> str:
        first = min(len(self.words), round(start * DEMO_RATE))
        last = len(self.words) if final else min(len(self.words), round((start + len(pcm) / BYTES_PER_SECOND) * DEMO_RATE))
        return " ".join(self.words[first:last])


def quiet(pcm: bytes) -> bool:
    """Whether every 30 ms frame of `pcm` is below :data:`QUIET_RMS`."""
    return trailing_quiet(pcm) * BYTES_PER_SECOND >= len(pcm) - len(pcm) % FRAME


def trailing_quiet(pcm: bytes) -> float:
    """Seconds of quiet at the end of `pcm`."""
    samples = array("h")
    samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if array("h", [1]).tobytes() != b"\x01\x00":  # the page sends little-endian samples
        samples.byteswap()
    step = FRAME // 2
    quiet_frames = 0
    for end in range(len(samples) - len(samples) % step, 0, -step):
        frame = samples[end - step:end]
        if sum(value * value for value in frame) > QUIET_RMS * QUIET_RMS * step:
            break
        quiet_frames += 1
    return quiet_frames * step / SAMPLE_RATE


# Sessions -----------------------------------------------------------------------

class Session:
    """One listening session on one page connection; events go to that page only."""

    def __init__(self, voice: "Voice", ident: str, mode: str, emit: Callable[[dict], None]):
        self.voice = voice
        self.id = ident
        self.mode = mode
        self._emit = emit
        self._cond = threading.Condition()
        self._audio = bytearray()
        self._start = 0.0
        self._received = 0
        self._segments: list[dict] = []
        self._stopping = False
        self._cancelled = False
        self._engines: dict[str, Engine] = {}
        self._memory: dict = {}
        self._thread = threading.Thread(target=self._run, name="rika-voice", daemon=True)
        self._thread.start()

    # The page --------------------------------------------------------------------

    def feed(self, pcm: bytes) -> None:
        with self._cond:
            if self._stopping or self._cancelled:
                return
            room = int(LIMITS[self.mode] * BYTES_PER_SECOND) - self._received
            pcm = pcm[:max(0, room)]
            self._audio += pcm
            self._received += len(pcm)
            if self._received >= LIMITS[self.mode] * BYTES_PER_SECOND:
                self._stopping = True  # the longest session ends by itself
            self._cond.notify_all()

    def switch(self, mode: str) -> None:
        with self._cond:
            self.mode = mode
        self.event("voice.state", state="listening")

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()

    def cancel(self) -> None:
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def event(self, kind: str, **fields: Any) -> None:
        self._emit({"type": kind, "session": self.id, "mode": self.mode, **fields})

    # The worker ------------------------------------------------------------------

    def _engine(self) -> Engine:
        """The engine for the current mode (the demo's canned speech follows a latch into a dump)."""
        mode = self.mode
        if mode not in self._engines:
            self._engines[mode] = self.voice.engine(mode, lambda: self._cancelled)
        return self._engines[mode]

    def _run(self) -> None:
        try:
            self.event("voice.state", state="loading")
            self._engine()
            self.event("voice.state", state="listening")
            heard = 0
            while True:
                with self._cond:
                    while not (self._cancelled or self._stopping or len(self._audio) - heard >= STEP * BYTES_PER_SECOND):
                        self._cond.wait(0.25)
                    if self._cancelled:
                        self.event("voice.state", state="cancelled")
                        return
                    if self._stopping:
                        break
                    window, start = bytes(self._audio), self._start
                heard = self._hear(window, start)
            self.event("voice.state", state="finishing")
            with self._cond:
                window, start = bytes(self._audio), self._start
            engine = self._engine()
            if window or not engine.hears_audio:
                self._commit(self._transcribe(engine, window, start, final=True), window, start)
            text = " ".join(segment["text"] for segment in self._segments).strip()
            self.event("voice.final", text=text, segments=self._segments,
                       duration=round(self._received / BYTES_PER_SECOND, 2))
        except VoiceError as exc:
            self.event("voice.error", message=str(exc))
        except Exception:  # noqa: BLE001 - one broken session must not stop the page's stream
            log.exception("Rika's voice session failed")
            self.event("voice.error", message="Voice input failed on this computer; nothing was sent")

    def _hear(self, window: bytes, start: float) -> int:
        """Transcribe the open window; commit it at a pause or when it is long.

        Returns how much of the (new) window has been heard."""
        text = self._transcribe(self._engine(), window, start, final=False)
        seconds = len(window) / BYTES_PER_SECOND
        if seconds >= MAX_WINDOW or (seconds >= MIN_COMMIT and trailing_quiet(window) >= PAUSE):
            self._commit(text, window, start)
            return 0
        self.event("voice.partial", text=text)
        return len(window)

    def _transcribe(self, engine: Engine, window: bytes, start: float, *, final: bool) -> str:
        if engine.hears_audio and quiet(window):
            return ""
        return self.voice.transcribe(engine, window, start=start, final=final, memory=self._memory)

    def _commit(self, text: str, window: bytes, start: float) -> None:
        end = start + len(window) / BYTES_PER_SECOND
        with self._cond:
            del self._audio[:len(window)]
            self._start = end
        if text:
            segment = {"start": round(start, 2), "end": round(end, 2), "text": text}
            self._segments.append(segment)
            self.event("voice.segment", index=len(self._segments) - 1, **segment)
        self.event("voice.partial", text="")


class Voice:
    """Rika's speech to text: the chosen model, its download and the loaded engine."""

    def __init__(self, app, publish: Callable[[dict], None]):
        self.app = app
        self.models = Models(app.settings.data_dir / "voice", publish)
        #: Tests replace this with a fake engine factory; it receives the session mode.
        self.factory: Callable[[str], Engine] | None = None
        self._loaded: tuple[str, Engine] | None = None
        self._load = threading.Lock()
        self._busy = threading.Lock()

    @staticmethod
    def chosen(conn) -> str:
        model = conn.execute("SELECT rika_voice_model FROM ai_settings WHERE id=1").fetchone()[0]
        return model if model in MODELS else DEFAULT_MODEL

    def status(self, conn) -> dict:
        model, demo = self.chosen(conn), bool(self.app.settings.demo)
        return {
            "engine": "demo" if demo else "whisper",
            "model": model,
            "models": [{"id": item.id, "label": item.label, "size": item.size,
                        "installed": not demo and self.models.installed(item.id)} for item in MODELS.values()],
            "installed": demo or self.models.installed(model),
            "downloading": None if demo else self.models.progress(model),
        }

    def choose(self, conn, model: Any) -> dict:
        from kitty.db.connection import transaction

        if model not in MODELS:
            raise VoiceError("Choose the tiny, base or small voice model")
        with transaction(conn):
            conn.execute("UPDATE ai_settings SET rika_voice_model=? WHERE id=1", (model,))
        return self.status(conn)

    def download(self, conn) -> bool:
        """Fetch the chosen model now (Settings) instead of on first use."""
        if self.app.settings.demo:
            raise VoiceError("The demo hears a canned transcript and downloads no voice model")
        return self.models.start(self.chosen(conn))

    def session(self, ident: str, mode: str, emit: Callable[[dict], None]) -> Session:
        return Session(self, ident, mode, emit)

    def engine(self, mode: str, cancelled: Callable[[], bool]) -> Engine:
        """The engine for a session mode, downloading and loading the model on first use."""
        if self.factory is not None:
            return self.factory(mode)
        if self.app.settings.demo:
            from kitty.core.rika.dumps import DEMO_DUMP

            return Canned(DEMO_DUMP if mode == "dump" else DEMO_COMMAND)
        from contextlib import closing
        from kitty.db import connect

        with closing(connect(self.app.settings.db_path)) as conn:
            model = self.chosen(conn)
        folder = self.models.ensure(model, cancelled)
        with self._load:
            if self._loaded is None or self._loaded[0] != model:
                self._loaded = (model, Whisper(folder))
            return self._loaded[1]

    def transcribe(self, engine: Engine, pcm: bytes, *, start: float, final: bool, memory: dict) -> str:
        with self._busy:  # one transcription at a time leaves the CPU for the page
            return engine.transcribe(pcm, start=start, final=final, memory=memory)

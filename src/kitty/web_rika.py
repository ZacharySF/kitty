"""Rika, rika!'s own agent (kitty.core.rika), always on beside the core.

POST /api/rika/messages {text, tier?, conversation_id?, spoken?}  ->  {conversation_id, message_id}
    Queues one request; the reply streams on the socket. ``tier`` (fast|standard|deep)
    overrides the router for this request; ``spoken`` marks what the user said aloud
    (it routes to the fast tier and its reply may be spoken back).
POST /api/rika/dumps {transcript, segments: [{start, end, text}], duration, conversation_id?}
    ->  {conversation_id, message_id}
    A brain dump: its dump note is saved and its plan arrives as a ``plan`` event.
POST /api/rika/plans/<id> {decision: approve, items: [item ids], choices?: {item id: index}}
    or {decision: dismiss}  ->  {plan}
    Approving runs the ticked items on Rika's worker and links them into the dump note.
GET  /api/rika/history[?before=<created_at>]  ->  {items, has_more, next_before}
    Messages, actions, approvals and plans, oldest first (shapes in kitty.core.rika.store).
POST /api/rika/approvals/<id> {decision: approve|reject}  ->  {approval}
POST /api/rika/actions/<id>/undo {}  ->  {action}
GET  /api/rika/agents  ->  {agents: [{id, kind, name, status, task, line, since, where, now}]}
    ``now`` is what the agent uses now (kitty.web_agent_skills), or null while agent
    skills are off for its kind.
POST /api/rika/agents/<id>/open {}  ->  {ok, window, view}
    Focuses the agent's pane in a Tern window; ``view`` asks the page to open a view
    instead (the herdr view when Tern is not installed).
GET  /api/rika/voice  ->  {engine, model, models, installed, downloading, speech}
POST /api/rika/voice {model}  ->  the same; chooses the Whisper model (Settings → AI)
POST /api/rika/voice/model {}  ->  {started}; downloads the chosen model now
GET  /api/rika/speech/<message id>  ->  {text, audio}
    A short summary of a reply to a spoken request and, with a local engine, its
    speech as a data: URL (audio is null without one, and always in the demo).
GET  /api/rika/stream  (WebSocket upgrade)
    Server-to-page JSON text messages: token, message, action, approval, plan, tier,
    navigate, agents and voice.model events. Like every upgrade it is guarded by the
    exact Host and Origin; a page that falls behind is dropped and reloads history on
    reconnect. The page may send voice input on it, and nothing else:
    ``{"type": "voice.start", "session", "mode": "command"|"dump"}``, binary frames of
    16 kHz mono little-endian 16-bit PCM, ``{"type": "voice.mode", "mode"}`` to latch a
    command into a brain dump, then ``voice.stop`` or ``voice.cancel``. Transcription
    stays on this machine and answers only that page (voice.state, voice.partial,
    voice.segment, voice.final, voice.error); nothing is stored until the page sends
    the text as a header-protected request.

Every mutation is header-protected like the rest of the API. Rika has no ingress route.
"""
from __future__ import annotations

import json
import queue
import re
import select
import struct

from kitty.core.rika import speech, store
from kitty.core.rika.agents import AgentError
from kitty.core.rika.loop import Rika, RikaError
from kitty.core.rika.voice import FRAME_MAX, MODES, Session, VoiceError
from kitty.websocket import Closed, Reader, accept, check_upgrade, frame

PREFIX = "/api/rika"
STREAM = PREFIX + "/stream"
_ID = r"([0-9a-f]{32})"
_AGENT = r"((?:herdr|tern):[A-Za-z0-9:_.-]{1,200})"
_SESSION = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    app.rika = Rika(app)


def stop(app) -> None:
    app.rika.close()


def _keys(body: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if set(body) - allowed or required - set(body):
        raise _error(400, "Unexpected or missing Rika request fields")


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except RikaError as exc:
        raise _error(exc.status, str(exc)) from None


def _voice(app, conn) -> dict:
    found = None if app.settings.demo else speech.engine(app.settings.data_dir)
    return {**app.rika.voice.status(conn), "speech": found[0] if found else None}


def get(app, conn, path, query):
    if path == STREAM:
        raise _error(426, "Open Rika's stream with a WebSocket upgrade")
    if path == PREFIX + "/history":
        if set(query) - {"before"}:
            raise _error(400, "History accepts only before")
        before = query.get("before")
        if before is not None and not re.fullmatch(r"[0-9T:.+-]{10,40}", before):
            raise _error(400, "before must be an item's created_at")
        return store.history(conn, before)
    if path == PREFIX + "/agents":
        app._no_query(query)
        return {"agents": app.rika.agents.list()}
    if path == PREFIX + "/voice":
        app._no_query(query)
        return _voice(app, conn)
    match = re.fullmatch(PREFIX + "/speech/" + _ID, path)
    if match:
        app._no_query(query)
        return _call(app.rika.speak, conn, match[1])
    return None


def post(app, conn, path, body):
    if not path.startswith(PREFIX + "/"):
        return None
    if path == PREFIX + "/messages":
        _keys(body, {"text", "tier", "conversation_id", "spoken"}, {"text"})
        return _call(app.rika.submit, conn, body["text"], tier=body.get("tier"),
                     conversation_id=body.get("conversation_id"), spoken=body.get("spoken", False))
    if path == PREFIX + "/dumps":
        _keys(body, {"transcript", "segments", "duration", "conversation_id"}, {"transcript", "segments", "duration"})
        return _call(app.rika.dump, conn, body["transcript"], body["segments"], body["duration"],
                     conversation_id=body.get("conversation_id"))
    match = re.fullmatch(PREFIX + "/plans/" + _ID, path)
    if match:
        _keys(body, {"decision", "items", "choices"}, {"decision"})
        return {"plan": _call(app.rika.decide_plan, conn, match[1], body["decision"], body.get("items"),
                              body.get("choices"))}
    if path == PREFIX + "/voice":
        _keys(body, {"model"}, {"model"})
        try:
            app.rika.voice.choose(conn, body["model"])
        except VoiceError as exc:
            raise _error(400, str(exc)) from None
        return _voice(app, conn)
    if path == PREFIX + "/voice/model":
        _keys(body, set())
        try:
            return {"started": app.rika.voice.download(conn)}
        except VoiceError as exc:
            raise _error(409, str(exc)) from None
    match = re.fullmatch(PREFIX + "/approvals/" + _ID, path)
    if match:
        _keys(body, {"decision"}, {"decision"})
        return {"approval": _call(app.rika.decide, conn, match[1], body["decision"])}
    match = re.fullmatch(PREFIX + "/actions/" + _ID + "/undo", path)
    if match:
        _keys(body, set())
        return {"action": _call(app.rika.undo, conn, match[1])}
    match = re.fullmatch(PREFIX + "/agents/" + _AGENT + "/open", path)
    if match:
        _keys(body, set())
        try:
            return app.rika.agents.open(match[1])
        except LookupError as exc:
            raise _error(404, str(exc)) from None
        except AgentError as exc:
            raise _error(503, str(exc)) from None
    return None


def upgrade(app, handler, path, query) -> bool:
    if path != STREAM:
        return False
    app._no_query(query)
    key = check_upgrade(handler.headers)
    subscriber = app.rika.hub.subscribe()
    try:
        accept(handler, key)
        _pump(handler.connection, subscriber, app)
    finally:
        app.rika.hub.unsubscribe(subscriber)
    return True


class _Listening:
    """The voice input of one page connection: at most one session at a time."""

    def __init__(self, app, subscriber: queue.Queue):
        self.app = app
        self.subscriber = subscriber
        self.session: Session | None = None

    def emit(self, event: dict) -> None:
        try:
            self.subscriber.put_nowait(event)
        except queue.Full:
            pass  # the page fell behind; the hub drops it and it reconnects

    def control(self, payload: bytes) -> None:
        try:
            message = json.loads(payload)
        except ValueError:
            raise Closed(1003) from None
        if not isinstance(message, dict):
            raise Closed(1003)
        kind = message.get("type")
        if kind == "voice.start" and set(message) == {"type", "session", "mode"} \
                and isinstance(message["session"], str) and _SESSION.fullmatch(message["session"]) \
                and message["mode"] in MODES:
            self.close()
            self.session = self.app.rika.voice.session(message["session"], message["mode"], self.emit)
        elif kind == "voice.mode" and set(message) == {"type", "mode"} and message["mode"] in MODES:
            if self.session is not None:
                self.session.switch(message["mode"])
        elif kind in ("voice.stop", "voice.cancel") and set(message) == {"type"}:
            if self.session is not None:
                (self.session.stop if kind == "voice.stop" else self.session.cancel)()
        else:
            raise Closed(1003)

    def audio(self, payload: bytes) -> None:
        if len(payload) % 2 or len(payload) > FRAME_MAX:
            raise Closed(1003)
        if self.session is not None:
            self.session.feed(payload)  # a frame after voice.stop is ignored

    def close(self) -> None:
        if self.session is not None:
            self.session.cancel()
            self.session = None


def _pump(connection, subscriber: queue.Queue, app) -> None:
    """Send events until the page closes, falls behind or the server shuts down."""
    connection.settimeout(None)
    reader, code = Reader(), 1000
    hub = app.rika.hub
    listening = _Listening(app, subscriber)
    try:
        while not app.streams_closed.is_set():
            ready, _, _ = select.select([connection], [], [], 0.1)
            if ready:
                data = connection.recv(65536)
                if not data:
                    return
                for opcode, payload in reader.feed(data):
                    if opcode == 9:
                        connection.sendall(frame(10, payload))
                    elif opcode == 8:
                        raise Closed(struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1000)
                    elif opcode == 1:
                        listening.control(payload)
                    elif opcode == 2:
                        listening.audio(payload)
            while True:
                try:
                    event = subscriber.get_nowait()
                except queue.Empty:
                    break
                connection.sendall(frame(1, json.dumps(event, ensure_ascii=False).encode("utf-8")))
            if not hub.is_subscribed(subscriber):
                code = 1013  # fell behind: reconnect and reload history
                break
        else:
            code = 1001
    except Closed as closed:
        code = closed.code
    except OSError:
        return
    finally:
        listening.close()
    try:
        connection.sendall(frame(8, struct.pack("!H", code)))
    except OSError:
        pass

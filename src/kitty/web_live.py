"""Core: the worker's side of the live channel.

rika-server owns every window's live socket (server/src/live/: ``POST /api/live/session``,
``GET /api/live/socket``, the protocol, the ``op_log`` poller and resume). Behind it, this
module serves the one route only the front reaches (it refuses ``/api/internal/...`` to
pages); windows' block transactions are applied by the front's block engine
(server/src/blocks/):

GET  /api/internal/live/events   (WebSocket upgrade)
    The front's subscription to this worker. A binary frame ``{"type": "nudge"}`` follows
    every committed mutation (the front pushes it now instead of at its next poll, and
    syncs the build a Settings screen may have written); each text frame is one message
    another extension sends to every window (``broadcast``: the page mirror's frames),
    forwarded as it is.
"""
from __future__ import annotations

import json
import os
import queue
import select
import socket
import threading

from kitty.web_socket import Closed, Reader, accept, close_code, frame, handshake_key, send_close

EVENTS_PATH = "/api/internal/live/events"
NUDGE = json.dumps({"type": "nudge"}).encode()


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


class Subscriber:
    """One front's stream: frames queued for its socket thread, woken through a self-pipe."""

    def __init__(self):
        self.queue: queue.SimpleQueue[tuple[int, bytes]] = queue.SimpleQueue()
        self.wake_read, self.wake_write = os.pipe()
        os.set_blocking(self.wake_read, False)
        os.set_blocking(self.wake_write, False)

    def put(self, opcode: int, payload: bytes) -> None:
        self.queue.put((opcode, payload))
        try:
            os.write(self.wake_write, b"!")
        except BlockingIOError:
            pass  # Already awake: the pipe holds unread wake-ups.

    def close(self) -> None:
        os.close(self.wake_read)
        os.close(self.wake_write)


class Live:
    """What the worker pushes through the front."""

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._fronts: set[Subscriber] = set()

    def _put(self, opcode: int, payload: bytes) -> None:
        with self._lock:
            for front in self._fronts:
                front.put(opcode, payload)

    def nudge(self) -> None:
        """A mutation committed: the front pushes it now rather than at its next poll."""
        self._put(2, NUDGE)

    def broadcast(self, message: dict) -> None:
        """Send one message (not a block change) to every open window now."""
        self._put(1, json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode())

    def subscribe(self) -> Subscriber:
        subscriber = Subscriber()
        with self._lock:
            self._fronts.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._fronts.discard(subscriber)
        subscriber.close()


def start(app) -> None:
    app.live = Live(app)


def mutated(app) -> None:
    """An HTTP mutation committed (a Notes save, say): the front pushes it now."""
    app.live.nudge()


def get(app, conn, path, query):
    return None


def post(app, conn, path, body):
    return None


def upgrade(app, handler, path, query) -> bool:
    if path != EVENTS_PATH:
        return False
    if query:
        raise _error(400, "Unexpected live event parameters")
    key = handshake_key(handler.headers)
    live = app.live
    subscriber = live.subscribe()
    try:
        accept(handler, key)
        _serve(handler.connection, app, subscriber)
    finally:
        live.unsubscribe(subscriber)
    return True


def _serve(connection, app, subscriber: Subscriber) -> None:
    """Relay events to the front until it closes the stream or this worker shuts down."""
    connection.settimeout(None)
    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    reader = Reader()
    code = 1000
    try:
        while not app.streams_closed.is_set():
            ready, _, _ = select.select([connection, subscriber.wake_read], [], [], 0.25)
            if subscriber.wake_read in ready:
                while True:
                    try:
                        os.read(subscriber.wake_read, 4096)
                    except BlockingIOError:
                        break
            while True:
                try:
                    opcode, payload = subscriber.queue.get_nowait()
                except queue.Empty:
                    break
                connection.sendall(frame(opcode, payload))
            if connection in ready:
                data = connection.recv(65536)
                if not data:
                    return
                for opcode, payload in reader.feed(data):
                    if opcode == 9:
                        connection.sendall(frame(10, payload))
                    elif opcode == 8:
                        raise Closed(close_code(payload))
        else:
            code = 1001
    except Closed as closed:
        code = closed.code
    except OSError:
        return
    send_close(connection, code)

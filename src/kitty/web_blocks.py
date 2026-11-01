"""Workspace module: block transactions over a same-origin WebSocket, with live push.

POST /api/blocks/session  {}  ->  {"token", "socket"}
    Header-protected like every mutation, so only kitty!'s own page gets a token.
GET  /api/blocks/socket?token=<token>   (WebSocket upgrade)
    Exact Host and Origin plus a single-use token, as for the terminal.

Text messages are JSON objects. A window sends:

- ``{"type": "txn", "txn_id"?, "ops": [...], "base"?: {page_id: revision}}``. The
  server applies it (kitty.core.blocks.transact) and answers the sender with
  ``{"type": "ack", "txn_id", "seq": {page_id: seq}, "revisions": {...}}`` or
  ``{"type": "reject", "txn_id", "status", "error"}``.
- ``{"type": "resume", "after": seq}``: replays the changes it missed while away,
  or ``{"type": "reset", "horizon"}`` when history before the horizon was compacted.
- ``{"type": "subscribe", "pages": [page_id, ...] | null}``: limits live changes.

Every window receives ``{"type": "hello", "seq", "device_id"}`` on connect, then one
``{"type": "change", "seq", "txn_id", "page_id", "at", "device_id", "root", "ops"}``
per committed op_log row, whoever wrote it: this socket, the Notes HTTP API, a
background import or another process. ``root`` is ``{"type", "number"}`` of the
page the change belongs to, so the existing screens can find their page.
"""
from __future__ import annotations

import json
import os
import queue
import select
import socket
import sqlite3
import threading
import uuid
from contextlib import closing

from kitty.core import blocks
from kitty.db import connect
from kitty.web_socket import Closed, Reader, Tokens, accept, close_code, frame, handshake_key, send_close

SOCKET_PATH = "/api/blocks/socket"
POLL_SECONDS = 0.1
BATCH = 200


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def changes(conn: sqlite3.Connection, after: int, upto: int | None = None, limit: int = BATCH) -> list[dict]:
    """Committed op_log rows after `after` as push messages."""
    rows = conn.execute(
        "SELECT o.seq,o.txn_id,o.page_id,o.at,o.device_id,o.ops,b.type,json_extract(b.properties,'$.number') AS number "
        "FROM op_log o LEFT JOIN blocks b ON b.id=o.page_id AND b.parent_id IS NULL WHERE o.seq>? AND o.seq<=? ORDER BY o.seq LIMIT ?",
        (after, upto if upto is not None else 2**63 - 1, limit))
    return [{"type": "change", "seq": row["seq"], "txn_id": row["txn_id"], "page_id": row["page_id"], "at": row["at"],
             "device_id": row["device_id"], "root": {"type": row["type"], "number": row["number"]} if row["type"] else None,
             "ops": json.loads(row["ops"])["ops"]} for row in rows]


class Subscriber:
    def __init__(self, seq: int):
        self.queue: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self.pages: set[str] | None = None
        #: The last sequence this window had when it connected (its hello).
        self.seq = seq
        # A self-pipe wakes the window's socket thread as soon as a change is queued.
        self.wake_read, self.wake_write = os.pipe()
        os.set_blocking(self.wake_read, False)
        os.set_blocking(self.wake_write, False)

    def put(self, messages: list[dict]) -> None:
        for message in messages:
            self.queue.put(message)
        try:
            os.write(self.wake_write, b"!")
        except BlockingIOError:
            pass  # Already awake: the pipe holds unread wake-ups.

    def close(self) -> None:
        os.close(self.wake_read)
        os.close(self.wake_write)


class Live:
    """Tokens for the socket and one thread fanning committed changes out to every window.

    The thread starts with the first window, so a server nobody watches never polls.
    """

    def __init__(self, app):
        self.app = app
        self.tokens = Tokens()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._subscribers: set[Subscriber] = set()
        self._thread: threading.Thread | None = None
        self.seq = 0
        self.device_id = ""

    def nudge(self) -> None:
        self._wake.set()

    def subscribe(self) -> Subscriber:
        with self._lock:
            if self._thread is None:
                with closing(connect(self.app.settings.db_path)) as conn:
                    self.seq = conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log").fetchone()[0]
                    self.device_id = blocks.device_id(conn)
                self._thread = threading.Thread(target=self._run, name="kitty-blocks-live", daemon=True)
                self._thread.start()
            subscriber = Subscriber(self.seq)
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)
        subscriber.close()

    def _run(self) -> None:
        with closing(connect(self.app.settings.db_path)) as conn:
            while not self.app.streams_closed.is_set():
                self._wake.wait(POLL_SECONDS)
                self._wake.clear()
                try:
                    batch = changes(conn, self.seq)
                except sqlite3.OperationalError:
                    continue
                while batch:
                    with self._lock:
                        self.seq = batch[-1]["seq"]
                        for subscriber in self._subscribers:
                            subscriber.put(batch)
                    batch = changes(conn, self.seq) if len(batch) == BATCH else []

    def stop(self) -> None:
        if self._thread is not None:
            self.nudge()
            self._thread.join(timeout=5)

    def transact(self, message: dict) -> dict:
        txn_id = message.get("txn_id") or str(uuid.uuid4())
        try:
            if set(message) - {"type", "txn_id", "ops", "base"}:
                raise blocks.BlockError("Unexpected transaction fields")
            with self.app.mutation_lock, closing(connect(self.app.settings.db_path)) as conn:
                result = blocks.transact(conn, message.get("ops"), base=message.get("base"), txn_id=txn_id)
        except blocks.BlockError as exc:
            return {"type": "reject", "txn_id": txn_id, "status": exc.status, "error": str(exc)}
        except sqlite3.OperationalError:
            return {"type": "reject", "txn_id": txn_id, "status": 503, "error": "Local workspace is busy; send the transaction again"}
        self.nudge()
        return {"type": "ack", **result}

    def resume(self, subscriber: Subscriber, after) -> list[dict]:
        if type(after) is not int or after < 0:
            return [{"type": "error", "error": "Resume needs the last sequence number seen"}]
        with closing(connect(self.app.settings.db_path)) as conn:
            horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
            if after < horizon:
                return [{"type": "reset", "horizon": horizon}]
            missed, cursor = [], after
            while batch := changes(conn, cursor, subscriber.seq):
                missed.extend(batch)
                cursor = batch[-1]["seq"]
        return missed


def start(app) -> None:
    app.blocks_live = Live(app)


def stop(app) -> None:
    app.blocks_live.stop()


def mutated(app) -> None:
    """An HTTP mutation committed (a Notes save, say): push it now rather than at the next poll."""
    app.blocks_live.nudge()


def get(app, conn, path, query):
    if path == SOCKET_PATH:
        raise _error(426, "Open live blocks with a WebSocket upgrade")
    return None


def post(app, conn, path, body):
    if path != "/api/blocks/session":
        return None
    if body:
        raise _error(400, "A blocks session request expects an empty object")
    return {"token": app.blocks_live.tokens.issue(), "socket": SOCKET_PATH}


def upgrade(app, handler, path, query) -> bool:
    if path != SOCKET_PATH:
        return False
    if set(query) - {"token"}:
        raise _error(400, "Unexpected blocks socket parameters")
    key = handshake_key(handler.headers)
    live = app.blocks_live
    if not live.tokens.consume(query.get("token", "")):
        raise _error(403, "Blocks token is missing, expired or already used")
    subscriber = live.subscribe()
    try:
        accept(handler, key)
        _serve(handler.connection, live, subscriber, app.streams_closed)
    finally:
        live.unsubscribe(subscriber)
    return True


def _send(connection, message: dict) -> None:
    connection.sendall(frame(1, json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()))


def _handle(connection, live: Live, subscriber: Subscriber, payload: bytes) -> None:
    try:
        message = json.loads(payload)
    except ValueError:
        raise Closed(1007) from None
    kind = message.get("type") if isinstance(message, dict) else None
    if kind == "txn":
        _send(connection, live.transact(message))
    elif kind == "resume":
        for change in live.resume(subscriber, message.get("after")):
            _send(connection, change)
    elif kind == "subscribe":
        pages = message.get("pages")
        if pages is not None and (not isinstance(pages, list) or len(pages) > 1000 or not all(isinstance(page, str) for page in pages)):
            _send(connection, {"type": "error", "error": "Subscribe to a list of page IDs or null for all"})
        else:
            subscriber.pages = None if pages is None else set(pages)
    else:
        _send(connection, {"type": "error", "error": "Unknown message type"})


def _serve(connection, live: Live, subscriber: Subscriber, closing_event: threading.Event) -> None:
    """Relay pushes and handle messages until the window closes or the server shuts down."""
    connection.settimeout(None)
    # Acks and pushes are small and back to back; Nagle's algorithm would hold one behind the other.
    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    reader = Reader()
    code = 1000
    try:
        _send(connection, {"type": "hello", "seq": subscriber.seq, "device_id": live.device_id})
        while not closing_event.is_set():
            ready, _, _ = select.select([connection, subscriber.wake_read], [], [], 0.25)
            if subscriber.wake_read in ready:
                while True:
                    try:
                        os.read(subscriber.wake_read, 4096)
                    except BlockingIOError:
                        break
            while True:
                try:
                    change = subscriber.queue.get_nowait()
                except queue.Empty:
                    break
                if subscriber.pages is None or change["page_id"] in subscriber.pages:
                    _send(connection, change)
            if connection in ready:
                data = connection.recv(65536)
                if not data:
                    return
                for opcode, payload in reader.feed(data):
                    if opcode == 1:
                        _handle(connection, live, subscriber, payload)
                    elif opcode == 9:
                        connection.sendall(frame(10, payload))
                    elif opcode == 8:
                        raise Closed(close_code(payload))
                    elif opcode == 2:
                        raise Closed(1003)
        else:
            code = 1001
    except Closed as closed:
        code = closed.code
    except OSError:
        return
    send_close(connection, code)

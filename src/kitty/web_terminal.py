"""Terminal module: kitty!'s herdr session streamed over a same-origin WebSocket.

GET  /api/terminal/activity  ->  {"running", "workspaces": [{id, label, status, focused, agents}]}
    herdr's own agent states (idle/working/blocked/done/unknown) per workspace,
    read with herdr's CLI; each agent also carries its task (terminal title), last
    output line, output revision and ``since`` (epoch ms its state began). Never
    starts the session: ``running`` is false while kitty!'s session is not running.
POST /api/terminal/focus  {"pane"}  ->  {"ok"}
    Focuses one agent pane herdr currently lists (the dock's Open pane).
POST /api/terminal/session  {}  ->  {"token", "socket"}
    Header-protected like every mutation (exact Host/Origin, X-Kitty-Request,
    application/json), so only kitty!'s own page can obtain a token.
GET  /api/terminal/socket?token=<token>[&cols=<n>&rows=<n>]   (WebSocket upgrade)
    Browsers cannot add X-Kitty-Request to an upgrade. The equivalent same-origin
    proof is the exact Host and Origin (checked by the server) plus a token that
    is single-use and expires after TOKEN_TTL seconds. A refused proof never
    reaches herdr; a valid one attaches a new herdr client on a pty.

Binary messages carry terminal bytes both ways. A text message resizes the pty:
``{"type": "resize", "cols": <1-1000>, "rows": <1-1000>}``.
"""
from __future__ import annotations

import json
import os
import select
import threading

from kitty.core.terminal import CODING_AGENTS, HerdrRuntime, TerminalClient, TerminalNotRunning, TerminalUnavailable
from kitty.web_socket import Closed, Reader, Tokens, accept, close_code, frame, handshake_key, send_close

SOCKET_PATH = "/api/terminal/socket"
DEFAULT_SIZE = (80, 24)


class Terminal(Tokens):
    """Per-process token store plus kitty!'s herdr runtime."""

    def __init__(self, runtime: HerdrRuntime):
        super().__init__()
        self.runtime = runtime


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    settings = app.settings
    app.terminal = Terminal(HerdrRuntime(settings.herdr_path, settings.herdr_session,
                                         settings.root / ".private" / "herdr"))


def stop(app) -> None:
    app.terminal.runtime.stop()


def get(app, conn, path, query):
    if path == SOCKET_PATH:
        raise _error(426, "Open the terminal with a WebSocket upgrade")
    if path == "/api/terminal/agents":
        runtime = app.terminal.runtime
        return {"binary": runtime.binary, "session": runtime.session,
                "cwd": str(app.settings.root), "default": "omp", "kinds": list(CODING_AGENTS)}
    if path == "/api/terminal/activity":
        try:
            return app.terminal.runtime.activity()
        except TerminalUnavailable as exc:
            raise _error(503, str(exc)) from None


def post(app, conn, path, body):
    if path == "/api/terminal/agents":
        kind = body.get("kind", "omp")
        if set(body) - {"kind"} or not isinstance(kind, str) or kind not in CODING_AGENTS:
            raise _error(400, "Choose a supported coding agent")
        try:
            return app.terminal.runtime.start_agent(kind, app.settings.root)
        except TerminalNotRunning as exc:
            raise _error(409, str(exc)) from None
        except TerminalUnavailable as exc:
            raise _error(503, str(exc)) from None
    if path == "/api/terminal/focus":
        pane = body.get("pane")
        if set(body) != {"pane"} or not isinstance(pane, str):
            raise _error(400, "Name the agent pane to open")
        try:
            app.terminal.runtime.focus_agent(pane)
        except ValueError as exc:
            raise _error(400, str(exc)) from None
        except TerminalNotRunning as exc:
            raise _error(409, str(exc)) from None
        except TerminalUnavailable as exc:
            raise _error(503, str(exc)) from None
        return {"ok": True}
    if path != "/api/terminal/session":
        return None
    if body:
        raise _error(400, "A terminal session request expects an empty object")
    return {"token": app.terminal.issue(), "socket": SOCKET_PATH}


def _size(query: dict[str, str]) -> tuple[int, int]:
    size = []
    for name, default in zip(("cols", "rows"), DEFAULT_SIZE):
        value = query.get(name, str(default))
        if not value.isdigit() or not 1 <= int(value) <= 1000:
            raise _error(400, f"{name} must be between 1 and 1000")
        size.append(int(value))
    return size[0], size[1]


def upgrade(app, handler, path, query) -> bool:
    """Authorise, attach and stream one terminal; False when `path` is not ours."""
    if path != SOCKET_PATH:
        return False
    headers = handler.headers
    if set(query) - {"token", "cols", "rows"}:
        raise _error(400, "Unexpected terminal parameters")
    key = handshake_key(headers)
    cols, rows = _size(query)
    if not app.terminal.consume(query.get("token", "")):
        raise _error(403, "Terminal token is missing, expired or already used")
    try:
        client = app.terminal.runtime.attach(cols, rows)
    except TerminalUnavailable as exc:
        raise _error(503, str(exc)) from None
    except OSError:
        raise _error(503, "kitty!'s herdr session could not start") from None
    try:
        accept(handler, key)
        _pump(handler.connection, client, app.streams_closed)
    finally:
        client.close()
    return True


def _resize(client: TerminalClient, payload: bytes) -> None:
    try:
        message = json.loads(payload)
    except ValueError:
        raise Closed(1007) from None
    cols, rows = (message.get("cols"), message.get("rows")) if isinstance(message, dict) else (None, None)
    if (not isinstance(message, dict) or set(message) != {"type", "cols", "rows"}
            or message["type"] != "resize"
            or not all(type(value) is int and 1 <= value <= 1000 for value in (cols, rows))):
        raise Closed(1008)
    client.resize(cols, rows)


def _pump(connection, client: TerminalClient, closing: threading.Event) -> None:
    """Relay until the page closes, the client exits, or the server shuts down."""
    connection.settimeout(None)
    reader = Reader()
    code = 1000
    try:
        while not closing.is_set():
            ready, _, _ = select.select([connection, client.master], [], [], 0.25)
            if client.master in ready:
                try:
                    data = os.read(client.master, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                connection.sendall(frame(2, data))
            if connection in ready:
                data = connection.recv(65536)
                if not data:
                    return
                for opcode, payload in reader.feed(data):
                    if opcode == 2:
                        view = memoryview(payload)
                        while view:
                            view = view[os.write(client.master, view):]
                    elif opcode == 1:
                        _resize(client, payload)
                    elif opcode == 9:
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

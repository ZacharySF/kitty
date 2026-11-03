"""Rika, rika!'s own agent (kitty.core.rika), always on beside the core.

POST /api/rika/messages {text, tier?, conversation_id?}  ->  {conversation_id, message_id}
    Queues one request; the reply streams on the socket. ``tier`` (fast|standard|deep)
    overrides the router for this request.
GET  /api/rika/history[?before=<created_at>]  ->  {items, has_more, next_before}
    Messages, actions and approvals, oldest first (shapes in kitty.core.rika.store).
POST /api/rika/approvals/<id> {decision: approve|reject}  ->  {approval}
POST /api/rika/actions/<id>/undo {}  ->  {action}
GET  /api/rika/agents  ->  {agents: [{id, kind, name, status, task, line, since, where}]}
POST /api/rika/agents/<id>/open {}  ->  {ok, window, view}
    Focuses the agent's pane in a Tern window; ``view`` asks the page to open a view
    instead (the herdr view when Tern is not installed).
GET  /api/rika/stream  (WebSocket upgrade)
    Server-to-page JSON text messages: token, message, action, approval, tier, navigate
    and agents events. Read-only, so like every upgrade it is guarded by the exact Host
    and Origin and the UI secret as a subprotocol (kitty.web_auth); a page that falls
    behind is dropped and reloads history on reconnect.

Every mutation is header-protected like the rest of the API. Rika has no ingress route.
"""
from __future__ import annotations

import json
import queue
import re
import select

from kitty.core.rika.agents import AgentError
from kitty.core.rika.loop import Rika, RikaError
from kitty.core.rika import store
from kitty.web_socket import Closed, Reader, accept, close_code, frame, handshake_key, send_close

PREFIX = "/api/rika"
STREAM = PREFIX + "/stream"
_ID = r"([0-9a-f]{32})"
_AGENT = r"((?:herdr|tern):[A-Za-z0-9:_.-]{1,200})"


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
    return None


def post(app, conn, path, body):
    if not path.startswith(PREFIX + "/"):
        return None
    if path == PREFIX + "/messages":
        _keys(body, {"text", "tier", "conversation_id"}, {"text"})
        return _call(app.rika.submit, conn, body["text"], tier=body.get("tier"), conversation_id=body.get("conversation_id"))
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
    key = handshake_key(handler.headers)
    subscriber = app.rika.hub.subscribe()
    try:
        accept(handler, key)
        _pump(handler.connection, subscriber, app)
    finally:
        app.rika.hub.unsubscribe(subscriber)
    return True


def _pump(connection, subscriber: queue.Queue, app) -> None:
    """Send events until the page closes, falls behind or the server shuts down."""
    connection.settimeout(None)
    reader, code = Reader(), 1000
    hub = app.rika.hub
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
                        raise Closed(close_code(payload))
                    elif opcode in (1, 2):
                        raise Closed(1003)  # the stream only speaks server to page
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
    send_close(connection, code)

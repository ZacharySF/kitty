"""Paired clients (platform design Q7): the client ingress and Settings → Connected apps.

Outside programs (the kitty Tern plugin, the browser extension) use only the ingress
under ``/api/ingress``. It skips the page guard (Origin, ``X-Kitty-Request``), which
any local program can forge, and requires instead:

- the exact loopback ``Host`` (no DNS rebinding) and, when present, an ``Origin`` that
  is a browser extension's own origin; web pages are refused;
- ``Authorization: Bearer <token>`` on every route except pairing, reaching only the
  routes of the token's scopes; a token never reaches any other API route;
- for an extension, the ``Origin`` it paired from. CORS (with preflight) answers only
  the recorded origins of live paired extensions; pairing itself answers any
  extension origin. Extensions call from extension pages or the background, not from
  content scripts (Chrome's Local Network Access applies to those).

Ingress routes::

    POST /api/ingress/pair          {name, kind, scopes} -> 202 {status, request, code, expires_in}
    POST /api/ingress/pair/status   {request} -> {status: pending|denied|expired}
                                    or {status: approved, token, client} (once)
    GET  /api/ingress/client        any scope: this client
    POST /api/ingress/notes         capture: {text, tint?} -> quick note
    POST /api/ingress/inbox         capture: {blocks: [{type?, text}]} -> appended to the Inbox page
    POST /api/ingress/mirror        capture: {url, title, text?, selection?} -> page snapshot in the Inbox
    POST /api/ingress/mirror/frame  capture: {tab, url, title, text?, selection?, image?} -> live mirror frame
                                    (memory only, pushed to every window; see kitty.web_mirror)
    POST /api/ingress/mirror/stop   capture: {tab} -> that tab's mirror is dropped at once
    GET  /api/ingress/recent        read: ?limit=1-50 -> {inbox, pages, tasks} titles
    /api/ingress/agent/*            agent tabs (kitty.web_agents): the launch exchange and the
                                    mediated block API; agent tokens reach nothing else here

Page routes (behind the normal page guard)::

    GET  /api/clients               {clients, requests, address}
    POST /api/clients/approve|deny  {id} of a waiting request
    POST /api/clients/revoke        {id} of a paired client
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from kitty import web_mirror
from kitty.core import clients, endpoint, ingress, mirror
from kitty.core.blocks.actor import Actor, acting
from kitty.db import connect

INGRESS = "/api/ingress"
PAIR = endpoint.PAIRING_PATH
PAIR_STATUS = PAIR + "/status"
#: (method, path) -> the scope it needs; None means any paired client.
ROUTES: dict[tuple[str, str], str | None] = {
    ("GET", INGRESS + "/client"): None,
    ("POST", INGRESS + "/notes"): "capture",
    ("POST", INGRESS + "/inbox"): "capture",
    ("POST", INGRESS + "/mirror"): "capture",
    ("POST", INGRESS + "/mirror/frame"): "capture",
    ("POST", INGRESS + "/mirror/stop"): "capture",
    ("GET", INGRESS + "/recent"): "read",
}
CAPTURES = {INGRESS + "/notes": ingress.note, INGRESS + "/inbox": ingress.append, INGRESS + "/mirror": ingress.mirror}
MIRROR_FRAMES = {INGRESS + "/mirror/frame": web_mirror.frame, INGRESS + "/mirror/stop": web_mirror.stop_tab}
#: The request headers a client may send cross-origin.
CORS_HEADERS = ("Authorization", "Content-Type")
MAX_BODY = 2 * 1024 * 1024


def is_ingress(raw_path: str) -> bool:
    path = raw_path.partition("?")[0]
    return path == INGRESS or path.startswith(INGRESS + "/")


def start(app) -> None:
    app.pairing = clients.Pairing()


def _error(exc: clients.ClientError):
    from kitty.web_server import APIError

    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else {}
    return APIError(exc.status, str(exc), headers=headers)


def _state(app, conn) -> dict:
    # The address is what a person pastes into the browser extension, which cannot read
    # .private/endpoint.json.
    return {"clients": clients.listing(conn), "requests": app.pairing.waiting(), "address": app.origin}


def get(app, conn, path, query):
    if path != "/api/clients":
        return None
    app._no_query(query)
    return _state(app, conn)


def post(app, conn, path, body):
    from kitty.web_server import APIError

    action = path.removeprefix("/api/clients/")
    if not path.startswith("/api/clients/") or action not in {"approve", "deny", "revoke"}:
        return None
    if set(body) != {"id"}:
        raise APIError(400, "Expected {\"id\"}")
    try:
        if action == "revoke":
            clients.revoke(conn, body["id"])
            web_mirror.revoked(app, body["id"])
        else:
            app.pairing.decide(body["id"], approve=action == "approve")
    except clients.ClientError as exc:
        raise _error(exc) from None
    return _state(app, conn)


def _origin(handler) -> str | None:
    """The request's browser extension origin, or None for a program outside a browser."""
    from kitty.web_server import APIError

    if handler.headers.get_all("Host", []) != [handler.server.authority]:
        raise APIError(403, "Use the server's loopback address")
    origins = handler.headers.get_all("Origin", [])
    if not origins:
        return None
    if len(origins) != 1 or not clients.extension_origin(origins[0]):
        raise APIError(403, "Web pages cannot reach the client ingress")
    return origins[0]


def _allow(handler, origin: str) -> None:
    handler.response_headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin"})


def _preflight(handler, app, path: str, origin: str | None) -> None:
    from kitty.web_server import APIError

    if origin is None:
        raise APIError(403, "Cross-origin preflight needs a browser extension origin")
    if path in {PAIR, PAIR_STATUS}:
        methods, allowed = {"POST"}, True
    else:
        methods = {method for method, route in ROUTES if route == path}
        with closing(connect(app.settings.db_path)) as conn:
            allowed = bool(methods) and clients.allowed_origin(conn, origin)
    method = handler.headers.get("Access-Control-Request-Method", "")
    requested = {item.strip().lower() for item in handler.headers.get("Access-Control-Request-Headers", "").split(",")}
    requested.discard("")
    if not allowed or method not in methods or not requested <= {item.lower() for item in CORS_HEADERS}:
        raise APIError(403, "This origin, method or header is not allowed here")
    handler.send_response(204)
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Access-Control-Allow-Methods", ", ".join(sorted(methods)))
    handler.send_header("Access-Control-Allow-Headers", ", ".join(CORS_HEADERS))
    handler.send_header("Access-Control-Max-Age", "600")
    if handler.headers.get("Access-Control-Request-Private-Network") == "true":
        handler.send_header("Access-Control-Allow-Private-Network", "true")
    handler.send_header("Vary", "Origin")
    handler.send_header("Content-Length", "0")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()


def _body(handler, limit: int = MAX_BODY) -> dict:
    from kitty.web_server import APIError

    kind = handler.headers.get_all("Content-Type", [])
    if len(kind) != 1 or kind[0].partition(";")[0].strip().lower() != "application/json":
        raise APIError(415, "Use application/json")
    lengths = handler.headers.get_all("Content-Length", [])
    if len(lengths) == 1 and lengths[0].isdigit() and int(lengths[0]) > limit:
        raise APIError(413, "Request body is too large")
    return handler._body()


def _token(handler) -> str | None:
    values = handler.headers.get_all("Authorization", [])
    if len(values) != 1 or not values[0].startswith("Bearer "):
        return None
    return values[0].removeprefix("Bearer ").strip()


def _limit(query: dict[str, str]) -> int:
    from kitty.web_server import APIError, _integer

    if set(query) - {"limit"}:
        raise APIError(400, "Only limit is accepted")
    return _integer(query["limit"], "limit", 1, 50) if "limit" in query else 20


def ingress_request(handler) -> None:
    """Serve one request under /api/ingress (errors propagate to the handler's dispatcher)."""
    from kitty.web_server import APIError

    app = handler.server.app
    origin = _origin(handler)
    path, query = handler._target()
    if handler.command == "OPTIONS":
        _preflight(handler, app, path, origin)
        return
    if handler.command not in {"GET", "POST"}:
        raise APIError(405, "Use GET or POST")
    try:
        if path.startswith(INGRESS + "/agent/"):
            from kitty import web_agents

            web_agents.ingress(handler, path, query)
            return
        if path in {PAIR, PAIR_STATUS}:
            if origin is not None:
                _allow(handler, origin)
            if handler.command != "POST":
                raise APIError(405, "Use POST")
            app._no_query(query)
            body = _body(handler)
            if path == PAIR:
                if isinstance(body, dict) and body.get("kind") == "tern-plugin":
                    _require_tern_plugin(app)
                handler._json(202, app.pairing.request(body, origin))
                return
            if set(body) != {"request"}:
                raise APIError(400, "Expected {\"request\"}")
            with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
                result = app.pairing.status(conn, body["request"], origin)
            handler._json(200, result)
            return
        unauthorized = APIError(401, "A paired client token is required", headers={"WWW-Authenticate": 'Bearer realm="kitty"'})
        token = _token(handler)
        if token is None:
            raise unauthorized
        with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
            client = clients.authenticate(conn, token)
        if client is None:
            raise unauthorized
        if client["kind"] == clients.AGENT_KIND:
            raise APIError(403, "Agent tokens reach only /api/ingress/agent")
        if client["origin"] != origin:
            raise APIError(403, "This token works only from the origin that paired it")
        if client["kind"] == "tern-plugin":
            _require_tern_plugin(app)
        if origin is not None:
            _allow(handler, origin)
        route = (handler.command, path)
        if route not in ROUTES:
            raise APIError(404, "Ingress route not found")
        scope = ROUTES[route]
        if scope is not None and scope not in client["scopes"]:
            raise APIError(403, f"This client was not granted the {scope} scope")
        if path == INGRESS + "/client":
            app._no_query(query)
            handler._json(200, client)
            return
        app.require_module("workspace")
        if path == INGRESS + "/recent":
            limit = _limit(query)
            with closing(connect(app.settings.db_path)) as conn:
                result = ingress.recent(conn, limit)
            handler._json(200, result)
            return
        app._no_query(query)
        if path in MIRROR_FRAMES:
            # Live frames stay in memory (kitty.core.mirror): no database, no mutation lock.
            body = _body(handler, mirror.MAX_FRAME_BODY)
            handler._json(200, MIRROR_FRAMES[path](app, client, body))
            return
        body = _body(handler)
        # The capture's block transactions are the paired client's (its pairing names it).
        with app.mutation_lock, closing(connect(app.settings.db_path)) as conn, \
                acting(Actor("client", client["name"] or client["kind"], client["id"])):
            result = CAPTURES[path](conn, body)
        app.mutated()
        handler._json(200, result)
    except clients.ClientError as exc:
        raise _error(exc) from None
    except sqlite3.IntegrityError:
        raise APIError(409, "The request conflicts with saved data; try again") from None


def _require_tern_plugin(app) -> None:
    """The build's ``settings.tern.plugin`` (Q8): off refuses the kitty Tern plugin's pairing
    and captures, without revoking it, so turning it back on needs no new pairing."""
    from kitty.core.builds import project
    from kitty.web_server import APIError

    with closing(connect(app.settings.db_path)) as conn:
        if not project.tern_plugin_enabled(conn):
            raise APIError(403, "The kitty Tern plugin is turned off in this rika!'s build (Settings > Build)")

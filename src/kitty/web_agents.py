"""Agent mediation over HTTP: the launch exchange, the mediated block API, Settings → Agents.

Launcher and agent routes live under the client ingress (kitty.web_clients: exact
loopback Host, no page guard) and refuse any ``Origin``: only programs call them, never
a browser page::

    POST /api/ingress/agent/launch    {nonce} -> the launch spec (kitty.core.agent_sessions)
    POST /api/ingress/agent/exchange  {nonce, pid, report} -> {run, session, guarantee, message, env}
    GET  /api/ingress/agent/session   Bearer agent token -> {session, name, kind, scopes, guarantee, tools}
    POST /api/ingress/agent/call      Bearer agent token, {tool, arguments} -> the tool's result
                                      (kitty.core.agent_access: journaled in the same transaction)

Page routes (UI secret, like every page route)::

    GET  /api/agents                      {host, sessions, policy, versions, retention, purposes}
    GET  /api/agents/session?id=&before=  {session, journal, next, held, planted}
    POST /api/agents/policy/simulate      {source, session?} -> flips under the draft
    POST /api/agents/policy/apply         {source} -> {version, policy}
    POST /api/agents/sessions/hold        {id, hold: bool} -> retention user hold on or off
    POST /api/agents/sessions/end         {id} -> the token is revoked
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kitty.core import agent_access, agent_policy, agent_sessions
from kitty.core.blocks import BlockError, history, retention
from kitty.db import connect

PREFIX = "/api/agents"
INGRESS = "/api/ingress/agent"
MAX_BODY = 4 * 1024 * 1024


def _error(status: int, message: str, problems: list[str] | None = None):
    from kitty.web_server import APIError
    return APIError(status, message, problems=problems)


def _herdr_dir(app) -> Path | None:
    terminal = getattr(app, "terminal", None)
    if terminal is None:
        return None
    from kitty.core.terminal import herdr_config_dir

    return herdr_config_dir(terminal.runtime.environ) / "sessions"


def _exceptions(app):
    """Leaves inside .private an agent tab still reads: rika!'s activity hook and omp extension
    (kitty.web_agent_skills; they report through herdr's socket, which a Full tab cannot
    reach, so they stay silent there) and the tab's startup instructions (kitty.core.terminal)."""
    from kitty.web_agent_skills import EXTENSION, HOOK

    def leaves(kind: str) -> list[tuple[str, str]]:
        private = app.settings.root / ".private"
        return [(str(private / "agents" / HOOK), "read"), (str(private / "agents" / EXTENSION), "read"),
                (str(private / "herdr" / "SKILL.md"), "read")]
    return leaves


def start(app) -> None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    home = os.environ.get("HOME")

    def port() -> int | None:
        origin = getattr(app, "origin", None)
        return int(origin.rsplit(":", 1)[1]) if origin else None

    app.agent_launches = agent_sessions.Launches(
        app.settings, port=port, herdr_dir=lambda: _herdr_dir(app), runtime_dir=Path(runtime) if runtime else None,
        home=Path(home) if home else None, exceptions=_exceptions(app))


# ------------------------------------------------------------------------- ingress

def _bearer(handler) -> str | None:
    values = handler.headers.get_all("Authorization", [])
    if len(values) != 1 or not values[0].startswith("Bearer "):
        return None
    return values[0].removeprefix("Bearer ").strip()


def ingress(handler, path: str, query: dict) -> None:
    """Serve one request under /api/ingress/agent (errors propagate to the dispatcher)."""
    from kitty.web_clients import _body

    app = handler.server.app
    if handler.headers.get_all("Origin", []):
        raise _error(403, "Agent routes are for programs in an agent tab, not web pages")
    app._no_query(query)
    if path in {INGRESS + "/launch", INGRESS + "/exchange"}:
        if handler.command != "POST":
            raise _error(405, "Use POST")
        body = _body(handler)
        try:
            with closing(connect(app.settings.db_path)) as conn:
                if path.endswith("/launch"):
                    if set(body) != {"nonce"}:
                        raise _error(400, "Expected {\"nonce\"}")
                    result = app.agent_launches.spec(conn, body["nonce"])
                else:
                    if set(body) != {"nonce", "pid", "report"}:
                        raise _error(400, "Expected {\"nonce\", \"pid\", \"report\"}")
                    # No mutation lock: the tab's start (which holds it) waits for this exchange.
                    result = app.agent_launches.exchange(conn, body["nonce"], pid=body["pid"], report=body["report"],
                                                         peer_port=handler.client_address[1],
                                                         server_port=handler.server.server_address[1])
        except agent_sessions.LaunchError as exc:
            raise _error(exc.status, str(exc)) from None
        handler._json(200, result)
        return
    unauthorized = _error(401, "An agent token is required")
    unauthorized.headers["WWW-Authenticate"] = 'Bearer realm="kitty-agent"'
    token = _bearer(handler)
    if token is None:
        raise unauthorized
    with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
        session = app.agent_launches.authenticate(conn, token)
    if session is None:
        raise unauthorized
    if path == INGRESS + "/session":
        if handler.command != "GET":
            raise _error(405, "Use GET")
        handler._json(200, {"session": session["id"], "name": session["name"], "kind": session["kind"],
                            "scopes": session["scopes"], "guarantee": session["guarantee"],
                            "modules": list(app.modules),
                            "tools": [name for name, tool in agent_access.TOOLS.items() if tool.scope in session["scopes"]]})
        return
    if path != INGRESS + "/call":
        raise _error(404, "Agent route not found")
    if handler.command != "POST":
        raise _error(405, "Use POST")
    app.require_module("workspace")
    body = _body(handler, MAX_BODY)
    if not isinstance(body, dict) or set(body) - {"tool", "arguments"} or "tool" not in body:
        raise _error(400, "Expected {\"tool\", \"arguments\"}")
    try:
        with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
            result = agent_access.call(conn, session, body["tool"], body.get("arguments") or {})
    except BlockError as exc:
        raise _error(exc.status, str(exc)) from None
    if agent_access.TOOLS[body["tool"]].op == "write":
        app.mutated()
    handler._json(200, result)


# ---------------------------------------------------------------------- page routes

def _cutoff(conn: sqlite3.Connection) -> str:
    return (datetime.now(UTC) - timedelta(days=history.retention_days(conn))).isoformat()


def _overview(app, conn: sqlite3.Connection) -> dict:
    version, policy = agent_policy.active(conn)
    return {"host": agent_sessions.host_guarantee(), "sessions": agent_sessions.listing(conn),
            "policy": {"version": version, "mode": policy["mode"], "source": agent_policy.source(conn)},
            "versions": agent_policy.versions(conn), "retention": retention.status(conn, _cutoff(conn)),
            "purposes": agent_policy.PURPOSES, "tools": {name: {"op": tool.op, "scope": tool.scope}
                                                         for name, tool in agent_access.TOOLS.items()}}


def get(app, conn, path, query):
    if path == PREFIX:
        app._no_query(query)
        return _overview(app, conn)
    if path == PREFIX + "/session":
        if set(query) - {"id", "before"} or "id" not in query:
            raise _error(400, "Expected ?id= (and optionally before=)")
        before = None
        if "before" in query:
            from kitty.web_server import _integer

            before = _integer(query["before"], "before", 1, 2**62)
        try:
            return agent_sessions.detail(conn, query["id"], before=before)
        except LookupError as exc:
            raise _error(404, str(exc)) from None
    return None


def _source(body: dict, allowed: set[str]) -> str:
    if not isinstance(body, dict) or set(body) - allowed or not isinstance(body.get("source"), str):
        raise _error(400, "Expected {\"source\": policy YAML}")
    return body["source"]


def post(app, conn, path, body):
    if not path.startswith(PREFIX + "/"):
        return None
    try:
        if path == PREFIX + "/policy/simulate":
            source = _source(body, {"source", "session"})
            session = body.get("session")
            if session is not None and not isinstance(session, str):
                raise _error(400, "session is an agent session ID")
            return agent_policy.simulate(conn, source, session=session)
        if path == PREFIX + "/policy/apply":
            source = _source(body, {"source"})
            result = agent_policy.apply(conn, source)
            return {**result, **_overview(app, conn)}
        if path in {PREFIX + "/sessions/hold", PREFIX + "/sessions/end"}:
            session = body.get("id") if isinstance(body, dict) else None
            if not isinstance(session, str):
                raise _error(400, "Expected {\"id\"}")
            if path.endswith("/hold"):
                if set(body) != {"id", "hold"} or type(body["hold"]) is not bool:
                    raise _error(400, "Expected {\"id\", \"hold\": true|false}")
                retention.hold(conn, session) if body["hold"] else retention.release(conn, session)
            else:
                if set(body) != {"id"}:
                    raise _error(400, "Expected {\"id\"}")
                agent_sessions.end(conn, session, "Ended in Settings → Agents; its token was revoked")
            return agent_sessions.detail(conn, session)
    except agent_policy.PolicyError as exc:
        raise _error(400, "The policy has problems", exc.problems) from None
    except LookupError as exc:
        raise _error(404, str(exc)) from None
    raise _error(404, "API route not found")

"""Settings → Account: the Microsoft (Outlook) and Slack connections (kitty.core.connect).

Page routes (behind the normal page guard; mutations behind the mutation guard)::

    GET  /api/connect                          {demo, microsoft: {...}, slack: {...}}
    POST /api/connect/microsoft/config         {client_id}
    POST /api/connect/slack/config             {client_id?, reaction?}
    POST /api/connect/<provider>/start         {}          -> {url, ticket}
    POST /api/connect/<provider>/poll          {ticket}    -> {status: pending | connected | error, error?}
    POST /api/connect/<provider>/cancel        {ticket}
    POST /api/connect/<provider>/sync          {}          -> state + {result}
    POST /api/connect/<provider>/disconnect    {}

Both are read-only: Outlook calendars and inbox come in, and a Slack reaction becomes
a task; rika! sends, posts and changes nothing there. Connecting opens the provider's
sign-in in the browser and finishes on a loopback listener (PKCE, no client secret).
A connected account is synced when it connects, on Sync now, and every few minutes.
Nothing here runs for the demo workspace.
"""
from __future__ import annotations

import logging
import re
import threading
from contextlib import closing

from kitty.core import connect
from kitty.core.connect import ConnectError, microsoft, slack
from kitty.db.connection import connect as db_connect

log = logging.getLogger(__name__)

PREFIX = "/api/connect"
_ROUTE = re.compile(re.escape(PREFIX) + r"/(microsoft|slack)/(config|start|poll|cancel|sync|disconnect)")
MODULES = {"microsoft": microsoft, "slack": slack}
#: Seconds between background syncs of connected accounts.
INTERVAL = 300.0
DEMO = "Connections are off in the demo workspace"


def start(app) -> None:
    app.connect_flows = {provider: connect.Loopback(connect.LABELS[provider]) for provider in connect.PROVIDERS}
    app.connect_halted = threading.Event()
    app.connect_syncer = None
    if not app.settings.demo:
        app.connect_syncer = threading.Thread(target=_syncer, args=(app,), daemon=True, name="kitty-connect-sync")
        app.connect_syncer.start()


def stop(app) -> None:
    app.connect_halted.set()
    for flow in app.connect_flows.values():
        flow.close()
    if app.connect_syncer is not None:
        app.connect_syncer.join(timeout=2)
        app.connect_syncer = None


def _syncer(app) -> None:
    while not app.connect_halted.wait(INTERVAL):
        for provider in connect.PROVIDERS:
            try:
                with app.mutation_lock, closing(db_connect(app.settings.db_path)) as conn:
                    if not _connected(app, conn, provider):
                        continue
                    _sync(app, conn, provider)
                app.mutated()
            except ConnectError:
                pass  # recorded on the account row, shown in Settings
            except Exception:  # noqa: BLE001 - the syncer must survive one bad round
                log.exception("Syncing %s failed", provider)


def _connected(app, conn, provider: str) -> bool:
    return connect.account(conn, provider) is not None and connect.load_token(app.settings, provider) is not None


def _sync(app, conn, provider: str) -> dict:
    """One sync, recorded on the account row. A sign-in the provider no longer accepts is dropped."""
    try:
        result = MODULES[provider].sync(app.settings, conn)
    except ConnectError as exc:
        if exc.status == 401:
            connect.drop_token(app.settings, provider)
        connect.record_sync(conn, provider, str(exc))
        raise
    connect.record_sync(conn, provider, None)
    return result


def _one(app, conn, provider: str) -> dict:
    row = connect.account(conn, provider)
    return {"client_id": connect.config(conn, f"{provider}_client_id"), "connected": _connected(app, conn, provider),
            "account": row["account"] if row else None, "last_sync": row["last_sync"] if row else None,
            "error": row["error"] if row else None}


def _state(app, conn) -> dict:
    return {"demo": bool(app.settings.demo),
            "microsoft": {**_one(app, conn, "microsoft"), "redirect": "http://localhost"},
            "slack": {**_one(app, conn, "slack"), "reaction": slack.reaction(conn), "manifest": slack.manifest(),
                      "redirect": slack.REDIRECT}}


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return _state(app, conn)


def _configure(app, conn, provider: str, body: dict) -> None:
    allowed = {"client_id"} | ({"reaction"} if provider == "slack" else set())
    if not body or set(body) - allowed or any(not isinstance(value, str) for value in body.values()):
        raise ConnectError(400, "Expected {" + ", ".join(sorted(allowed)) + "}")
    if "client_id" in body:
        value = body["client_id"].strip()
        if _connected(app, conn, provider):
            raise ConnectError(409, "Disconnect before changing the client ID")
        if value and not MODULES[provider].CLIENT_ID.fullmatch(value):
            raise ConnectError(400, "That is not a client ID: copy the Application (client) ID of your app registration"
                               if provider == "microsoft" else
                               "That is not a client ID: copy Client ID from your Slack app's Basic Information page")
        connect.set_config(conn, f"{provider}_client_id", value)
    if "reaction" in body:
        value = body["reaction"].strip().strip(":").lower()
        if not slack.REACTION.fullmatch(value):
            raise ConnectError(400, "Name one Slack emoji, like pushpin")
        connect.set_config(conn, "slack_reaction", value)


def _start(app, conn, provider: str) -> dict:
    client_id = connect.config(conn, f"{provider}_client_id")
    if not client_id:
        raise ConnectError(409, "Save your app's client ID first")
    module = MODULES[provider]

    def finish(code: str, verifier: str, redirect: str) -> None:
        found = module.exchange(app.settings, client_id, code, verifier, redirect)
        with app.mutation_lock, closing(db_connect(app.settings.db_path)) as own:
            connect.save_account(own, provider, found["account"], found["scopes"])
            try:
                _sync(app, own, provider)
            except ConnectError:
                pass  # connected; the row carries why the first sync failed
        app.mutated()

    def authorize(redirect: str, state: str, challenge: str) -> str:
        return module.authorize_url(client_id, redirect, state, challenge)

    if provider == "slack":
        return app.connect_flows[provider].start(authorize, finish, port=slack.PORT, path=slack.CALLBACK)
    return app.connect_flows[provider].start(authorize, finish)


def post(app, conn, path, body):
    from kitty.web_server import APIError

    match = _ROUTE.fullmatch(path)
    if match is None:
        return None
    provider, action = match.groups()
    if app.settings.demo:
        raise APIError(403, DEMO)
    flow = app.connect_flows[provider]
    try:
        if action == "config":
            _configure(app, conn, provider, body)
            return _state(app, conn)
        if action in {"poll", "cancel"}:
            if set(body) != {"ticket"} or not isinstance(body["ticket"], str) or len(body["ticket"]) > 100:
                raise ConnectError(400, "Expected a sign-in ticket")
            if action == "cancel":
                flow.cancel(body["ticket"])
                return {"status": "cancelled"}
            return flow.poll(body["ticket"])
        if body:
            raise ConnectError(400, "Expected an empty object")
        if action == "start":
            return _start(app, conn, provider)
        if action == "disconnect":
            flow.close()
            MODULES[provider].disconnect(app.settings, conn)
            return _state(app, conn)
        if not _connected(app, conn, provider):
            raise ConnectError(409, f"Connect {connect.LABELS[provider]} first")
        try:
            result = _sync(app, conn, provider)
        except ConnectError as exc:
            # 401 would read as rika!'s own page guard; an expired provider sign-in is a conflict.
            raise ConnectError(409 if exc.status == 401 else exc.status, str(exc)) from None
        return {**_state(app, conn), "result": result}
    except ConnectError as exc:
        raise APIError(exc.status, str(exc)) from None

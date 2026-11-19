"""Settings → Connected apps → Phone: turn the phone surface on and add phones.

Page routes (behind the normal page guard; mutations behind the mutation guard)::

    GET  /api/phone                    {network, port, addresses, listening, address, error, phones}
    POST /api/phone/network            {network: off | wifi | tailscale}
    POST /api/phone/devices            {name, scopes}   -> state + {setup: {url, code, expires_in}}
    POST /api/phone/devices/remove     {id}

The surface itself (kitty.core.phone) is a second listener that serves only
``/phone/v1/*``; it is off until a network is chosen, and never runs for the demo
workspace. Adding a phone creates its token and a five-minute setup code; the phone
opens the setup address once and is shown the token there, so the token never appears
in the desktop window.
"""
from __future__ import annotations

import time
from contextlib import closing
from datetime import UTC, datetime, timedelta

from kitty.core import phone
from kitty.db.connection import connect

PREFIX = "/api/phone"


def _services(app) -> phone.Services:
    def capture(conn, body: dict) -> str:
        from kitty.core import ingress

        app.require_module("workspace")
        line = phone.capture_line(body)
        with app.mutation_lock:
            ingress.append(conn, {"blocks": [{"text": line}]})
        app.mutated()
        return "Added to your Inbox"

    def ask(conn, text: str) -> str:
        rika = getattr(app, "rika", None)
        if rika is None:
            raise phone.PhoneError("Rika is not running", 503)
        with app.mutation_lock:
            sent = rika.submit(conn, text)
        deadline = time.monotonic() + phone.RIKA_WAIT
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT text, status FROM rika_messages WHERE reply_to=? AND role='rika' ORDER BY created_at DESC LIMIT 1",
                (sent["message_id"],)).fetchone()
            if row is not None and row["status"] != "pending" and row["text"].strip():
                return row["text"].strip() if row["status"] == "done" else "Rika could not do that; it is in rika!"
            time.sleep(0.25)
        return "Rika is on it; the answer will be in rika!"

    def today(conn) -> dict:
        from kitty.core import calendar

        app.require_module("workspace")
        start = datetime.now(UTC)
        events = calendar.occurrences(conn, start.isoformat(), (start + timedelta(days=1)).isoformat(), applyai="applyai" in app.modules)
        tasks = [task for task in calendar.tasks(conn) if task["status"] in {"todo", "doing"}]
        return {"events": [{"title": event["title"], "start": event["start"], "all_day": bool(event.get("all_day"))} for event in events[:5]],
                "tasks": [{"title": task["title"], "due": task.get("due")} for task in tasks[:5]],
                "open_tasks": len(tasks)}

    return phone.Services(connect=lambda: connect(app.settings.db_path), capture=capture, ask=ask, today=today)


def _listen(app) -> None:
    """Start, move or stop the listener to match the saved network; records why it could not."""
    current = getattr(app, "phone_listener", None)
    if current is not None:
        current.stop()
        app.phone_listener = None
    app.phone_error = None
    if app.settings.demo:
        return
    with closing(connect(app.settings.db_path)) as conn:
        network = phone.network(conn)
    if network == "off":
        return
    host = phone.addresses()[network]
    if host is None:
        app.phone_error = "This computer is not on Tailscale" if network == "tailscale" else "This computer has no Wi-Fi or wired address"
        return
    try:
        app.phone_listener = phone.PhoneListener(host, _services(app), app.phone_setup)
        app.phone_listener.start()
    except OSError as error:
        app.phone_error = f"Port {phone.PORT} could not be opened: {error.strerror or error}"


def start(app) -> None:
    app.phone_setup = phone.Setup()
    app.phone_listener = None
    app.phone_error = None
    try:
        _listen(app)
    except Exception as error:  # noqa: BLE001 - the phone surface never stops rika! from starting
        app.phone_error = str(error)


def stop(app) -> None:
    listener = getattr(app, "phone_listener", None)
    if listener is not None:
        listener.stop()
        app.phone_listener = None


def _state(app, conn) -> dict:
    listener = app.phone_listener
    return {"network": phone.network(conn), "port": phone.PORT, "addresses": phone.addresses(),
            "listening": listener is not None, "address": f"http://{listener.authority}" if listener else None,
            "error": "The phone surface is off in the demo workspace" if app.settings.demo else app.phone_error,
            "phones": phone.listing(conn)}


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return _state(app, conn)


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if path not in {PREFIX + "/network", PREFIX + "/devices", PREFIX + "/devices/remove"}:
        return None
    try:
        if path == PREFIX + "/network":
            if set(body) != {"network"}:
                raise APIError(400, "Expected {network}")
            phone.set_network(conn, body["network"])
            conn.commit()
            _listen(app)
            return _state(app, conn)
        if path == PREFIX + "/devices/remove":
            if set(body) != {"id"}:
                raise APIError(400, "Expected {id}")
            phone.revoke(conn, body["id"])
            return _state(app, conn)
        if set(body) != {"name", "scopes"}:
            raise APIError(400, "Expected {name, scopes}")
        if app.phone_listener is None:
            raise APIError(409, "Choose a network for the phone first")
        added, token = phone.create(conn, body["name"], body["scopes"])
        setup = app.phone_setup.issue(token, added["name"])
        return {**_state(app, conn), "setup": {**setup, "url": f"http://{app.phone_listener.authority}/phone/v1/setup/{setup['code']}"}}
    except phone.PhoneError as error:
        raise APIError(error.status, str(error)) from None

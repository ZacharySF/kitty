"""Optional Google identity connection (Settings → Account).

``GET /api/account`` and ``POST /api/account/onboarding`` (the welcome and tutorial flags)
are answered by rika-server (server/src/account.rs), which learns ``configured`` from this
worker's readiness line. The sign-in stays here: its pending attempt, PKCE verifier and
loopback callback live in this process's ``GoogleConnection``.

    POST /api/account/google/start {}       -> {url, ticket}
    POST /api/account/google/poll {ticket}  -> {status, identity?}; a connected identity is saved
    POST /api/account/google/cancel {ticket}
    POST /api/account/google/disconnect {}  -> the account state, the identity forgotten
"""
from __future__ import annotations

import os

from kitty.google_connection import ConnectionError, GoogleConnection


def start(app):
    app.google_connection = GoogleConnection(os.environ.get("KITTY_GOOGLE_CLIENT_ID", "").strip(),
                                             os.environ.get("KITTY_GOOGLE_CLIENT_SECRET", "").strip())


def close(app):
    app.google_connection.close()


def state(app, conn):
    row = conn.execute("SELECT * FROM workspace_onboarding WHERE id=1").fetchone()
    return {
        "configured": app.google_connection.configured,
        "identity": {"sub": row["google_sub"], "email": row["google_email"], "name": row["google_name"]} if row["google_sub"] else None,
        "onboarding": {"welcome_completed": bool(row["welcome_completed"]), "tutorial_completed": bool(row["tutorial_completed"])},
    }


def get(app, conn, path, query):
    return None


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if not path.startswith("/api/account/google/"):
        return None
    try:
        if path in {"/api/account/google/start", "/api/account/google/disconnect"}:
            if body:
                raise APIError(400, "Expected an empty object")
            if path.endswith("start"):
                if not app.google_connection.configured:
                    raise APIError(503, "Google connection is not configured. You can continue locally.")
                return app.google_connection.start()
            app.google_connection.close()
            conn.execute("UPDATE workspace_onboarding SET google_sub=NULL, google_email=NULL, google_name=NULL WHERE id=1")
            conn.commit()
            return state(app, conn)
        if path in {"/api/account/google/poll", "/api/account/google/cancel"}:
            if set(body) != {"ticket"} or not isinstance(body["ticket"], str) or len(body["ticket"]) > 100:
                raise APIError(400, "Expected a sign-in ticket")
            if path.endswith("cancel"):
                app.google_connection.cancel(body["ticket"])
                return {"status": "cancelled"}
            result = app.google_connection.poll(body["ticket"])
            if result["status"] == "connected":
                identity = result["identity"]
                conn.execute("UPDATE workspace_onboarding SET google_sub=?, google_email=?, google_name=? WHERE id=1",
                             (identity["sub"], identity["email"], identity["name"]))
                conn.commit()
            return result
    except ConnectionError as exc:
        raise APIError(400, str(exc)) from None
    return None

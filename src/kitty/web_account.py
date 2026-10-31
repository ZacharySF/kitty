"""Local welcome/tutorial preferences and optional Google identity connection."""
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
    if path != "/api/account":
        return None
    app._no_query(query)
    return state(app, conn)


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if not path.startswith("/api/account/"):
        return None
    try:
        if path == "/api/account/onboarding":
            if (not body or not set(body) <= {"welcome_completed", "tutorial_completed"}
                    or any(type(value) is not bool for value in body.values())):
                raise APIError(400, "Expected welcome_completed or tutorial_completed booleans")
            for key, value in body.items():
                conn.execute(f"UPDATE workspace_onboarding SET {key}=? WHERE id=1", (int(value),))
            conn.commit()
            return state(app, conn)
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

"""Fixed-origin external LearnHarness app, with an optional bundled runtime owner."""
from __future__ import annotations

import sys
from http.client import HTTPConnection, HTTPException
from pathlib import Path

URL = "http://127.0.0.1:3210"


def start(app):
    from kitty.learnharness_runtime import LearnHarnessRuntime

    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "desktop/.build"))
    app.learnharness = LearnHarnessRuntime(bundle / "learnharness", app.settings.data_dir / "learnharness")


def stop(app):
    app.learnharness.close()


def get(app, conn, path, query):
    if path != "/api/extensions/learnharness":
        return None
    app._no_query(query)
    available = False
    connection = HTTPConnection("127.0.0.1", 3210, timeout=2)
    try:
        # No redirects, response bodies, cookies, credentials or Kitty data.
        connection.request("HEAD", "/")
        available = connection.getresponse().status == 200
    except (OSError, HTTPException):
        pass
    finally:
        connection.close()
    return {"url": URL, "available": available}


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if path != "/api/extensions/learnharness/start":
        return None
    if body:
        raise APIError(400, "Start does not accept settings, URLs or credentials")
    try:
        app.learnharness.open()
    except RuntimeError as error:
        raise APIError(503, str(error)) from None
    return {"url": URL, "available": True}


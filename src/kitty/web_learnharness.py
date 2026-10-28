"""Optional external LearnHarness app: availability only, never a data proxy."""
from __future__ import annotations

from http.client import HTTPConnection, HTTPException

URL = "http://127.0.0.1:3210"


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
    return None

"""Settings → Keys (platform design Q10): status of each key, never its value.

Page routes (behind the normal page guard; mutations behind the mutation guard)::

    GET  /api/keys          {keys: [{name, label, env, custom, set, backend, in_file, error}],
                             keyring, keyring_label, store}
    POST /api/keys/set      {name, value} -> the same status (the value is never echoed)
    POST /api/keys/remove   {name}
    POST /api/keys/move     {name}: its owner-only file copy into the OS keyring

A value arrives once in a request body and goes straight to the keyring or the
owner-only file; it is never logged, echoed or included in an error message.
"""
from __future__ import annotations

from kitty.core import keys

PREFIX = "/api/keys"


def _store(app) -> keys.Keys:
    return keys.for_settings(app.settings)


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return _store(app).status()


def post(app, conn, path, body):
    from kitty.web_server import APIError

    action = path.removeprefix(PREFIX + "/")
    if not path.startswith(PREFIX + "/") or action not in {"set", "remove", "move"}:
        return None
    expected = {"name", "value"} if action == "set" else {"name"}
    if set(body) != expected:
        raise APIError(400, "Expected {\"name\", \"value\"}" if action == "set" else "Expected {\"name\"}")
    store = _store(app)
    try:
        if action == "set":
            store.set(body["name"], body["value"])
        elif action == "remove":
            store.remove(body["name"])
        else:
            store.move_to_keyring(body["name"])
    except keys.KeysError as exc:
        raise APIError(exc.status, str(exc)) from None
    return store.status()

"""Settings → Devices (platform design Q16): pair kittys and see how their sync is doing.

Page routes (behind the normal page guard; mutations behind the mutation guard)::

    GET  /api/devices                state: this device, the pairing code it shows,
                                     devices seen on the LAN, paired devices
    POST /api/devices/name           {name}
    POST /api/devices/pair/show      {}                show a pairing code (5 minutes)
    POST /api/devices/pair/cancel    {}
    POST /api/devices/pair/enter     {code, address}   pair with the device showing code
    POST /api/devices/sync           {device_id?}      sync now (one or every paired device)
    POST /api/devices/unpair         {device_id}
    POST /api/devices/address        {device_id, address | null}
    POST /api/devices/pause          {paused}

Every mutation answers with the new state. The service (kitty.core.sync.service)
starts with the server unless ``KITTY_SYNC=off``.
"""
from __future__ import annotations

from kitty.core import keys
from kitty.core.sync import peers, service

PREFIX = "/api/devices"
_BODIES = {
    "name": {"name"}, "pair/show": set(), "pair/cancel": set(), "pair/enter": {"code", "address"},
    "sync": None, "unpair": {"device_id"}, "address": {"device_id", "address"}, "pause": {"paused"},
}


def start(app) -> None:
    app.sync = service.from_environment(app.settings.db_path, keys.for_settings(app.settings))
    if app.sync is not None:
        app.sync.start()


def stop(app) -> None:
    sync = getattr(app, "sync", None)
    if sync is not None:
        sync.stop()


def _service(app) -> service.SyncService:
    from kitty.web_server import APIError

    sync = getattr(app, "sync", None)
    if sync is None:
        raise APIError(503, "Sync is off on this device (KITTY_SYNC=off)")
    return sync


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return _service(app).state()


def post(app, conn, path, body):
    from kitty.web_server import APIError

    action = path.removeprefix(PREFIX + "/")
    if not path.startswith(PREFIX + "/") or action not in _BODIES:
        return None
    expected = _BODIES[action]
    if expected is None:
        if set(body) - {"device_id"}:
            raise APIError(400, "Expected {} or {\"device_id\"}")
    elif set(body) != expected:
        raise APIError(400, "Expected " + (", ".join(sorted(expected)) or "an empty object"))
    sync = _service(app)
    try:
        if action == "name":
            sync.rename(body["name"])
        elif action == "pair/show":
            sync.show_code()
        elif action == "pair/cancel":
            sync.cancel_code()
        elif action == "pair/enter":
            sync.pair(body["code"], body["address"])
        elif action == "sync":
            sync.sync_now(body.get("device_id"))
        elif action == "unpair":
            sync.unpair(body["device_id"])
        elif action == "address":
            sync.set_address(body["device_id"], body["address"])
        else:
            sync.set_paused(body["paused"])
    except peers.PeersError as exc:
        raise APIError(exc.status, str(exc)) from None
    return sync.state()

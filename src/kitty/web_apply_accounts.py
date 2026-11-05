"""Settings → Apply → Accounts (proposal-07 Step 4): the account registry and family grants.

Page routes (owner windows only: every ``/api`` route needs the per-launch UI secret,
kitty.web_auth; agents and paired programs cannot reach them)::

    GET  /api/apply/accounts          {accounts: [...], grants: [...], caps, store,
                                            keyring, keyring_locked, warnings}
    POST /api/apply/accounts/grant    {family, granted} -> the same listing

No secret is ever listed: rows show the login email, state, password-policy profile
and where the password is stored (keyring or owner-only file), never the password.
"""
from __future__ import annotations

from kitty.core.apply import accounts
from kitty.core.apply.grants import FAMILIES

PREFIX = "/api/apply/accounts"


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return accounts.listing(conn, app.settings, app.config)


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if path != PREFIX + "/grant":
        return None
    if not isinstance(body, dict) or set(body) != {"family", "granted"}:
        raise APIError(400, "Expected {\"family\", \"granted\"}")
    if body["family"] not in FAMILIES or not isinstance(body["granted"], bool):
        raise APIError(400, "Unknown ATS family or grant value")
    accounts.set_grant(conn, body["family"], body["granted"])
    return accounts.listing(conn, app.settings, app.config)

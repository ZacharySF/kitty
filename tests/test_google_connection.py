"""Optional local Google connection: synthetic provider, real loopback callbacks."""
from __future__ import annotations

import hashlib
import http.client
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from kitty.google_connection import GoogleConnection, ConnectionError
from test_web_modules import make_root, site


CLIENT = "synthetic.apps.googleusercontent.com"
SECRET = "synthetic-installed-app-secret"


def callback(url, **query):
    target = urlsplit(url)
    client = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
    try:
        client.request("GET", target.path + "?" + urlencode(query))
        response = client.getresponse()
        return response.status, response.read().decode()
    finally:
        client.close()


@pytest.mark.parametrize("client_id,client_secret", [(None, None), (CLIENT, None), (None, SECRET)])
def test_no_credentials_preserves_local_api_and_remembers_tutorial(tmp_path, monkeypatch, client_id, client_secret):
    # Google's Desktop app clients reject the token exchange without their secret.
    for name, value in (("KITTY_GOOGLE_CLIENT_ID", client_id), ("KITTY_GOOGLE_CLIENT_SECRET", client_secret)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    settings = make_root(tmp_path, ())
    with site(settings) as (_, request):
        assert request("/api/bootstrap")[0] == 200
        status, state = request("/api/account")
        assert status == 200
        assert state["configured"] is False and state["identity"] is None
        assert state["onboarding"] == {"welcome_completed": False, "tutorial_completed": False}
        assert request("/api/account/google/start", {})[0] == 503
        assert request("/api/account/onboarding", {"welcome_completed": True})[0] == 200
        assert request("/api/account/onboarding", {"tutorial_completed": True})[0] == 200
        assert request("/api/account/onboarding", {"tutorial_completed": "yes"})[0] == 400
    with site(settings) as (_, request):
        assert request("/api/account")[1]["onboarding"] == {"welcome_completed": True, "tutorial_completed": True}
        assert request("/api/bootstrap")[0] == 200


def test_loopback_state_pkce_and_receipt_are_independent(monkeypatch):
    exchanged = []
    def exchange(code, client_id, client_secret, redirect, verifier, nonce):
        exchanged.append((code, client_id, client_secret, redirect, verifier, nonce))
        return {"sub": "synthetic-user", "email": "test@example.invalid", "name": "Synthetic User"}
    monkeypatch.setattr("kitty.google_connection.exchange_identity", exchange)
    connection = GoogleConnection(CLIENT, SECRET)
    try:
        started = connection.start()
        params = parse_qs(urlsplit(started["url"]).query)
        assert set(params["scope"][0].split()) == {"openid", "email", "profile"}
        assert params["code_challenge_method"] == ["S256"]
        assert params["access_type"] == ["online"]
        redirect = params["redirect_uri"][0]
        assert urlsplit(redirect).hostname == "127.0.0.1"
        assert callback(redirect, code="code", state="wrong")[0] == 400
        assert exchanged == []
        assert callback(redirect, code="code", state=params["state"][0])[0] == 200
        import base64
        assert params["code_challenge"] == [base64.urlsafe_b64encode(hashlib.sha256(exchanged[0][4].encode()).digest()).rstrip(b"=").decode()]
        assert exchanged[0][:4] == ("code", CLIENT, SECRET, redirect)
        assert exchanged[0][5] == params["nonce"][0]
        assert "client_secret" not in params and SECRET not in started["url"]
        with pytest.raises(ConnectionError):
            connection.poll(params["state"][0])
        assert connection.poll(started["ticket"])["identity"]["sub"] == "synthetic-user"
        with pytest.raises(ConnectionError):
            connection.poll(started["ticket"])
    finally:
        connection.close()


def test_cancel_denial_replay_and_expiry(monkeypatch):
    connection = GoogleConnection(CLIENT, SECRET, lifetime=0.2)
    try:
        first = connection.start()
        second = connection.start()
        with pytest.raises(ConnectionError):
            connection.poll(first["ticket"])
        params = parse_qs(urlsplit(second["url"]).query)
        assert callback(params["redirect_uri"][0], error="access_denied", state=params["state"][0])[0] == 200
        result = connection.poll(second["ticket"])
        assert result["status"] == "error" and "cancelled" in result["error"]
        expired = connection.start()
        time.sleep(0.25)
        assert connection.poll(expired["ticket"])["status"] == "error"
        cancelled = connection.start()
        connection.cancel(cancelled["ticket"])
        with pytest.raises(ConnectionError):
            connection.poll(cancelled["ticket"])
    finally:
        connection.close()


def test_callback_never_leaks_provider_error(monkeypatch):
    def fail(*args):
        raise ValueError("SECRET-provider-token")
    monkeypatch.setattr("kitty.google_connection.exchange_identity", fail)
    connection = GoogleConnection(CLIENT, SECRET)
    try:
        started = connection.start()
        params = parse_qs(urlsplit(started["url"]).query)
        status, body = callback(params["redirect_uri"][0], code="secret-code", state=params["state"][0])
        result = connection.poll(started["ticket"])
        assert status == 200 and result["status"] == "error"
        assert "SECRET" not in json.dumps(result) + body
        assert "secret-code" not in body
    finally:
        connection.close()


@pytest.mark.parametrize("changed,accepted", [
    ({}, True), ({"nonce": "other"}, False), ({"email_verified": False}, False),
    ({"aud": "other-client"}, False), ({"iss": "https://attacker.invalid"}, False),
    ({"exp": 1}, False), ({"sub": ""}, False),
])
def test_real_signed_id_token_verification(monkeypatch, changed, accepted):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    import httpx
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from google.auth import crypt, jwt
    from kitty.google_connection import exchange_identity

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic.invalid")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT, "sub": "synthetic",
              "iat": int(time.time()), "exp": int(time.time()) + 300,
              "nonce": "our-nonce", "email": "synthetic@example.invalid", "email_verified": True, **changed}
    token = jwt.encode(crypt.RSASigner.from_string(pem, key_id="synthetic"), claims).decode()
    sent = []
    def token_endpoint(request):
        sent.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"id_token": token})
    transport = httpx.MockTransport(token_endpoint)
    real_client = httpx.Client
    monkeypatch.setattr("kitty.google_connection.httpx.Client", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr("kitty.google_connection.Request", lambda: lambda *args, **kw: SimpleNamespace(status=200, data=json.dumps({"synthetic": cert}).encode()))
    if accepted:
        assert exchange_identity("code", CLIENT, SECRET, "http://127.0.0.1:1/callback", "verifier", "our-nonce")["sub"] == "synthetic"
        assert sent == [{"client_id": [CLIENT], "client_secret": [SECRET], "code": ["code"], "code_verifier": ["verifier"],
                         "grant_type": ["authorization_code"], "redirect_uri": ["http://127.0.0.1:1/callback"]}]
    else:
        from google.auth.exceptions import GoogleAuthError
        with pytest.raises((ValueError, GoogleAuthError, ConnectionError)):
            exchange_identity("code", CLIENT, SECRET, "http://127.0.0.1:1/callback", "verifier", "our-nonce")


def test_identity_is_saved_only_by_one_use_initiator_receipt_and_disconnect_keeps_data(tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_GOOGLE_CLIENT_ID", CLIENT)
    monkeypatch.setenv("KITTY_GOOGLE_CLIENT_SECRET", SECRET)
    identity = {"sub": "local-synthetic", "email": "synthetic@example.invalid", "name": "Synthetic"}
    monkeypatch.setattr("kitty.google_connection.exchange_identity", lambda *args: identity)
    settings = make_root(tmp_path, ())
    with site(settings) as (_, request):
        _, started = request("/api/account/google/start", {})
        params = parse_qs(urlsplit(started["url"]).query)
        assert callback(params["redirect_uri"][0], code="synthetic-code", state=params["state"][0])[0] == 200
        assert request("/api/account")[1]["identity"] is None
        assert request("/api/account/google/poll", {"ticket": params["state"][0]})[0] == 400
        assert request("/api/account/google/poll", {"ticket": started["ticket"]})[1]["status"] == "connected"
        assert request("/api/account/google/poll", {"ticket": started["ticket"]})[0] == 400
        assert request("/api/account")[1]["identity"] == identity
        request("/api/account/onboarding", {"welcome_completed": True, "tutorial_completed": True})
    with site(settings) as (_, request):
        assert request("/api/account")[1]["identity"] == identity
        assert request("/api/account/google/disconnect", {})[1]["identity"] is None
        assert request("/api/account")[1]["onboarding"]["tutorial_completed"] is True
        assert request("/api/bootstrap")[0] == 200

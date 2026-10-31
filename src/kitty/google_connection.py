"""Optional identity connection for a local app, not an API access-control boundary.

Only the initiating page receives the one-use completion ticket. The separate
loopback listener accepts Google's top-level redirect without relaxing kitty's
API origin guards. Google tokens and PKCE material never enter SQLite or logs.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token


class ConnectionError(Exception):
    """A safe, user-facing connection failure."""


def exchange_identity(code: str, client_id: str, client_secret: str, redirect: str, verifier: str, nonce: str) -> dict:
    """Exchange once, verify Google's signature/issuer/audience/time and our nonce.

    Google requires a Desktop app client's secret at the token endpoint even with
    PKCE; for installed apps it is not confidential, but it stays out of URLs.
    """
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
        })
        response.raise_for_status()
        token = response.json()["id_token"]
    request = Request()
    claims = verify_oauth2_token(token, lambda *args, **kw: request(*args, **{**kw, "timeout": 15}), client_id)
    if (claims.get("nonce") != nonce or claims.get("email_verified") is not True
            or not isinstance(claims.get("sub"), str) or not claims["sub"]
            or not isinstance(claims.get("email"), str) or not claims["email"]):
        raise ConnectionError("Google did not return a verified identity. Try again.")
    return {"sub": claims["sub"], "email": claims["email"], "name": str(claims.get("name") or "")[:200]}


class GoogleConnection:
    """At most one bounded, cancellable authorization attempt per local workspace."""

    def __init__(self, client_id: str = "", client_secret: str = "", *, lifetime: float = 300):
        self.client_id = client_id
        self.client_secret = client_secret
        self.lifetime = lifetime
        self._lock = threading.Lock()
        self._attempt: dict | None = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def start(self) -> dict:
        if not self.configured:
            raise ConnectionError("Google connection is not configured. Continue locally or see Settings for setup.")
        self.close()
        state, ticket, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        verifier = secrets.token_urlsafe(64)
        attempt = {"state": state, "ticket": ticket, "expires": time.monotonic() + self.lifetime,
                   "result": {"status": "pending"}, "stop": threading.Event()}
        owner = self

        class Callback(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_args):
                pass

            def do_GET(self):
                accepted = False
                try:
                    target = urlsplit(self.path)
                    query = parse_qs(target.query, keep_blank_values=True, max_num_fields=10)
                    valid = (target.path == "/callback" and query.get("state") == [state]
                             and self.headers.get_all("Host") == [f"127.0.0.1:{server.server_port}"]
                             and all(len(values) == 1 for values in query.values())
                             and ((bool(query.get("code", [""])[0]) and "error" not in query)
                                  or ("error" in query and "code" not in query)))
                    with owner._lock:
                        accepted = bool(valid and owner._attempt is attempt
                                        and time.monotonic() < attempt["expires"]
                                        and attempt["result"]["status"] == "pending")
                        if accepted:
                            attempt["result"] = {"status": "exchanging"}
                    if accepted:
                        if "error" in query:
                            result = {"status": "error", "error": "Google sign-in was cancelled. You can continue locally."}
                        else:
                            try:
                                identity = exchange_identity(query["code"][0], owner.client_id, owner.client_secret,
                                                             redirect, verifier, nonce)
                                result = {"status": "connected", "identity": identity}
                            except Exception:
                                result = {"status": "error", "error": "Google sign-in could not complete. Try again or continue locally."}
                        with owner._lock:
                            if owner._attempt is attempt:
                                attempt["result"] = result
                        attempt["stop"].set()
                except (ValueError, KeyError):
                    accepted = False
                text = ("Google sign-in finished. Return to kitty! You may close this tab." if accepted
                        else "This sign-in link is invalid or expired. Start again in kitty!.")
                body = ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        "<title>Return to kitty!</title><h1>kitty!</h1><p>" + text + "</p></html>").encode()
                self.send_response(200 if accepted else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), Callback)
        server.timeout = min(0.1, self.lifetime)
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        attempt["server"] = server
        with self._lock:
            self._attempt = attempt

        def listen():
            try:
                while not attempt["stop"].is_set() and time.monotonic() < attempt["expires"]:
                    server.handle_request()
            finally:
                server.server_close()

        worker = threading.Thread(target=listen, daemon=True, name="kitty-google-callback")
        attempt["worker"] = worker
        worker.start()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": self.client_id, "redirect_uri": redirect, "response_type": "code",
            "scope": "openid email profile", "access_type": "online", "state": state,
            "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
            "prompt": "select_account",
        })
        return {"url": url, "ticket": ticket}

    def poll(self, ticket: str) -> dict:
        with self._lock:
            attempt = self._require(ticket)
            if time.monotonic() >= attempt["expires"]:
                result = {"status": "error", "error": "Google sign-in expired. Try again or continue locally."}
            else:
                result = attempt["result"]
            if result["status"] in {"pending", "exchanging"}:
                return {"status": "pending"}
            self._attempt = None
            attempt["stop"].set()
            return result

    def _require(self, ticket: str) -> dict:
        attempt = self._attempt
        if not attempt or not secrets.compare_digest(attempt["ticket"], ticket):
            raise ConnectionError("This sign-in attempt is no longer available. Start again.")
        return attempt

    def cancel(self, ticket: str) -> None:
        with self._lock:
            self._require(ticket)["stop"].set()
            self._attempt = None

    def close(self) -> None:
        with self._lock:
            attempt, self._attempt = self._attempt, None
            if attempt:
                attempt["stop"].set()
        if attempt:
            attempt["worker"].join(timeout=0.5)

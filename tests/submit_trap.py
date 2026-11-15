"""SubmitTrap: a local web server for the no-submit tests that records every request.

It is the ground truth that nothing left the browser (research note N3 §6): a
submit that kitty or the page lets through reaches this server as a POST.
data: and file: pages cannot send real POSTs, so submit fixtures are served here,
over HTTP or (with a throwaway self-signed certificate) HTTPS. Every request is
recorded as (method, path); bodies are read and dropped (`received` keeps each
write's query and body length, to prove a request arrived unmodified). Fixture
pages are synthetic: they come from `pages` or a folder of static test files.
"""

from __future__ import annotations

import datetime
import ipaddress
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".pdf": "application/pdf"}


def owner_click(frame, shared_id: str) -> None:
    """A raw pointer click on one element, as the owner's own hand would make it.

    It bypasses kitty's admission (bidi.Frame.click): tests use it for the
    owner's actions and for the sensitivity controls (what happens without the layer).
    """
    frame.bidi.command("input.performActions", {"context": frame.context, "actions": [{
        "type": "pointer", "id": "owner-mouse", "parameters": {"pointerType": "mouse"},
        "actions": [
            {"type": "pointerMove", "x": 0, "y": 0,
             "origin": {"type": "element", "element": {"sharedId": shared_id}}},
            {"type": "pointerDown", "button": 0},
            {"type": "pointerUp", "button": 0},
        ],
    }]})
    frame.bidi.command("input.releaseActions", {"context": frame.context})


def owner_keys(frame, text: str) -> None:
    """Raw key presses into whatever has the focus (WebDriver key codes allowed), as the owner types."""
    actions: list[dict] = []
    for char in text:
        actions += [{"type": "keyDown", "value": char}, {"type": "keyUp", "value": char},
                    {"type": "pause", "duration": 20}]
    frame.bidi.command("input.performActions", {"context": frame.context, "actions": [{
        "type": "key", "id": "owner-keyboard", "actions": actions,
    }]})
    frame.bidi.command("input.releaseActions", {"context": frame.context})


class SubmitTrap:
    """Serves synthetic pages and records (method, path) of every request it receives."""

    def __init__(self, root: Path | None = None, *, tls: ssl.SSLContext | None = None) -> None:
        self.root = root
        self.pages: dict[str, str] = {}
        self.statuses: dict[str, int] = {}                        # pages answered with another HTTP status
        self.requests: list[tuple[str, str]] = []
        self.received: list[tuple[str, str, str, int]] = []    # writes: method, path, query, body length
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        if tls is not None:
            self._server.socket = tls.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        scheme = "https" if tls is not None else "http"
        self.origin = f"{scheme}://127.0.0.1:{self._server.server_address[1]}"

    def url(self, path: str) -> str:
        return self.origin + "/" + path.lstrip("/")

    def writes(self, allow: tuple[str, ...] = ()) -> list[tuple[str, str]]:
        """Non-GET requests whose path is not in `allow`: what a submit would look like."""
        with self._lock:
            return [(method, path) for method, path in self.requests
                    if method not in _SAFE and path not in allow]

    def wait_for_write(self, seconds: float = 3.0, allow: tuple[str, ...] = ()) -> list[tuple[str, str]]:
        """Writes once one arrived, or after `seconds` (the sensitivity controls wait here)."""
        deadline = time.monotonic() + seconds
        while not self.writes(allow) and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.writes(allow)

    def hits(self, path: str) -> list[str]:
        """Methods of every request to `path` (a GET can submit too, on a legacy site)."""
        with self._lock:
            return [method for method, seen in self.requests if seen == path]

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()
            self.received.clear()
        self.pages.clear()
        self.statuses.clear()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        trap = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self) -> str:
                path = urlsplit(self.path).path
                with trap._lock:
                    trap.requests.append((self.command, path))
                return path

            def _send(self, status: int, body: bytes, kind: str = "text/html; charset=utf-8") -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_GET(self):
                path = self._record()
                if path in trap.pages:
                    self._send(trap.statuses.get(path, 200), trap.pages[path].encode())
                    return
                name = path.lstrip("/")
                if trap.root is not None and name and "/" not in name and not name.startswith("."):
                    file = trap.root / name
                    if file.is_file():
                        self._send(200, file.read_bytes(), _TYPES.get(file.suffix, "application/octet-stream"))
                        return
                self._send(404, b"<p>not here</p>")

            do_HEAD = do_GET

            def _write(self):
                path = self._record()
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                with trap._lock:
                    trap.received.append((self.command, path, urlsplit(self.path).query, len(body)))
                self._send(200, b"<!doctype html><p>The trap received this request.</p>")

            do_POST = do_PUT = do_PATCH = do_DELETE = _write

        return Handler


def self_signed(folder: Path) -> ssl.SSLContext:
    """A server TLS context with a throwaway certificate for 127.0.0.1 (tests only)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    return context

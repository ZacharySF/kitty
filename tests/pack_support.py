"""Synthetic module packs for tests: a throwaway minisign key, signatures in the form
`tauri signer sign` writes, and a loopback server standing in for a GitHub release.

Nothing here touches the real release key or GitHub; rika-server takes the test key and the
loopback index through its test-only KITTY_PACKS_KEY and KITTY_PACKS_URL.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import importlib.util
import json
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

REPO = Path(__file__).resolve().parents[1]
#: The app version the front compiles in (desktop/tauri.conf.json); only its packs count.
VERSION = json.loads((REPO / "desktop/tauri.conf.json").read_text())["version"]
_spec = importlib.util.spec_from_file_location("build_packs", REPO / "scripts/build-packs.py")
build_packs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_packs)


class Key:
    """A minisign key pair; `public` is the form tauri.conf.json's updater pubkey takes."""

    def __init__(self):
        self.secret = Ed25519PrivateKey.generate()
        self.id = os.urandom(8)
        raw = self.secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        text = f"untrusted comment: minisign public key: {self.id[::-1].hex().upper()}\n" \
               f"{base64.b64encode(b'Ed' + self.id + raw).decode()}\n"
        self.public = base64.b64encode(text.encode()).decode()

    def sign(self, data: bytes, file: str = "packs.json") -> bytes:
        """A prehashed minisign signature, base64-wrapped as `tauri signer sign` writes `.sig`."""
        signature = self.secret.sign(hashlib.blake2b(data, digest_size=64).digest())
        comment = f"timestamp:0\tfile:{file}"
        overall = self.secret.sign(signature + comment.encode())
        text = ("untrusted comment: signature from tauri secret key\n"
                f"{base64.b64encode(b'ED' + self.id + signature).decode()}\n"
                f"trusted comment: {comment}\n{base64.b64encode(overall).decode()}\n")
        return base64.b64encode(text.encode())


class Release(http.server.ThreadingHTTPServer):
    """Serves `files` (name -> bytes) on loopback, like a release's download URLs.

    `cut` names a file sent with its full Content-Length but only half its bytes, then a closed
    connection; `hold` is an Event every download of `held` waits on midway, to watch progress.
    """

    daemon_threads = True

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.cut: str | None = None
        self.held: str | None = None
        self.hold = threading.Event()
        self.requests: list[str] = []
        super().__init__(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"

    def publish(self, key: Key, index: dict, archives: dict[str, bytes] | None = None, *, signer: Key | None = None):
        data = json.dumps(index).encode()
        self.files["packs.json"] = data
        self.files["packs.json.sig"] = (signer or key).sign(data)
        self.files.update(archives or {})

    def close(self):
        self.hold.set()
        self.shutdown()
        self.server_close()


class _Handler(http.server.BaseHTTPRequestHandler):
    server: Release

    def do_GET(self):
        name = self.path.lstrip("/")
        self.server.requests.append(name)
        data = self.server.files.get(name)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        half = len(data) // 2
        if name == self.server.cut:
            self.wfile.write(data[:half])
            self.wfile.flush()
            self.close_connection = True
            return
        if name == self.server.held:
            self.wfile.write(data[:half])
            self.wfile.flush()
            self.server.hold.wait(30)
            self.wfile.write(data[half:])
            return
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def make_pack(tmp: Path, files: dict[str, bytes], version: str = VERSION, pack: str = "brain") -> tuple[dict, dict[str, bytes], Path]:
    """A website whose `pack` files are built into an archive and stripped: (index, archives, dist)."""
    dist = tmp / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (dist / name).write_bytes(data)
    (dist / "pack-files.json").write_text(json.dumps({pack: sorted(files)}))
    out = tmp / "packs-out"
    index = build_packs.build(dist, out, version)
    archives = {entry["file"]: (out / entry["file"]).read_bytes() for entry in index["packs"]}
    return index, archives, dist

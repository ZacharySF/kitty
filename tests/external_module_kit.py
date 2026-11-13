"""Synthetic external modules (docs/MODULES.md) and a loopback stand-in for GitHub.

Every module here is built by the test that uses it; nothing is downloaded. The fake
GitHub serves ``GET /repos/<owner>/<repo>/releases/latest`` and the asset routes the
installer follows, so the real installer code (headers, redirects, checksums) runs.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def manifest(module_id: str = "rika-synth", version: str = "0.1.0", *, kind: str = "python",
             package: str = "rika_synth", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": module_id, "name": "Synthetic", "version": version, "api": 1,
        "description": "A synthetic test module.", "license": "MIT", "kind": kind,
    }
    if kind == "python":
        data["python"] = {"package": package}
        data["view"] = {"id": "synth", "label": "Synthetic", "icon": "study", "group": "study",
                        "first": True, "ui": "element",
                        "element": {"script": "ui/view.js", "tag": "rika-synth-view"}}
    else:
        data["process"] = {"command": ["python3", "server.py"], "cwd": ".",
                           "env": {"SYNTH_DATA": "{data}", "PORT": "{port}"}, "health": "/", "ready_timeout_s": 20}
        data["view"] = {"id": "synth", "label": "Synthetic", "icon": "study", "group": "study",
                        "first": False, "ui": "iframe"}
    data.update(overrides)
    return data


PYTHON_MODULE = '''
"""Synthetic external module: a counter it keeps in its own table."""
started = []


def start(app):
    started.append(app)


def get(app, conn, path, query):
    if path == "/api/modules/{id}/hello":
        count = conn.execute("SELECT COUNT(*) FROM synth_counter").fetchone()[0]
        return {{"hello": "synthetic", "count": count, "started": len(started)}}
    return None


def post(app, conn, path, body):
    if path == "/api/modules/{id}/count":
        conn.execute("INSERT INTO synth_counter (note) VALUES (?)", (str(body.get("note", "")),))
        return {{"ok": True}}
    return None
'''

PROCESS_SERVER = '''
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        body = json.dumps({"data": os.environ.get("SYNTH_DATA"), "home": os.environ.get("HOME"),
            "secrets": [name for name in ("OPENROUTER_API_KEY", "GITHUB_TOKEN", "KITTY_HOME") if name in os.environ]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
'''


def python_files(module_id: str = "rika-synth", version: str = "0.1.0", package: str = "rika_synth",
                 **overrides: Any) -> dict[str, bytes]:
    return {
        "rika-module.json": json.dumps(manifest(module_id, version, package=package, **overrides)).encode(),
        f"{package}/__init__.py": PYTHON_MODULE.format(id=module_id).encode(),
        "migrations/001_counter.sql": b"CREATE TABLE synth_counter (id INTEGER PRIMARY KEY, note TEXT NOT NULL);\n",
        "ui/view.js": b"customElements.define('rika-synth-view', class extends HTMLElement {});\n",
    }


def process_files(module_id: str = "rika-proc", version: str = "0.1.0", *, command: list[str] | None = None,
                  **overrides: Any) -> dict[str, bytes]:
    data = manifest(module_id, version, kind="process", **overrides)
    if command is not None:
        data["process"]["command"] = command
    return {"rika-module.json": json.dumps(data).encode(), "server.py": PROCESS_SERVER.encode()}


def tarball(files: dict[str, bytes], *, extra: list[tuple[tarfile.TarInfo, bytes | None]] = ()) -> bytes:
    """A gzip tarball whose root holds `files`; `extra` adds hand-made (hostile) members."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeGitHub:
    """A loopback GitHub REST stand-in: releases/latest plus asset downloads (with a redirect)."""

    def __init__(self) -> None:
        self.releases: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.requests: list[dict[str, Any]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                fake.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                      "accept": self.headers.get("Accept")})
                if self.path.endswith("/releases/latest"):
                    repo = self.path.removeprefix("/repos/").removesuffix("/releases/latest")
                    release = fake.releases.get(repo)
                    if release is None:
                        self._reply(404, b'{"message": "Not Found"}', "application/json")
                    else:
                        self._reply(200, json.dumps(release).encode(), "application/json")
                elif self.path.startswith("/api-assets/"):
                    # Like api.github.com: the asset API redirects to a signed download URL.
                    self.send_response(302)
                    self.send_header("Location", f"{fake.origin}/downloads/{self.path.removeprefix('/api-assets/')}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path.startswith("/downloads/") and self.path.removeprefix("/downloads/") in fake.blobs:
                    self._reply(200, fake.blobs[self.path.removeprefix("/downloads/")], "application/octet-stream")
                else:
                    self._reply(404, b'{"message": "Not Found"}', "application/json")

            def _reply(self, status: int, body: bytes, kind: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> FakeGitHub:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def publish(self, repo: str, module_id: str, version: str, data: bytes, *, digest: str | None = "auto",
                checksum_file: str | None = None, tag: str | None = None) -> None:
        """Make `data` the latest release of `repo` as ``<id>-<version>.tar.gz``."""
        name = f"{module_id}-{version}.tar.gz"
        key = f"{repo.replace('/', '_')}_{version}_{name}"
        self.blobs[key] = data
        asset = {"name": name, "url": f"{self.origin}/api-assets/{key}", "size": len(data),
                 "browser_download_url": f"{self.origin}/downloads/{key}"}
        if digest == "auto":
            asset["digest"] = f"sha256:{sha256(data)}"
        elif digest is not None:
            asset["digest"] = digest
        assets = [asset]
        if checksum_file is not None:
            self.blobs[key + ".sha256"] = checksum_file.encode()
            assets.append({"name": name + ".sha256", "url": f"{self.origin}/api-assets/{key}.sha256",
                           "size": len(checksum_file), "browser_download_url": f"{self.origin}/downloads/{key}.sha256"})
        self.releases[repo] = {"tag_name": tag or f"v{version}", "name": f"Synthetic {version}", "assets": assets}

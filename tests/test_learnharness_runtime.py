"""A packaged install can open Study without Git, npm, or an upstream checkout."""
from __future__ import annotations

import json
import socket
import sys
from http.client import HTTPConnection

import pytest

from kitty.learnharness_runtime import LearnHarnessRuntime


@pytest.fixture
def payload(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "node").symlink_to(sys.executable)
    # Executable external-service fixture, not a mocked Popen/readiness receipt.
    (bundle / "server.js").write_text('''
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
store = Path(os.environ["LH_DATA_DIR"])
marker = store / "visits"
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_HEAD(self):
        self.send_response(200); self.end_headers()
    def do_GET(self):
        count = int(marker.read_text()) + 1 if marker.exists() else 1
        marker.write_text(str(count))
        body = json.dumps({"visits": count, "data": str(store),
            "auth": os.environ["LH_AUTH_FILE"], "home": os.environ["HOME"],
            "secrets": [name for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "KITTY_HOME") if name in os.environ]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
HTTPServer((os.environ["HOSTNAME"], int(os.environ["PORT"])), Handler).serve_forever()
''')
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return bundle, tmp_path / "learning", port


def read(port):
    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", "/")
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


def test_packaged_study_starts_without_developer_tools_and_preserves_learning(payload, monkeypatch):
    bundle, data, port = payload
    monkeypatch.setenv("PATH", "/no-developer-tools")
    for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "KITTY_HOME"):
        monkeypatch.setenv(name, "synthetic-never-forward")
    runtime = LearnHarnessRuntime(bundle, data, port=port)
    try:
        runtime.open()
        assert read(port) == {"visits": 1, "data": str(data),
            "auth": str(data / "auth.json"), "home": str(data / "home"), "secrets": []}
        runtime.open()  # Reopening must not spawn another server or erase progress.
        assert read(port)["visits"] == 2
        runtime.close()
        with pytest.raises(OSError):
            read(port)
        runtime.open()
        assert read(port)["visits"] == 3
        assert data.stat().st_mode & 0o777 == 0o700
    finally:
        runtime.close()


def test_reusing_external_service_does_not_stop_or_modify_it(payload):
    bundle, data, port = payload
    external = LearnHarnessRuntime(bundle, data, port=port)
    consumer = LearnHarnessRuntime(bundle.parent / "missing", data.parent / "unused", port=port)
    try:
        external.open()
        consumer.open()
        consumer.close()
        assert read(port)["visits"] == 1
        assert not consumer.data.exists()
    finally:
        consumer.close()
        external.close()


def test_incomplete_package_reports_reinstall_instead_of_source_commands(tmp_path):
    runtime = LearnHarnessRuntime(tmp_path / "missing", tmp_path / "learning", port=1)
    with pytest.raises(RuntimeError, match="reinstall"):
        runtime.open()
    assert not runtime.data.exists()

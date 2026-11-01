"""The owner-only `.private/endpoint.json` that tells paired programs where kitty! listens."""
from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kitty.core import endpoint

PROJECT = Path(__file__).resolve().parents[1]


def test_published_file_is_owner_only_and_holds_only_the_origin_and_pairing_path(tmp_path):
    with endpoint.published(tmp_path, "http://127.0.0.1:41234") as path:
        assert path == tmp_path / ".private" / "endpoint.json"
        assert json.loads(path.read_text()) == {"origin": "http://127.0.0.1:41234", "pairing": endpoint.PAIRING_PATH}
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / ".private").stat().st_mode) == 0o700
        assert "token" not in path.read_text()
        # Written through a temporary name and renamed: no partial file or leftover is visible.
        assert [item.name for item in (tmp_path / ".private").iterdir()] == ["endpoint.json"]
    assert not path.exists()


def test_publishing_tightens_an_existing_private_directory(tmp_path):
    private = tmp_path / ".private"
    private.mkdir(mode=0o755)
    private.chmod(0o755)
    with endpoint.published(tmp_path, "http://127.0.0.1:41234"):
        assert stat.S_IMODE(private.stat().st_mode) == 0o700


def test_a_symlink_in_place_of_the_file_is_replaced_not_followed(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged")
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    (private / "endpoint.json").symlink_to(victim)
    with endpoint.published(tmp_path, "http://127.0.0.1:41234") as path:
        assert not path.is_symlink() and path.is_file()
    assert victim.read_text() == "unchanged"


def test_a_symlinked_private_directory_is_refused(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (tmp_path / ".private").symlink_to(elsewhere)
    with pytest.raises(endpoint.EndpointError), endpoint.published(tmp_path, "http://127.0.0.1:41234"):
        pass
    assert list(elsewhere.iterdir()) == []


def test_shutdown_leaves_a_file_another_server_rewrote(tmp_path):
    with endpoint.published(tmp_path, "http://127.0.0.1:41234") as path:
        with endpoint.published(tmp_path, "http://127.0.0.1:50000"):
            pass
        endpoint.write(tmp_path, "http://127.0.0.1:50001")
    assert json.loads(path.read_text())["origin"] == "http://127.0.0.1:50001"


def test_reading_the_published_endpoint(tmp_path):
    assert endpoint.read(tmp_path) is None
    with endpoint.published(tmp_path, "http://127.0.0.1:41234"):
        assert endpoint.read(tmp_path) == {"origin": "http://127.0.0.1:41234", "pairing": endpoint.PAIRING_PATH}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM])
def test_kitty_web_publishes_its_endpoint_and_removes_it_on_shutdown(tmp_path, stop):
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "modules.yaml").write_text("enabled: [workspace]\n")
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Synthetic</title>")
    port = _free_port()
    environment = {key: value for key, value in os.environ.items() if key not in {"KITTY_HOME", "KITTY_DB"}}
    environment["PYTHONPATH"] = str(PROJECT / "src")
    process = subprocess.Popen([sys.executable, "-m", "kitty.web_server", "--root", str(root), "--port", str(port),
                                "--static-dir", str(static)], cwd=tmp_path, env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    path = root / ".private" / "endpoint.json"
    try:
        # The file is in place before the server announces itself.
        assert "website: http://127.0.0.1:" in process.stdout.readline()
        assert json.loads(path.read_text()) == {"origin": f"http://127.0.0.1:{port}", "pairing": endpoint.PAIRING_PATH}
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        process.send_signal(stop)
        assert process.wait(timeout=30) == 0
    assert not path.exists()

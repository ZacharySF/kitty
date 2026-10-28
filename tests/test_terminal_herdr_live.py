"""Real herdr v0.9.5 end to end through kitty!'s WebSocket. Opt-in, isolated lab only.

    KITTY_HERDR_LIVE=/path/to/herdr-0.9.5 uv run pytest tests/test_terminal_herdr_live.py

Runs in a throwaway HOME/XDG tree with an ``fm-lab-*`` session (never herdr's
``default`` session) and stops only the session this test created.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from terminal_support import FrameReader, make_root, send_frame, site

LIVE = os.environ.get("KITTY_HERDR_LIVE")
pytestmark = pytest.mark.skipif(not LIVE, reason="set KITTY_HERDR_LIVE to a herdr 0.9.5 binary")
HARNESS_ACCENT = b"2;222;178;195"  # #deb2c3 as a truecolor SGR parameter
USER_ACCENT = b"2;18;52;86"  # #123456 from the lab's own herdr config


def listening(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


@pytest.fixture
def lab(monkeypatch):
    assert subprocess.run([LIVE, "--version"], capture_output=True, text=True, check=True).stdout.strip() == "herdr 0.9.5"
    home = Path(tempfile.mkdtemp(prefix="kt", dir="/tmp"))
    session = f"fm-lab-kitty-{secrets.token_hex(3)}"
    environment = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                   "XDG_STATE_HOME": str(home / ".local/state"), "XDG_DATA_HOME": str(home / ".local/share"),
                   "XDG_CACHE_HOME": str(home / ".cache"), "SHELL": "/bin/sh",
                   "KITTY_HERDR": LIVE, "KITTY_HERDR_SESSION": session,
                   # kitty! itself running inside herdr must still start its own herdr.
                   "HERDR_ENV": "1", "HERDR_SESSION": "default"}
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    user_config = home / ".config/herdr/config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text('onboarding = false\n[theme]\nname = "dracula"\n[theme.custom]\naccent = "#123456"\n')
    try:
        yield session, home, user_config
    finally:
        folder = home / ".config/herdr/sessions" / session
        if listening(folder / "herdr.sock"):
            clean = {key: value for key, value in os.environ.items() if not key.startswith("HERDR_")}
            subprocess.run([LIVE, "--session", session, "session", "stop", session], env=clean,
                           capture_output=True, timeout=60, check=False)
        shutil.rmtree(home, ignore_errors=True)


def test_real_herdr_session_streams_detaches_reattaches_and_stops(tmp_path, lab):
    session, home, user_config = lab
    user_theme = user_config.read_text()
    folder = home / ".config/herdr/sessions" / session
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, _headers, connection, rest = running.connect(running.token(), query="&cols=120&rows=36")
        assert status == 101, rest
        reader = FrameReader(connection, rest)
        drawn = reader.until(HARNESS_ACCENT, timeout=30)
        assert USER_ACCENT not in drawn
        send_frame(connection, 2, b"echo kitty-$((6*7))\r")
        reader.until(b"kitty-42", timeout=30)
        # herdr sizes the pane to the client minus its own sidebar and tab bar.
        # herdr redraws the whole screen on resize, so each probe carries its own label.
        def pane_size(label: bytes) -> tuple[int, int]:
            send_frame(connection, 2, b"echo " + label + b":$(stty size | tr ' ' x):Z\r")
            found = reader.match(re.compile(re.escape(label) + rb":(\d+)x(\d+):Z"), timeout=30)
            return int(found[1]), int(found[2])

        before = pane_size(b"first")
        send_frame(connection, 1, json.dumps({"type": "resize", "cols": 100, "rows": 30}).encode())
        time.sleep(1)
        after = pane_size(b"second")
        assert after[0] < before[0] and after[1] < before[1], (before, after)

        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        for name in ("herdr.sock", "herdr-client.sock"):
            info = (folder / name).lstat()
            assert stat.S_ISSOCK(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600

        send_frame(connection, 8, b"\x03\xe8")
        reader.closed(timeout=30)
        connection.close()
        time.sleep(1)
        assert listening(folder / "herdr-client.sock"), "closing the page must only detach"

        status, _headers, again, rest = running.connect(running.token(), query="&cols=100&rows=30")
        assert status == 101
        redrawn = FrameReader(again, rest).until(b"kitty-42", timeout=30)
        assert re.search(rb"kitty-\$\(\(6\*7\)\)|kitty-42", redrawn)
        again.close()
    # Quit stops kitty!'s session; the lab's own herdr config was never touched.
    assert not listening(folder / "herdr.sock")
    assert user_config.read_text() == user_theme

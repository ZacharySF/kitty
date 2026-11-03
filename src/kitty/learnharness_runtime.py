"""Own only the bundled external learning server, never a user's existing service."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path


class LearnHarnessRuntime:
    def __init__(self, bundle: Path, data: Path, *, port: int = 3210):
        self.bundle = bundle
        self.data = data
        self.port = port
        self._child: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request("HEAD", "/")
            return connection.getresponse().status == 200
        except (OSError, HTTPException):
            return False
        finally:
            connection.close()

    def open(self) -> None:
        with self._lock:
            if self.available():
                return
            node = self.bundle / "node"
            server = self.bundle / "server.js"
            if not node.is_file() or not server.is_file():
                raise RuntimeError("LearnHarness runtime is missing; reinstall rika! with the complete desktop package.")
            self._stop()
            self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
            home = self.data / "home"
            home.mkdir(exist_ok=True, mode=0o700)
            # No inherited provider keys, Kitty paths, NODE_OPTIONS, or user tool config.
            environment = {
                "HOME": str(home), "LANG": "C.UTF-8", "NODE_ENV": "production",
                "HOSTNAME": "127.0.0.1", "PORT": str(self.port),
                "LH_DATA_DIR": str(self.data), "LH_AUTH_FILE": str(self.data / "auth.json"),
                "NEXT_TELEMETRY_DISABLED": "1",
            }
            # Nix's host Node needs its loader/library search environment. These
            # contain runtime paths, not provider or workspace configuration.
            for name in ("NIX_LD", "NIX_LD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
                if name in os.environ:
                    environment[name] = os.environ[name]
            try:
                self._child = subprocess.Popen(
                    [str(node), str(server)], cwd=self.bundle, env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 30
                while self._child.poll() is None and time.monotonic() < deadline:
                    if self.available():
                        return
                    time.sleep(0.1)
            except OSError:
                self._stop()
                raise RuntimeError("LearnHarness could not start; reinstall rika! for this system.") from None
            self._stop()
            raise RuntimeError("LearnHarness did not become ready. Check that port 3210 is free, then try Start again.")

    def _stop(self) -> None:
        child, self._child = self._child, None
        if child is None:
            return
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        else:
            child.wait()

    def close(self) -> None:
        with self._lock:
            self._stop()

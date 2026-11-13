"""A local web server rika! starts on a loopback port and shows in a frame.

Used by the bundled LearnHarness (kitty.learnharness_runtime) and by external
``process`` modules (kitty.web_external_modules). The child gets a clean environment
(never provider keys, workspace paths or NODE_OPTIONS), is started on demand, is
ready once its health path answers, and is stopped when rika! closes.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Container, Sequence
from http.client import HTTPConnection, HTTPException
from pathlib import Path

#: Runtime-loader variables Nix's host Node needs: paths, not provider or workspace configuration.
LOADER_ENV = ("NIX_LD", "NIX_LD_LIBRARY_PATH", "LD_LIBRARY_PATH")


def bundle_dir() -> Path:
    """The packaged desktop payload (PyInstaller), or the developer staging folder."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "desktop/.build"))


def bundled_node() -> Path:
    """The Node runtime shipped for LearnHarness; external modules may use it too."""
    return bundle_dir() / "learnharness" / "node"


def free_port() -> int:
    """A loopback port nothing listens on right now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def loader_environment() -> dict[str, str]:
    return {name: os.environ[name] for name in LOADER_ENV if name in os.environ}


class LoopbackProcess:
    """One child web server on ``127.0.0.1:<port>``, owned by this rika! process.

    ``reuse`` accepts a healthy server already answering on the port (LearnHarness's
    fixed port may be a user's own service, which is then neither started nor stopped).
    """

    label = "The service"

    def __init__(self, port: int, *, health: str = "/", method: str = "GET", healthy: Container[int] = range(200, 400),
                 ready_timeout: float = 30, reuse: bool = False, log: Path | None = None):
        self.port = port
        self.health = health
        self.method = method
        self.healthy = healthy
        self.ready_timeout = ready_timeout
        self.reuse = reuse
        self.log = log
        self._child: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def available(self) -> bool:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request(self.method, self.health)
            return connection.getresponse().status in self.healthy
        except (OSError, HTTPException):
            return False
        finally:
            connection.close()

    def prepare(self) -> tuple[Sequence[str], Path, dict[str, str]]:
        """``(argv, cwd, environment)`` for a start; raise RuntimeError with a plain reason."""
        raise NotImplementedError

    def not_ready(self) -> str:
        return f"{self.label} did not become ready. Check that port {self.port} is free, then try Start again."

    def open(self) -> None:
        with self._lock:
            if self._child is not None and self._child.poll() is None and self.available():
                return
            if self.reuse and self.available():
                return
            argv, cwd, environment = self.prepare()
            self._stop()
            output = subprocess.DEVNULL
            try:
                if self.log is not None:
                    output = self.log.open("wb")
                try:
                    self._child = subprocess.Popen(
                        list(argv), cwd=cwd, env=environment,
                        stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    )
                finally:
                    if output is not subprocess.DEVNULL:
                        output.close()
                deadline = time.monotonic() + self.ready_timeout
                while self._child.poll() is None and time.monotonic() < deadline:
                    if self.available():
                        return
                    time.sleep(0.1)
            except OSError:
                self._stop()
                raise RuntimeError(self.cannot_start()) from None
            self._stop()
            raise RuntimeError(self.not_ready())

    def cannot_start(self) -> str:
        return f"{self.label} could not start on this system."

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

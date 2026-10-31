"""Stencil Tern (https://stencil.so/tern) as an optional terminal beside kitty!'s herdr.

Tern is closed-beta proprietary software: kitty! never bundles it. It runs only the
``tern`` the user installed, found on PATH or at the path saved in kitty!'s Tern
settings (``.private/tern/settings.json``), and does nothing at all without it.

kitty! keeps its own Tern session daemon on a private socket,
``$XDG_RUNTIME_DIR/kitty/tern-<workspace hash>/daemon.sock`` (directory 0700), so
it never reads or changes the user's own Tern daemon and sessions. Only Tern's
public commands are used, always with ``TERN_DAEMON_SOCKET`` set to that socket:

* ``tern daemon --socket PATH`` starts the daemon (on demand, never on startup);
* ``tern new session kitty --cwd WORKSPACE`` gives it kitty!'s session;
* ``tern inspect --json`` reads sessions, panes and attached clients for status;
* ``tern --dir WORKSPACE`` opens a native Tern window on that daemon; it shows
  kitty!'s session and only starts a fresh session in the workspace when none exists.

Tern's web client (``tern web serve``) needs WebGPU, which kitty!'s WebKitGTK view
does not provide, so kitty! shows status and opens native windows instead of
embedding a terminal. Quit stops the windows kitty! opened and kitty!'s daemon
(Tern keeps the layout in ``daemon.sock.state`` and restores it on the next start),
but only when this process used Tern.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path

INSTALL_URL = "https://stencil.so/tern"
SESSION = "kitty"
#: Linux ``sockaddr_un.sun_path`` holds 107 bytes plus the terminating NUL.
SOCKET_PATH_MAX = 107
READY_TIMEOUT = 15.0
STOP_TIMEOUT = 10.0
COMMAND_TIMEOUT = 10.0
#: Tern variables a user sets for every Tern; everything else TERN_* describes the
#: pane or window kitty! itself might run in, and would aim commands at it.
_USER_TERN = ("TERN_CONFIG_DIR", "TERN_AUTH_URL", "TERN_BUILD_URL")
#: The terminal kitty! itself runs in, and bundle library overlays (see
#: kitty.core.apply.browser._start) that break a separately installed program.
_OUTER = ("TMUX", "TMUX_PANE", "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
          "LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG")


class TernUnavailable(RuntimeError):
    """kitty!'s Tern session cannot be used; the message is safe to show."""


class TernMissing(TernUnavailable):
    """No usable ``tern`` is installed (or the configured one is broken)."""


def child_environment(environ: Mapping[str, str], daemon_socket: Path) -> dict[str, str]:
    """Environment for every ``tern`` kitty! runs: aimed at kitty!'s daemon only."""
    result = {key: value for key, value in environ.items()
              if key not in _OUTER and not key.startswith("HERDR_")
              and (not key.startswith("TERN_") or key in _USER_TERN)}
    result["TERN_DAEMON_SOCKET"] = str(daemon_socket)
    return result


def _write_private(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _owner_only_directory(path: Path) -> None:
    """Create `path` 0700 (parents too), or tighten kitty!'s existing directory to 0700."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise TernUnavailable(f"{path} is not a directory owned by this user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


def _listening(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _socket_owner(path: Path) -> int | None:
    """The pid serving `path`, when it runs as this user (SO_PEERCRED)."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
        pid, uid, _gid = struct.unpack("3i", probe.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return pid if uid == os.getuid() and pid > 0 else None
    except OSError:
        return None
    finally:
        probe.close()


def _running(pid: int) -> bool:
    """True while `pid` runs; an exited process awaiting its parent's wait() does not."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _terminate_group(process: subprocess.Popen) -> None:
    """Stop a window kitty! started in its own process group."""
    if process.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=STOP_TIMEOUT / 2)
            return
        except subprocess.TimeoutExpired:
            continue
    process.wait()


def _block(block: dict, working: set) -> dict:
    return {"id": block["id"], "title": str(block.get("title") or ""), "cwd": block.get("cwd"),
            "program": block.get("program"), "cols": block.get("cols"), "rows": block.get("rows"),
            "focused": block.get("focused") is True, "live": block.get("live") is True,
            "exited": block.get("exited"), "working": block["id"] in working}


class TernRuntime:
    """Detection, settings and kitty!'s own Tern daemon for one workspace."""

    def __init__(self, workspace: Path, private_dir: Path, environ: Mapping[str, str] | None = None):
        self.workspace = workspace
        self.private_dir = private_dir
        self.environ = os.environ if environ is None else environ
        self._lock = threading.Lock()
        self._used = False
        self._daemon: subprocess.Popen | None = None
        self._windows: list[subprocess.Popen] = []
        self._versions: dict[tuple, str | None] = {}

    # Detection and settings -------------------------------------------------

    @property
    def settings_path(self) -> Path:
        return self.private_dir / "settings.json"

    def override(self) -> str | None:
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8")).get("path")
        except (OSError, ValueError, AttributeError):
            return None
        return value if isinstance(value, str) and value else None

    def save_override(self, path: str | None) -> None:
        if path is not None and not os.path.isabs(path):
            raise ValueError("The Tern path must be an absolute path")
        _owner_only_directory(self.private_dir)
        _write_private(self.settings_path, json.dumps({"path": path or None}) + "\n")

    def _version(self, binary: str) -> str | None:
        """``tern --version``'s version, or None when `binary` is not Stencil Tern."""
        info = os.stat(binary)
        key = (binary, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if key not in self._versions:
            version = None
            try:
                result = subprocess.run([binary, "--version"], env=child_environment(self.environ, self.socket_path),
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                        timeout=COMMAND_TIMEOUT, check=False)
                words = result.stdout.split()
                if result.returncode == 0 and len(words) >= 2 and words[0] == "tern":
                    version = words[1]
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._versions = {key: version}
        return self._versions[key]

    def detect(self) -> dict:
        """Which ``tern`` kitty! would run: the saved override, else PATH's."""
        override = self.override()
        found = {"override": override, "installed": False, "path": None, "source": None,
                 "version": None, "problem": None}
        if override:
            found["source"] = "settings"
            candidate = override
            if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
                found["problem"] = f"The Tern path in settings is not an executable file: {override}"
                return found
        else:
            candidate = shutil.which("tern", path=self.environ.get("PATH", ""))
            if candidate is None:
                return found
            found["source"] = "path"
        version = self._version(candidate)
        if version is None:
            found["problem"] = f"{candidate} is not Stencil Tern (`tern --version` did not identify it)"
            return found
        found.update(installed=True, path=candidate, version=version)
        return found

    def _binary(self) -> str:
        found = self.detect()
        if not found["installed"]:
            raise TernMissing((found["problem"] + ". " if found["problem"] else "Tern is not installed. ")
                              + f"Install Stencil Tern from {INSTALL_URL} or set its path in kitty!'s Tern view.")
        return found["path"]

    # kitty!'s daemon ---------------------------------------------------------

    @property
    def socket_path(self) -> Path:
        runtime = self.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        digest = hashlib.sha256(os.fsencode(self.workspace)).hexdigest()[:12]
        return Path(runtime) / "kitty" / f"tern-{digest}" / "daemon.sock"

    def _environment(self) -> dict[str, str]:
        return child_environment(self.environ, self.socket_path)

    def _run(self, binary: str, *arguments: str) -> str:
        try:
            result = subprocess.run([binary, *arguments], env=self._environment(), cwd=self.workspace,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=COMMAND_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise TernUnavailable(f"`tern {arguments[0]}` did not finish") from None
        if result.returncode:
            raise TernUnavailable(f"`tern {arguments[0]}` failed: {result.stderr.strip()[:300]}")
        return result.stdout

    def _inspect(self, binary: str) -> dict:
        try:
            found = json.loads(self._run(binary, "inspect", "--json"))
            if not isinstance(found.get("sessions"), list):
                raise ValueError
            return found
        except (TernUnavailable, ValueError, AttributeError):
            raise TernUnavailable("kitty!'s Tern daemon did not report its sessions") from None

    def _verify_private(self) -> None:
        path = self.socket_path
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise TernUnavailable(f"Tern's socket {path} has unsafe permissions; kitty! stopped it")

    def _start_locked(self) -> str:
        binary = self._binary()
        path = self.socket_path
        if len(os.fsencode(path)) > SOCKET_PATH_MAX:
            raise TernUnavailable(f"Tern's socket path is longer than {SOCKET_PATH_MAX} bytes: {path}")
        _owner_only_directory(path.parent)
        self._used = True
        if not _listening(path):
            if self._daemon is not None and self._daemon.poll() is None:
                self._stop_daemon()
            self._daemon = subprocess.Popen(
                [binary, "daemon", "--socket", str(path)], env=self._environment(), cwd=self.workspace,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            deadline = time.monotonic() + READY_TIMEOUT
            while not _listening(path):
                if self._daemon.poll() is not None or time.monotonic() > deadline:
                    self._stop_daemon()
                    raise TernUnavailable("kitty!'s Tern daemon did not start")
                time.sleep(0.05)
        try:
            self._verify_private()
            sessions = self._inspect(binary)["sessions"]
            if not any(item.get("name") == SESSION for item in sessions):
                self._run(binary, "new", "session", SESSION, "--cwd", str(self.workspace), "--json")
        except (TernUnavailable, OSError) as exc:
            self._stop_daemon()
            if isinstance(exc, TernUnavailable):
                raise
            raise TernUnavailable("kitty!'s Tern daemon could not be checked") from None
        return binary

    def start(self) -> None:
        """Start kitty!'s daemon and session if needed; never touches another daemon."""
        with self._lock:
            self._start_locked()

    def open_window(self) -> None:
        """A native Tern window on kitty!'s daemon, showing kitty!'s session."""
        with self._lock:
            binary = self._start_locked()
            self._windows = [window for window in self._windows if window.poll() is None]
            try:
                self._windows.append(subprocess.Popen(
                    [binary, "--dir", str(self.workspace)], env=self._environment(), cwd=self.workspace,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True))
            except OSError:
                raise TernUnavailable("Tern's window could not start") from None

    def status(self) -> dict:
        """Detection plus kitty!'s session; reading never starts anything."""
        found = self.detect()
        result = {"install_url": INSTALL_URL, **found, "workspace": str(self.workspace),
                  "socket": str(self.socket_path), "running": False, "windows": 0, "sessions": []}
        if not found["installed"] or not _listening(self.socket_path):
            return result
        report = self._inspect(found["path"])
        working = {pane.get("pane") for pane in report.get("panes") or [] if pane.get("working") is True}
        try:
            result.update(running=True, windows=sum(
                client.get("kind") == "window" for client in report.get("clients") or []), sessions=[{
                    "id": session["id"], "name": str(session.get("name") or ""), "shown": session.get("shown") is True,
                    "tabs": [{"id": tab["id"], "number": tab.get("number"), "name": tab.get("name"),
                              "shown": tab.get("shown") is True,
                              "blocks": [_block(block, working) for block in tab.get("blocks") or []]}
                             for tab in session.get("tabs") or []],
                } for session in report["sessions"]])
        except (KeyError, TypeError, AttributeError):
            raise TernUnavailable("kitty!'s Tern daemon did not report its sessions") from None
        return result

    # Quit --------------------------------------------------------------------

    def _stop_daemon(self) -> None:
        """Stop the daemon on kitty!'s private socket (and the one this process spawned)."""
        spawned, self._daemon = self._daemon, None
        targets = {spawned.pid} if spawned is not None and spawned.poll() is None else set()
        owner = _socket_owner(self.socket_path)
        if owner is not None:
            targets.add(owner)

        def remaining() -> set[int]:
            if spawned is not None:
                spawned.poll()  # reap it, so it stops counting as running
            return {pid for pid in targets if _running(pid)}

        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in remaining():
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + STOP_TIMEOUT
            while remaining() and time.monotonic() < deadline:
                time.sleep(0.05)
        if spawned is not None:
            spawned.wait()

    def stop(self) -> None:
        """Quit: close kitty!'s Tern windows, then its daemon, if this process used Tern."""
        with self._lock:
            used, self._used = self._used, False
            windows, self._windows = self._windows, []
            if not used:
                return
            for window in windows:
                _terminate_group(window)
            self._stop_daemon()

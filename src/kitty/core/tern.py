"""Stencil Tern (https://stencil.so/tern) as an optional terminal beside rika!'s herdr.

Tern is closed-beta proprietary software: rika! never bundles it. It runs only the
``tern`` the user installed, found on PATH or at the path saved in rika!'s Tern
settings (``.private/tern/settings.json``), and does nothing at all without it.

rika! keeps its own Tern session daemon on a private socket,
``$XDG_RUNTIME_DIR/kitty/tern-<workspace hash>/daemon.sock`` (directory 0700), so
it never reads or changes the user's own Tern daemon and sessions. Only Tern's
public commands are used, always with ``TERN_DAEMON_SOCKET`` set to that socket:

* ``tern daemon --socket PATH`` starts the daemon (on demand, never on startup);
* ``tern new session kitty --cwd WORKSPACE`` gives it rika!'s session;
* ``tern inspect --json`` reads sessions, panes and attached clients for status;
* ``tern --dir WORKSPACE`` opens a native Tern window on that daemon; it shows
  rika!'s session and only starts a fresh session in the workspace when none exists.
  With a window already open on that daemon Tern raises it instead (the new process
  exits), so focusing a pane runs ``tern focus`` and then ``tern --dir``;
* ``tern new tab kitty --cwd WORKSPACE --json`` adds a pane to rika!'s session;
* ``tern new tab kitty --cwd DIR --keep-open -- COMMAND...`` and ``tern focus BLOCK``
  run and show Rika's panes: an approved shell command, or a herdr client that
  surfaces a worker agent's herdr pane in a Tern window;
* ``tern run kitty/ID -- LINE``, ``tern focus kitty/ID`` and
  ``tern capture kitty/ID --scrollback`` act on one pane of rika!'s session (the
  ``kitty/`` prefix makes Tern itself refuse a block of any other session); rika!
  first checks the pane is listed there, accepts one line of text without control
  characters for ``run``, and strips terminal controls from captures;
* ``tern events --filter pane_spawned,pane_exited`` follows the daemon's pane starts
  and exits (with exit status) while it runs, for the recent-activity list;
* ``tern plugin dir|list|link|unlink`` installs and reports the kitty Tern plugin
  (kitty.core.tern_plugin) in the user's plugins folder, which Tern's daemons watch.

On NixOS rika!'s AppImage runs inside appimage-run's FHS sandbox (bubblewrap), whose
own ``/etc/profile`` and ``/usr`` stand in for the system's. Tern starts every pane as
a login shell, which there finds none of the user's programs (no git, nix or python3).
So inside such a sandbox the daemon and the windows run outside it, as transient
systemd user services (``systemd-run --user --wait``) in the user's session
environment plus rika!'s socket, Tern and display variables (by name, never values
on a command line); without ``systemd-run`` they run as rika!'s children, as
elsewhere. Status and session commands are socket clients and stay rika!'s children.

Tern's web client (``tern web serve``) needs WebGPU, which rika!'s WebKitGTK view
does not provide, so rika! shows status and opens native windows instead of
embedding a terminal. Quit stops the windows rika! opened and rika!'s daemon
(Tern keeps the layout in ``daemon.sock.state`` and restores it on the next start),
but only when this process used Tern.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

INSTALL_URL = "https://stencil.so/tern"
SESSION = "kitty"
#: Linux ``sockaddr_un.sun_path`` holds 107 bytes plus the terminating NUL.
SOCKET_PATH_MAX = 107
READY_TIMEOUT = 15.0
STOP_TIMEOUT = 10.0
COMMAND_TIMEOUT = 10.0
#: Tern variables a user sets for every Tern; everything else TERN_* describes the
#: pane or window rika! itself might run in, and would aim commands at it.
_USER_TERN = ("TERN_CONFIG_DIR", "TERN_AUTH_URL", "TERN_BUILD_URL")
#: The terminal rika! itself runs in, and bundle library overlays (see
#: kitty.core.apply.browser._start) that break a separately installed program.
_OUTER = ("TMUX", "TMUX_PANE", "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
          "LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG")
#: One command line typed into a pane, at most this many characters.
MAX_COMMAND = 4096
#: Captured text kept for a quick note (whose limit is 64 KiB), newest output first.
MAX_CAPTURE_BYTES = 60 * 1024
TRIMMED = "[earlier output trimmed]"
EVENT_KINDS = ("pane_spawned", "pane_exited")
MAX_EVENT_LINE = 64 * 1024
#: Pane events kept in memory, and how many the status shows (newest first).
EVENT_BUFFER = 200
ACTIVITY_LIMIT = 20
#: Minimum seconds between two starts of the events follower.
FOLLOW_RETRY = 5.0
#: Terminal control sequences: OSC (to BEL or ST), CSI, other ESC sequences, then
#: any remaining C0/C1 control except tab and newline.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_ESCAPE = re.compile(r"\x1b[ -/]*[0-~]?")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
#: nixpkgs' FHS sandbox (buildFHSEnv, which appimage-run uses) binds the real /etc here.
FHS_HOST_ETC = Path("/.host-etc")
#: rika!'s display a Tern started outside that sandbox keeps; the rest of its
#: environment is the user's session (the systemd user manager's), not rika!'s.
_DISPLAY = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY")


class TernUnavailable(RuntimeError):
    """rika!'s Tern session cannot be used; the message is safe to show."""


class TernMissing(TernUnavailable):
    """No usable ``tern`` is installed (or the configured one is broken)."""


class TernNotRunning(TernUnavailable):
    """rika!'s Tern daemon is not running, so it has no panes to act on."""


class TernNoPane(TernUnavailable):
    """The requested block is not a pane of rika!'s Tern session."""


def pane_id(value: object) -> int:
    """A Tern block ID as ``status`` reports it: a JSON integer, never a string or path."""
    if type(value) is not int or not 0 <= value < 2 ** 64:
        raise ValueError("Expected a pane ID (block number) from rika!'s Tern session")
    return value


def command_line(value: object) -> str:
    """One line of text for ``tern run``: no newline, escape or other control character."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a command to run in the Tern pane")
    if len(value) > MAX_COMMAND or "\n" in value or _CONTROL.search(value):
        raise ValueError(f"A command for Tern is one line of at most {MAX_COMMAND} characters, "
                         "without control characters")
    return value


def plain_text(raw: str) -> str:
    """Terminal output as plain text: no escape sequences or control characters."""
    text = raw.replace("\r\n", "\n")
    for pattern in (_OSC, _CSI, _ESCAPE):
        text = pattern.sub("", text)
    text = _CONTROL.sub("", text.replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def newest(text: str, limit: int = MAX_CAPTURE_BYTES) -> tuple[str, bool]:
    """The end of `text` within `limit` UTF-8 bytes, cut at a line start; and whether it was cut."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text, False
    tail = data[-(limit - len(TRIMMED.encode()) - 1):].decode("utf-8", errors="ignore")
    tail = tail.split("\n", 1)[1] if "\n" in tail else tail
    return f"{TRIMMED}\n{tail}", True


def parse_event(line: bytes) -> dict | None:
    """One ``tern events`` line as an activity entry, or None to skip it."""
    try:
        data = json.loads(line)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("event") not in EVENT_KINDS:
        return None
    pane, status = data.get("pane"), data.get("status")
    if type(pane) is not int or pane < 0 or (status is not None and type(status) is not int):
        return None
    return {"event": data["event"], "pane": pane, "status": status, "at": datetime.now(UTC).isoformat()}


def child_environment(environ: Mapping[str, str], daemon_socket: Path) -> dict[str, str]:
    """Environment for every ``tern`` rika! runs: aimed at rika!'s daemon only."""
    result = {key: value for key, value in environ.items()
              if key not in _OUTER and not key.startswith("HERDR_")
              and (not key.startswith("TERN_") or key in _USER_TERN)}
    result["TERN_DAEMON_SOCKET"] = str(daemon_socket)
    return result


def _host_launcher(environ: Mapping[str, str]) -> str | None:
    """``systemd-run``, when rika! runs in an FHS sandbox that Tern has to run outside."""
    if not FHS_HOST_ETC.is_dir():
        return None
    return shutil.which("systemd-run", path=environ.get("PATH", ""))


class _Child:
    """A detached Tern process: rika!'s own child, or a transient systemd user service
    whose `process` is the ``systemd-run --wait`` that lasts exactly as long as it."""

    def __init__(self, process: subprocess.Popen, stop_service: list[str] | None):
        self.process = process
        self._stop_service = stop_service

    def running(self) -> bool:
        return self.process.poll() is None

    def stop_service(self) -> None:
        """``systemctl --user stop`` the service; killing ``systemd-run`` would leave it running."""
        if self._stop_service is None:
            return
        try:
            subprocess.run(self._stop_service, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=STOP_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


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
    """Create `path` 0700 (parents too), or tighten rika!'s existing directory to 0700."""
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
    """Stop a window rika! started in its own process group."""
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
            "program": block.get("program"), "command": block.get("command"),
            "cols": block.get("cols"), "rows": block.get("rows"),
            "focused": block.get("focused") is True, "live": block.get("live") is True,
            "exited": block.get("exited"), "working": block["id"] in working}


class TernRuntime:
    """Detection, settings and rika!'s own Tern daemon for one workspace."""

    def __init__(self, workspace: Path, private_dir: Path, environ: Mapping[str, str] | None = None):
        self.workspace = workspace
        self.private_dir = private_dir
        self.environ = os.environ if environ is None else environ
        self._lock = threading.Lock()
        self._used = False
        self._daemon: _Child | None = None
        self._windows: list[_Child] = []
        self._versions: dict[tuple, str | None] = {}
        self._events_lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=EVENT_BUFFER)
        self._follower: subprocess.Popen | None = None
        self._next_follow = 0.0

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
        """Which ``tern`` rika! would run: the saved override, else PATH's."""
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
                              + f"Install Stencil Tern from {INSTALL_URL} or set its path in rika!'s Tern view.")
        return found["path"]

    # rika!'s daemon ---------------------------------------------------------

    @property
    def socket_path(self) -> Path:
        runtime = self.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        digest = hashlib.sha256(os.fsencode(self.workspace)).hexdigest()[:12]
        return Path(runtime) / "kitty" / f"tern-{digest}" / "daemon.sock"

    def _environment(self) -> dict[str, str]:
        return child_environment(self.environ, self.socket_path)

    def _spawn(self, binary: str, *arguments: str, unit: str) -> _Child:
        """Start `binary` detached in the workspace; inside an FHS sandbox, outside it as
        the systemd user service `unit` (see the module docstring)."""
        environment = self._environment()
        command, stop_service = [binary, *arguments], None
        launcher = _host_launcher(environment)
        if launcher:
            names = ["TERN_DAEMON_SOCKET", *(key for key in (*_USER_TERN, *_DISPLAY) if key in environment)]
            command = [launcher, "--user", "--wait", "--quiet", "--collect", "--service-type=exec",
                       f"--unit={unit}", f"--working-directory={self.workspace}",
                       *(f"--setenv={key}" for key in names), "--", *command]
            stop_service = [str(Path(launcher).with_name("systemctl")), "--user", "stop", unit]
        process = subprocess.Popen(command, env=environment, cwd=self.workspace, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return _Child(process, stop_service)

    @property
    def _unit(self) -> str:
        """The daemon's systemd unit name, ``kitty-tern-<workspace hash>``; windows add to it."""
        return f"kitty-{self.socket_path.parent.name}"

    def _run(self, binary: str, *arguments: str) -> str:
        try:
            result = subprocess.run([binary, *arguments], env=self._environment(), cwd=self.workspace,
                                    stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                                    errors="replace", timeout=COMMAND_TIMEOUT, check=False)
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
            raise TernUnavailable("rika!'s Tern daemon did not report its sessions") from None

    def _verify_private(self) -> None:
        path = self.socket_path
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise TernUnavailable(f"Tern's socket {path} has unsafe permissions; rika! stopped it")

    def _start_locked(self) -> tuple[str, bool]:
        """Start rika!'s daemon and session if needed: the binary, and whether the session is new."""
        binary = self._binary()
        path = self.socket_path
        if len(os.fsencode(path)) > SOCKET_PATH_MAX:
            raise TernUnavailable(f"Tern's socket path is longer than {SOCKET_PATH_MAX} bytes: {path}")
        _owner_only_directory(path.parent)
        self._used = True
        if not _listening(path):
            if self._daemon is not None and self._daemon.running():
                self._stop_daemon()
            self._daemon = self._spawn(binary, "daemon", "--socket", str(path), unit=self._unit)
            deadline = time.monotonic() + READY_TIMEOUT
            while not _listening(path):
                if not self._daemon.running() or time.monotonic() > deadline:
                    self._stop_daemon()
                    raise TernUnavailable("rika!'s Tern daemon did not start")
                time.sleep(0.05)
        try:
            self._verify_private()
            sessions = self._inspect(binary)["sessions"]
            created = not any(item.get("name") == SESSION for item in sessions)
            if created:
                self._run(binary, "new", "session", SESSION, "--cwd", str(self.workspace), "--json")
        except (TernUnavailable, OSError) as exc:
            self._stop_daemon()
            if isinstance(exc, TernUnavailable):
                raise
            raise TernUnavailable("rika!'s Tern daemon could not be checked") from None
        self._follow(binary)
        return binary, created

    def start(self) -> None:
        """Start rika!'s daemon and session if needed; never touches another daemon."""
        with self._lock:
            self._start_locked()

    def _open_window_locked(self, binary: str) -> None:
        self._windows = [window for window in self._windows if window.running()]
        try:
            self._windows.append(self._spawn(binary, "--dir", str(self.workspace),
                                             unit=f"{self._unit}-window-{os.urandom(4).hex()}"))
        except OSError:
            raise TernUnavailable("Tern's window could not start") from None

    def open_window(self) -> None:
        """A native Tern window on rika!'s daemon, showing rika!'s session."""
        with self._lock:
            self._open_window_locked(self._start_locked()[0])

    # Panes of rika!'s session ----------------------------------------------

    def new_pane(self) -> None:
        """A new pane (tab) in rika!'s session; a session started now is that pane."""
        with self._lock:
            binary, created = self._start_locked()
            if not created:
                self._run(binary, "new", "tab", SESSION, "--cwd", str(self.workspace), "--json")

    def _pane_locked(self, block: int) -> str:
        """The binary, once `block` is a pane of rika!'s session on rika!'s running daemon."""
        binary = self._binary()
        if not _listening(self.socket_path):
            raise TernNotRunning("rika!'s Tern session is not running; open a Tern pane first")
        self._verify_private()
        report = self._inspect(binary)
        self._used = True
        try:
            listed = any(item.get("id") == block for session in report["sessions"] if session.get("name") == SESSION
                         for tab in session.get("tabs") or [] for item in tab.get("blocks") or [])
        except (TypeError, AttributeError):
            raise TernUnavailable("rika!'s Tern daemon did not report its sessions") from None
        if not listed:
            raise TernNoPane(f"rika!'s Tern session has no pane {block}")
        return binary

    def _pane_command(self, binary: str, verb: str, block: int, *arguments: str) -> str:
        try:
            return self._run(binary, verb, f"{SESSION}/{block}", *arguments)
        except TernUnavailable as exc:
            if "no block is called" in str(exc):  # closed since it was listed
                raise TernNoPane(f"rika!'s Tern session has no pane {block}") from None
            raise

    def run(self, block: object, text: object) -> None:
        """Type one command line (and Enter) into a pane of rika!'s session."""
        block, line = pane_id(block), command_line(text)
        with self._lock:
            binary = self._pane_locked(block)
            self._pane_command(binary, "run", block, "--", line)

    def focus(self, block: object) -> None:
        """Show a pane of rika!'s session in rika!'s Tern window, raising it or opening one."""
        block = pane_id(block)
        with self._lock:
            binary = self._pane_locked(block)
            self._pane_command(binary, "focus", block)
            # `tern focus` only changes what the window shows; `tern --dir` hands over to a window
            # already on rika!'s daemon (Tern raises it and the new process exits), else opens one.
            self._open_window_locked(binary)

    def capture(self, block: object) -> tuple[str, bool]:
        """A pane's text with its scrollback, as plain text; the newest output when it is long."""
        block = pane_id(block)
        with self._lock:
            binary = self._pane_locked(block)
            raw = self._pane_command(binary, "capture", block, "--scrollback")
        return newest(plain_text(raw))

    # Pane events ---------------------------------------------------------------

    def _follow(self, binary: str) -> None:
        """Follow rika!'s daemon's pane starts and exits, unless already (or just) following."""
        with self._events_lock:
            if self._follower is not None and self._follower.poll() is None:
                return
            if time.monotonic() < self._next_follow:
                return
            self._next_follow = time.monotonic() + FOLLOW_RETRY
            try:
                process = subprocess.Popen(
                    [binary, "events", "--filter", ",".join(EVENT_KINDS)], env=self._environment(),
                    cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                return
            self._follower = process
        threading.Thread(target=self._read_events, args=(process,), name="kitty-tern-events", daemon=True).start()

    def _read_events(self, process: subprocess.Popen) -> None:
        try:
            for line in iter(lambda: process.stdout.readline(MAX_EVENT_LINE), b""):
                entry = parse_event(line)
                if entry is not None:
                    with self._events_lock:
                        self._events.append(entry)
        except (OSError, ValueError):
            pass  # the stream closed under the reader (Quit)
        finally:
            process.stdout.close()
            _terminate_group(process)

    def activity(self) -> list[dict]:
        """Recent pane starts and exits on rika!'s daemon, newest first."""
        with self._events_lock:
            return [dict(item) for item in reversed(self._events)][:ACTIVITY_LIMIT]

    # Rika's panes (kitty.core.rika) ----------------------------------------------

    def _front_locked(self, binary: str, block: int) -> None:
        """Show `block` in a window, opening one when none shows rika!'s daemon."""
        self._run(binary, "focus", str(block))
        if not any(client.get("kind") == "window" for client in self._inspect(binary).get("clients") or []):
            self._open_window_locked(binary)

    def show(self, block: int) -> None:
        """Bring one pane of rika!'s session to the front of a Tern window (an agent card's Open)."""
        with self._lock:
            self._front_locked(self._start_locked()[0], block)

    def _tab_locked(self, binary: str, command: list[str], cwd: Path) -> int:
        try:
            block = json.loads(self._run(binary, "new", "tab", SESSION, "--cwd", str(cwd), "--keep-open",
                                         "--json", "--", *command))["block"]
            if type(block) is not int:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise TernUnavailable("Tern did not report the new tab") from None
        self._front_locked(binary, block)
        return block

    def run_tab(self, command: list[str], cwd: Path) -> int:
        """`command` in a new tab of rika!'s session, kept open after it ends, in front."""
        with self._lock:
            return self._tab_locked(self._start_locked()[0], command, cwd)

    def show_program(self, command: list[str], cwd: Path) -> int:
        """Bring the live pane running `command` to the front, else start it in a new tab."""
        with self._lock:
            binary = self._start_locked()[0]
            for session in self._inspect(binary)["sessions"]:
                for tab in session.get("tabs") or []:
                    for block in tab.get("blocks") or []:
                        try:
                            running = shlex.split(block.get("command") or "")
                        except ValueError:
                            continue
                        if running == command and block.get("exited") is None and type(block.get("id")) is int:
                            self._front_locked(binary, block["id"])
                            return block["id"]
            return self._tab_locked(binary, command, cwd)

    def status(self) -> dict:
        """Detection plus rika!'s session; reading never starts anything."""
        found = self.detect()
        result = {"install_url": INSTALL_URL, **found, "workspace": str(self.workspace),
                  "socket": str(self.socket_path), "running": False, "windows": 0, "sessions": [],
                  "activity": self.activity()}
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
            raise TernUnavailable("rika!'s Tern daemon did not report its sessions") from None
        self._follow(found["path"])
        result["activity"] = self.activity()
        return result

    # The kitty Tern plugin (kitty.core.tern_plugin) ----------------------------

    def plugin(self, *arguments: str) -> str:
        """``tern plugin ARGS``'s standard output. Like every ``tern`` rika! runs it is aimed at
        rika!'s socket, so its reload reaches only rika!'s daemon; Tern's daemons and windows
        watch the plugins folder and load a change there by themselves."""
        return self._run(self._binary(), "plugin", *arguments)

    # Quit --------------------------------------------------------------------

    def _stop_daemon(self) -> None:
        """Stop the daemon on rika!'s private socket (and the one this process spawned)."""
        spawned, self._daemon = self._daemon, None
        if spawned is not None:
            spawned.stop_service()
        targets = {spawned.process.pid} if spawned is not None and spawned.running() else set()
        owner = _socket_owner(self.socket_path)
        if owner is not None:
            targets.add(owner)

        def remaining() -> set[int]:
            if spawned is not None:
                spawned.process.poll()  # reap it, so it stops counting as running
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
            spawned.process.wait()

    def stop(self) -> None:
        """Quit: stop following events; close rika!'s Tern windows, then its daemon, if this process used Tern."""
        with self._events_lock:
            follower, self._follower = self._follower, None
        if follower is not None:
            _terminate_group(follower)
        with self._lock:
            used, self._used = self._used, False
            windows, self._windows = self._windows, []
            if not used:
                return
            for window in windows:
                window.stop_service()
                _terminate_group(window.process)
            self._stop_daemon()

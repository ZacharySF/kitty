"""Synthetic Stencil Tern stand-in; never the real ``tern`` or anyone's Tern daemon.

The fake mirrors only the Tern 0.9.8 surface rika! uses:

* ``tern --version`` prints ``tern 0.9.8 (f21d9a3)``;
* ``tern daemon --socket PATH`` serves sessions on a Unix socket (created 0600 like
  Tern's) and stops on SIGTERM, removing the socket;
* ``tern inspect --json`` and ``tern new session NAME --cwd DIR --json`` talk to the
  daemon named by ``$TERN_DAEMON_SOCKET`` and fail like Tern when none listens;
* ``tern --dir DIR`` is a native window: it attaches to that daemon as a window
  client until it is terminated.

Beside it, fake ``systemd-run --user --wait`` and ``systemctl --user stop`` stand in
for the user's systemd manager, which runs a command as a transient service in the
manager's own environment (``MANAGER``) plus the ``--setenv`` names; killing
``systemd-run`` leaves the service running, as with the real one.

Every invocation is appended to a JSON-lines log (argv, environment, cwd, pid) so
tests can prove what ran, against which daemon, and that nothing outlived Quit.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

FAKE_TERN = r'''
import json, os, signal, socket, sys, threading, time
LOG = {log!r}
MODE_FILE = {mode_file!r}
argv = sys.argv[1:]
with open(LOG, "a") as handle:
    handle.write(json.dumps({{"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "pid": os.getpid()}}) + "\n")
mode = open(MODE_FILE).read().strip() if os.path.exists(MODE_FILE) else ""

def target():
    return os.environ.get("TERN_DAEMON_SOCKET", "/nonexistent/tern/daemon.sock")

def connect():
    client = socket.socket(socket.AF_UNIX)
    try:
        client.connect(target())
    except OSError as exc:
        sys.stderr.write("tern %s: no Tern is running (no session daemon on %s: %s)\n" % (argv[0], target(), exc))
        sys.exit(1)
    return client

def request(message):
    client = connect()
    client.sendall((json.dumps(message) + "\n").encode())
    data = b""
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        data += chunk
    return json.loads(data)

def daemon(path):
    if mode == "hang":
        time.sleep(3600)
    state_path = path + ".state"
    state = json.load(open(state_path)) if os.path.exists(state_path) else {{"sessions": [], "next": 1}}
    windows = []
    lock = threading.Lock()
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX)
    old = os.umask(0o077)
    server.bind(path)
    os.umask(old)
    os.chmod(path, 0o666 if mode == "unsafe" else 0o600)
    server.listen(16)

    def stop(*_):
        try:
            os.unlink(path)
        except OSError:
            pass
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)

    def save():
        with open(state_path, "w") as handle:
            json.dump(state, handle)

    def serve(connection):
        reader = connection.makefile("rb")
        line = reader.readline()
        if not line:
            connection.close()
            return
        message = json.loads(line)
        if message["op"] == "window":
            with lock:
                windows.append(message["pid"])
            reader.read()  # until the window goes away
            with lock:
                windows.remove(message["pid"])
            connection.close()
            return
        with lock:
            if message["op"] == "new":
                ids = []
                for _ in range(3):
                    ids.append(state["next"])
                    state["next"] += 1
                state["sessions"].append({{"id": ids[0], "name": message["name"], "shown": not state["sessions"],
                    "tabs": [{{"id": ids[1], "number": 1, "name": None, "shown": True, "zoomed": False,
                    "blocks": [{{"id": ids[2], "title": message["cwd"], "cwd": message["cwd"], "program": "/bin/sh",
                    "args": [], "command": None, "cols": 80, "rows": 24, "exited": None, "keep_open": False,
                    "focused": True, "live": True}}], "splits": {{"Leaf": ids[2]}}}}]}})
                save()
                reply = {{"session": ids[0], "tab": ids[1], "block": ids[2]}}
            else:
                panes = [{{"pane": block["id"], "working": block["id"] == 3, "quiet_ms": 10}}
                         for session in state["sessions"] for tab in session["tabs"] for block in tab["blocks"]]
                reply = {{"sessions": state["sessions"], "detached": [], "panes": panes,
                          "clients": [{{"conn": pid, "window": "", "kind": "window"}} for pid in windows]
                                     + [{{"conn": 0, "window": "", "kind": "cli"}}]}}
        connection.sendall(json.dumps(reply).encode())
        connection.close()

    while True:
        connection, _ = server.accept()
        threading.Thread(target=serve, args=(connection,), daemon=True).start()

if argv in (["--version"], ["-V"]):
    print("not tern 1.0" if mode == "impostor" else "tern 0.9.8 (f21d9a3)")
elif argv[:1] == ["daemon"] and "--socket" in argv:
    daemon(argv[argv.index("--socket") + 1])
elif argv == ["inspect", "--json"]:
    if mode == "garbled":
        connect().close()
        print("not json")
    else:
        print(json.dumps(request({{"op": "inspect"}})))
elif argv[:2] == ["new", "session"] and "--cwd" in argv:
    print(json.dumps(request({{"op": "new", "name": argv[2], "cwd": argv[argv.index("--cwd") + 1]}})))
elif argv[:1] == ["--dir"] and len(argv) == 2:
    client = connect()
    client.sendall((json.dumps({{"op": "window", "pid": os.getpid()}}) + "\n").encode())
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    while True:
        time.sleep(3600)
else:
    sys.stderr.write("fake tern: unsupported arguments %r\n" % (argv,))
    sys.exit(2)
'''

FAKE_SYSTEMD = r'''
import json, os, signal, subprocess, sys, time
LOG = {log!r}
UNITS = {units!r}
MANAGER = {manager!r}
name = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
with open(LOG, "a") as handle:
    handle.write(json.dumps({{"argv": [name, *argv], "env": dict(os.environ), "cwd": os.getcwd(), "pid": os.getpid()}}) + "\n")

def alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False

def unit_pid(unit):
    try:
        with open(os.path.join(UNITS, unit)) as handle:
            return int(handle.read())
    except (OSError, ValueError):
        return None

if name == "systemctl":
    if argv[:2] != ["--user", "stop"]:
        sys.stderr.write("fake systemctl: unsupported arguments %r\n" % (argv,))
        sys.exit(2)
    for unit in argv[2:]:
        pid = unit_pid(unit)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if pid is None or not alive(pid):
                break
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + 5
            while alive(pid) and time.monotonic() < deadline:
                time.sleep(0.02)
        if pid is not None and not alive(pid):
            try:
                os.unlink(os.path.join(UNITS, unit))
            except FileNotFoundError:
                pass
    sys.exit(0)

split = argv.index("--")
options, command = argv[:split], argv[split + 1:]
if options[:1] != ["--user"] or "--wait" not in options:
    sys.stderr.write("fake systemd-run: unsupported options %r\n" % (options,))
    sys.exit(2)
unit = cwd = None
env = dict(MANAGER)
for option in options:
    key, _, value = option.partition("=")
    if key == "--unit":
        unit = value
    elif key == "--working-directory":
        cwd = value
    elif key == "--setenv":
        variable, given, text = value.partition("=")
        env[variable] = text if given else os.environ[variable]
running = unit_pid(unit)
if running is not None and alive(running):
    sys.stderr.write("Unit %s.service was already loaded\n" % unit)
    sys.exit(1)
child = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.DEVNULL, start_new_session=True)
with open(os.path.join(UNITS, unit), "w") as handle:
    handle.write(str(child.pid))
signal.signal(signal.SIGTERM, lambda *_: os._exit(143))
status = child.wait()
try:
    os.unlink(os.path.join(UNITS, unit))
except FileNotFoundError:
    pass  # stopped by systemctl
sys.exit(status)
'''


class FakeTern:
    """Executable fakes of ``tern`` and the systemd tools in one bin directory, plus a short runtime dir."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.log = folder / "tern-invocations.jsonl"
        #: "hang" (daemon never listens), "unsafe" (socket readable by others),
        #: "impostor" (--version is not Tern's), "garbled" (inspect prints junk).
        self.mode_file = folder / "tern-mode"
        self.bin = folder / "tern-bin"
        self.bin.mkdir()
        self.binary = self.bin / "tern"
        self.binary.write_text(f"#!{sys.executable}\n" + textwrap.dedent(
            FAKE_TERN.format(log=str(self.log), mode_file=str(self.mode_file))))
        self.binary.chmod(0o755)
        # sun_path is 108 bytes; a short XDG_RUNTIME_DIR keeps rika!'s Tern socket addressable.
        self.runtime = Path(tempfile.mkdtemp(prefix="kt", dir="/tmp"))
        os.chmod(self.runtime, 0o700)
        self._daemons: list[subprocess.Popen] = []
        # The user's systemd manager: its environment is the desktop session's, not rika!'s
        # (nix-ld's variables let this test's interpreter start on NixOS, as they do there).
        self.units = folder / "systemd-units"
        self.units.mkdir()
        self.manager = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/"), "SYSTEMD_MANAGER_ENV": "1",
                        **{key: os.environ[key] for key in ("NIX_LD", "NIX_LD_LIBRARY_PATH") if key in os.environ}}
        systemd = f"#!{sys.executable}\n" + textwrap.dedent(
            FAKE_SYSTEMD.format(log=str(self.log), units=str(self.units), manager=self.manager))
        for tool in ("systemd-run", "systemctl"):
            (self.bin / tool).write_text(systemd)
            (self.bin / tool).chmod(0o755)

    def mode(self, value: str) -> None:
        self.mode_file.write_text(value)

    def invocations(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def calls(self) -> list[list[str]]:
        return [item["argv"] for item in self.invocations()]

    def start_daemon(self, path: Path) -> subprocess.Popen:
        """Someone else's daemon (the user's own Tern) on `path`, outside rika!."""
        process = subprocess.Popen([str(self.binary), "daemon", "--socket", str(path)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        self._daemons.append(process)
        wait_listening(path)
        return process

    def run(self, *arguments: str, socket_path: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ, TERN_DAEMON_SOCKET=str(socket_path))
        return subprocess.run([str(self.binary), *arguments], env=env, capture_output=True, text=True, timeout=10)

    def cleanup(self) -> None:
        """Stop every fake process a test (or a failed test) left behind."""
        for process in self._daemons:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        for item in self.invocations():
            if alive(item["pid"]):
                try:
                    os.kill(item["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass  # exited since (a systemd-run whose service just stopped)


def listening(path: Path) -> bool:
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def wait_listening(path: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not listening(path):
        assert time.monotonic() < deadline, f"nothing listens on {path}"
        time.sleep(0.02)


def alive(pid: int) -> bool:
    """True while `pid` runs (a zombie counts as gone)."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False

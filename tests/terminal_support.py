"""Synthetic herdr stand-in, a rika! site harness and a minimal RFC 6455 client.

The fake mirrors only the herdr v0.9.5 surface rika! uses: ``herdr --session NAME``
launches (or attaches to) a detached server and runs a client on its terminal;
``herdr --session NAME session stop NAME`` stops that server. Sockets live where
herdr keeps them (``$XDG_CONFIG_HOME/herdr/sessions/NAME``). Every invocation is
appended to a JSON-lines log so tests can prove what ran, with which environment.
"""
from __future__ import annotations

import base64
import http.client
import json
import re
import secrets
import socket
import struct
import sys
import tempfile
import textwrap
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from kitty import web_auth
from kitty.core.workspace import open_workspace
from kitty.settings import Settings
from kitty.web_server import LocalHTTPServer, WebApplication

FAKE_HERDR = r'''
import json, os, select, signal, socket, struct, subprocess, sys, termios, time, fcntl, tty
LOG = {log!r}
MODE_FILE = {mode_file!r}
ACTIVITY_FILE = {activity_file!r}
AGENT_ERROR = {agent_error!r}
SKILL = {skill!r}
argv = sys.argv[1:]
with open(LOG, "a") as handle:
    handle.write(json.dumps({{"argv": argv, "env": dict(os.environ), "cwd": os.getcwd()}}) + "\n")
if argv[:1] != ["--session"] or len(argv) < 2:
    sys.exit(2)
name = argv[1]
rest = argv[2:]
base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.environ["HOME"], ".config")
folder = os.path.join(base, "herdr", "sessions", name)
api, client = os.path.join(folder, "herdr.sock"), os.path.join(folder, "herdr-client.sock")

def listening(path):
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()

if rest == ["server"]:
    os.makedirs(folder, exist_ok=True)
    mode = int(open(MODE_FILE).read(), 8) if os.path.exists(MODE_FILE) else 0o600
    servers = []
    for path in (api, client):
        if os.path.exists(path):
            os.unlink(path)
        server = socket.socket(socket.AF_UNIX)
        server.bind(path)
        os.chmod(path, mode)
        server.listen(8)
        servers.append(server)
    while True:
        ready, _, _ = select.select(servers, [], [])
        for server in ready:
            connection, _ = server.accept()
            if server is servers[0] and connection.recv(16) == b"stop":
                for path in (api, client):
                    os.unlink(path)
                connection.sendall(b"ok")
                sys.exit(0)
            connection.close()
elif rest == ["session", "stop", name]:
    if not listening(api):
        sys.exit(1)
    stopper = socket.socket(socket.AF_UNIX)
    stopper.connect(api)
    stopper.sendall(b"stop")
    stopper.recv(16)
    sys.exit(0)
elif rest == ["--skill"]:
    sys.stdout.write(SKILL)
elif rest[:2] == ["tab", "create"]:
    def option(key):
        return rest[rest.index(key) + 1]
    wid = option("--workspace")
    activity = json.load(open(ACTIVITY_FILE))
    number = len(activity.get("agents", [])) + 2
    tab, pane = wid + ":t" + str(number), wid + ":p" + str(number)
    print(json.dumps({{"result": {{"tab": {{"tab_id": tab, "label": option("--label")}},
                                  "root_pane": {{"pane_id": pane}}}}}}))
elif rest[:2] == ["agent", "start"]:
    if os.path.exists(AGENT_ERROR):
        print(json.dumps({{"error": {{"code": open(AGENT_ERROR).read(), "message": "not ready"}}}}), file=sys.stderr)
        sys.exit(1)
    kind = rest[rest.index("--kind") + 1]
    pane = rest[rest.index("--pane") + 1]
    activity = json.load(open(ACTIVITY_FILE))
    agent = {{"workspace_id": pane.split(":")[0], "pane_id": pane, "name": rest[2],
              "agent": kind, "agent_status": "idle"}}
    activity.setdefault("agents", []).append(agent)
    with open(ACTIVITY_FILE, "w") as handle:
        json.dump(activity, handle)
    print(json.dumps({{"result": {{"agent": agent}}}}))
elif rest == []:
    if not listening(client):
        subprocess.Popen([sys.executable, __file__, "--session", name, "server"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while not listening(client) and time.time() < deadline:
            time.sleep(0.02)
    tty.setraw(0)
    os.write(1, ("FAKE-HERDR session=" + name + "\r\n").encode())
    last = None
    while True:
        rows, cols = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))[:2]
        if (cols, rows) != last:
            last = (cols, rows)
            os.write(1, ("SIZE %dx%d\r\n" % last).encode())
        ready, _, _ = select.select([0], [], [], 0.05)
        if ready:
            try:
                data = os.read(0, 4096)
            except OSError:
                sys.exit(0)
            if not data:
                sys.exit(0)
            os.write(1, b"ECHO:" + data + b"\r\n")
elif rest in (["workspace", "list"], ["agent", "list"]):
    if not listening(api):
        sys.stderr.write("error: no running herdr server for session " + name + "\n")
        sys.exit(1)
    kind = rest[0]
    activity = open(ACTIVITY_FILE).read() if os.path.exists(ACTIVITY_FILE) else "{{}}"
    if activity.startswith("RAW:"):
        sys.stdout.write(activity[4:])
        sys.exit(0)
    items = json.loads(activity).get(kind + "s", [])
    print(json.dumps({{"id": "cli:" + kind + ":list", "result": {{"type": kind + "_list", kind + "s": items}}}}))
elif rest[:2] == ["agent", "read"] or rest[:2] == ["agent", "focus"]:
    if not listening(api):
        sys.exit(1)
    agents = json.load(open(ACTIVITY_FILE)).get("agents", [])
    agent = next((item for item in agents if item.get("pane_id") == rest[2]), None)
    if agent is None:
        print(json.dumps({{"error": {{"code": "agent_not_found", "message": "no agent"}}}}), file=sys.stderr)
        sys.exit(1)
    if rest[1] == "read":
        # The fixture's own "screen" key stands in for the pane's recent output.
        sys.stdout.write(agent.get("screen", ""))
    else:
        print(json.dumps({{"result": {{"agent": agent}}}}))
else:
    sys.exit(2)
'''


class FakeHerdr:
    """An executable fake herdr plus the isolated HOME it keeps its sessions under."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.log = folder / "invocations.jsonl"
        self.mode_file = folder / "socket-mode"
        #: JSON {"workspaces": [...], "agents": [...]} answered by `workspace/agent list`;
        #: text starting with "RAW:" is printed verbatim instead.
        self.activity_file = folder / "activity.json"
        self.agent_error = folder / "agent-error"
        self.skill = "---\nname: herdr\n---\n# Synthetic bundled Herdr skill\nUse explicit pane IDs.\n"
        self.binary = folder / "herdr"
        # sun_path is 108 bytes; a short HOME keeps herdr's session sockets addressable.
        self.home = Path(tempfile.mkdtemp(prefix="kt", dir="/tmp"))
        self.binary.write_text(f"#!{sys.executable}\n" + textwrap.dedent(
            FAKE_HERDR.format(log=str(self.log), mode_file=str(self.mode_file),
                              activity_file=str(self.activity_file), agent_error=str(self.agent_error),
                              skill=self.skill)))
        self.binary.chmod(0o755)

    def session_dir(self, name: str) -> Path:
        return self.home / ".config" / "herdr" / "sessions" / name

    def invocations(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def listening(self, name: str) -> bool:
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.connect(str(self.session_dir(name) / "herdr-client.sock"))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def cleanup(self, name: str) -> None:
        """Stop a fake server a failed test left behind (never a real herdr)."""
        api = self.session_dir(name) / "herdr.sock"
        if api.exists():
            stopper = socket.socket(socket.AF_UNIX)
            try:
                stopper.connect(str(api))
                stopper.sendall(b"stop")
                stopper.recv(16)
            except OSError:
                pass
            finally:
                stopper.close()


def websocket(port: int, path: str, headers: dict[str, str | None], *,
              secret: str | None = None) -> tuple[int, dict[str, str], socket.socket, bytes]:
    """Send an upgrade request; return (status, headers, socket, leftover bytes).

    With `secret`, the upgrade offers the UI secret as rika!'s page does (kitty.web_auth).
    """
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    fields = {"Host": f"127.0.0.1:{port}", "Upgrade": "websocket", "Connection": "Upgrade",
              "Sec-WebSocket-Key": key, "Sec-WebSocket-Version": "13", "Origin": f"http://127.0.0.1:{port}"}
    if secret is not None:
        fields["Sec-WebSocket-Protocol"] = f"{web_auth.PROTOCOL}, {web_auth.PROTOCOL_PREFIX}{secret}"
    fields.update(headers)
    lines = [f"GET {path} HTTP/1.1"] + [f"{name}: {value}" for name, value in fields.items() if value is not None]
    connection = socket.create_connection(("127.0.0.1", port), timeout=10)
    connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    status = int(status_line.split()[1]) if status_line else 0
    parsed = {}
    for line in header_lines:
        name, _, value = line.partition(":")
        parsed[name.strip().lower()] = value.strip()
    parsed["status-line"] = status_line
    if status != 101:
        # A refusal is an ordinary HTTP response; return its whole JSON body.
        while len(rest) < int(parsed.get("content-length", 0)):
            chunk = connection.recv(4096)
            if not chunk:
                break
            rest += chunk
    return status, parsed, connection, rest


def send_frame(connection: socket.socket, opcode: int, payload: bytes, *, mask: bool = True) -> None:
    header = bytearray([0x80 | opcode])
    length = len(payload)
    flag = 0x80 if mask else 0
    if length < 126:
        header.append(flag | length)
    elif length < 65536:
        header.append(flag | 126)
        header += struct.pack("!H", length)
    else:
        header.append(flag | 127)
        header += struct.pack("!Q", length)
    if mask:
        key = secrets.token_bytes(4)
        header += key
        payload = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
    connection.sendall(bytes(header) + payload)


class FrameReader:
    """Reads unmasked server frames from a socket (plus bytes already received)."""

    def __init__(self, connection: socket.socket, initial: bytes = b""):
        self.connection = connection
        self.buffer = initial
        #: Terminal output received but not yet returned by `until`.
        self.output = b""

    def _take(self, count: int, deadline: float) -> bytes:
        while len(self.buffer) < count:
            self.connection.settimeout(max(0.01, deadline - time.time()))
            chunk = self.connection.recv(65536)
            if not chunk:
                raise EOFError("connection closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def frame(self, timeout: float = 10) -> tuple[int, bytes]:
        deadline = time.time() + timeout
        first, second = self._take(2, deadline)
        length = second & 0x7F
        assert not second & 0x80, "server frames must not be masked"
        if length == 126:
            length = struct.unpack("!H", self._take(2, deadline))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._take(8, deadline))[0]
        return first & 0x0F, self._take(length, deadline)

    def until(self, needle: bytes, timeout: float = 10) -> bytes:
        """Terminal output up to and including `needle`; later output stays queued."""
        deadline = time.time() + timeout
        while needle not in self.output:
            opcode, payload = self.frame(max(0.01, deadline - time.time()))
            if opcode == 2:
                self.output += payload
            elif opcode == 8:
                raise EOFError(f"closed before {needle!r}: {self.output[-200:]!r}")
        end = self.output.index(needle) + len(needle)
        seen, self.output = self.output[:end], self.output[end:]
        return seen

    def match(self, pattern: re.Pattern[bytes], timeout: float = 10) -> re.Match[bytes]:
        """Wait for terminal output matching `pattern`; consume through the match."""
        deadline = time.time() + timeout
        while (found := pattern.search(self.output)) is None:
            opcode, payload = self.frame(max(0.01, deadline - time.time()))
            if opcode == 2:
                self.output += payload
            elif opcode == 8:
                raise EOFError(f"closed before {pattern.pattern!r}: {self.output[-200:]!r}")
        self.output = self.output[found.end():]
        return found

    def closed(self, timeout: float = 10) -> bytes:
        """Skip terminal output until the server's close frame; return its payload."""
        deadline = time.time() + timeout
        while True:
            opcode, payload = self.frame(max(0.01, deadline - time.time()))
            if opcode == 8:
                return payload


def make_root(tmp_path: Path, modules: tuple[str, ...]) -> Settings:
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": list(modules)}))
    return Settings.load(root=root, db_path=root / "data" / "web.db")


class Site:
    def __init__(self, settings: Settings, server_class=LocalHTTPServer):
        static = settings.root / "dist"
        static.mkdir(exist_ok=True)
        (static / "index.html").write_text("<!doctype html><title>Synthetic</title>")
        with open_workspace(settings) as workspace:
            config = workspace.config
        self.app = WebApplication(settings, config, static)
        self.server = server_class(self.app, 0)
        self.port = self.server.server_address[1]
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    @property
    def secret(self) -> str:
        """This launch's UI secret, as rika!'s own window holds it (kitty.web_auth)."""
        return self.server.ui_auth.secret

    def request(self, path, body=None, headers=None, method=None):
        fields = {"Origin": self.server.origin, web_auth.HEADER: self.secret}
        payload = None
        if body is not None:
            fields.update({"Content-Type": "application/json", "X-Kitty-Request": "1"})
            payload = json.dumps(body).encode()
        for key, value in (headers or {}).items():
            if value is None:
                fields.pop(key, None)
            else:
                fields[key] = value
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            client.request(method or ("POST" if body is not None else "GET"), path, body=payload, headers=fields)
            response = client.getresponse()
            data = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(data) if data else None
            return response.status, data
        finally:
            client.close()

    def token(self) -> str:
        status, body = self.request("/api/terminal/session", {})
        assert status == 200, body
        assert body["socket"] == "/api/terminal/socket"
        return body["token"]

    def connect(self, token: str | None, *, query: str = "", headers=None):
        path = "/api/terminal/socket" + (f"?token={token}{query}" if token is not None else "")
        return websocket(self.port, path, headers or {}, secret=self.secret)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        self.app.close()


@contextmanager
def site(settings: Settings, server_class=LocalHTTPServer):
    running = Site(settings, server_class)
    try:
        yield running
    finally:
        running.close()


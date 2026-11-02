"""kitty!'s own herdr runtime: one named session with a private config and socket.

herdr (https://github.com/herdrdev/herdr, Apache-2.0) is bundled at the version
pinned in ``desktop/fetch_herdr.py``; kitty! never runs a herdr found on PATH and
never uses herdr's ``default`` session. Only herdr's public commands are used:

* ``herdr --session NAME`` starts the session's server when it is not running (as a
  detached daemon) and attaches a client on its terminal;
* ``herdr --session NAME session stop NAME`` stops that server and its panes.

The session's sockets live where herdr keeps every named session,
``$XDG_CONFIG_HOME/herdr/sessions/NAME`` (default ``~/.config/herdr``), so agents
can reach it with ``herdr --session NAME``. kitty! creates that directory owner-only
and refuses a session whose sockets are readable by anyone else. The Harness theme
lives in kitty!'s own ``.private/herdr/config.toml`` (``HERDR_CONFIG_PATH``), so the
user's own herdr configuration is never read or changed.

Nothing here starts until a page attaches; ``stop`` stops the session only when this
process used it.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import socket
import stat
import struct
import subprocess
import tempfile
import termios
import threading
import time
from collections.abc import Mapping
from pathlib import Path

#: Linux ``sockaddr_un.sun_path`` holds 107 bytes plus the terminating NUL.
SOCKET_PATH_MAX = 107
READY_TIMEOUT = 15.0
STOP_TIMEOUT = 30.0
#: Variables describing the terminal kitty! itself runs in. The pty kitty! creates is
#: none of these; herdr refuses nested launches while HERDR_ENV=1 is inherited.
_OUTER_TERMINAL = ("TMUX", "TMUX_PANE", "TERM_PROGRAM", "TERM_PROGRAM_VERSION")
_SESSION_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")
#: herdr's ``AgentStatus`` values; anything else is shown as ``unknown``.
AGENT_STATES = ("idle", "working", "blocked", "done", "unknown")
ACTIVITY_TIMEOUT = 5.0
#: Longest task title or output line the dock's agent cards receive.
LINE_MAX = 200
#: Box-drawing, block and braille glyphs agents use for their own chrome.
_CHROME = re.compile(r"[\u2500-\u259f\u2800-\u28ff]+")
#: Agents with a native startup-instruction option; the executable stays user-installed.
CODING_AGENTS = ("omp", "claude", "codex")

#: herdr config for kitty!'s session: the Harness palette (web/src/lib/themes/catalog.ts) on
#: herdr's terminal base, with transparent panels so the page's surface shows through.
#: Updates arrive only with kitty!'s signed releases, so herdr's own checks are off.
HARNESS_CONFIG = """\
# Written by kitty! for its own herdr session; replaced whenever kitty! starts it.
onboarding = false

[theme]
name = "terminal"

[theme.custom]
panel_bg = "reset"
sidebar_bg = "reset"
surface_dim = "#1c1c1c"
surface0 = "#262626"
surface1 = "#434343"
active_row_bg = "#262626"
selection_bg = "#434343"
overlay0 = "#8d8d8d"
overlay1 = "#b4b4b4"
text = "#e6e6e6"
subtext0 = "#b4b4b4"
accent = "#deb2c3"
mauve = "#c6b5f0"
peach = "#e4bdcc"
green = "#a8d5b5"
yellow = "#e8c872"
red = "#ee928c"
blue = "#9cc7f0"
teal = "#9cc7f0"

[update]
version_check = false
manifest_check = false
"""


class TerminalUnavailable(RuntimeError):
    """kitty!'s herdr session cannot be used; the message is safe to show."""


class TerminalNotRunning(TerminalUnavailable):
    """The page must attach its terminal before creating an agent tab."""


def validate_session_name(name: str) -> str:
    """herdr's session-name rules, minus its ``default`` session."""
    if not _SESSION_NAME.fullmatch(name) or name in {".", "..", "default"}:
        raise TerminalUnavailable(
            "The herdr session name must be 1-64 letters, digits, '.', '_' or '-', "
            "and not herdr's default session")
    return name


def herdr_config_dir(environ: Mapping[str, str]) -> Path:
    """herdr's config directory, resolved exactly as herdr v0.9.5 does on Linux."""
    if "XDG_CONFIG_HOME" in environ:
        return Path(environ["XDG_CONFIG_HOME"]) / "herdr"
    return Path(environ["HOME"]) / ".config" / "herdr"


def child_environment(environ: Mapping[str, str], config_path: Path) -> dict[str, str]:
    """Environment for kitty!'s herdr: nothing from an outer herdr or terminal."""
    result = {key: value for key, value in environ.items()
              if not key.startswith("HERDR_") and key not in _OUTER_TERMINAL}
    result.update(HERDR_CONFIG_PATH=str(config_path), TERM="xterm-256color", COLORTERM="truecolor")
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
    """Create `path` 0700, or tighten kitty!'s existing directory to 0700."""
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise TerminalUnavailable(f"{path} is not a directory owned by this user")
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


class TerminalClient:
    """One herdr client on a pty; the page stream owning it closes it on detach."""

    def __init__(self, master: int, process: subprocess.Popen):
        self.master = master
        self.process = process

    def resize(self, cols: int, rows: int) -> None:
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def close(self) -> None:
        """Detach: the client exits on hangup; the session keeps running."""
        if self.master >= 0:
            os.close(self.master)
            self.master = -1
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class HerdrRuntime:
    """kitty!'s named herdr session; clients attach lazily, ``stop`` ends it."""

    def __init__(self, binary: str | None, session: str, private_dir: Path,
                 environ: Mapping[str, str] | None = None):
        self.binary = binary
        self.session = session
        self.private_dir = private_dir
        self.environ = os.environ if environ is None else environ
        self._lock = threading.Lock()
        self._used = False
        #: pane -> (state, epoch ms it was first seen in); the dock cards' running timers.
        self._states: dict[str, tuple[str, int]] = {}
        self._states_lock = threading.Lock()

    @property
    def config_path(self) -> Path:
        return self.private_dir / "config.toml"

    def _command(self, *arguments: str) -> list[str]:
        return [self.binary, "--session", self.session, *arguments]

    def _session_folder(self) -> Path:
        """Where herdr keeps this session's sockets, once the runtime and name are valid."""
        if not self.binary or not os.access(self.binary, os.X_OK) or not Path(self.binary).is_file():
            raise TerminalUnavailable("The bundled herdr runtime is missing; reinstall kitty!")
        validate_session_name(self.session)
        return herdr_config_dir(self.environ) / "sessions" / self.session

    def _prepare(self) -> tuple[dict[str, str], Path]:
        folder = self._session_folder()
        client_socket = folder / "herdr-client.sock"
        if len(os.fsencode(client_socket)) > SOCKET_PATH_MAX:
            raise TerminalUnavailable(f"herdr's socket path is longer than {SOCKET_PATH_MAX} bytes: {client_socket}")
        # Parents may be the user's own herdr directories: create them private, never chmod them.
        folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _owner_only_directory(folder)
        self.private_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _owner_only_directory(self.private_dir)
        _write_private(self.config_path, HARNESS_CONFIG)
        return child_environment(self.environ, self.config_path), folder

    @staticmethod
    def _verify_private(folder: Path) -> None:
        if stat.S_IMODE(folder.lstat().st_mode) != 0o700:
            raise TerminalUnavailable(f"herdr session directory {folder} lost its owner-only permissions")
        for name in ("herdr.sock", "herdr-client.sock"):
            info = (folder / name).lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise TerminalUnavailable(f"herdr socket {folder / name} has unsafe permissions; kitty! stopped it")

    def attach(self, cols: int, rows: int) -> TerminalClient:
        """Start the session if needed and attach a new client of the given size."""
        with self._lock:
            environment, folder = self._prepare()
            master, slave = os.openpty()
            try:
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                process = subprocess.Popen(
                    self._command(), stdin=slave, stdout=slave, stderr=slave, env=environment,
                    cwd=self.environ.get("HOME") or "/", start_new_session=True)
            except BaseException:
                os.close(master)
                raise
            finally:
                os.close(slave)
            self._used = True
            client = TerminalClient(master, process)
            try:
                deadline = time.monotonic() + READY_TIMEOUT
                while not _listening(folder / "herdr-client.sock"):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise TerminalUnavailable("kitty!'s herdr session did not start")
                    time.sleep(0.02)
                self._verify_private(folder)
            except BaseException:
                client.close()
                self._stop_session()
                self._used = False
                raise
            return client

    def _stop_session(self) -> None:
        try:
            subprocess.run(self._command("session", "stop", self.session), env=child_environment(
                self.environ, self.config_path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=STOP_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass  # Nothing else may stop it: kitty! never signals a herdr it did not spawn.

    def _read(self, *arguments: str) -> dict:
        """One read-only herdr CLI call against kitty!'s running session."""
        try:
            result = subprocess.run(
                self._command(*arguments), env=child_environment(self.environ, self.config_path),
                stdin=subprocess.DEVNULL, capture_output=True, timeout=ACTIVITY_TIMEOUT, check=False)
            if result.returncode:
                raise ValueError(result.returncode)
            return json.loads(result.stdout)["result"]
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            raise TerminalUnavailable("kitty!'s herdr session did not report its agent states") from None

    def _last_line(self, pane: str) -> str:
        """The pane's last line of real output: blank lines and box-drawing chrome skipped."""
        try:
            result = subprocess.run(
                self._command("agent", "read", pane, "--source", "recent", "--lines", "40", "--format", "text"),
                env=child_environment(self.environ, self.config_path), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=ACTIVITY_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode:
            return ""
        for line in reversed(result.stdout.splitlines()):
            text = " ".join(_CHROME.sub(" ", line).split())
            if sum(character.isalnum() for character in text) >= 3:
                return text[:LINE_MAX]
        return ""

    def activity(self) -> dict:
        """herdr's own agent states per workspace, with each agent's task, last output
        line, output revision and when its state began; never starts the session."""
        folder = self._session_folder()
        if not _listening(folder / "herdr.sock"):
            return {"running": False, "workspaces": []}

        def state(item: dict) -> str:
            value = item.get("agent_status")
            return value if value in AGENT_STATES else "unknown"

        now = int(time.time() * 1000)
        seen: dict[str, tuple[str, int]] = {}

        def card(agent: dict) -> dict:
            pane, status = str(agent["pane_id"]), state(agent)
            with self._states_lock:
                previous = self._states.get(pane)
            since = previous[1] if previous and previous[0] == status else now
            seen[pane] = (status, since)
            revision = agent.get("revision")
            return {
                "pane": pane,
                "name": str(agent.get("name") or agent.get("display_agent") or agent.get("agent") or "agent"),
                "status": status,
                "task": str(agent.get("terminal_title_stripped") or agent.get("terminal_title") or "").strip()[:LINE_MAX],
                "line": self._last_line(pane),
                "revision": revision if type(revision) is int else 0,
                "since": since,
            }

        try:
            workspaces = self._read("workspace", "list")["workspaces"]
            agents = self._read("agent", "list")["agents"]
            result = {"running": True, "workspaces": [{
                "id": str(space["workspace_id"]), "label": str(space.get("label") or space["workspace_id"]),
                "status": state(space), "focused": space.get("focused") is True,
                "agents": [card(agent) for agent in agents if agent.get("workspace_id") == space["workspace_id"]],
            } for space in workspaces]}
        except (KeyError, TypeError, AttributeError):
            raise TerminalUnavailable("kitty!'s herdr session did not report its agent states") from None
        with self._states_lock:
            self._states = seen
        return result

    def focus_agent(self, pane: str) -> None:
        """Focus one agent pane that herdr currently lists (the dock's Open pane)."""
        if not _listening(self._session_folder() / "herdr.sock"):
            raise TerminalNotRunning("Open the terminal before opening an agent's pane")
        listed = {str(agent.get("pane_id")) for agent in self._read("agent", "list").get("agents", [])}
        if pane not in listed:
            raise ValueError("That agent is no longer running")
        self._agent_command("agent", "focus", pane)

    def _agent_command(self, *arguments: str, skill: bool = False):
        """A bounded native herdr command; never replay a potentially applied mutation."""
        try:
            result = subprocess.run(
                self._command(*arguments), env=child_environment(self.environ, self.config_path),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=40, check=False)
            if result.returncode:
                try:
                    code = json.loads(result.stderr)["error"]["code"]
                except (ValueError, KeyError, TypeError):
                    code = "herdr_command_failed"
                if not isinstance(code, str) or not re.fullmatch(r"[a-z_]+", code):
                    code = "herdr_command_failed"
                raise TerminalUnavailable(code)
            if skill:
                if not result.stdout.strip():
                    raise ValueError
                return result.stdout
            return json.loads(result.stdout)["result"]
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            raise TerminalUnavailable("herdr_command_failed") from None

    def start_agent(self, kind: str, cwd: Path) -> dict:
        """Create an Agent tab, then let herdr validate/start its chosen coding agent.

        No arbitrary commands or focused-pane writes: only the pane returned by tab
        creation is used. A failed start keeps the tab for inspection, never retries.
        The bundled skill is private; no global agent configuration is installed.
        """
        if kind not in CODING_AGENTS:
            raise ValueError("Unsupported coding agent")
        with self._lock:
            folder = self._session_folder()
            if not _listening(folder / "herdr.sock"):
                raise TerminalNotRunning("Open the terminal before starting an Agent tab")
            self._verify_private(folder)
            self._prepare()
            spaces = self._read("workspace", "list").get("workspaces", [])
            space = next((item for item in spaces if item.get("focused") is True), None)
            if not space:
                raise TerminalNotRunning("Select a herdr workspace before starting an Agent tab")
            skill_path = self.private_dir / "SKILL.md"
            _write_private(skill_path, self._agent_command("--skill", skill=True))
            # The signed desktop binary is called kitty-herdr, while the skill uses
            # `herdr`. Only this tab's PATH gets the private alias to the pinned binary.
            bin_dir = self.private_dir / "bin"
            _owner_only_directory(bin_dir)
            alias = bin_dir / "herdr"
            if alias.is_symlink():
                alias.unlink()
            alias.symlink_to(Path(self.binary).resolve())
            path = str(bin_dir) + os.pathsep + self.environ.get("PATH", os.defpath)
            created = self._agent_command(
                "tab", "create", "--workspace", str(space["workspace_id"]),
                "--label", "Agent", "--cwd", str(cwd), "--env", f"PATH={path}", "--focus")
            try:
                tab, pane = created["tab"]["tab_id"], created["root_pane"]["pane_id"]
            except (KeyError, TypeError):
                raise TerminalUnavailable("herdr did not identify the new Agent tab; not retried") from None
            name = f"{kind}-{secrets.token_hex(4)}"
            if kind == "omp":
                arguments = ["--append-system-prompt", str(skill_path)]
            elif kind == "claude":
                arguments = ["--append-system-prompt-file", str(skill_path)]
            else:
                instructions = f"Use herdr to control your panes. Read and follow its skill at {skill_path}."
                arguments = ["-c", "developer_instructions=" + json.dumps(instructions)]
            try:
                self._agent_command("agent", "start", name, "--kind", kind, "--pane", pane,
                                    "--", *arguments)
            except TerminalUnavailable as exc:
                raise TerminalUnavailable(
                    f"Agent startup reported {exc}. Inspect Agent tab {tab}, pane {pane}; "
                    "the tab was kept and startup was not retried.") from None
            self._used = True
            return {"tab": tab, "pane": pane, "name": name, "kind": kind}

    def stop(self) -> None:
        """Quit: stop the session if this process used it; its clients then exit."""
        with self._lock:
            used, self._used = self._used, False
        if used:
            self._stop_session()

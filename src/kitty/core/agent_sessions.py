"""Agent tabs: a launch nonce, self-confinement, then a scoped token (paper §13 step 1, §17).

Fail-closed launch. rika! registers a launch before it opens an agent tab: an
``agent_sessions`` row (``pending``) and a one-time 256-bit nonce, which the tab's
environment carries (``KITTY_LAUNCH_NONCE``). The tab's PATH resolves the agent's name to
kitty-agent-launch (kitty.agent_launch), which

1. trades the nonce for its launch spec (``POST /api/ingress/agent/launch``): what it may
   read and write, the deny set, the allowed ports;
2. confines itself (kitty.core.confine) and reports what it applied;
3. exchanges the nonce (``POST /api/ingress/agent/exchange``), once, for the session's
   token, and execs the agent with it in ``KITTY_AGENT_TOKEN``.

rika! issues the token only after checking, from outside, that the exchange came from
the launcher it started: the TCP connection belongs to the reported pid, that pid
descends from the tab's pane (herdr's ``pane process-info`` shell, or rika!'s own Tern
daemon), it runs rika!'s launcher image (the interpreter or desktop sidecar with the
launcher's exact arguments, Python's isolated mode), and ``/proc`` shows no_new_privs
and a seccomp filter. Landlock itself is not visible from outside; the launcher's code
applies it before the exchange. A tab that never completes the exchange (say a shell
start-up file put another PATH first) gets no token, and its session says so.

The token is a paired-client token of kind ``agent-session`` (hashed at rest), scoped
``notes.read``, ``notes.write`` and ``search``; it ends when the agent's process does.

The guarantee the UI shows comes from what the launcher closed (kitty.core.confine
``guarantee``): ``full`` when every route of the paper's kernel matrix is closed (Landlock
ABI 9 or later, seccomp), ``attribution`` otherwise: the agent's API accesses are still
journaled and its writes carry the session, but other routes to the data may be open.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import secrets
import shlex
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kitty.core import agent_access, agent_policy, clients, confine
from kitty.db import connect

#: A nonce must be exchanged within this many seconds of the tab's creation.
LAUNCH_TTL = 120.0
MAX_PENDING = 16
HOSTS = ("herdr", "tern", "direct")
#: How the UI names each guarantee (a tab's label, Settings → Agents).
GUARANTEE_LABELS = {"full": "Full", "attribution": "Attribution only", "none": "Not mediated"}
#: Rights by name in a launch spec.
RIGHTS = {"read": confine.READ, "write": confine.WRITE, "device": confine.DEVICE,
          "unix": confine.READ | confine.FS_RESOLVE_UNIX}
#: Read-only system locations an agent needs (the ones that exist are granted).
SYSTEM_READ = ("/nix/store", "/nix/var/nix/profiles", "/run/current-system", "/run/wrappers", "/etc", "/usr", "/bin",
               "/sbin", "/lib", "/lib32", "/lib64", "/opt", "/snap", "/proc", "/sys", "/run/systemd/resolve",
               "/var/lib/flatpak")
SYSTEM_WRITE = ("/tmp", "/var/tmp", "/dev/shm")
DEVICES = ("/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom", "/dev/tty", "/dev/pts", "/dev/ptmx")
#: Pathname sockets an agent may connect to: name lookups only.
SOCKETS = ("/run/nscd",)
#: Each agent's own state, written by the agent itself (relative to the home folder).
AGENT_STATE = {"omp": (".omp",), "claude": (".claude", ".claude.json"), "codex": (".codex",)}
HOME_WRITE = (".cache",)
#: Home folders closed to agents: browser profiles (a browser-mode rika! tab keeps its UI
#: secret in session storage), keys and keyrings.
HOME_DENY = (".mozilla", ".librewolf", ".config/chromium", ".config/google-chrome", ".config/BraveSoftware",
             ".config/vivaldi", ".var/app", ".ssh", ".gnupg", ".password-store", ".local/share/keyrings", ".pki")
#: Files an unconfined program runs later when found in a project (planted files): kept
#: read-only where the launch can afford it, and compared with their launch baseline.
PLANTED = (".envrc", ".git/config", ".git/hooks", ".git/info", ".vscode/tasks.json", ".vscode/settings.json",
           ".devcontainer/devcontainer.json")
MEDIATION_NOTE = """\
rika! confined this Agent tab. rika!'s database, private files, saved answers, profile and
resumes are closed to this process, and so are herdr's and Tern's sockets (no new panes).
Read and write rika!'s notes through `kitty notes ...` or the `kitty mcp` server: both use
$KITTY_AGENT_TOKEN, and rika! records every page and block you read or write.
"""


class LaunchError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def launcher_command() -> list[str]:
    """How a tab runs kitty-agent-launch: this interpreter in isolated mode, or the desktop sidecar."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "agent-launch"]
    return [sys.executable, "-I", "-m", "kitty.agent_launch"]


def host_guarantee() -> dict:
    """What this kernel lets a launcher close (the paper's kernel matrix, for this machine)."""
    version = confine.abi()
    try:
        confine.seccomp_program()
        closed = confine.closures(version) + ["tiocsti"]
    except confine.ConfineError:
        closed = confine.closures(version)
    level, open_routes = confine.guarantee(closed if version else [])
    return {"kernel": f"{platform.system()} {platform.release()}", "abi": version, "guarantee": level,
            "closed": closed if version else [], "open": open_routes}


# ------------------------------------------------------------------------------ spec

def deny_set(settings, *, herdr_dir: Path | None, runtime_dir: Path | None, home: Path | None) -> list[tuple[str, str]]:
    """``(path, purpose)`` nothing in an agent tab may reach."""
    root, db = settings.root, Path(settings.db_path)
    paths = [(db, "store"), *((db.with_name(db.name + suffix), "store") for suffix in ("-wal", "-shm", "-journal")),
             (root / "data", "store"), (root / ".cache", "store"), (root / ".private", "secrets"), (root / ".env", "secrets"),
             (root / "config" / "profile.yaml", "applyai.profile"), (root / "config" / "answers.yaml", "applyai.answers"),
             (root / "resumes", "resume")]
    if herdr_dir is not None:
        paths.append((herdr_dir, "deputy:herdr"))
    if runtime_dir is not None:
        paths.append((runtime_dir / "kitty", "deputy:tern"))
    if home is not None:
        paths += [(home / name, "secrets") for name in HOME_DENY]
    out: list[tuple[str, str]] = []
    for path, label in paths:
        for variant in dict.fromkeys((os.path.abspath(path), os.path.realpath(path))):
            out.append((variant, label))
    return out


def runtime_paths() -> list[str]:
    """Where rika!'s interpreter (or desktop sidecar) and its package live."""
    import kitty

    package = Path(kitty.__file__).resolve().parent.parent
    return list(dict.fromkeys(str(Path(path)) for path in (sys.prefix, sys.base_prefix, package,
                                                          Path(sys.executable).parent)))


def grants(settings, kind: str, cwd: Path, launch: dict, *, home: Path | None,
           exceptions: list[tuple[str, str]] = ()) -> dict:
    """The launch spec's grants: ``{grants, readonly, exceptions}`` of ``[path, rights name]``."""
    out: list[tuple[str, str]] = [(path, "read") for path in SYSTEM_READ]
    # rika!'s own interpreter and package, so `kitty notes` and `kitty mcp` run in the tab.
    out += [(path, "read") for path in runtime_paths()]
    if home is not None:
        out.append((str(home), "read"))
        out += [(str(home / name), "write") for name in (*HOME_WRITE, *AGENT_STATE.get(kind, ()))]
        out += [(os.path.expanduser(path) if path.startswith("~/") else path, "write") for path in launch["writable"]]
    out += [(path, "write") for path in SYSTEM_WRITE]
    out += [(path, "device") for path in DEVICES]
    out += [(path, "unix") for path in SOCKETS]
    projects = [str(cwd), *(os.path.expanduser(path) for path in launch["projects"])]
    out += [(path, "write") for path in dict.fromkeys(projects)]
    # rika!'s configuration (the build file, tracks, watchlists) and planted files stay
    # read-only in rika!'s workspace root, which is expanded around the deny set anyway
    # (no file can be created directly in it).
    readonly = [str(settings.root / "config"), *(str(settings.root / name) for name in PLANTED)]
    return {"grants": [list(item) for item in out], "readonly": readonly, "exceptions": [list(item) for item in exceptions]}


def compile_plan(spec: dict) -> confine.Plan:
    """The confine.Plan a launch spec describes (the launcher and rika! compute the same one)."""
    def rights(name: str) -> int:
        if name not in RIGHTS:
            raise LaunchError(f"Unknown rights {name!r} in the launch spec")
        return RIGHTS[name]

    return confine.plan([(path, rights(name)) for path, name in spec["grants"]], [path for path, _ in spec["deny"]],
                        readonly=spec["readonly"], exceptions=[(path, rights(name)) for path, name in spec["exceptions"]],
                        tcp=spec["tcp"], udp=spec["udp"])


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def planted_baseline(directories: list[str]) -> dict:
    """The planted-file candidates under each writable project directory, hashed."""
    files: dict[str, str | None] = {}
    for directory in directories:
        base = Path(directory)
        for name in PLANTED:
            target = base / name
            if target.is_dir():
                for child in sorted(target.iterdir())[:200]:
                    if child.is_file():
                        files[str(child)] = _digest(child)
                files[str(target) + "/"] = "dir"
            else:
                files[str(target)] = _digest(target)
    return {"dirs": directories, "files": files}


def planted_changes(baseline: dict | None) -> list[dict]:
    """Planted-file candidates created, changed or removed since the launch."""
    if not baseline:
        return []
    now_ = planted_baseline(baseline["dirs"])["files"]
    out = []
    for path in sorted(set(baseline["files"]) | set(now_)):
        before, after = baseline["files"].get(path), now_.get(path)
        if before == after or path.endswith("/"):
            continue
        change = "created" if before is None else "removed" if after is None else "changed"
        out.append({"path": path, "change": change})
    return out


# ---------------------------------------------------------------------------- /proc

def _stat_fields(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return raw[raw.rindex(")") + 2:].split()


def parent(pid: int) -> int | None:
    fields = _stat_fields(pid)
    return int(fields[1]) if fields else None


def start_time(pid: int) -> int | None:
    fields = _stat_fields(pid)
    return int(fields[19]) if fields else None


def ancestry(pid: int) -> list[int]:
    chain, current = [], pid
    while current and current not in chain and len(chain) < 256:
        chain.append(current)
        current = parent(current) or 0
    return chain


def proc_status(pid: int) -> dict[str, str]:
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except OSError:
        return {}
    return dict(line.split(":\t", 1) for line in lines if ":\t" in line)


def cmdline(pid: int) -> list[str]:
    try:
        return [part.decode(errors="replace") for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]]
    except OSError:
        return []


def executable(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def _loopback(port: int) -> str:
    return f"0100007F:{port:04X}"


def tcp_inode(local_port: int, remote_port: int) -> int | None:
    """The socket inode of the loopback TCP connection 127.0.0.1:local -> 127.0.0.1:remote."""
    try:
        lines = Path("/proc/net/tcp").read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) > 9 and parts[1] == _loopback(local_port) and parts[2] == _loopback(remote_port):
            return int(parts[9])
    return None


def holds_socket(pid: int, inode: int) -> bool:
    wanted = f"socket:[{inode}]"
    try:
        names = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return False
    for name in names:
        try:
            if os.readlink(f"/proc/{pid}/fd/{name}") == wanted:
                return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- launches

@dataclass
class Launch:
    session: str
    nonce_hash: str
    host: str
    kind: str
    cwd: Path
    #: The pid every process of the tab descends from, given the tab's pane (None: unknown or gone).
    ancestor: Callable[[str | None], int | None]
    created: float = field(default_factory=time.monotonic)
    spec: dict | None = None
    pane: str | None = None


@dataclass(frozen=True)
class AgentLaunch:
    """What a terminal host needs to start one agent through kitty-agent-launch."""

    session: str
    #: The launcher (`launcher_command`), which the tab's shims run.
    command: tuple[str, ...]
    #: The tab's environment: the one-time nonce and rika!'s origin.
    env: dict[str, str]
    #: True when this kernel lets the launcher close every route (herdr's skill is then
    #: withheld: its socket is closed to the tab).
    full: bool
    #: Records the tab's pane once the host created it.
    bind: Callable[[str], None]


def shim(command: tuple[str, ...] | list[str], kind: str) -> str:
    """The shell script a tab's PATH finds as `kind`: kitty-agent-launch for that agent."""
    return "#!/bin/sh\n# Written by rika!: agents in its tabs start confined (kitty.agent_launch).\nexec " + " ".join(
        shlex.quote(part) for part in command) + f" --kind {shlex.quote(kind)} -- \"$@\"\n"



def _digest_nonce(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def _row(conn: sqlite3.Connection, session: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session,)).fetchone()
    return dict(row) if row else None


def describe(row: dict) -> dict:
    """A session as the API lists it."""
    report = json.loads(row["report"]) if row.get("report") else None
    return {key: row[key] for key in ("id", "kind", "name", "host", "pane", "cwd", "state", "guarantee", "abi", "pid",
                                      "message", "policy_version", "created_at", "confined_at", "ended_at")} | {
        "report": report, "token": row["client_id"] is not None}


class Launches:
    """Pending launches of this web application (memory; their sessions are rows)."""

    def __init__(self, settings, *, port: Callable[[], int | None], herdr_dir: Callable[[], Path | None] = lambda: None,
                 runtime_dir: Path | None = None, home: Path | None = None,
                 exceptions: Callable[[str], list[tuple[str, str]]] = lambda kind: []):
        self.settings = settings
        self._port = port
        self._herdr_dir = herdr_dir
        self.runtime_dir = runtime_dir
        self.home = home
        self._exceptions = exceptions
        self._lock = threading.Lock()
        self._pending: dict[str, Launch] = {}

    def _expire(self, conn: sqlite3.Connection) -> None:
        stale = [launch for launch in self._pending.values() if time.monotonic() - launch.created > LAUNCH_TTL]
        for launch in stale:
            del self._pending[launch.nonce_hash]
            conn.execute("UPDATE agent_sessions SET state='expired', ended_at=?, message=? WHERE id=? AND state='pending'",
                         (now(), "The tab never completed rika!'s launch exchange: no token was issued", launch.session))

    def register(self, conn: sqlite3.Connection, *, host: str, kind: str, cwd: Path,
                 ancestor: Callable[[str | None], int | None], name: str | None = None) -> tuple[str, str]:
        """A pending launch: returns ``(session id, nonce)``; the nonce goes into the tab's environment only."""
        if host not in HOSTS:
            raise LaunchError("Unknown agent host")
        nonce = secrets.token_urlsafe(32)
        session = str(uuid.uuid4())
        version, policy = agent_policy.active(conn)
        projects = list(dict.fromkeys([str(cwd), *(os.path.expanduser(path) for path in policy["launch"]["projects"])]))
        with self._lock:
            self._expire(conn)
            if len(self._pending) >= MAX_PENDING:
                raise LaunchError("Too many agent tabs are starting at once", 429)
            conn.execute(
                "INSERT INTO agent_sessions(id, kind, name, host, cwd, state, guarantee, policy_version, baseline, created_at) "
                "VALUES (?,?,?,?,?,'pending','none',?,?,?)",
                (session, kind, name or kind, host, str(cwd), version, json.dumps(planted_baseline(projects)), now()))
            self._pending[_digest_nonce(nonce)] = Launch(session, _digest_nonce(nonce), host, kind, cwd, ancestor)
        return session, nonce

    def prepare(self, conn: sqlite3.Connection, *, host: str, kind: str, cwd: Path,
                ancestor: Callable[[str | None], int | None], origin: str | None) -> AgentLaunch:
        """Register a launch and describe it for the terminal host that opens its tab."""
        if not origin:
            raise LaunchError("rika!'s address is not known yet", 503)
        session, nonce = self.register(conn, host=host, kind=kind, cwd=cwd, ancestor=ancestor)

        def bind(pane: str) -> None:
            with closing(connect(self.settings.db_path)) as own:
                self.bind(own, session, pane)

        return AgentLaunch(session, tuple(launcher_command()), self.environment(nonce, origin),
                           host_guarantee()["guarantee"] == confine.FULL, bind)

    def outcome(self, conn: sqlite3.Connection, session: str) -> dict:
        """How a launch went, for the response that opened its tab."""
        row = _row(conn, session) or {}
        message = row.get("message") or ("The tab has not completed rika!'s launch exchange, so no token was issued"
                                          if row.get("state") == "pending" else "")
        return {"session": session, "state": row.get("state"), "guarantee": row.get("guarantee", "none"), "message": message}

    def environment(self, nonce: str, origin: str) -> dict[str, str]:
        return {"KITTY_LAUNCH_NONCE": nonce, "KITTY_LAUNCH_ORIGIN": origin}

    def bind(self, conn: sqlite3.Connection, session: str, pane: str) -> None:
        """Name the pane a pending launch runs in (its processes must descend from it)."""
        with self._lock:
            for launch in self._pending.values():
                if launch.session == session:
                    launch.pane = pane
        conn.execute("UPDATE agent_sessions SET pane=? WHERE id=?", (pane, session))

    def _launch(self, nonce: Any) -> Launch:
        if not isinstance(nonce, str) or len(nonce) != 43:
            raise LaunchError("Unknown or used launch", 410)
        digest = _digest_nonce(nonce)
        launch = self._pending.get(digest)
        if launch is None or not hmac.compare_digest(launch.nonce_hash, digest):
            raise LaunchError("Unknown or used launch", 410)
        if time.monotonic() - launch.created > LAUNCH_TTL:
            raise LaunchError("This launch expired; start a new Agent tab from rika!", 410)
        return launch

    def spec(self, conn: sqlite3.Connection, nonce: Any) -> dict:
        """The launch spec for a pending launch (the nonce stays valid for its exchange)."""
        with self._lock:
            launch = self._launch(nonce)
            _, policy = agent_policy.active(conn)
            launch_cfg = policy["launch"]
            port = self._port()
            herdr = self._herdr_dir()
            spec = {"session": launch.session, "kind": launch.kind, "cwd": str(launch.cwd),
                    "deny": [list(item) for item in deny_set(self.settings, herdr_dir=herdr, runtime_dir=self.runtime_dir,
                                                             home=self.home)],
                    **grants(self.settings, launch.kind, launch.cwd, launch_cfg, home=self.home,
                             exceptions=self._exceptions(launch.kind)),
                    "tcp": sorted({*(launch_cfg["tcp_ports"]), *([port] if port else [])}),
                    "udp": launch_cfg["udp_ports"], "unconfined": launch_cfg["unconfined"]}
            launch.spec = spec
            return spec

    def exchange(self, conn: sqlite3.Connection, nonce: Any, *, pid: Any, report: Any, peer_port: int,
                 server_port: int) -> dict:
        """Consume the nonce: check the launcher from outside and issue the token when it confined itself."""
        with self._lock:
            launch = self._launch(nonce)
            if launch.spec is None:
                raise LaunchError("Ask for the launch spec before the exchange", 409)
            del self._pending[launch.nonce_hash]
        if type(pid) is not int or pid <= 1 or not isinstance(report, dict):
            raise LaunchError("The exchange carries the launcher's pid and report")
        checks, problems = self.verify(launch, pid, peer_port, server_port)
        landlock = report.get("landlock") is True and report.get("seccomp") is True and type(report.get("abi")) is int
        host_abi = confine.abi()
        if landlock and report["abi"] > host_abi:
            problems.append(f"the launcher reported Landlock ABI {report['abi']}, above this kernel's {host_abi}")
        if landlock and not (checks["no_new_privs"] and checks["seccomp"]):
            problems.append("the launcher reported confinement, but its process shows no seccomp filter")
        closed = confine.closures(min(report.get("abi") or 0, host_abi)) + (["tiocsti"] if report.get("seccomp") is True else [])
        level, open_routes = confine.guarantee(closed if landlock else [])
        links = self._db_links()
        if links:
            level = confine.ATTRIBUTION
            open_routes.append(f"the database has another hard link ({links}), outside the deny set")
        _, policy = agent_policy.active(conn)
        checked = {"checks": checks, "problems": problems, "open": open_routes, "host_abi": host_abi}
        stored = json.dumps({**{key: report.get(key) for key in ("abi", "landlock", "rules", "seccomp", "no_new_privs",
                                                                "error", "closed", "missing", "fds_closed")}, **checked})
        name = launch.kind
        if problems:
            message = "rika! could not verify this launch, so it issued no token: " + "; ".join(problems)
            conn.execute("UPDATE agent_sessions SET state='refused', guarantee='none', pid=?, report=?, message=?, ended_at=? "
                         "WHERE id=?", (pid, stored, message, now(), launch.session))
            return {"run": False, "session": launch.session, "guarantee": "none", "message": message, "env": {}}
        start = start_time(pid)
        if not landlock:
            run = policy["launch"]["unconfined"] == "run"
            message = (f"Attribution only: the launcher could not confine this tab ({report.get('error') or 'no Landlock'}). "
                       + ("The agent runs without a token; its kitty notes writes carry this session." if run else
                          "The policy refuses unconfined agents, so it was not started."))
            conn.execute("UPDATE agent_sessions SET state=?, guarantee=?, abi=?, pid=?, pid_start=?, report=?, message=?, "
                         "ended_at=? WHERE id=?", ("unconfined" if run else "refused", "attribution" if run else "none",
                                                   report.get("abi"), pid, start, stored, message, None if run else now(),
                                                   launch.session))
            env = {"KITTY_AGENT_SESSION": launch.session, "KITTY_AGENT_NAME": name, "KITTY_AGENT_GUARANTEE": "attribution"}
            return {"run": run, "session": launch.session, "guarantee": "attribution" if run else "none",
                    "message": message, "env": env if run else {}}
        client, token = clients.create(conn, name, clients.AGENT_KIND, list(agent_access.SCOPES), None)
        message = ("Full: every route to rika!'s data from this tab goes through rika!'s API, and every access is journaled."
                   if level == confine.FULL else
                   "Attribution only: API accesses are journaled, but " + "; ".join(open_routes) + ".")
        conn.execute("UPDATE agent_sessions SET state='confined', guarantee=?, client_id=?, abi=?, pid=?, pid_start=?, report=?, "
                     "message=?, confined_at=? WHERE id=?",
                     (level, client["id"], report["abi"], pid, start, stored, message, now(), launch.session))
        return {"run": True, "session": launch.session, "guarantee": level, "message": message,
                "env": {"KITTY_AGENT_TOKEN": token, "KITTY_AGENT_SESSION": launch.session, "KITTY_AGENT_NAME": name,
                        "KITTY_AGENT_GUARANTEE": level}}

    def _db_links(self) -> int:
        try:
            info = os.stat(self.settings.db_path)
        except OSError:
            return 0
        return info.st_nlink - 1

    def verify(self, launch: Launch, pid: int, peer_port: int, server_port: int) -> tuple[dict, list[str]]:
        """What rika! checks from outside before it trusts a launcher's report."""
        problems: list[str] = []
        inode = tcp_inode(peer_port, server_port)
        checks = {"connection": inode is not None and holds_socket(pid, inode)}
        if not checks["connection"]:
            problems.append(f"the exchange did not come from pid {pid}")
        ancestor = launch.ancestor(launch.pane)
        checks["pane"] = ancestor is not None and ancestor in ancestry(pid)[1:]
        if not checks["pane"]:
            problems.append(f"pid {pid} is not a process of the tab's pane")
        expected = launcher_command()
        image = executable(pid)
        # The interpreter (or sidecar) image rika! itself runs: a Nix or venv wrapper resolves to it.
        checks["launcher"] = (image is not None and image == executable(os.getpid())
                              and cmdline(pid)[1:len(expected)] == expected[1:])
        if not checks["launcher"]:
            problems.append("the process is not rika!'s agent launcher")
        status = proc_status(pid)
        checks["no_new_privs"] = status.get("NoNewPrivs", "").strip() == "1"
        checks["seccomp"] = status.get("Seccomp", "").strip() == "2"
        return checks, problems

    # ---------------------------------------------------------------- tokens in use

    def authenticate(self, conn: sqlite3.Connection, token: Any) -> dict | None:
        """The live session holding agent token `token`, or None (an exited agent's token ends)."""
        client = clients.authenticate(conn, token)
        if client is None or client["kind"] != clients.AGENT_KIND:
            return None
        row = conn.execute("SELECT * FROM agent_sessions WHERE client_id=?", (client["id"],)).fetchone()
        if row is None or row["state"] != "confined":
            return None
        if start_time(row["pid"]) != row["pid_start"]:
            end(conn, row["id"], "The agent's process exited; its token ended")
            return None
        return {"id": row["id"], "kind": row["kind"], "name": row["name"], "scopes": client["scopes"],
                "guarantee": row["guarantee"], "client": client["id"]}


def end(conn: sqlite3.Connection, session: str, message: str) -> None:
    """End a session: its token is revoked; the journal stays."""
    row = conn.execute("SELECT client_id, state FROM agent_sessions WHERE id=?", (session,)).fetchone()
    if row is None:
        raise LookupError("No such agent session")
    if row["client_id"]:
        conn.execute("UPDATE clients SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (now(), row["client_id"]))
    if row["state"] in {"pending", "confined", "unconfined"}:
        conn.execute("UPDATE agent_sessions SET state='ended', ended_at=?, message=? WHERE id=?", (now(), message, session))


def listing(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute("SELECT s.*, (SELECT COUNT(*) FROM agent_access a WHERE a.session=s.id) AS accesses, "
                        "(SELECT COUNT(*) FROM retention_holds h WHERE h.session=s.id AND h.released_at IS NULL) AS held "
                        "FROM agent_sessions s ORDER BY s.created_at DESC, s.id LIMIT ?", (limit,)).fetchall()
    return [{**describe(dict(row)), "accesses": row["accesses"], "held": bool(row["held"])} for row in rows]


def detail(conn: sqlite3.Connection, session: str, *, limit: int = 200, before: int | None = None) -> dict:
    row = _row(conn, session)
    if row is None:
        raise LookupError("No such agent session")
    journal = agent_access.journal(conn, session, limit=limit, before=before)
    held = conn.execute("SELECT 1 FROM retention_holds WHERE session=? AND released_at IS NULL", (session,)).fetchone()
    return {"session": describe(row), "journal": journal["rows"], "next": journal["next"], "held": held is not None,
            "planted": planted_changes(json.loads(row["baseline"]) if row["baseline"] else None)}


def pane_guarantees(conn: sqlite3.Connection, host: str) -> dict[str, str]:
    """Pane -> guarantee of the live agent the newest launch in it started (none when it exited)."""
    out: dict[str, str] = {}
    for row in conn.execute("SELECT pane, state, guarantee, pid, pid_start FROM agent_sessions WHERE host=? AND pane IS NOT NULL "
                            "ORDER BY created_at, rowid", (host,)):
        live = row["state"] in {"confined", "unconfined"} and row["pid"] and start_time(row["pid"]) == row["pid_start"]
        out[row["pane"]] = row["guarantee"] if live else "none"
    return out

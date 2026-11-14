"""GitHub backup: an opt-in, push-only copy of the notes in a private GitHub repository.

The owner pastes a repository in Settings → Devices; nothing here ever picks one. A run

1. asks GitHub's API about the repository and stops unless it is not public (a public
   repository pauses the backup with a problem the owner sees);
2. exports into ``.private/reposync/tree/``. The exporter writes only what
   :data:`ALLOWED` names, and everything else found in that folder is removed before a
   commit, so the copy can never hold more than::

       rika-sync.json          format, app version, device name, exported_at
       pages/<page_id>.md      readable text of the page (title, outline, document text)
       pages/<page_id>.loro    the page's Loro snapshot (kitty.core.blocks.merge.export)
       build.json              config/build.json

   A page is rewritten only when its version vector changed since the last run;
3. commits on branch ``devices/<device_id>`` with a message made from the page titles
   (no model), and pushes fast-forward with the owner's own git credentials.

The Git directory is ``.private/reposync/repo.git``, never the workspace's own ``.git``:
``git`` always runs with an explicit ``--git-dir`` and ``--work-tree``, a small fixed
environment and a timeout.

:class:`RepoSync` also keeps the schedule: a run 10 minutes after the last change, at
most 60 minutes after the first unsynced one, and when asked; failed runs back off up
to an hour. Every run is a row in ``repo_sync_runs``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from collections.abc import Callable
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kitty import __version__
from kitty.core.blocks import convert, merge
from kitty.core.blocks import store as blocks
from kitty.core.builds.store import BUILD_FILE
from kitty.core.sync import peers
from kitty.dates import parse as parse_instant
from kitty.db.connection import connect, transaction
from kitty.settings import Settings

log = logging.getLogger(__name__)

FORMAT = 1
_PAGE_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
#: Every path the exporter may write, relative to the work tree. Nothing else is committed.
ALLOWED = re.compile(rf"rika-sync\.json|build\.json|pages/{_PAGE_ID}\.(?:md|loro)")
MANIFEST = "rika-sync.json"
BUILD_MAX = 1024 * 1024

#: A run starts this long after the last change …
QUIET = timedelta(minutes=10)
#: … and at the latest this long after the first change that is not backed up.
MAX_WAIT = timedelta(minutes=60)
#: The wait after the first failed run; it doubles up to BACKOFF_MAX.
BACKOFF = timedelta(minutes=5)
BACKOFF_MAX = timedelta(hours=1)
#: Rika mentions a backup that has not worked for this long while changes wait.
STALE = timedelta(days=3)
#: Seconds between the scheduler's checks.
INTERVAL = 30.0
GIT_TIMEOUT = 120
OK = ("pushed", "unchanged")
FAILED = ("offline", "error")
TOKEN_KEY = "GITHUB"

_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_NAME = r"[A-Za-z0-9._-]{1,100}"
_REPO = re.compile(
    rf"(?:(?:https?://|ssh://git@|git://)?(?:www\.)?github\.com[/:]|git@github\.com:)?({_OWNER})/({_NAME}?)(?:\.git)?/?")
#: What git and ssh need from the environment; everything else is dropped.
_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY", "NIX_SSL_CERT_FILE", "SSL_CERT_FILE", "GIT_SSL_CAINFO")


class RepoSyncError(Exception):
    """A refusal the owner can read."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Unreachable(Exception):
    """GitHub could not be asked; the run is retried later."""


class GitError(Exception):
    """A git command failed; the message is its last line of output."""


class Rejected(GitError):
    """GitHub refused a fast-forward push: its branch has commits this device lacks."""


# The repository the owner names ---------------------------------------------------

def parse_repo(value: object) -> tuple[str, str]:
    """(owner, name) from ``owner/name`` or a GitHub address."""
    found = _REPO.fullmatch(value.strip()) if isinstance(value, str) else None
    if found is None or found[2] in {".", ".."}:
        raise RepoSyncError("Enter the repository as owner/name or its GitHub address")
    return found[1], found[2]


def github_visibility(owner: str, repo: str, token: str = "") -> str:
    """``private``, ``public`` or ``missing`` as GitHub's API reports the repository.

    ``missing`` is GitHub's answer for a private repository the caller may not see (and
    for one that does not exist): a public repository always answers.
    """
    import httpx

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "rika"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=10, follow_redirects=True)
    except httpx.HTTPError:
        raise Unreachable("GitHub could not be reached") from None
    if response.status_code == 404:
        return "missing"
    if response.status_code != 200:
        raise Unreachable(f"GitHub answered {response.status_code} when asked about the repository"
                          + (" (check the GITHUB key in Settings → Keys)" if token and response.status_code in (401, 403) else ""))
    try:
        private = response.json().get("private")
    except ValueError:
        raise Unreachable("GitHub's answer about the repository could not be read") from None
    return "private" if private is True else "public"


def github_remotes(owner: str, repo: str) -> list[str]:
    """Where a push is tried, in order: HTTPS (a credential helper), then SSH (a key)."""
    return [f"https://github.com/{owner}/{repo}.git", f"git@github.com:{owner}/{repo}.git"]


# Files ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Paths:
    base: Path

    @property
    def repo(self) -> Path:
        return self.base / "repo.git"

    @property
    def tree(self) -> Path:
        return self.base / "tree"

    @property
    def state(self) -> Path:
        return self.base / "state.json"


def paths(root: Path) -> Paths:
    return Paths(Path(root) / ".private" / "reposync")


def _write(tree: Path, name: str, data: bytes) -> None:
    """Write one file of the copy; a name outside :data:`ALLOWED` is a bug and is refused."""
    if not ALLOWED.fullmatch(name):
        raise RepoSyncError(f"{name} is not part of a GitHub backup", 500)
    target = tree / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.parent.is_symlink():
        raise RepoSyncError("The backup folder must not contain symbolic links", 409)
    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(data)
    os.replace(temporary, target)


def _sweep(tree: Path) -> None:
    """Remove everything in the work tree that the allowlist does not name."""
    for path in sorted(tree.rglob("*"), reverse=True):
        name = path.relative_to(tree).as_posix()
        if path.is_symlink() or path.is_file():
            if path.is_symlink() or not ALLOWED.fullmatch(name):
                path.unlink()
        elif name != "pages":
            shutil.rmtree(path)


def title_of(text: str) -> str:
    first = " ".join((text.strip().splitlines() or [""])[0].split())
    return (first[:59] + "…" if len(first) > 60 else first) or "Untitled"


def render(doc) -> str:
    """A page as readable Markdown: its title, then its outline or document text."""
    root = doc.root
    head, _, rest = root.text.strip().partition("\n")
    lines = [f"# {' '.join(head.split()) or 'Untitled'}", ""]
    if rest.strip():
        lines += [rest.strip(), ""]
    if root.type == "document":
        body = convert.join_document(doc.children(root.id)).strip()
        if body:
            lines += [body, ""]
        return "\n".join(lines)

    def walk(parent_id: str, depth: int) -> None:
        for child in doc.children(parent_id):
            if child.type in blocks.OPAQUE_TYPES:
                continue
            first, *more = child.text.split("\n")
            pad = "  " * depth
            lines.append(f"{pad}- {first}")
            lines.extend(f"{pad}  {line}" for line in more)
            walk(child.id, depth + 1)

    walk(root.id, 0)
    return "\n".join(lines).rstrip("\n") + "\n"


@dataclass
class Export:
    #: Titles of the pages written and removed by this run, most recently changed first.
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    build: bool = False
    #: Pages in the copy after the run.
    total: int = 0
    #: The newest op_log row the copy includes.
    seq: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.written or self.removed or self.build)


def _state(place: Paths) -> dict:
    try:
        state = json.loads(place.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pages": {}}
    return state if isinstance(state, dict) and isinstance(state.get("pages"), dict) else {"pages": {}}


def _build(root: Path) -> bytes | None:
    source = Path(root) / "config" / BUILD_FILE
    if source.is_symlink() or not source.is_file() or source.stat().st_size > BUILD_MAX:
        return None
    data = source.read_bytes()
    try:
        return data if isinstance(json.loads(data), dict) else None
    except ValueError:
        return None


def export(conn: sqlite3.Connection, root: Path, place: Paths, *, at: datetime, lock=None) -> Export:
    """Bring the work tree up to date with the notes; rewrites only pages that changed."""
    tree = place.tree
    (tree / "pages").mkdir(parents=True, exist_ok=True)
    result = Export(seq=conn.execute("SELECT COALESCE(MAX(seq),0) FROM op_log").fetchone()[0])
    select = ("SELECT b.id, b.text, c.vv FROM blocks b LEFT JOIN page_crdt c ON c.page_id=b.id "
              "WHERE b.parent_id IS NULL AND b.deleted_at IS NULL ORDER BY c.updated_at DESC, b.id")
    rows = conn.execute(select).fetchall()
    legacy = [row["id"] for row in rows if row["vv"] is None]
    if legacy:
        with lock or nullcontext():
            merge.seed(conn, legacy)
        rows = conn.execute(select).fetchall()
    state = _state(place)
    known: dict[str, dict] = state["pages"]
    pages: dict[str, dict] = {}
    for row in rows:
        page_id = row["id"]
        if row["vv"] is None or not re.fullmatch(_PAGE_ID, page_id):
            continue
        version = hashlib.sha256(bytes(row["vv"])).hexdigest()
        title = title_of(row["text"])
        files = [tree / "pages" / f"{page_id}.{kind}" for kind in ("md", "loro")]
        if known.get(page_id, {}).get("vv") != version or not all(path.is_file() for path in files):
            snapshot = merge.export(conn, page_id, None)
            if snapshot is None:
                continue
            _write(tree, f"pages/{page_id}.loro", snapshot)
            _write(tree, f"pages/{page_id}.md", render(blocks.load_page(conn, page_id)).encode())
            result.written.append(title)
        pages[page_id] = {"vv": version, "title": title}
    for path in sorted((tree / "pages").iterdir()):
        if path.stem not in pages and path.is_file():
            path.unlink()
            if path.suffix == ".md":
                result.removed.append(known.get(path.stem, {}).get("title", "Untitled"))
    build = _build(root)
    target = tree / BUILD_FILE
    if build is None:
        if target.exists():
            target.unlink()
            result.build = True
    elif not target.is_file() or target.read_bytes() != build:
        _write(tree, BUILD_FILE, build)
        result.build = True
    _sweep(tree)
    result.total = len(pages)
    if result.changed or not (tree / MANIFEST).is_file():
        manifest = {"format": FORMAT, "app": "rika", "version": __version__, "device": peers.device_name(conn),
                    "exported_at": at.astimezone(timezone.utc).isoformat(timespec="seconds")}
        _write(tree, MANIFEST, (json.dumps(manifest, indent=2) + "\n").encode())
    place.state.write_text(json.dumps({"pages": pages}), encoding="utf-8")
    return result


def commit_message(result: Export) -> str:
    """"3 pages: Lecture 7, Groceries, +1": the count, then up to two titles."""
    titles = result.written + [f"{title} (removed)" for title in result.removed]
    if not titles:
        return "Build file" if result.build else "Backup"
    more = len(titles) - 2
    return f"{len(titles)} page{'s' if len(titles) != 1 else ''}: " + ", ".join(titles[:2]) + (f", +{more}" if more > 0 else "")


# Git ------------------------------------------------------------------------------

def _git(place: Paths, *args: str, timeout: float = GIT_TIMEOUT) -> str:
    """Run git on the backup's own repository; its standard output, or GitError."""
    environment = {name: os.environ[name] for name in _ENV if name in os.environ}
    environment |= {"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", "LC_ALL": "C"}
    command = ["git", "--git-dir", str(place.repo), "--work-tree", str(place.tree),
               "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "gc.auto=0", *args]
    try:
        done = subprocess.run(command, cwd=place.base, env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, errors="replace", timeout=timeout, check=False)
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise GitError("git did not finish in time") from None
    if done.returncode:
        if args[0] == "push" and any(line.startswith("!") for line in done.stdout.splitlines()):
            raise Rejected("GitHub rejected the push")
        lines = [line.strip() for line in done.stderr.splitlines() if line.strip()]
        raise GitError((lines[-1] if lines else f"git {args[0]} failed")[:300])
    return done.stdout


def _branch(device_id: str) -> str:
    return f"refs/heads/devices/{device_id}"


def _head(place: Paths) -> str | None:
    try:
        return _git(place, "rev-parse", "-q", "--verify", "HEAD").strip() or None
    except GitError:
        return None


def commit(place: Paths, device_id: str, device_name: str, message: str) -> str | None:
    """Commit the work tree on this device's branch; the new commit, or None when nothing changed."""
    place.tree.mkdir(parents=True, exist_ok=True)
    if not (place.repo / "HEAD").is_file():
        _git(place, "init", "-q")
    _git(place, "symbolic-ref", "HEAD", _branch(device_id))
    _sweep(place.tree)
    _git(place, "add", "-A", "-f", "--", ".")
    staged = [name for name in _git(place, "ls-files", "-z").split("\0") if name]
    stray = [name for name in staged if not ALLOWED.fullmatch(name)]
    if stray:
        _git(place, "rm", "-q", "--cached", "-r", "--", *stray)
        raise GitError("The backup held a file it must not; nothing was committed")
    if not _git(place, "status", "--porcelain").strip():
        return None
    _git(place, "-c", f"user.name={device_name or 'rika!'}", "-c", "user.email=rika@localhost",
         "commit", "-q", "--no-verify", "-m", message)
    return _head(place)


def push(place: Paths, device_id: str, remotes: list[str]) -> bool:
    """Fast-forward push this device's branch; False when GitHub already has it."""
    head = _head(place)
    tracking = f"refs/remotes/origin/devices/{device_id}"
    try:
        if head is None or _git(place, "rev-parse", "-q", "--verify", tracking).strip() == head:
            return False
    except GitError:
        pass
    branch = _branch(device_id)
    failure: GitError | None = None
    for remote in remotes:
        try:
            _git(place, "push", "--porcelain", remote, f"{branch}:{branch}")
        except Rejected:
            raise
        except GitError as error:
            failure = error
            continue
        _git(place, "update-ref", tracking, head)
        return True
    raise failure or GitError("No address to push to")


# The service ----------------------------------------------------------------------

def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def config(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM repo_sync_config WHERE id=1").fetchone()


def _last(conn: sqlite3.Connection, results: tuple[str, ...] | None = None) -> sqlite3.Row | None:
    if results is None:
        return conn.execute("SELECT * FROM repo_sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    marks = ",".join("?" * len(results))
    return conn.execute(f"SELECT * FROM repo_sync_runs WHERE result IN ({marks}) ORDER BY id DESC LIMIT 1", results).fetchone()


def _failures(conn: sqlite3.Connection) -> int:
    """How many runs failed in a row, counting back from the newest."""
    count = 0
    for row in conn.execute("SELECT result FROM repo_sync_runs ORDER BY id DESC LIMIT 16"):
        if row["result"] not in FAILED:
            break
        count += 1
    return count


def _waiting(conn: sqlite3.Connection, synced_seq: int) -> tuple[datetime, datetime] | None:
    """When the first and the last change that is not backed up were made."""
    row = conn.execute("SELECT MIN(at), MAX(at) FROM op_log WHERE seq>?", (synced_seq,)).fetchone()
    first, last = parse_instant(row[0]), parse_instant(row[1])
    return (first, last) if first is not None and last is not None else None


def next_run(conn: sqlite3.Connection) -> datetime | None:
    """When the schedule runs next, or None while there is nothing to do or it may not run."""
    row = config(conn)
    if not row["repo"] or row["paused"] or row["problem"]:
        return None
    waiting = _waiting(conn, row["synced_seq"])
    failures = _failures(conn)
    if waiting is None and not failures:
        return None
    when = min(waiting[1] + QUIET, waiting[0] + MAX_WAIT) if waiting else None
    if failures:
        retry = parse_instant(_last(conn)["at"]) + min(BACKOFF_MAX, BACKOFF * 2 ** (failures - 1))
        when = max(when, retry) if when else retry
    return when


def problems(conn: sqlite3.Connection, at: datetime | None = None) -> list[dict[str, str]]:
    """What Rika tells the owner: ``[{text, at}]``, empty while the backup is healthy or off."""
    row = config(conn)
    if not row["repo"]:
        return []
    at = at or datetime.now(timezone.utc)
    last = _last(conn)
    if row["problem"]:
        return [{"text": row["problem"], "at": last["at"] if last else _utc(at)}]
    waiting = None if row["paused"] else _waiting(conn, row["synced_seq"])
    good = _last(conn, OK)
    if waiting and waiting[0] < at - STALE and (good is None or parse_instant(good["at"]) < at - STALE):
        reason = f": {last['error']}" if last is not None and last["error"] else ""
        return [{"text": f"The GitHub backup has not worked for 3 days and changes are waiting{reason}",
                 "at": _utc(waiting[0] + STALE)}]
    return []


class RepoSync:
    """The backup of one workspace: its settings, its runs and its schedule."""

    def __init__(self, settings: Settings, *, lock=None, visibility: Callable[[str, str, str], str] = github_visibility,
                 remotes: Callable[[str, str], list[str]] = github_remotes,
                 token: Callable[[], str] | None = None, clock: Callable[[], datetime] | None = None,
                 interval: float = INTERVAL) -> None:
        self.settings = settings
        self.place = paths(settings.root)
        #: Held around the one write a run makes to the notes (giving an old page its Loro document).
        self.lock = lock
        self.visibility = visibility
        self.remotes = remotes
        self.token = token or self._token
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.interval = interval
        #: What GitHub last said about the connected repository: True private, None not known.
        self.private: bool | None = None
        self._running = threading.Lock()
        self._asked = threading.Event()
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def _token(self) -> str:
        from kitty.core import keys

        try:
            return keys.for_settings(self.settings).read(TOKEN_KEY)
        except keys.KeysError:
            return ""

    # What the owner does ----------------------------------------------------------

    def _exclusive(self):
        if not self._running.acquire(blocking=False):
            raise RepoSyncError("A backup is running; try again in a moment", 409)
        return self._running

    def connect(self, conn: sqlite3.Connection, value: object) -> None:
        """Back up to the repository the owner named; refused unless GitHub says it is not public."""
        owner, repo = parse_repo(value)
        if shutil.which("git") is None:
            raise RepoSyncError("git is not installed on this computer", 409)
        try:
            found = self.visibility(owner, repo, self.token())
        except Unreachable as error:
            raise RepoSyncError(f"{error}, so the repository could not be checked", 502) from None
        if found == "public":
            raise RepoSyncError(f"{owner}/{repo} is public. The backup only goes to a private repository.", 409)
        running = self._exclusive()
        try:
            row = config(conn)
            if (row["owner"], row["repo"]) != (owner, repo):
                shutil.rmtree(self.place.base, ignore_errors=True)
            conn.execute("UPDATE repo_sync_config SET owner=?, repo=?, paused=0, problem=NULL, synced_seq=0 WHERE id=1", (owner, repo))
        finally:
            running.release()
        self.private = True if found == "private" else None
        self.ask()

    def pause(self, conn: sqlite3.Connection, paused: object) -> None:
        if not isinstance(paused, bool):
            raise RepoSyncError("paused is true or false")
        if not config(conn)["repo"]:
            raise RepoSyncError("No repository is connected", 409)
        conn.execute("UPDATE repo_sync_config SET paused=?, problem=CASE WHEN ? THEN problem ELSE NULL END WHERE id=1", (int(paused), paused))
        if not paused:
            self.ask()

    def disconnect(self, conn: sqlite3.Connection) -> None:
        """Stop backing up and remove the local copy; the repository on GitHub is left as it is."""
        running = self._exclusive()
        try:
            conn.execute("UPDATE repo_sync_config SET owner=NULL, repo=NULL, paused=0, problem=NULL, synced_seq=0 WHERE id=1")
            shutil.rmtree(self.place.base, ignore_errors=True)
        finally:
            running.release()
        self.private = None

    def sync_now(self, conn: sqlite3.Connection) -> None:
        """Ask for a run now (the owner's Sync now, or Rika's sync_now)."""
        row = config(conn)
        if not row["repo"]:
            raise RepoSyncError("No repository is connected", 409)
        if row["paused"]:
            raise RepoSyncError("The backup is paused", 409)
        conn.execute("UPDATE repo_sync_config SET problem=NULL WHERE id=1")
        self.ask()

    def ask(self) -> None:
        self._asked.set()
        self._wake.set()

    def state(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = config(conn)
        last, good = _last(conn), _last(conn, OK)
        upcoming = next_run(conn)
        return {
            "enabled": True, "reason": None,
            "repo": f"{row['owner']}/{row['repo']}" if row["repo"] else None,
            "private": self.private if row["repo"] else None,
            "paused": bool(row["paused"]), "problem": row["problem"],
            "running": self._running.locked() or (bool(row["repo"]) and self._asked.is_set()),
            "synced_at": good["at"] if good and row["repo"] else None,
            "pages": good["pages"] if good and row["repo"] else 0,
            "last_run": ({"at": last["at"], "commit": last["commit"], "pages": last["pages"], "result": last["result"],
                          "error": last["error"]} if last and row["repo"] else None),
            "next_run": _utc(upcoming) if upcoming else None,
        }

    # Runs -------------------------------------------------------------------------

    def run(self) -> dict[str, Any] | None:
        """One backup run now; its ``repo_sync_runs`` row, or None when there was nothing to run."""
        if not self._running.acquire(blocking=False):
            return None
        try:
            with closing(connect(self.settings.db_path)) as conn:
                return self._run(conn)
        finally:
            self._running.release()

    def _record(self, conn: sqlite3.Connection, at: datetime, result: str, *, commit: str | None = None, pages: int = 0,
                error: str | None = None, problem: str | None = None, synced_seq: int | None = None) -> dict[str, Any]:
        with transaction(conn, immediate=True):
            conn.execute('INSERT INTO repo_sync_runs(at, "commit", pages, result, error) VALUES (?,?,?,?,?)',
                         (_utc(at), commit, pages, result, error))
            if result == "refused":
                conn.execute("UPDATE repo_sync_config SET paused=1, problem=? WHERE id=1", (problem,))
            elif result == "rejected":
                conn.execute("UPDATE repo_sync_config SET problem=? WHERE id=1", (problem,))
            elif synced_seq is not None:
                conn.execute("UPDATE repo_sync_config SET problem=NULL, synced_seq=? WHERE id=1", (synced_seq,))
        return {"at": _utc(at), "commit": commit, "pages": pages, "result": result, "error": error}

    def _run(self, conn: sqlite3.Connection) -> dict[str, Any] | None:
        row = config(conn)
        if not row["repo"]:
            return None
        owner, repo, at = row["owner"], row["repo"], self.clock()
        pages = (_last(conn, OK) or {"pages": 0})["pages"]
        try:
            found = self.visibility(owner, repo, self.token())
        except Unreachable as error:
            return self._record(conn, at, "offline", pages=pages, error=str(error))
        self.private = True if found == "private" else False if found == "public" else None
        if found == "public":
            problem = f"{owner}/{repo} is public on GitHub, so the backup is paused. Make it private, then choose Resume."
            return self._record(conn, at, "refused", pages=pages, error="The repository is public", problem=problem)
        made = None
        try:
            device_id = blocks.device_id(conn)
            result = export(conn, self.settings.root, self.place, at=at, lock=self.lock)
            pages = result.total
            made = commit(self.place, device_id, peers.device_name(conn), commit_message(result))
            pushed = push(self.place, device_id, self.remotes(owner, repo))
        except Rejected:
            problem = ("GitHub rejected the backup: its copy of this device's branch has changes that are not here. "
                       "Nothing was overwritten.")
            return self._record(conn, at, "rejected", commit=made, pages=pages, error="The push was rejected", problem=problem)
        except (GitError, RepoSyncError, OSError) as error:
            return self._record(conn, at, "error", commit=made, pages=pages, error=str(error)[:300])
        return self._record(conn, at, "pushed" if pushed else "unchanged", commit=_head(self.place), pages=pages,
                            synced_seq=result.seq)

    # The schedule -----------------------------------------------------------------

    def due(self, conn: sqlite3.Connection, at: datetime) -> bool:
        when = next_run(conn)
        return when is not None and when <= at

    def tick(self) -> dict[str, Any] | None:
        """Run when asked or due; the run's row, or None."""
        asked = self._asked.is_set()
        self._asked.clear()
        if not asked:
            with closing(connect(self.settings.db_path)) as conn:
                if not self.due(conn, self.clock()):
                    return None
        return self.run()

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("The GitHub backup failed")
            self._wake.wait(self.interval)
            self._wake.clear()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="rika-reposync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

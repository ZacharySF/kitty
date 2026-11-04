"""The active build file and its history on disk.

The active build is ``config/build.json`` in the workspace. It sits beside the other
files a person edits by hand (config/*.yaml), so ``kitty-web --root`` and the desktop
workspace find it where they find the rest of the configuration, an external editor can
open it, and demo workspaces (a temporary root) never touch the real one. It is
owner-only and ignored by Git, like the YAML beside it.

Every write is atomic (temporary file, fsync, rename, directory fsync) and happens under
an exclusive lock on the config directory, so two rika! processes never interleave
writes. A write never follows a symbolic link. Installs and rollbacks first copy the
build they replace into ``data/build-history/`` (runtime state, not configuration); the
newest ``HISTORY_KEEP`` copies are kept.

Before builds, the module selection lived in ``config/modules.yaml``. The first time a
workspace is opened without a build file, ``ensure`` writes one holding that selection
and removes modules.yaml; settings rows and browser preferences follow on first use
(kitty.core.builds.project, the window's one-time move).
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kitty import __version__
from kitty.core.builds import document

BUILD_FILE = "build.json"
LEGACY_MODULES_FILE = "modules.yaml"
HISTORY_DIR = ("data", "build-history")
HISTORY_KEEP = 10
_HISTORY_ID = re.compile(r"[0-9]{8}T[0-9]{12}Z")


class BuildStoreError(Exception):
    """A build operation that cannot proceed; ``status`` is the HTTP equivalent."""

    def __init__(self, message: str, status: int = 400, problems: list[document.Problem] | None = None):
        super().__init__(message)
        self.status = status
        self.problems = problems or []


def build_path(root: Path) -> Path:
    return Path(root) / "config" / BUILD_FILE


def history_dir(root: Path) -> Path:
    return Path(root).joinpath(*HISTORY_DIR)


def revision(text: str | None) -> str:
    """A build text's identity: what windows compare and editors send back."""
    return hashlib.sha256((text or "").encode("utf-8", "surrogatepass")).hexdigest()


@contextmanager
def locked(root: Path) -> Iterator[None]:
    """Exclusive lock on the config directory for one read-modify-write of the build."""
    folder = Path(root) / "config"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def read_text(root: Path) -> str | None:
    """The build file's text, or None when there is none. Never follows a symbolic link."""
    path = build_path(root)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise BuildStoreError(f"config/{BUILD_FILE} is a symbolic link; replace it with a regular file", 409) from None
        raise
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise BuildStoreError(f"config/{BUILD_FILE} is not a regular file", 409)
        data = handle.read(document.MAX_BYTES + 1)
    return data.decode("utf-8", "replace")


def _atomic_write(target: Path, text: str, *, mode: int = 0o600) -> None:
    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.is_symlink():
        raise BuildStoreError(f"{target.name} is a symbolic link; replace it with a regular file", 409)
    if target.exists():
        if not target.is_file():
            raise BuildStoreError(f"{target.name} is not a regular file", 409)
        mode = stat.S_IMODE(target.stat().st_mode)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_text(root: Path, text: str) -> None:
    """Atomically replace the build file (the caller holds ``locked``)."""
    _atomic_write(build_path(root), text)


def _legacy_modules(root: Path) -> list[str] | None:
    """The selection in a pre-build config/modules.yaml, or None when there is none."""
    from kitty.modules import parse_legacy_modules

    path = Path(root) / "config" / LEGACY_MODULES_FILE
    if not path.exists() and not path.is_symlink():
        return None
    return list(parse_legacy_modules(path))


def initial_text(modules: list[str] | None = None) -> str:
    """A new workspace's build: format, version and (when given) the module selection."""
    data: dict[str, Any] = {"schema": document.SCHEMA_VERSION, "kitty_version": __version__}
    if modules is not None:
        data["modules"] = modules
    return document.dump(data)


def ensure(root: Path) -> str:
    """The build file's text, writing it first when the workspace has none.

    A workspace from before builds keeps its module selection (config/modules.yaml,
    which is then removed); one with neither file keeps every module, as before.
    """
    text = read_text(root)
    if text is not None:
        return text
    with locked(root):
        text = read_text(root)
        if text is not None:
            return text
        text = initial_text(_legacy_modules(root))
        write_text(root, text)
        (Path(root) / "config" / LEGACY_MODULES_FILE).unlink(missing_ok=True)
    return text


def current(root: Path) -> document.Checked:
    """The build file as it is now (written first if missing)."""
    return document.check(ensure(root))


def edit(root: Path, mutate: Callable[[dict[str, Any]], None]) -> str:
    """Change the build file through `mutate` (on the document as written); return the new text.

    Refused (409) while the file has an error, so a half-finished edit made in another
    editor is never overwritten; the new document must itself be valid.
    """
    ensure(root)
    with locked(root):
        text = read_text(root) or initial_text()
        new = changed(text, mutate)
        if new != text:
            write_text(root, new)
        return new


def changed(text: str, mutate: Callable[[dict[str, Any]], None]) -> str:
    """The text after `mutate`, validated; unchanged documents keep their exact text."""
    checked = document.check(text)
    if not checked.ok:
        raise BuildStoreError(
            f"config/{BUILD_FILE} has an error ({checked.errors[0]}); fix it in the build editor first",
            409, checked.problems)
    data = json.loads(text)
    before = json.dumps(data, sort_keys=True)
    mutate(data)
    if json.dumps(data, sort_keys=True) == before:
        return text
    new = document.dump(data)
    result = document.check(new)
    if not result.ok:
        raise BuildStoreError(f"That change makes the build invalid: {result.errors[0]}", 400, result.problems)
    return new


def section(data: dict[str, Any], *path: str) -> dict[str, Any]:
    """The object at `path` in a build document, created when missing."""
    node = data
    for key in path:
        child = node.get(key)
        if not isinstance(child, dict):
            child = node[key] = {}
        node = child
    return node


# History ------------------------------------------------------------------------------

def push_history(root: Path, text: str, reason: str, source: str = "") -> str:
    """Keep a copy of a build about to be replaced; return its ID. Prunes old copies."""
    folder = history_dir(root)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.now(timezone.utc)
    entry_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    while (folder / f"{entry_id}.json").exists():
        now = now.replace(microsecond=(now.microsecond + 1) % 1_000_000)
        entry_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    body = json.dumps({"saved_at": now.isoformat(), "reason": reason, "source": source[:500], "text": text})
    _atomic_write(folder / f"{entry_id}.json", body + "\n")
    entries = sorted(path for path in folder.glob("*.json") if _HISTORY_ID.fullmatch(path.stem))
    for old in entries[:-HISTORY_KEEP]:
        old.unlink(missing_ok=True)
    return entry_id


def _read_entry(path: Path) -> dict[str, Any] | None:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
        return None
    return entry


def list_history(root: Path) -> list[dict[str, Any]]:
    """Saved builds, newest first: ID, when, why, where from, and the build's name."""
    folder = history_dir(root)
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        if not _HISTORY_ID.fullmatch(path.stem) or path.is_symlink():
            continue
        entry = _read_entry(path)
        if entry is None:
            continue
        checked = document.check(entry["text"])
        result.append({
            "id": path.stem, "saved_at": entry.get("saved_at", ""), "reason": entry.get("reason", ""),
            "source": entry.get("source", ""), "name": (checked.build or {}).get("name", ""),
            "valid": checked.ok, "revision": revision(entry["text"]),
        })
    return result


def history_text(root: Path, entry_id: str) -> str:
    if not isinstance(entry_id, str) or not _HISTORY_ID.fullmatch(entry_id):
        raise BuildStoreError("Choose a saved build from the history", 400)
    path = history_dir(root) / f"{entry_id}.json"
    entry = None if path.is_symlink() else _read_entry(path)
    if entry is None:
        raise BuildStoreError("That saved build is no longer in the history", 404)
    return entry["text"]

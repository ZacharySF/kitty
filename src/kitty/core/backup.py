"""Whole-workspace backup: one archive file to recover from after loss or corruption.

Three different things keep work safe, and none replaces another:

- **History** recovers an earlier version of a page inside this workspace (op log, Trash).
- **Backup** (this module) is a separate copy with a restore path: one archive holding the
  database, the files its rows point at and the settings files.
- **Sync** (kitty.core.sync) propagates changes between paired devices, mistakes included.

The archive is a zip::

    manifest.json        format, app and schema version, created_at, the source root, per-category
                         counts, every member with its size and SHA-256, what was excluded and why,
                         files rows pointed at that were already missing
    workspace.db         a consistent snapshot taken through SQLite (``VACUUM INTO``), never a copy
                         of the live file; in the copy, pairings and live handoffs are removed
    config/<name>        build.json and the YAML settings files
    files/<path>         durable files under the workspace (:data:`FOLDERS`, and files rows name)
    external/<n>/<name>  files outside the workspace that rows name (a resume registered in place)

Secrets are never read: keys, passwords and the device's sync key live in the keyring or
``.private/``, sign-in tokens in ``.private/`` and ``data/``, and ``.env`` may hold keys.

A restore writes only into a new or empty folder that is not the open workspace, staged
beside it and renamed into place once every member's hash matched, paths in rows were moved
to the new root and the database passed its checks. The open workspace is never touched;
switching to the copy is the owner's choice (start rika! with ``--workspace``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import zipfile
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from kitty import __version__
from kitty.db import connect, latest_version, migrate
from kitty.settings import Settings

FORMAT = 1
KIND = "rika-workspace-backup"
MANIFEST = "manifest.json"
DATABASE = "workspace.db"
#: Folders under the workspace root whose files are durable content, all copied.
FOLDERS = ("data/notes-attachments", "data/document-originals", "resumes", "data/resume_versions",
           "data/approved_resumes", "data/exports", "data/build-history", "data/modules", "data/modules-data",
           "data/demo_resumes")
#: Never copied, even when a row names a file inside: credentials, caches, runtime state and the
#: database itself (it travels as the snapshot).
DENIED = (".private", ".env", ".git", ".cache", "config", "data/firefox", "data/voice", "data/application-recovery",
          "data/apply.db")
#: Columns holding a file path (absolute, or relative to the root).
PATH_COLUMNS = (("resumes", "file_path"), ("resume_versions", "source_path"), ("resume_versions", "pdf_path"),
                ("resume_versions", "manifest_path"), ("resume_artifacts", "file_path"),
                ("resume_artifacts", "original_path"), ("note_attachments", "filepath"),
                ("note_page_maps", "export_path"), ("concept_map_migration", "export_path"))
#: What the manifest and the preview count, in the order they are shown.
COUNTS = (
    ("Note pages", "SELECT count(*) FROM blocks WHERE type='page' AND parent_id IS NULL AND deleted_at IS NULL"),
    ("Documents", "SELECT count(*) FROM blocks WHERE type='document' AND parent_id IS NULL AND deleted_at IS NULL"),
    ("Quick notes", "SELECT count(*) FROM blocks WHERE type='quick_note' AND parent_id IS NULL AND deleted_at IS NULL"),
    ("Note attachments", "SELECT count(*) FROM note_attachments"),
    ("Document originals", "SELECT count(*) FROM document_originals"),
    ("Calendar events", "SELECT count(*) FROM calendar_events WHERE deleted=0"),
    ("Tasks", "SELECT count(*) FROM calendar_tasks WHERE deleted=0"),
    ("Study tasks", "SELECT count(*) FROM study_tasks"),
    ("Flashcards", "SELECT count(*) FROM flash_cards"),
    ("Flashcard reviews", "SELECT count(*) FROM flash_reviews"),
    ("Goals", "SELECT count(*) FROM goals"),
    ("Opportunities", "SELECT count(*) FROM opportunities"),
    ("Jobs", "SELECT count(*) FROM jobs"),
    ("Applications", "SELECT count(*) FROM applications"),
    ("Resumes", "SELECT count(*) FROM resumes"),
    ("Rika messages", "SELECT count(*) FROM rika_messages"),
)
#: Left out of every backup, and why; restoring needs them set up again.
EXCLUDED = (
    {"category": "Keys and passwords",
     "reason": "API keys, account passwords and this device's sync key stay in the keyring or .private/"},
    {"category": "Sign-ins", "reason": "Google, Gmail, Microsoft and Slack tokens; sign in again"},
    {"category": "Paired devices", "reason": "pair again from Settings → Devices"},
    {"category": "Paired programs and phones", "reason": "their tokens are revoked in the copy; pair again"},
    {"category": "Email-verification handoffs", "reason": "live authorizations, not kept"},
    {"category": "GitHub backup connection", "reason": "connect it again in Settings → Backup"},
    {"category": "Caches, browser profiles and voice models", "reason": "rebuilt or downloaded again"},
    {"category": "Module packs", "reason": "downloaded again when a module is turned on"},
)
#: The copy's changes: credentials out, live handoffs and pairings gone (tables that exist).
SCRUB = (
    ("peers", "DELETE FROM peers"),
    ("application_email_verification_requests", "DELETE FROM application_email_verification_requests"),
    ("clients", "UPDATE clients SET token_hash='removed-by-backup:'||id, revoked_at=coalesce(revoked_at, :now)"),
    ("phones", "UPDATE phones SET token_hash='removed-by-backup:'||id, revoked_at=coalesce(revoked_at, :now)"),
    ("repo_sync_config", "UPDATE repo_sync_config SET owner=NULL, repo=NULL, paused=0, problem=NULL, synced_seq=0"),
)
_CONFIG = re.compile(r"build\.json|[A-Za-z0-9_.-]+\.yaml")
_CHUNK = 1 << 20


class BackupError(Exception):
    """A refusal the owner can read; ``status`` is the HTTP status the route answers with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def default_folder() -> Path:
    """Where backups go unless the owner names a folder: outside the workspace."""
    return Path.home() / "rika-backups"


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _absolute(path: Path | str | None, what: str) -> Path:
    if path is None or not str(path).strip():
        raise BackupError(f"Enter the {what} as a full path")
    found = Path(path).expanduser()
    if not found.is_absolute():
        raise BackupError(f"Enter the {what} as a full path, starting with / or ~")
    return Path(os.path.normpath(found))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Per-category counts of a workspace database (categories whose table exists)."""
    found, present = {}, _tables(conn)
    for label, sql in COUNTS:
        table = sql.split(" FROM ")[1].split()[0]
        if table in present:
            found[label] = conn.execute(sql).fetchone()[0]
    return found


def _denied(relative: PurePosixPath) -> bool:
    text = relative.as_posix()
    return any(text == item or text.startswith(item + "/") for item in DENIED) or bool(
        re.fullmatch(r"data/gmail[^/]*", text))


# Backing up -----------------------------------------------------------------------

@dataclass
class _Plan:
    """What goes into one archive: members to the files they are read from."""

    files: dict[str, Path] = field(default_factory=dict)
    relocated: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)


def _walk(root: Path, plan: _Plan) -> None:
    for folder in FOLDERS:
        base = root / folder
        if base.is_symlink() or not base.is_dir():
            continue
        for directory, names, files in os.walk(base):
            names.sort()
            for name in sorted(files):
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink() or not path.is_file():
                    plan.excluded.append({"category": relative, "reason": "a link, not a file"})
                    continue
                plan.files[f"files/{relative}"] = path


def _config(root: Path, plan: _Plan) -> None:
    folder = root / "config"
    if not folder.is_dir():
        return
    for path in sorted(folder.iterdir()):
        if path.is_file() and not path.is_symlink() and _CONFIG.fullmatch(path.name):
            plan.files[f"config/{path.name}"] = path
        elif not path.name.endswith(".example"):
            plan.excluded.append({"category": f"config/{path.name}", "reason": "not a settings file; may hold a secret"})


def _leftovers(root: Path, db_path: Path, plan: _Plan) -> None:
    """List what is in data/ and the root that a backup leaves out, so nothing is silently dropped."""
    if (root / ".env").exists():
        plan.excluded.append({"category": ".env", "reason": "environment settings may hold keys"})
    data = root / "data"
    kept = {Path(folder).parts[1] for folder in FOLDERS if folder.startswith("data/")}
    if data.is_dir():
        for path in sorted(data.iterdir()):
            if path.name in kept or path.name in {db_path.name + suffix for suffix in ("", "-wal", "-shm", "-journal")}:
                continue
            plan.excluded.append({"category": f"data/{path.name}", "reason": "not kept by a backup (tokens, caches, other databases, runtime state)"})


def _referenced(conn: sqlite3.Connection, root: Path, plan: _Plan) -> None:
    present, outside = _tables(conn), 0
    for table, column in PATH_COLUMNS:
        if table not in present:
            continue
        for (value,) in conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
            if not isinstance(value, str) or not value or value in plan.relocated:
                continue
            path = Path(os.path.normpath(Path(value).expanduser() if Path(value).expanduser().is_absolute()
                                         else root / value))
            usable = path.is_file() and not path.is_symlink()
            if path.is_relative_to(root):
                relative = PurePosixPath(path.relative_to(root).as_posix())
                member = f"files/{relative}"
                if member in plan.files or _denied(relative):
                    continue
                if usable:
                    plan.files[member] = path
                else:
                    plan.missing.append(f"{table}.{column}: {value}")
            elif usable:
                outside += 1
                member = f"external/{outside}/{path.name}"
                plan.files[member] = path
                plan.relocated[value] = member
            else:
                plan.missing.append(f"{table}.{column}: {value}")
    if "document_originals" in present:
        for original_id, name in conn.execute("SELECT id, name FROM document_originals ORDER BY id"):
            if f"files/data/document-originals/{original_id}" not in plan.files:
                plan.missing.append(f"document_originals: {name} ({original_id})")


def _snapshot(db_path: Path, target: Path, now: str) -> None:
    """A consistent copy through SQLite, then the copy's credentials and pairings removed."""
    if not db_path.is_file():
        raise BackupError(f"No workspace database at {db_path}", 404)
    with closing(sqlite3.connect(db_path, timeout=30)) as live:
        live.execute("PRAGMA busy_timeout=30000")
        live.execute("VACUUM INTO ?", (str(target),))
    with closing(sqlite3.connect(target, isolation_level=None)) as copy:
        copy.execute("PRAGMA journal_mode=DELETE")
        copy.execute("PRAGMA foreign_keys=ON")
        present = _tables(copy)
        copy.execute("BEGIN")
        for table, sql in SCRUB:
            if table in present:
                copy.execute(sql, {"now": now} if ":now" in sql else {})
        copy.execute("COMMIT")
        # Scrubbed rows must not survive in free pages.
        copy.execute("VACUUM")


def _archive_path(out: Path, stamp: str) -> Path:
    if out.suffix.lower() == ".zip" and not out.is_dir():
        if out.exists():
            raise BackupError(f"{out} already exists", 409)
        return out
    name = f"rika-backup-{stamp}"
    candidate, number = out / f"{name}.zip", 2
    while candidate.exists():
        candidate, number = out / f"{name}-{number}.zip", number + 1
    return candidate


def _add(bundle: zipfile.ZipFile, member: str, source: Path, when: tuple) -> dict:
    info = zipfile.ZipInfo(member, date_time=when)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as stream, bundle.open(info, "w", force_zip64=True) as out:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)
    return {"path": member, "size": size, "sha256": digest.hexdigest()}


def create(settings: Settings, out: Path | str, *, now: datetime | None = None) -> dict:
    """Write one archive of the workspace to ``out`` (a folder, or a new ``.zip`` path)."""
    out = _absolute(out, "backup folder")
    moment = now or _now()
    created_at = moment.isoformat()
    root = settings.root.resolve()
    plan = _Plan()
    _walk(root, plan)
    _config(root, plan)
    _leftovers(root, settings.db_path.resolve(), plan)
    target = _archive_path(out, moment.strftime("%Y%m%d-%H%M%S"))
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = target.parent / f".{target.name}.{secrets.token_hex(4)}.partial"
    work = target.parent / f".{target.name}.{secrets.token_hex(4)}.snapshot"
    work.mkdir(mode=0o700)
    try:
        snapshot = work / DATABASE
        _snapshot(settings.db_path, snapshot, created_at)
        with closing(sqlite3.connect(snapshot)) as copy:
            _referenced(copy, root, plan)
            found = counts(copy)
            schema = copy.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()[0]
        when = moment.timetuple()[:6]
        entries, missing = [], list(plan.missing)
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as raw:
            with zipfile.ZipFile(raw, "w", allowZip64=True) as bundle:
                entries.append(_add(bundle, DATABASE, snapshot, when))
                for member, path in sorted(plan.files.items()):
                    try:
                        entries.append(_add(bundle, member, path, when))
                    except FileNotFoundError:
                        missing.append(f"{member}: removed while backing up")
                files = [entry for entry in entries if entry["path"] != DATABASE]
                manifest = {
                    "kind": KIND, "format": FORMAT, "app_version": __version__, "schema_version": schema,
                    "created_at": created_at, "source_root": str(root), "counts": found,
                    "attachments": {"count": len(files), "bytes": sum(entry["size"] for entry in files)},
                    "files": entries, "relocated": plan.relocated, "missing": missing,
                    "excluded": [*EXCLUDED, *plan.excluded],
                }
                bundle.writestr(zipfile.ZipInfo(MANIFEST, date_time=when),
                                json.dumps(manifest, ensure_ascii=False, indent=1), compress_type=zipfile.ZIP_DEFLATED)
            # A backup is only a backup once it is on disk.
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    result = {"path": str(target), "bytes": target.stat().st_size, "created_at": created_at, "counts": found,
              "files": manifest["attachments"], "excluded": manifest["excluded"], "missing": missing}
    _remember(root, result)
    return result


def _last_path(root: Path) -> Path:
    return root / ".private" / "backup" / "last.json"


def _remember(root: Path, result: dict) -> None:
    path = _last_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {key: result[key] for key in ("path", "bytes", "created_at", "counts", "files")}
    temporary = path.with_name(f".last.{secrets.token_hex(4)}.json")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def last(root: Path) -> dict | None:
    """The last archive this workspace wrote, or None."""
    try:
        return json.loads(_last_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Previewing and restoring ---------------------------------------------------------

def _manifest(bundle: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(bundle.read(MANIFEST))
    except (KeyError, ValueError):
        raise BackupError("This file is not a rika! backup (no readable manifest)") from None
    if not isinstance(manifest, dict) or manifest.get("kind") != KIND or not isinstance(manifest.get("format"), int):
        raise BackupError("This file is not a rika! backup")
    if not isinstance(manifest.get("files"), list):
        raise BackupError("The backup's file list is unreadable")
    return manifest


def _open(archive: Path | str) -> tuple[Path, zipfile.ZipFile]:
    path = _absolute(archive, "backup file")
    if not path.is_file():
        raise BackupError(f"Backup file not found: {path}")
    try:
        return path, zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise BackupError("This file is not a rika! backup (not a zip archive)") from None


def _destination(into: Path | str | None, live_root: Path) -> tuple[Path | None, list[tuple[int, str]]]:
    if into is None or not str(into).strip():
        return None, []
    path = _absolute(into, "restore folder")
    live = live_root.resolve()
    resolved = path.resolve()
    if resolved == live or resolved.is_relative_to(live):
        return path, [(409, "That is the open workspace; restore into a new, empty folder")]
    if path.is_symlink():
        return path, [(409, f"{path} is a link; restore into a new, empty folder")]
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        return path, [(409, f"{path} is not empty; restore into a new, empty folder")]
    return path, []


def _target(member: str) -> PurePosixPath:
    """Where a member is written, relative to the new root; refuses anything that could escape."""
    parts = PurePosixPath(member).parts
    if member == DATABASE:
        return PurePosixPath("data/apply.db")
    if "\\" in member or any(part in {"", ".", ".."} for part in parts) or member.startswith("/"):
        raise BackupError(f"Refusing an unsafe path in the backup: {member}")
    if len(parts) == 2 and parts[0] == "config" and _CONFIG.fullmatch(parts[1]):
        return PurePosixPath(member)
    if len(parts) >= 2 and parts[0] == "files" and not _denied(PurePosixPath(*parts[1:])):
        return PurePosixPath(*parts[1:])
    if len(parts) == 3 and parts[0] == "external" and parts[1].isdigit():
        return PurePosixPath("data/restored-files", parts[1], parts[2])
    raise BackupError(f"Refusing an unexpected path in the backup: {member}")


def _check(bundle: zipfile.ZipFile, manifest: dict) -> tuple[list[tuple[int, str]], list[str]]:
    problems: list[tuple[int, str]] = []
    warnings: list[str] = []
    if manifest["format"] > FORMAT:
        problems.append((409, f"This backup was made by a newer rika! (format {manifest['format']}); update rika! first"))
    schema = manifest.get("schema_version")
    if isinstance(schema, int) and schema > latest_version():
        problems.append((409, f"This backup's database is newer than this rika! (schema {schema}); update rika! first"))
    elif isinstance(schema, int) and schema < latest_version():
        warnings.append(f"Made with an older database (schema {schema}); it is upgraded during restore")
    if manifest.get("app_version") != __version__:
        warnings.append(f"Made by rika! {manifest.get('app_version')}; this is {__version__}")
    names = {info.filename: info for info in bundle.infolist()}
    seen: set[str] = set()
    for entry in manifest["files"]:
        member = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(member, str) or member in seen:
            problems.append((400, "The backup's file list is damaged"))
            continue
        seen.add(member)
        try:
            _target(member)
        except BackupError as error:
            problems.append((400, str(error)))
            continue
        info = names.get(member)
        if info is None:
            problems.append((400, f"Missing from the backup: {member}"))
        elif info.file_size != entry.get("size"):
            problems.append((400, f"Damaged in the backup: {member}"))
    if DATABASE not in seen:
        problems.append((400, "The backup has no database"))
    for item in manifest.get("missing") or []:
        warnings.append(f"Missing when backed up: {item}")
    return problems, warnings


def preview(archive: Path | str, into: Path | str | None, *, live_root: Path) -> dict:
    """What a restore would bring back and what stops it, without writing anything."""
    path, bundle = _open(archive)
    with bundle:
        manifest = _manifest(bundle)
        problems, warnings = _check(bundle, manifest)
    destination, refusals = _destination(into, live_root)
    problems += refusals
    excluded = manifest.get("excluded") or []
    if excluded:
        warnings.append("Not in this backup: " + ", ".join(item.get("category", "?") for item in excluded[:len(EXCLUDED)]))
    return {
        "archive": str(path), "format": manifest["format"], "app_version": manifest.get("app_version"),
        "schema_version": manifest.get("schema_version"), "created_at": manifest.get("created_at"),
        "counts": manifest.get("counts") or {}, "files": manifest.get("attachments") or {"count": 0, "bytes": 0},
        "excluded": excluded, "missing": manifest.get("missing") or [], "warnings": warnings,
        "problems": [message for _, message in problems], "can_restore": not problems,
        "into": str(destination) if destination else None,
    }


def _extract(bundle: zipfile.ZipFile, entry: dict, staging: Path) -> None:
    relative = _target(entry["path"])
    target = staging.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(staging.resolve()):
        raise BackupError(f"Refusing an unsafe path in the backup: {entry['path']}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256()
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        raise BackupError(f"The backup lists {entry['path']} twice") from None
    with os.fdopen(descriptor, "wb") as out, bundle.open(entry["path"]) as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != entry.get("sha256"):
        raise BackupError(f"Damaged in the backup: {entry['path']}")


def _moved(value: str, source: str, into: Path, relocated: dict[str, Path]) -> str:
    if value in relocated:
        return str(relocated[value])
    if value == source or value.startswith(source + os.sep):
        return str(into) + value[len(source):]
    return value


def _relocate(conn: sqlite3.Connection, manifest: dict, into: Path, staging: Path) -> dict[str, str]:
    """Point path columns and resume-version manifests at the new root; returns old → new."""
    source = str(manifest.get("source_root") or "")
    relocated = {old: into.joinpath(*_target(member).parts) for old, member in (manifest.get("relocated") or {}).items()}
    changes: dict[str, str] = {}
    present = _tables(conn)
    for table, column in PATH_COLUMNS:
        if table not in present:
            continue
        for rowid, value in conn.execute(f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL').fetchall():
            if not isinstance(value, str):
                continue
            new = _moved(value, source, into, relocated) if source else relocated.get(value, value)
            if new != value:
                conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (new, rowid))
                if not value.startswith(source + os.sep):
                    changes[value] = new
    if "resume_versions" in present:
        for version_id, manifest_path in conn.execute("SELECT id, manifest_path FROM resume_versions").fetchall():
            staged = staging / Path(manifest_path).relative_to(into) if Path(manifest_path).is_relative_to(into) else None
            if staged is None or not staged.is_file():
                continue
            data = json.loads(staged.read_text(encoding="utf-8"))
            moved = {key: _moved(value, source, into, relocated) if isinstance(value, str) else value
                     for key, value in data.items()}
            if moved != data:
                staged.write_text(json.dumps(moved, ensure_ascii=False, indent=2), encoding="utf-8")
                conn.execute("UPDATE resume_versions SET manifest_hash=? WHERE id=?", (_sha256(staged), version_id))
    return changes


def restore(archive: Path | str, into: Path | str, *, live_root: Path) -> dict:
    """Restore an archive into a new or empty folder; the open workspace is never written."""
    path, bundle = _open(archive)
    with bundle:
        manifest = _manifest(bundle)
        problems, warnings = _check(bundle, manifest)
        destination, refusals = _destination(into, live_root)
        if destination is None:
            raise BackupError("Enter the restore folder as a full path")
        problems = refusals + problems
        if problems:
            status, message = problems[0]
            raise BackupError(message, status)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = destination.parent / f".{destination.name}.{secrets.token_hex(4)}.restoring"
        staging.mkdir(mode=0o700)
        try:
            for entry in manifest["files"]:
                _extract(bundle, entry, staging)
            for folder in ("config", "data"):
                (staging / folder).mkdir(exist_ok=True, mode=0o700)
            with closing(connect(staging / "data" / "apply.db")) as conn:
                conn.execute("BEGIN")
                changes = _relocate(conn, manifest, destination, staging)
                conn.execute("COMMIT")
                migrate(conn)
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("The restored database failed its integrity check")
                if conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise BackupError("The restored database has broken links between rows")
                found = counts(conn)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if destination.exists():
                destination.rmdir()
            os.rename(staging, destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    quoted = shlex.quote(str(destination))
    return {"path": str(destination), "archive": str(path), "counts": found, "warnings": warnings, "relocated": changes,
            "files": manifest.get("attachments") or {"count": 0, "bytes": 0}, "excluded": manifest.get("excluded") or [],
            "open": f"kitty-workspace --workspace {quoted}", "open_cli": f"KITTY_HOME={quoted} kitty-web"}

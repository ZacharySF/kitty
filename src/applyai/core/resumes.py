"""Resume registration, preserving reviewed and historically selected bytes."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from applyai.dates import now_iso
from applyai.models import Resume


def file_hash(path: Path) -> str:
    """sha256 of a file, read in chunks so a big PDF is fine."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _registration(conn: sqlite3.Connection, label: str):
    # Compose with promotion's transaction without committing its savepoint.
    conn.execute("SAVEPOINT resume_registration")
    try:
        # Acquire the writer lock before either the identity or protection read.
        conn.execute("UPDATE resumes SET id=id WHERE label=?", (label,))
        yield
    except BaseException:
        conn.execute("ROLLBACK TO resume_registration")
        conn.execute("RELEASE resume_registration")
        raise
    else:
        conn.execute("RELEASE resume_registration")


def add_resume(
    conn: sqlite3.Connection,
    label: str,
    file_path: Path,
    *,
    description: str | None = None,
    default_tracks: list[str] | None = None,
) -> Resume:
    """Register (or re-register) a resume file under `label`.

    Unreferenced labels may be refreshed. Reviewed artifacts and resumes already
    selected by jobs/applications require a new label for changed content.
    """
    resolved = Path(file_path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"resume file not found: {resolved}")
    digest = file_hash(resolved)
    with _registration(conn, label):
        existing = get_resume_by_label(conn, label)
        if existing is not None and _protected(conn, existing.id):
            if digest != existing.file_hash:
                raise ValueError("This resume is reviewed or historically selected; register changed bytes under a new label")
            stored = Path(existing.file_path)
            if not stored.is_file() or file_hash(stored) != existing.file_hash:
                raise ValueError("Protected resume bytes are missing or changed; restore the original artifact")
            resolved = stored

        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO resumes (label, file_path, file_hash, description, default_tracks,
                                 active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(label) DO UPDATE SET
                file_path      = excluded.file_path,
                file_hash      = excluded.file_hash,
                description    = COALESCE(excluded.description, resumes.description),
                default_tracks = COALESCE(excluded.default_tracks, resumes.default_tracks),
                active         = 1,
                updated_at     = excluded.updated_at
            """,
            (
                label, str(resolved), digest, description,
                json.dumps(default_tracks) if default_tracks else None, timestamp, timestamp,
            ),
        )
        stored = get_resume_by_label(conn, label)
        assert stored is not None            # just inserted
        return stored


def import_resume(
    conn: sqlite3.Connection,
    root: Path,
    source: Path,
    label: str,
    *,
    description: str | None = None,
    default_tracks: list[str] | None = None,
) -> Resume:
    """Copy a resume file into <root>/resumes (gitignored) and register it under `label`.

    Existing files are never overwritten. A name collision uses a content-addressed
    filename. Absolute paths keep imports independent of the caller's directory.
    """
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"resume file not found: {source}")
    existing = get_resume_by_label(conn, label)
    digest = file_hash(source)
    if existing is not None and _protected(conn, existing.id) and digest != existing.file_hash:
        raise ValueError("This resume is reviewed or historically selected; import changed bytes under a new label")
    folder = (root / "resumes").resolve()
    folder.mkdir(exist_ok=True)
    target = folder / source.name
    if target.exists() and file_hash(target) != digest:
        target = folder / f"{source.stem}-{digest}{source.suffix}"
    if source != target:
        if target.exists():
            if file_hash(target) != digest:
                raise ValueError("Imported resume destination has conflicting bytes")
        else:
            # Exclusive creation also protects against concurrent imports.
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
    return add_resume(conn, label, target,
                      description=description, default_tracks=default_tracks)


def _protected(conn: sqlite3.Connection, resume_id: int) -> bool:
    return any(conn.execute(query, (resume_id,)).fetchone() is not None for query in (
        "SELECT 1 FROM resume_artifacts WHERE resume_id=?",
        "SELECT 1 FROM jobs WHERE resume_id=? LIMIT 1",
        "SELECT 1 FROM applications WHERE resume_id=? LIMIT 1",
    ))


def get_resume(conn: sqlite3.Connection, resume_id: int) -> Resume | None:
    """One resume by id."""
    row = conn.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
    return Resume.from_row(row) if row else None


def get_resume_by_label(conn: sqlite3.Connection, label: str) -> Resume | None:
    """One resume by label."""
    row = conn.execute("SELECT * FROM resumes WHERE label = ?", (label,)).fetchone()
    return Resume.from_row(row) if row else None


def list_resumes(conn: sqlite3.Connection, *, active_only: bool = True) -> list[Resume]:
    """Registered resumes, newest label order."""
    sql = "SELECT * FROM resumes"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY label COLLATE NOCASE"
    return [Resume.from_row(row) for row in conn.execute(sql)]


def check_files(conn: sqlite3.Connection) -> list[tuple[Resume, str]]:
    """Return (resume, problem) for resumes whose file is missing or changed.

    A changed hash is not an error, just something `doctor` tells me about: it
    usually means I edited the resume and should re-run `applyai resumes add`.
    """
    problems: list[tuple[Resume, str]] = []
    for resume in list_resumes(conn):
        path = Path(resume.file_path)
        if not path.exists():
            problems.append((resume, "file is missing"))
        elif file_hash(path) != resume.file_hash:
            problems.append((resume, "file changed since it was registered"))
    return problems

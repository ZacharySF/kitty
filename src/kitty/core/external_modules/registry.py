"""Installed external modules: pinned rows in ``external_modules`` and their folders.

    data/modules/<id>/<version>/   one release's unpacked files (read-only to rika!)
    data/modules-data/<id>/        the module's own data; kept when the module is removed

Removal forgets the pin at once, but the running process may still be using the files,
so they are deleted by :func:`prune` when rika! next starts.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kitty.core.external_modules.manifest import (
    ID_RE,
    VERSION_RE,
    Manifest,
    parse_manifest,
)
from kitty.db.connection import transaction
from kitty.settings import Settings


@dataclass(frozen=True)
class Record:
    id: str
    repo: str
    version: str
    sha256: str
    installed_at: str
    manifest: Manifest


def modules_root(settings: Settings) -> Path:
    return settings.data_dir / "modules"


def install_dir(settings: Settings, module_id: str, version: str) -> Path:
    return modules_root(settings) / module_id / version


def data_dir(settings: Settings, module_id: str) -> Path:
    return settings.data_dir / "modules-data" / module_id


def installed(conn: sqlite3.Connection) -> list[Record]:
    """Every pinned module in id order. A row whose manifest no longer validates raises ValueError."""
    rows = conn.execute(
        "SELECT id, repo, version, sha256, installed_at, manifest FROM external_modules ORDER BY id").fetchall()
    return [Record(row["id"], row["repo"], row["version"], row["sha256"], row["installed_at"],
                   parse_manifest(json.loads(row["manifest"]), expect_id=row["id"], expect_version=row["version"]))
            for row in rows]


def get(conn: sqlite3.Connection, module_id: str) -> Record | None:
    return next((record for record in installed(conn) if record.id == module_id), None)


def save(conn: sqlite3.Connection, record: Record) -> None:
    with transaction(conn, immediate=True):
        conn.execute(
            "INSERT INTO external_modules (id, repo, version, sha256, installed_at, manifest) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET repo = excluded.repo, version = excluded.version, sha256 = excluded.sha256, "
            "installed_at = excluded.installed_at, manifest = excluded.manifest",
            (record.id, record.repo, record.version, record.sha256, record.installed_at,
             json.dumps(record.manifest.raw, sort_keys=True)))


def remove(conn: sqlite3.Connection, module_id: str) -> bool:
    with transaction(conn, immediate=True):
        return conn.execute("DELETE FROM external_modules WHERE id = ?", (module_id,)).rowcount > 0


def delete_files(settings: Settings, module_id: str, version: str | None = None) -> None:
    """Delete one version's folder (or every version of the module) under data/modules."""
    folder = modules_root(settings) / module_id
    if version is not None:
        folder = folder / version
    if folder.is_symlink():
        folder.unlink()
    elif folder.exists():
        shutil.rmtree(folder)


def prune(settings: Settings, records: list[Record]) -> None:
    """Remove every module folder that no pin uses (start-up, before anything is loaded).

    Staging leftovers of an interrupted install go too; module data folders stay.
    """
    root = modules_root(settings)
    if not root.is_dir():
        return
    pinned = {record.id: record.version for record in records}
    for entry in root.iterdir():
        if entry.name not in pinned or not ID_RE.fullmatch(entry.name) or not entry.is_dir() or entry.is_symlink():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
            continue
        for version in entry.iterdir():
            if version.name != pinned[entry.name] or not VERSION_RE.fullmatch(version.name):
                if version.is_dir() and not version.is_symlink():
                    shutil.rmtree(version)
                else:
                    version.unlink(missing_ok=True)

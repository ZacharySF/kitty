"""Install or update a module from its repository's latest GitHub release.

Steps, each refusing with a plain reason and leaving nothing behind:

1. find the latest release and its ``<id>-<version>.tar.gz``;
2. download it and check its SHA-256 against the release's published checksum
   (GitHub's asset ``digest`` and/or a ``<asset>.sha256`` file; none = refused);
3. unpack it safely (kitty.core.external_modules.archive) and check rika-module.json
   matches the release and the files it names exist;
4. move it to data/modules/<id>/<version>/ and pin it in ``external_modules``.

Nothing is imported or started here; that happens when rika! next starts.
"""
from __future__ import annotations

import importlib.util
import json
import secrets
import shutil
import sqlite3
from pathlib import Path

from kitty.core.external_modules import archive, github, registry
from kitty.core.external_modules.manifest import (
    MANIFEST_FILE,
    Manifest,
    ManifestError,
    parse_manifest,
    version_key,
)
from kitty.dates import now_iso
from kitty.settings import Settings

MAX_MANIFEST_BYTES = 64 * 1024


class InstallError(ValueError):
    """The module was not installed; the message says why and what to do next."""


def newer(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def _read_manifest(root: Path, release: github.Release) -> Manifest:
    path = root / MANIFEST_FILE
    if path.is_symlink() or not path.is_file():
        raise InstallError(f"{release.asset.name} has no {MANIFEST_FILE} at its top level, so it is not a rika! module")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise InstallError(f"{MANIFEST_FILE} is too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise InstallError(f"{MANIFEST_FILE} is not valid JSON") from None
    try:
        return parse_manifest(data, expect_id=release.module_id, expect_version=release.version)
    except ManifestError as exc:
        raise InstallError(str(exc)) from None


def _inside_file(root: Path, relative: str) -> bool:
    target = (root / relative).resolve()
    return target.is_relative_to(root.resolve()) and target.is_file()


def check_files(root: Path, manifest: Manifest, modules: Path) -> None:
    """The files the manifest names exist inside the module, and its package name is free."""
    if manifest.kind == "python":
        package = manifest.python_package
        assert package is not None
        if not _inside_file(root, f"{package}/__init__.py"):
            raise InstallError(f"The module names Python package {package} but has no {package}/__init__.py")
        try:
            spec = importlib.util.find_spec(package)
        except (ImportError, ValueError):
            spec = None
        origin = Path(spec.origin).resolve() if spec is not None and spec.origin else None
        if spec is not None and (origin is None or not origin.is_relative_to(modules.resolve())):
            raise InstallError(f"The Python package name {package} is already used by rika! or another library; "
                               "the module must choose a different package name")
    if manifest.process is not None:
        head = manifest.process.command[0]
        if "/" in head and not head.startswith("/") and not _inside_file(root, head):
            raise InstallError(f"The module's command {head} is not a file inside the module")
        cwd = (root / manifest.process.cwd).resolve()
        if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
            raise InstallError(f"The module's working folder {manifest.process.cwd} does not exist")
    if manifest.view is not None and manifest.view.script is not None and not _inside_file(root, manifest.view.script):
        raise InstallError(f"The module's view script {manifest.view.script} is missing")


def install(settings: Settings, conn: sqlite3.Connection, repo: str, *, token: str,
            expect_id: str | None = None) -> registry.Record:
    """Install (or update to) the latest release of `repo` and pin it; returns the pin."""
    try:
        repo = github.parse_repo(repo)
        with github.client() as http:
            release = github.latest_release(http, repo, token, expect_id=expect_id)
            existing = registry.get(conn, release.module_id)
            if existing is not None and existing.repo.lower() != repo.lower():
                raise InstallError(f"A module called {release.module_id} is already installed from {existing.repo}; "
                                   "remove it first to install it from another repository")
            expected = github.published_checksum(http, release, token)
            root = registry.modules_root(settings)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            staging = root / f".staging-{secrets.token_hex(8)}"
            staging.mkdir(mode=0o700)
            try:
                tarball = staging / release.asset.name
                actual = github.download(http, release.asset, token, tarball)
                if actual != expected:
                    raise InstallError(f"{release.asset.name} does not match the release's published SHA-256 checksum; "
                                       "it was not installed. Try again later or tell the module's author.")
                if existing is not None and existing.version == release.version:
                    if existing.sha256 != actual:
                        raise InstallError(f"Release {release.version} of {repo} changed since it was installed "
                                           "(its checksum differs), so rika! keeps the installed copy")
                    if registry.install_dir(settings, existing.id, existing.version).is_dir():
                        return existing
                unpacked = staging / "module"
                try:
                    archive.extract(tarball, unpacked)
                except archive.ArchiveError as exc:
                    raise InstallError(f"{exc}; it was not installed") from None
                manifest = _read_manifest(unpacked, release)
                check_files(unpacked, manifest, root)
                target = registry.install_dir(settings, manifest.id, manifest.version)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.exists():
                    # A leftover of an interrupted or removed install of this very version.
                    registry.delete_files(settings, manifest.id, manifest.version)
                unpacked.rename(target)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    except github.GitHubError as exc:
        raise InstallError(str(exc)) from None
    record = registry.Record(manifest.id, repo, manifest.version, actual, now_iso(), manifest)
    registry.save(conn, record)
    return record


def check_updates(conn: sqlite3.Connection, *, token: str) -> tuple[dict[str, str], dict[str, str]]:
    """``({id: newer version}, {id: why it could not be checked})`` for every installed module."""
    updates: dict[str, str] = {}
    errors: dict[str, str] = {}
    records = registry.installed(conn)
    if not records:
        return updates, errors
    with github.client() as http:
        for record in records:
            try:
                release = github.latest_release(http, record.repo, token, expect_id=record.id)
            except github.GitHubError as exc:
                errors[record.id] = str(exc)
                continue
            if newer(release.version, record.version):
                updates[record.id] = release.version
    return updates, errors

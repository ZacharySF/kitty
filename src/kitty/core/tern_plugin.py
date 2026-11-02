"""The kitty Tern plugin (platform design Q1): installing kitty!'s plugin into the user's Tern.

The plugin's source ships with kitty! in ``integrations/tern`` (a desktop build carries it
as ``tern-plugin`` beside the website). Installing copies that package to
``<workspace>/.private/tern/plugin`` and runs ``tern plugin link`` on the copy:

* the copy is where the plugin finds this workspace: its endpoint file is
  ``<workspace>/.private/endpoint.json``, two directories up, so no setting is needed;
* it outlives the desktop sidecar's temporary extraction directory, which a link to the
  bundled files themselves would point into;
* installing again (after a kitty! update) replaces the copy in place, and Tern reloads it.

``tern plugin link`` writes ``<Tern config>/plugins/kitty.path``; Tern's daemons and windows
watch that folder, so the user's own Tern picks the plugin up without kitty! touching its
daemon (every ``tern`` kitty! runs is aimed at kitty!'s own socket). kitty! never downloads
or bundles Tern itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
import tomllib
from pathlib import Path

from kitty.core.tern import TernRuntime, TernUnavailable

PLUGIN_ID = "kitty"
#: Shipped files that are not part of the package Tern loads.
_SKIPPED = {"tests", "tern.d.luau"}


class PluginError(Exception):
    """The plugin cannot be installed or removed; the message is safe to show."""


def shipped_dir() -> Path | None:
    """The plugin package this kitty! ships, or None when this build has none."""
    bundle = getattr(sys, "_MEIPASS", None)
    folder = Path(bundle) / "tern-plugin" if bundle else Path(__file__).resolve().parents[3] / "integrations" / "tern"
    return folder if (folder / "plugin.toml").is_file() else None


def installed_dir(root: Path) -> Path:
    return root / ".private" / "tern" / "plugin"


def _files(folder: Path) -> list[Path]:
    """The package's files, relative to `folder`, in a stable order."""
    found = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if relative.parts[0] in _SKIPPED or any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_file() and not path.is_symlink():
            found.append(relative)
    return found


def digest(folder: Path) -> str:
    """One hash of every package file's path and bytes."""
    total = hashlib.sha256()
    for relative in _files(folder):
        total.update(relative.as_posix().encode() + b"\0")
        total.update(hashlib.sha256((folder / relative).read_bytes()).digest())
    return total.hexdigest()


def _manifest(folder: Path) -> dict:
    try:
        return tomllib.loads((folder / "plugin.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _plugins_folder(runtime: TernRuntime) -> Path:
    folder = runtime.plugin("dir").strip()
    if not os.path.isabs(folder):
        raise TernUnavailable("`tern plugin dir` did not name a folder")
    return Path(folder)


def _linked(plugins: Path) -> str | None:
    """Where ``kitty.path`` points, or None when Tern has no link for the plugin."""
    try:
        return (plugins / f"{PLUGIN_ID}.path").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _catalog(runtime: TernRuntime, target: Path) -> tuple[str | None, list[str]]:
    """The plugin's status in the catalog Tern reports (kitty!'s daemon's, else the folder's),
    and the folder problems that concern it."""
    try:
        report = json.loads(runtime.plugin("list", "--json"))
        entries = report.get("plugins") or []
        problems = report.get("problems") or []
    except (ValueError, AttributeError):
        raise TernUnavailable("`tern plugin list` did not report its plugins") from None
    found = next((item for item in entries if isinstance(item, dict) and item.get("id") == PLUGIN_ID), None)
    concerns = [f"{item.get('dir')}: {item.get('error')}" for item in problems if isinstance(item, dict)
                and (item.get("dir") == str(target) or f" {PLUGIN_ID}" in f" {item.get('error', '')}")]
    return (str(found.get("status")) if found else None), concerns


def status(runtime: TernRuntime, root: Path) -> dict:
    """What kitty! shipped, what is installed for this workspace and what Tern reports."""
    shipped = shipped_dir()
    target = installed_dir(root)
    copied = (target / "plugin.toml").is_file() and not target.is_symlink()
    plugins = _plugins_folder(runtime)
    linked = _linked(plugins)
    tern_status, problems = _catalog(runtime, target)
    return {
        "available": shipped is not None,
        "version": _manifest(shipped).get("version") if shipped else None,
        "dir": str(target),
        "installed": copied and linked == str(target),
        "installed_version": _manifest(target).get("version") if copied else None,
        "current": bool(shipped and copied and digest(shipped) == digest(target)),
        "linked_elsewhere": linked if linked and linked != str(target) else None,
        "tern_status": tern_status,
        "problems": problems,
    }


def _private_folder(path: Path) -> None:
    """`path` as a directory this user owns (0700); a symlink there is refused."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PluginError(f"{path} is not a directory owned by this user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


def _copy(shipped: Path, target: Path) -> None:
    """Replace `target` with a fresh copy of the package, never leaving half a copy there."""
    parent = target.parent
    _private_folder(parent.parent)
    _private_folder(parent)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise PluginError(f"{target} is not kitty!'s plugin folder")
    staging = parent / f".plugin-{secrets.token_hex(6)}"
    try:
        for relative in _files(shipped):
            destination = staging / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(shipped / relative, destination)
        if target.exists():
            retired = parent / f".plugin-old-{secrets.token_hex(6)}"
            os.rename(target, retired)
            os.rename(staging, target)
            shutil.rmtree(retired, ignore_errors=True)
        else:
            os.rename(staging, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install(runtime: TernRuntime, root: Path) -> dict:
    """Copy the shipped plugin into the workspace and link it into the user's Tern."""
    shipped = shipped_dir()
    if shipped is None:
        raise PluginError("This kitty! was built without the kitty Tern plugin")
    plugins = _plugins_folder(runtime)
    if (plugins / PLUGIN_ID).exists():
        raise PluginError(f"Tern already has a plugin installed as {plugins / PLUGIN_ID}; "
                          f"remove it with `tern plugin remove {PLUGIN_ID}` first")
    target = installed_dir(root)
    try:
        _copy(shipped, target)
    except OSError as exc:
        raise PluginError(f"kitty! could not copy the plugin to {target}: {exc.strerror or exc}") from None
    try:
        linked = json.loads(runtime.plugin("link", str(target), "--json"))
    except ValueError:
        raise TernUnavailable("`tern plugin link` did not report the plugin") from None
    if not isinstance(linked, dict) or linked.get("id") != PLUGIN_ID:
        raise TernUnavailable("`tern plugin link` linked something other than kitty!'s plugin")
    return status(runtime, root)


def remove(runtime: TernRuntime, root: Path) -> dict:
    """Unlink this workspace's copy from Tern (never another workspace's link) and delete it."""
    target = installed_dir(root)
    if _linked(_plugins_folder(runtime)) == str(target):
        runtime.plugin("unlink", PLUGIN_ID)
    if target.is_dir() and not target.is_symlink():
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise PluginError(f"kitty! could not delete {target}: {exc.strerror or exc}") from None
    return status(runtime, root)

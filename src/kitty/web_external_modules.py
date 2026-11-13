"""External modules in the website (docs/MODULES.md): Settings → Modules and loading.

Settings routes (core, always on)::

    POST /api/external-modules/install {"repo": "owner/repo"}  -> {task}; result = modules state
    POST /api/external-modules/update  {"id"}                   -> {task}; result = modules state
    POST /api/external-modules/remove  {"id"}                   -> modules state
    POST /api/external-modules/check   {}                       -> {task}; result = {updates, errors}

The installed set is read once at start-up, like the built-in module selection: an
install, update or removal is pinned at once but applies on the next start. Each active
module owns ``/api/modules/<id>`` and everything below it:

* ``python`` modules are imported from their install folder and get the same hooks as
  built-in extensions (``get``/``post``/``start``/``stop``/``upgrade``/``download``/
  ``mutated``), after their own ``migrations/`` ran in their own version namespace;
* ``process`` modules are web servers started on demand on a loopback port chosen at
  start-up (``GET /api/modules/<id>`` -> ``{url, available}``, ``POST .../start``), framed
  by the page; that port joins the CSP ``frame-src`` and the desktop navigation guard.

An ``element`` view's script is served same-origin from ``/modules/<id>/<version>/<path>``.
A module that cannot load is reported in Settings and never stops rika! from starting.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import shutil
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from kitty.core import keys
from kitty.core.external_modules import github, installer, registry
from kitty.core.external_modules.manifest import PLACEHOLDER_RE, Manifest
from kitty.db import connect, migrate
from kitty.process_runtime import (
    LoopbackProcess,
    bundled_node,
    free_port,
    loader_environment,
)

log = logging.getLogger(__name__)

PREFIX = "/api/external-modules"
ROUTES = "/api/modules/"
ASSETS = "/modules/"
TOKEN = "GITHUB"
#: Files an element view may load: scripts, styles, images and fonts, never HTML or source.
ASSET_TYPES = {
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
}
MAX_ASSET = 16 * 1024 * 1024


class ModuleProcess(LoopbackProcess):
    """A ``process`` module's server: clean environment, placeholders filled, output logged."""

    def __init__(self, manifest: Manifest, directory: Path, data: Path, port: int):
        spec = manifest.process
        assert spec is not None
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        super().__init__(port, health=spec.health, ready_timeout=spec.ready_timeout_s, log=data / "process.log")
        self.label = manifest.name
        self.manifest = manifest
        self.directory = directory
        self.data = data

    def _program(self, head: str) -> str:
        if os.path.isabs(head):
            program = Path(head)
        elif "/" in head:
            program = (self.directory / head).resolve()
            if not program.is_relative_to(self.directory.resolve()):
                raise RuntimeError(f"{self.label}'s command {head} is outside the module; reinstall it.")
        elif head == "node" and bundled_node().is_file():
            program = bundled_node()
        else:
            found = shutil.which(head)
            if found is None:
                raise RuntimeError(f"{self.label} needs the program '{head}', which is not installed on this computer.")
            program = Path(found)
        if not program.is_file() or not os.access(program, os.X_OK):
            raise RuntimeError(f"{self.label}'s program {head} is missing or not executable; reinstall the module.")
        return str(program)

    def prepare(self):
        spec = self.manifest.process
        assert spec is not None
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data, 0o700)
        home = self.data / "home"
        home.mkdir(exist_ok=True, mode=0o700)
        values = {"data": str(self.data), "port": str(self.port), "module": str(self.directory)}
        # Nothing inherited but the program search path and Nix's loader paths: no keys,
        # workspace paths, NODE_OPTIONS or user tool configuration.
        environment = {"HOME": str(home), "LANG": "C.UTF-8", "PATH": os.environ.get("PATH", os.defpath),
                       **loader_environment()}
        for name, text in spec.env.items():
            environment[name] = PLACEHOLDER_RE.sub(lambda match: values[match[1]], text)
        cwd = (self.directory / spec.cwd).resolve()
        if not cwd.is_relative_to(self.directory.resolve()) or not cwd.is_dir():
            raise RuntimeError(f"{self.label}'s working folder is missing; reinstall the module.")
        return [self._program(spec.command[0]), *spec.command[1:]], cwd, environment

    def cannot_start(self) -> str:
        return f"{self.label} could not start on this computer. Its output is in {self.log}."

    def not_ready(self) -> str:
        return (f"{self.label} did not become ready within {self.ready_timeout:g} seconds. "
                f"Its output is in {self.log}; try Start again.")


@dataclass
class Active:
    """A module this process tried to load at start-up."""

    record: registry.Record
    directory: Path
    error: str | None = None
    extension: ModuleType | None = None
    process: ModuleProcess | None = None
    started: bool = False

    @property
    def manifest(self) -> Manifest:
        return self.record.manifest

    @property
    def ok(self) -> bool:
        return self.error is None


def _unload(package: str, directory: Path) -> None:
    for name in [name for name in sys.modules if name == package or name.startswith(package + ".")]:
        del sys.modules[name]
    while str(directory) in sys.path:
        sys.path.remove(str(directory))
    importlib.invalidate_caches()


class ExternalModules:
    """The external modules of one rika! process, frozen at start-up."""

    def __init__(self, app: Any):
        self.app = app
        self.settings = app.settings
        self.active: dict[str, Active] = {}
        self.startup_error: str | None = None
        with closing(connect(self.settings.db_path)) as conn:
            try:
                records = registry.installed(conn)
            except ValueError as exc:
                self.startup_error = f"The installed modules list could not be read: {exc}"
                return
            registry.prune(self.settings, records)
            for record in records:
                self.active[record.id] = self._load(conn, record)

    def _load(self, conn, record: registry.Record) -> Active:
        directory = registry.install_dir(self.settings, record.id, record.version)
        active = Active(record, directory)
        if not (directory / "rika-module.json").is_file():
            active.error = "Its files are missing. Use Update to download it again, or Remove it."
            return active
        manifest = record.manifest
        if manifest.kind == "process":
            active.process = ModuleProcess(manifest, directory, registry.data_dir(self.settings, record.id), free_port())
            return active
        package = manifest.python_package
        assert package is not None
        try:
            spec = importlib.util.find_spec(package)
        except (ImportError, ValueError):
            spec = None
        if spec is not None:
            active.error = f"Its Python package name {package} is already used by rika! or another module."
            return active
        try:
            migrations = directory / "migrations"
            if migrations.is_dir():
                migrate(conn, migrations, namespace=record.id)
            registry.data_dir(self.settings, record.id).mkdir(parents=True, exist_ok=True, mode=0o700)
            sys.path.append(str(directory))
            extension = importlib.import_module(package)
            origin = Path(getattr(extension, "__file__", "") or "").resolve()
            if not origin.is_relative_to(directory.resolve()):
                raise ImportError(f"{package} was not loaded from the module's folder")
            active.extension = extension
            start = getattr(extension, "start", None)
            if start:
                start(self.app)
            active.started = True
        except Exception as exc:
            log.warning("External module %s failed to load", record.id, exc_info=True)
            active.error = f"{type(exc).__name__}: {exc}"
            active.extension = None
            _unload(package, directory)
        return active

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        for active in reversed(list(self.active.values())):
            if active.process is not None:
                active.process.close()
            if active.extension is not None:
                stop = getattr(active.extension, "stop", None) or getattr(active.extension, "close", None)
                if stop and active.started:
                    try:
                        stop(self.app)
                    except Exception:
                        log.warning("External module %s failed to stop", active.record.id, exc_info=True)
                _unload(active.manifest.python_package or "", active.directory)
                active.extension = None

    def frame_ports(self) -> tuple[int, ...]:
        return tuple(active.process.port for active in self.active.values() if active.ok and active.process is not None)

    def loaded(self) -> list[Active]:
        return [active for active in self.active.values() if active.ok]

    # ------------------------------------------------------------------ Settings state

    def views(self) -> list[dict[str, Any]]:
        views = []
        for active in self.loaded():
            view = active.manifest.view
            if view is None:
                continue
            item: dict[str, Any] = {
                "module": active.record.id, "version": active.record.version, "id": view.id, "label": view.label,
                "icon": view.icon, "group": view.group, "first": view.first, "ui": view.ui,
            }
            if view.ui == "element":
                item["script"] = f"{ASSETS}{active.record.id}/{active.record.version}/{view.script}"
                item["tag"] = view.tag
            views.append(item)
        return views

    def state(self, conn) -> tuple[dict[str, Any], bool]:
        """``(external, pending)``: the Settings list, active views and token status; pending
        is True when a change waits for a restart."""
        try:
            pinned = {record.id: record for record in registry.installed(conn)}
        except ValueError:
            pinned = {}
        entries = []
        for module_id in sorted(set(pinned) | set(self.active)):
            record = pinned.get(module_id)
            active = self.active.get(module_id)
            source = record or active.record  # type: ignore[union-attr]
            manifest = source.manifest
            if record is None:
                status, message = "restart-to-remove", "Removed. Restart rika! to finish removing it."
            elif active is None:
                status, message = "restart-to-install", "Installed. Restart rika! to finish installing."
            elif active.record.version != record.version:
                status = "restart-to-update"
                message = (f"Updated to {record.version}. Restart rika! to finish updating "
                           f"(version {active.record.version} runs until then).")
            elif not active.ok:
                status, message = "failed", "Could not start. Try Update, or Remove it."
            else:
                status, message = "active", "Installed and running."
            entry: dict[str, Any] = {
                "id": module_id, "name": manifest.name, "version": source.version, "repo": source.repo,
                "description": manifest.description, "license": manifest.license, "kind": manifest.kind,
                "installed_at": record.installed_at if record else None, "sha256": record.sha256 if record else None,
                "status": status, "active_version": active.record.version if active and active.ok else None,
                "message": message,
            }
            if status == "failed" and active is not None:
                entry["error"] = active.error
            entries.append(entry)
        try:
            configured = keys.for_settings(self.settings).lookup(TOKEN) is not None
        except keys.KeysError:
            configured = False
        pending = any(entry["status"].startswith("restart-") for entry in entries)
        return {"installed": entries, "views": self.views(), "token": {"configured": configured}}, pending

    # ------------------------------------------------------------------ routes

    def owner(self, conn, path: str) -> Active | None:
        """The active module owning an ``/api/modules/<id>`` path; APIError for one not running."""
        from kitty.web_server import APIError

        if not path.startswith(ROUTES):
            return None
        module_id = path[len(ROUTES):].split("/", 1)[0]
        active = self.active.get(module_id)
        if active is None:
            record = registry.get(conn, module_id) if module_id else None
            if record is not None:
                raise APIError(409, f"Restart rika! to finish installing {record.manifest.name}.")
            return None
        if not active.ok:
            raise APIError(503, f"{active.manifest.name} could not start: {active.error}")
        return active

    def asset(self, path: str) -> tuple[bytes, str] | None:
        """An active element view's same-origin file, or None (404)."""
        parts = path[len(ASSETS):].split("/")
        if len(parts) < 3:
            return None
        module_id, version, rest = parts[0], parts[1], parts[2:]
        active = self.active.get(module_id)
        if active is None or not active.ok or active.record.version != version:
            return None
        if any(not part or part.startswith(".") for part in rest):
            return None
        kind = ASSET_TYPES.get(Path(rest[-1]).suffix.lower())
        target = (active.directory / "/".join(rest)).resolve()
        if kind is None or not target.is_relative_to(active.directory.resolve()) or not target.is_file():
            return None
        if target.stat().st_size > MAX_ASSET:
            return None
        return target.read_bytes(), kind


# ---------------------------------------------------------------------- extension hooks


def start(app):
    app.external_modules = ExternalModules(app)


def stop(app):
    app.external_modules.close()


def mutated(app):
    for active in app.external_modules.loaded():
        hook = getattr(active.extension, "mutated", None) if active.extension else None
        if hook:
            try:
                hook(app)
            except Exception:
                log.warning("External module %s failed in mutated()", active.record.id, exc_info=True)


def get(app, conn, path, query):
    active = app.external_modules.owner(conn, path)
    if active is None:
        return None
    if active.process is not None:
        if path != f"{ROUTES}{active.record.id}":
            return None
        app._no_query(query)
        return {"url": active.process.url, "available": active.process.available()}
    hook = getattr(active.extension, "get", None)
    return hook(app, conn, path, query) if hook else None


def download(app, conn, path, query):
    active = app.external_modules.owner(conn, path)
    hook = getattr(active.extension, "download", None) if active is not None and active.extension else None
    return hook(app, conn, path, query) if hook else None


def upgrade(app, handler, path, query):
    with closing(connect(app.settings.db_path)) as conn:
        active = app.external_modules.owner(conn, path)
    hook = getattr(active.extension, "upgrade", None) if active is not None and active.extension else None
    return hook(app, handler, path, query) if hook else False


def post(app, conn, path, body):
    from kitty.web_server import APIError

    if path.startswith(PREFIX + "/"):
        return _settings(app, conn, path.removeprefix(PREFIX + "/"), body)
    active = app.external_modules.owner(conn, path)
    if active is None:
        return None
    if active.process is not None:
        if path != f"{ROUTES}{active.record.id}/start":
            return None
        if body:
            raise APIError(400, "Start does not accept settings, URLs or credentials")
        try:
            active.process.open()
        except RuntimeError as error:
            raise APIError(503, str(error)) from None
        return {"url": active.process.url, "available": True}
    hook = getattr(active.extension, "post", None)
    return hook(app, conn, path, body) if hook else None


def _token(app) -> str:
    from kitty.web_server import APIError

    try:
        return keys.read(app.settings, TOKEN)
    except keys.KeysError as exc:
        raise APIError(exc.status, str(exc)) from None


def _install(app, repo: str, expect_id: str | None) -> dict:
    from kitty.web_server import APIError

    token = _token(app)
    with closing(connect(app.settings.db_path)) as conn:
        try:
            installer.install(app.settings, conn, repo, token=token, expect_id=expect_id)
        except installer.InstallError as exc:
            raise APIError(400, str(exc)) from None
        return app.modules_state(conn)


def _check(app) -> dict:
    token = _token(app)
    with closing(connect(app.settings.db_path)) as conn:
        updates, errors = installer.check_updates(conn, token=token)
    return {"updates": updates, "errors": errors}


def _settings(app, conn, action: str, body: dict) -> dict:
    from kitty.web_server import APIError

    if action == "install":
        if set(body) != {"repo"}:
            raise APIError(400, "Expected {\"repo\": \"owner/repo\"}")
        try:
            repo = github.parse_repo(body["repo"])
        except github.GitHubError as exc:
            raise APIError(400, str(exc)) from None
        return {"task": app.tasks.start("external-module", lambda: _install(app, repo, None))}
    if action == "check":
        if body:
            raise APIError(400, "Checking for updates takes no settings")
        return {"task": app.tasks.start("external-module-check", lambda: _check(app))}
    if action not in ("update", "remove"):
        raise APIError(404, "API route not found")
    if set(body) != {"id"} or not isinstance(body["id"], str):
        raise APIError(400, "Expected {\"id\": module id}")
    record = registry.get(conn, body["id"])
    if record is None:
        raise APIError(404, "That module is not installed")
    if action == "update":
        return {"task": app.tasks.start("external-module", lambda: _install(app, record.repo, record.id))}
    registry.remove(conn, record.id)
    if record.id not in app.external_modules.active:
        # Never loaded by this process: nothing is using its files.
        registry.delete_files(app.settings, record.id)
    return app.modules_state(conn)

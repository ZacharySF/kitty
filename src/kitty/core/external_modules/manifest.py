"""``rika-module.json``: the api-1 contract an external module declares (docs/MODULES.md).

Validation is strict (unknown keys are errors, so a typo cannot silently change what a
module does) and every error names the offending field in plain words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

API = 1
MANIFEST_FILE = "rika-module.json"
ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
ID_MAX = 48
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?")
PACKAGE_RE = re.compile(r"[a-z_][a-z0-9_]*")
ENV_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
ICON_RE = re.compile(r"[a-z0-9-]{1,40}")
TAG_RE = re.compile(r"[a-z][a-z0-9._]*-[a-z0-9._-]*")
PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
#: ``{data}`` the module's own data folder, ``{port}`` its loopback port, ``{module}`` its install folder.
PLACEHOLDERS = frozenset({"data", "port", "module"})
GROUPS = ("plan", "study", "jobs", "settings")
#: Ids an external module may not take: the built-in modules (kitty.modules).
RESERVED = frozenset({"applyai", "workspace", "terminal"})

_TOP = {"id", "name", "version", "api", "description", "license", "kind", "python", "process", "view", "homepage"}
_PROCESS = {"command", "cwd", "env", "health", "ready_timeout_s"}
_VIEW = {"id", "label", "icon", "group", "first", "ui", "element"}


class ManifestError(ValueError):
    """rika-module.json does not follow the module contract; the message says which field."""


@dataclass(frozen=True)
class ProcessSpec:
    command: tuple[str, ...]
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)
    health: str = "/"
    ready_timeout_s: int = 30


@dataclass(frozen=True)
class ViewSpec:
    id: str
    label: str
    icon: str
    group: str
    first: bool
    ui: str
    script: str | None = None
    tag: str | None = None


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    description: str
    license: str
    kind: str
    python_package: str | None
    process: ProcessSpec | None
    view: ViewSpec | None
    homepage: str
    #: The manifest as published, kept so the registry can re-check it at every start.
    raw: dict[str, Any]


def _fail(message: str) -> ManifestError:
    return ManifestError(f"{MANIFEST_FILE}: {message}")


def _object(value: Any, where: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(f"{where} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail(f"{where} has unknown field {unknown[0]!r} (allowed: {', '.join(sorted(allowed))})")
    return value


def _text(data: dict[str, Any], key: str, where: str, *, required: bool = True, maximum: int = 200,
          default: str = "") -> str:
    if key not in data:
        if required:
            raise _fail(f"{where}{key} is required")
        return default
    value = data[key]
    if not isinstance(value, str) or not value.strip() and required or len(value) > maximum or "\x00" in value:
        raise _fail(f"{where}{key} must be text of at most {maximum} characters")
    return value.strip()


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) > ID_MAX or not ID_RE.fullmatch(value):
        raise _fail(f"{where} must be lower-case letters, digits and single hyphens, like rika-learn "
                    f"(at most {ID_MAX} characters)")
    return value


def relative_path(value: Any, where: str) -> str:
    """A forward-slash path inside the module folder: no absolute, ``.``/``..`` or hidden parts."""
    if not isinstance(value, str) or not value or len(value) > 200 or value.startswith("/") or "\\" in value \
            or "\x00" in value:
        raise _fail(f"{where} must be a relative path inside the module, like ui/view.js")
    parts = value.split("/")
    if value != "." and any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise _fail(f"{where} must be a relative path inside the module, like ui/view.js")
    return value


def _process(value: Any) -> ProcessSpec:
    data = _object(value, "process", _PROCESS)
    command = data.get("command")
    if (not isinstance(command, list) or not 1 <= len(command) <= 64
            or not all(isinstance(part, str) and part and "\x00" not in part and len(part) <= 4096 for part in command)):
        raise _fail("process.command must be a list of 1-64 non-empty strings, like [\"node\", \"server.js\"]")
    cwd = relative_path(data.get("cwd", "."), "process.cwd")
    env = data.get("env", {})
    if not isinstance(env, dict) or len(env) > 64:
        raise _fail("process.env must be an object of at most 64 variables")
    for key, text in env.items():
        if not ENV_RE.fullmatch(key):
            raise _fail(f"process.env name {key!r} must be upper-case letters, digits and _")
        if not isinstance(text, str) or len(text) > 4096 or "\x00" in text:
            raise _fail(f"process.env {key} must be text")
        for name in PLACEHOLDER_RE.findall(text):
            if name not in PLACEHOLDERS:
                raise _fail(f"process.env {key} uses unknown placeholder {{{name}}}; use {{data}}, {{port}} or {{module}}")
    health = data.get("health", "/")
    if not isinstance(health, str) or not health.startswith("/") or health.startswith("//") or len(health) > 200 \
            or any(character in health for character in " \r\n\x00#"):
        raise _fail("process.health must be a path on the module's own server, like /")
    timeout = data.get("ready_timeout_s", 30)
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise _fail("process.ready_timeout_s must be a whole number of seconds from 1 to 300")
    return ProcessSpec(tuple(command), cwd, dict(env), health, timeout)


def _view(value: Any, kind: str) -> ViewSpec:
    data = _object(value, "view", _VIEW)
    view_id = _identifier(data.get("id"), "view.id")
    label = _text(data, "label", "view.", maximum=40)
    icon = data.get("icon", "module")
    if not isinstance(icon, str) or not ICON_RE.fullmatch(icon):
        raise _fail("view.icon must be a short lower-case icon name, like graduation-cap")
    group = data.get("group")
    if group not in GROUPS:
        raise _fail(f"view.group must be one of {', '.join(GROUPS)}")
    first = data.get("first", False)
    if type(first) is not bool:
        raise _fail("view.first must be true or false")
    ui = data.get("ui")
    expected = "element" if kind == "python" else "iframe"
    if ui not in ("iframe", "element"):
        raise _fail("view.ui must be \"iframe\" or \"element\"")
    if ui != expected:
        raise _fail(f"view.ui must be \"{expected}\" for a {kind} module "
                    f"({'a script rika! serves' if kind == 'python' else 'a page from its own server'})")
    if ui == "iframe":
        if "element" in data:
            raise _fail("view.element is only for view.ui \"element\"")
        return ViewSpec(view_id, label, icon, group, first, ui)
    element = _object(data.get("element"), "view.element", {"script", "tag"})
    script = relative_path(element.get("script"), "view.element.script")
    if not script.endswith((".js", ".mjs")):
        raise _fail("view.element.script must be a .js or .mjs file")
    tag = element.get("tag")
    if not isinstance(tag, str) or len(tag) > 64 or not TAG_RE.fullmatch(tag):
        raise _fail("view.element.tag must be a custom element name with a hyphen, like rika-math-view")
    return ViewSpec(view_id, label, icon, group, first, ui, script, tag)


def parse_manifest(data: Any, *, expect_id: str | None = None, expect_version: str | None = None) -> Manifest:
    """The validated manifest, or ManifestError naming the first problem."""
    data = _object(data, "the manifest", _TOP)
    api = data.get("api")
    if type(api) is not int or api != API:
        raise _fail(f"api must be {API} (this rika! understands module api {API})")
    module_id = _identifier(data.get("id"), "id")
    if module_id in RESERVED:
        raise _fail(f"id {module_id!r} is a built-in module's name; choose another id")
    version = data.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise _fail("version must look like 1.2.3 (semantic versioning)")
    if expect_id is not None and module_id != expect_id:
        raise _fail(f"id is {module_id!r} but the release asset is for {expect_id!r}")
    if expect_version is not None and version != expect_version:
        raise _fail(f"version is {version} but the release is {expect_version}")
    name = _text(data, "name", "", maximum=60)
    description = _text(data, "description", "", required=False, maximum=500)
    license_name = _text(data, "license", "", required=False, maximum=64)
    homepage = _text(data, "homepage", "", required=False, maximum=300)
    kind = data.get("kind")
    if kind not in ("python", "process"):
        raise _fail("kind must be \"python\" or \"process\"")
    other = "process" if kind == "python" else "python"
    if other in data:
        raise _fail(f"{other} is not used by a {kind} module; remove it")
    package = None
    process = None
    if kind == "python":
        python = _object(data.get("python"), "python", {"package"})
        package = python.get("package")
        if not isinstance(package, str) or len(package) > 64 or not PACKAGE_RE.fullmatch(package):
            raise _fail("python.package must be a Python package name, like rika_math")
    else:
        if "process" not in data:
            raise _fail("process is required for a process module")
        process = _process(data["process"])
    view = _view(data["view"], kind) if "view" in data else None
    return Manifest(module_id, name, version, description, license_name, kind, package, process, view,
                    homepage, dict(data))


def version_key(version: str) -> tuple:
    """Semantic-version order: numbers compare as numbers and a pre-release sorts first."""
    match = VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"not a version: {version!r}")
    major, minor, patch, pre = match.groups()
    tail: tuple = (1,) if pre is None else (0, *((0, int(part)) if part.isdigit() else (1, part)
                                                  for part in pre.split(".")))
    return int(major), int(minor), int(patch), tail

"""Built-in feature modules: which of them a workspace has switched on.

Three independent modules sit on top of an always-on core (shell, settings, AI
provider and budget):

* ``applyai``   - jobs, discovery, sources, companies, profile, resumes, questions,
  preparation, recovery, applications, follow-ups, mail and career reports;
* ``workspace`` - notes, documents, canvas, schema, backlinks, flashcards, study,
  calendar, tasks and opportunities;
* ``terminal``  - kitty!'s own bundled herdr session (kitty.core.terminal), plus the
  user's installed Stencil Tern when present (kitty.core.tern). Off means no herdr
  or Tern process, socket or terminal route exists.

The selection is the ``modules`` list of the workspace's build file,
``config/build.json`` (kitty.core.builds; docs/platform-design.md, Q8)::

    {"schema": 1, "modules": ["applyai", "workspace", "terminal"]}

A build without the key keeps every module (existing installs are unchanged);
``kitty init`` writes ``"modules": []``. Before builds the selection lived in
``config/modules.yaml``; a workspace that still has only that file is read from it
until the first open writes the build file (kitty.core.builds.store.ensure). A
process reads the selection once at start-up and treats it as frozen: edits are
persisted but only take effect in the next process. Nothing is unloaded at run time.

Every function here takes the config directory, never a ``Settings``, so the
registry stays importable by the CLI, TUI, MCP server and web server alike.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator

MODULE_IDS: tuple[str, ...] = ("applyai", "workspace", "terminal")
#: Where the selection lives (inside the build) and where it lived before builds.
BUILD_FILE = "build.json"
LEGACY_MODULES_FILE = "modules.yaml"

_INFO: dict[str, tuple[str, str]] = {
    "applyai": (
        "ApplyAI",
        "Job discovery, sources, companies, resumes, preparation, applications, "
        "follow-ups, mail and career reports.",
    ),
    "workspace": (
        "Workspace",
        "Notes, documents, canvas, schema maps, backlinks, flashcards, study, "
        "calendar, tasks and opportunities.",
    ),
    "terminal": (
        "Terminal",
        "kitty!'s own herdr terminal session: workspaces, tabs, splits and agents, "
        "streamed into the window; plus your installed Stencil Tern, if any.",
    ),
}


class ModuleDisabledError(RuntimeError):
    """A feature of a module that this process does not have enabled was invoked."""

    def __init__(self, module: str):
        self.module = module
        super().__init__(
            f"The '{module}' module is disabled. Enable it in Settings or list it under "
            f"\"modules\" in config/{BUILD_FILE}, then restart kitty! (changes apply on the next start)."
        )


def available_modules() -> list[dict[str, str]]:
    """Every module as ``{id, label, description}``, in canonical order."""
    return [{"id": key, "label": _INFO[key][0], "description": _INFO[key][1]} for key in MODULE_IDS]


def normalize_modules(selection: Iterable[Any]) -> tuple[str, ...]:
    """Validate a selection and return it in canonical order.

    Rejects anything that is not exactly a known module id, and duplicates.
    """
    chosen: list[str] = []
    for item in selection:
        if not isinstance(item, str) or item not in MODULE_IDS:
            raise ValueError(f"unknown module {item!r}; use {list(MODULE_IDS)}")
        if item in chosen:
            raise ValueError(f"duplicate module {item!r}")
        chosen.append(item)
    return tuple(key for key in MODULE_IDS if key in chosen)


class ModulesFile(BaseModel):
    """A pre-build config/modules.yaml. Unknown keys, unknown ids and duplicates are errors."""

    model_config = ConfigDict(extra="forbid")

    enabled: list[StrictStr]

    @field_validator("enabled")
    @classmethod
    def _known_unique(cls, enabled: list[str]) -> list[str]:
        normalize_modules(enabled)
        return enabled


def _duplicate_key_problems(path: Path, node: Any) -> list[Any]:
    from kitty.config import ConfigProblem

    problems: list[ConfigProblem] = []
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key, _value in node.value:
            if key.value in seen:
                problems.append(ConfigProblem(
                    file=str(path), line=key.start_mark.line + 1, location=str(key.value),
                    message="duplicate key", hint="each key may appear once",
                ))
            seen.add(key.value)
    return problems


def parse_legacy_modules(path: Path) -> tuple[str, ...]:
    """The selection in a pre-build config/modules.yaml (kept only to migrate it)."""
    from kitty.config import ConfigError, ConfigProblem, _hint_for, _yaml_line, load_yaml

    data, node = load_yaml(path)
    duplicates = _duplicate_key_problems(path, node)
    if duplicates:
        raise ConfigError(duplicates)
    try:
        parsed = ModulesFile.model_validate(data)
    except ValidationError as exc:
        raise ConfigError([
            ConfigProblem(
                file=str(path), line=_yaml_line(node, error["loc"]),
                location=".".join(str(part) for part in error["loc"]),
                message=error["msg"].removeprefix("Value error, "),
                hint=_hint_for(dict(error)) or f"use a list of {list(MODULE_IDS)}",
            )
            for error in exc.errors()
        ]) from exc
    return normalize_modules(parsed.enabled)


def load_modules(config_dir: Path) -> tuple[str, ...]:
    """The selection persisted in the build file (canonical order).

    No build file and no legacy modules.yaml means a pre-module workspace: every
    module. A build with an error raises ``ConfigError`` pointing at the offending
    line. Process start-up calls this once; later edits are only a pending
    selection until restart.
    """
    from kitty.config import ConfigError, ConfigProblem
    from kitty.core.builds import document, store

    root = Path(config_dir).parent
    path = Path(config_dir) / BUILD_FILE
    try:
        text = store.read_text(root)
    except store.BuildStoreError as exc:
        raise ConfigError([ConfigProblem(file=str(path), line=None, location="", message=str(exc),
                                         hint="replace it with a regular file")]) from exc
    if text is None:
        legacy = Path(config_dir) / LEGACY_MODULES_FILE
        if legacy.exists() or legacy.is_symlink():
            return parse_legacy_modules(legacy)
        return MODULE_IDS
    checked = document.check(text)
    if not checked.ok:
        raise ConfigError([
            ConfigProblem(file=str(path), line=problem.line, location=problem.path, message=problem.message,
                          hint="fix it in Settings > Build or any editor; kitty! checks every save")
            for problem in checked.errors
        ])
    return tuple(checked.build["modules"])  # type: ignore[index]


def require_module(config_dir: Path, module: str) -> None:
    """Raise ``ModuleDisabledError`` unless `module` is in the persisted selection."""
    if module not in MODULE_IDS:
        raise ValueError(f"unknown module {module!r}")
    if module not in load_modules(config_dir):
        raise ModuleDisabledError(module)


def save_modules(config_dir: Path, enabled: Sequence[str]) -> tuple[str, ...]:
    """Persist a pending selection in the build file; return it in canonical order.

    Only validated ids reach the file, which is written atomically and owner-only
    (kitty.core.builds.store). A build file with an error, or a symbolic link, is
    refused rather than overwritten.
    """
    from kitty.core.builds import store

    if isinstance(enabled, (str, bytes)):
        raise ValueError("modules must be a list of module ids")
    selection = normalize_modules(enabled)

    def choose(build: dict[str, Any]) -> None:
        build["modules"] = list(selection)

    try:
        store.edit(Path(config_dir).parent, choose)
    except store.BuildStoreError as exc:
        raise ValueError(str(exc)) from None
    return selection


def modules_state(active: Sequence[str], config_dir: Path) -> dict[str, Any]:
    """The ``modules`` object shared by bootstrap and ``/api/modules``.

    ``enabled`` is this process's frozen snapshot; ``configured`` is what the build
    file selects now (applied on restart). An unreadable file never hides the core: the
    pending selection falls back to the active one and ``error`` says why.
    """
    from kitty.config import ConfigError

    enabled = list(active)
    state: dict[str, Any] = {"enabled": enabled, "available": available_modules()}
    try:
        configured = list(load_modules(config_dir))
    except ConfigError as exc:
        configured = list(enabled)
        state["error"] = str(exc)
    state["configured"] = configured
    state["restart_required"] = configured != enabled
    return state

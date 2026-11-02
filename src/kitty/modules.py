"""Built-in feature modules: which of them a workspace has switched on.

Three independent modules sit on top of an always-on core (shell, settings, AI
provider and budget):

* ``applyai``   - jobs, discovery, sources, companies, profile, resumes, questions,
  preparation, recovery, applications, follow-ups, mail and career reports;
* ``workspace`` - notes, documents, canvas, schema, backlinks, flashcards, study,
  calendar, tasks and opportunities;
* ``terminal``  - rika!'s own bundled herdr session (kitty.core.terminal), plus the
  user's installed Stencil Tern when present (kitty.core.tern). Off means no herdr
  or Tern process, socket or terminal route exists.

The selection lives in ``config/modules.yaml``::

    enabled: [applyai, workspace, terminal]

A workspace without the file keeps every module (existing installs are
unchanged); ``kitty init`` writes ``enabled: []``. A process reads the selection
once at start-up and treats it as frozen: edits are persisted but only take
effect in the next process. Nothing is unloaded at run time.

Every function here takes the config directory, never a ``Settings``, so the
registry stays importable by the CLI, TUI, MCP server and web server alike.
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator

MODULE_IDS: tuple[str, ...] = ("applyai", "workspace", "terminal")
MODULES_FILE = "modules.yaml"

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
        "rika!'s own herdr terminal session: workspaces, tabs, splits and agents, "
        "streamed into the window; plus your installed Stencil Tern, if any.",
    ),
}


class ModuleDisabledError(RuntimeError):
    """A feature of a module that this process does not have enabled was invoked."""

    def __init__(self, module: str):
        self.module = module
        super().__init__(
            f"The '{module}' module is disabled. Enable it in Settings or list it in "
            f"config/{MODULES_FILE}, then restart rika! (changes apply on the next start)."
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
    """config/modules.yaml. Unknown keys, unknown ids and duplicates are errors."""

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


def _parse(path: Path) -> tuple[str, ...]:
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
    """The selection persisted in ``config/modules.yaml`` (canonical order).

    An absent file means a pre-module workspace: both modules. A malformed file
    raises ``ConfigError`` pointing at the offending line. Process start-up calls
    this once; later edits are only a pending selection until restart.
    """
    path = Path(config_dir) / MODULES_FILE
    if not path.exists() and not path.is_symlink():
        return MODULE_IDS
    return _parse(path)


def require_module(config_dir: Path, module: str) -> None:
    """Raise ``ModuleDisabledError`` unless `module` is in the persisted selection."""
    if module not in MODULE_IDS:
        raise ValueError(f"unknown module {module!r}")
    if module not in load_modules(config_dir):
        raise ModuleDisabledError(module)


def save_modules(config_dir: Path, enabled: Sequence[str]) -> tuple[str, ...]:
    """Atomically persist a pending selection; return it in canonical order.

    Only validated ids reach the file. An existing file's permission bits are kept
    (never widened); a new file is owner-only. Symbolic links are refused rather
    than followed or replaced.
    """
    if isinstance(enabled, (str, bytes)):
        raise ValueError("modules must be a list of module ids")
    selection = normalize_modules(enabled)
    folder = Path(config_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MODULES_FILE
    mode = 0o600
    if target.is_symlink():
        raise ValueError(f"config/{MODULES_FILE} is a symbolic link; replace it with a regular file")
    if target.exists():
        if not target.is_file():
            raise ValueError(f"config/{MODULES_FILE} is not a regular file")
        mode = stat.S_IMODE(target.stat().st_mode)
    body = "enabled: [" + ", ".join(selection) + "]\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{MODULES_FILE}.", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return selection


def modules_state(active: Sequence[str], config_dir: Path) -> dict[str, Any]:
    """The ``modules`` object shared by bootstrap and ``/api/modules``.

    ``enabled`` is this process's frozen snapshot; ``configured`` is what the file
    selects now (applied on restart). An unreadable file never hides the core: the
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

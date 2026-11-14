"""Sharing builds: export, preview the difference, install, roll back.

What a build can and cannot do is decided by what the format holds (document.py and the
published schema): modules, settings, theme tokens, custom CSS and interface
preferences. It has no place for notes, keys, credentials, paired clients or devices,
or applicant data, so installing one cannot change them, and its CSS cannot load
anything (css.py).

A few settings change privacy or cost: whether notes may go to AI, which AI model, the
daily AI budget, and scheduled opportunity monitoring. An installed build changes those
only when the person installing it agrees (``accept_sensitive``); otherwise the current
values are kept and the preview lists what was held back. Module changes take effect
after a restart, and the preview says so. rika-server answers the build routes and fetches
build links (server/src/builds/); this module is the reference tests/test_web_build.py
compares its previews, installs and exports with.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from kitty import __version__
from kitty.core.builds import document

RESTART_PATHS = ("modules",)


class ShareError(Exception):
    def __init__(self, message: str, status: int = 400, problems: list[document.Problem] | None = None):
        super().__init__(message)
        self.status = status
        self.problems = problems or []


def _raises_cost_or_privacy(path: str, before: Any, after: Any) -> bool:
    if path == "settings.notes.allow_ai":
        return after is True and before is not True
    if path == "settings.opportunities.monitoring":
        return after is True and before is not True
    if path == "settings.ai.daily_budget_usd":
        return float(after) > float(before)
    if path in {"settings.ai.model", "settings.ai.rika_fast_model", "settings.ai.rika_standard_model", "settings.ai.rika_deep_model"}:
        return after != before and after != ""
    return False


SENSITIVE_PATHS = ("settings.notes.allow_ai", "settings.ai.model", "settings.ai.rika_fast_model",
                   "settings.ai.rika_standard_model", "settings.ai.rika_deep_model", "settings.ai.daily_budget_usd",
                   "settings.opportunities.monitoring")


def flatten(build: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Leaf values by dotted path; lists are leaves, each theme token is one."""
    out: dict[str, Any] = {}
    for key, value in build.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """What changes from one complete build to another, in schema order."""
    old, new = flatten(before), flatten(after)
    paths = list(old) + [path for path in new if path not in old]
    changes = []
    for path in paths:
        if path == "schema":
            continue
        was, now = old.get(path), new.get(path)
        if was == now:
            continue
        changes.append({
            "path": path, "before": was, "after": now,
            "restart": path in RESTART_PATHS,
            "sensitive": _raises_cost_or_privacy(path, was, now) if path in SENSITIVE_PATHS else False,
        })
    return changes


def _set(build: dict[str, Any], path: str, value: Any) -> None:
    node = build
    *parents, last = path.split(".")
    for key in parents:
        node = node[key]
    node[last] = value


def resolve(current: dict[str, Any], incoming_text: str, *, accept_sensitive: bool) -> dict[str, Any]:
    """The build an install would make active, and how it differs from `current`.

    Returns ``{"build", "changes", "held", "restart", "warnings", "about"}``: `changes`
    is what will change, `held` the cost/privacy changes kept back (unless accepted).
    """
    checked = document.check(incoming_text)
    if not checked.ok:
        raise ShareError(f"That build cannot be installed: {checked.errors[0]}", 400, checked.problems)
    incoming = checked.build
    assert incoming is not None
    all_changes = diff(current, incoming)
    held = [change for change in all_changes if change["sensitive"] and not accept_sensitive]
    final = copy.deepcopy(incoming)
    for change in held:
        _set(final, change["path"], change["before"])
    changes = diff(current, final)
    return {
        "build": final, "changes": changes, "held": held,
        "restart": any(change["restart"] for change in changes),
        "warnings": [problem.as_dict() for problem in checked.warnings],
        "about": {key: incoming[key] for key in ("name", "author", "description", "kitty_version")},
    }


def export_text(active: dict[str, Any]) -> str:
    """The shareable file for an active build: complete, stamped with this rika! version."""
    build = copy.deepcopy(active)
    build["kitty_version"] = __version__
    ordered = {"schema": build.pop("schema"), "kitty_version": build.pop("kitty_version"), **build}
    return document.dump(ordered)


def about(text: str) -> dict[str, Any]:
    """Name, author and description of a build text, best effort (for listings)."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return {key: data.get(key, "") for key in ("name", "author", "description")} if isinstance(data, dict) else {}

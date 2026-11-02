"""Sharing builds: export, fetch from a link, preview the difference, install, roll back.

What a build can and cannot do is decided by what the format holds (document.py and the
published schema): modules, settings, theme tokens, custom CSS and interface
preferences. It has no place for notes, keys, credentials, paired clients or devices,
or applicant data, so installing one cannot change them, and its CSS cannot load
anything (css.py).

A few settings change privacy or cost: whether notes may go to AI, which AI model, the
daily AI budget, and scheduled opportunity monitoring. An installed build changes those
only when the person installing it agrees (``accept_sensitive``); otherwise the current
values are kept and the preview lists what was held back. Module changes take effect
after a restart, and the preview says so.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from kitty import __version__
from kitty.core.builds import document

MAX_REDIRECTS = 3
FETCH_TIMEOUT = 10
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
    if path == "settings.ai.model":
        return after != before and after != ""
    return False


SENSITIVE_PATHS = ("settings.notes.allow_ai", "settings.ai.model", "settings.ai.daily_budget_usd",
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
    """The shareable file for an active build: complete, stamped with this kitty! version."""
    build = copy.deepcopy(active)
    build["kitty_version"] = __version__
    ordered = {"schema": build.pop("schema"), "kitty_version": build.pop("kitty_version"), **build}
    return document.dump(ordered)


# Fetching a build from a link ----------------------------------------------------------

def _public_host(host: str, resolver: Callable[..., Any]) -> None:
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ShareError(f"Could not find {host}") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global:
            raise ShareError("Build links must point to a public https address, not this machine or a local network")


def _check_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ShareError("That is not a valid link") from None
    if parts.scheme != "https":
        raise ShareError("Build links must use https (for example a GitHub gist or raw file link)")
    if not parts.hostname or parts.username or parts.password:
        raise ShareError("Build links need a host and cannot carry a user name or password")
    return parts.hostname


def fetch(url: str, *, transport: httpx.BaseTransport | None = None,
          resolver: Callable[..., Any] = socket.getaddrinfo) -> str:
    """The text at an https link, following at most three https redirects, capped in size."""
    if not isinstance(url, str) or len(url) > 2048:
        raise ShareError("That is not a valid link")
    with httpx.Client(transport=transport, timeout=FETCH_TIMEOUT, follow_redirects=False,
                      headers={"Accept": "application/json, text/plain;q=0.9"}) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            host = _check_url(url)
            _public_host(host, resolver)
            try:
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        url = urljoin(url, location)
                        if urlsplit(url).scheme != "https":
                            raise ShareError("That link redirects to a non-https address; refusing it")
                        continue
                    if response.status_code != 200:
                        raise ShareError(f"The link answered {response.status_code}", 502)
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > document.MAX_BYTES:
                        raise ShareError(f"Builds are limited to {document.MAX_BYTES // 1024} KB")
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > document.MAX_BYTES:
                            raise ShareError(f"Builds are limited to {document.MAX_BYTES // 1024} KB")
            except httpx.HTTPError as exc:
                raise ShareError(f"Could not fetch the link ({type(exc).__name__})", 502) from None
            try:
                return body.decode("utf-8")
            except UnicodeDecodeError:
                raise ShareError("The link did not return UTF-8 text") from None
    raise ShareError(f"That link redirects more than {MAX_REDIRECTS} times")


def about(text: str) -> dict[str, Any]:
    """Name, author and description of a build text, best effort (for listings)."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return {key: data.get(key, "") for key in ("name", "author", "description")} if isinstance(data, dict) else {}

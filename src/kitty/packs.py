"""The packs a worker can use: built into this program, or installed by rika-server.

A pack (docs/rust-core-design.md, milestone 3) is one module's large payload. The Brain's is
web code the front serves; the others are what this Python worker loads:

* ``discovery``, ``math``, ``voice``: Python distributions (``PYTHON``), an extra
  ``python/`` folder on ``sys.path``;
* ``learnharness``: the LearnHarness app and its Node.js runtime (``learnharness/``);
* ``herdr``: the pinned herdr binary (``bin/herdr``), handed on as ``$KITTY_HERDR``.

A source checkout and a full build carry all of them ("built in"). The base desktop install
carries none: rika-server reads its signed installs (server/src/packs.rs) and passes the
folders of those made for this version as ``$KITTY_PACKS`` (``{"<id>": "<absolute dir>"}``).
``state()`` settles once per process, before the workspace opens: it probes what is built
in first, then adds the installed folders. The readiness line reports both, so the front
lists a built-in pack as such, and the ``missing`` packs of the active modules, so their
routes answer 409 (``require``) and their views say how to download them.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from kitty.modules import MODULE_PACKS, REGISTRY

#: Python packs: the distributions a build ships in the pack (scripts/build-packs.py adds
#: whatever they need that the base does not have), and the import that shows it is here.
PYTHON: dict[str, tuple[tuple[str, ...], str]] = {
    "discovery": (("python-jobspy",), "jobspy"),
    "math": (("sympy", "lark", "antlr4-python3-runtime"), "sympy"),
    "voice": (("faster-whisper",), "faster_whisper"),
}
#: Packs the page loads (web code, served by the front); never missing from the worker's view.
WEB = frozenset({"brain", "themes", "animations"})
PROGRAMS = frozenset({"learnharness", "herdr"})
LABELS: dict[str, str] = {pack["id"]: pack["label"] for pack in REGISTRY["packs"]}
ENVIRONMENT = "KITTY_PACKS"
HERDR = "bin/herdr"


class PackMissing(RuntimeError):
    """A feature whose pack this process does not have was invoked; the message is safe to show."""

    def __init__(self, pack: str) -> None:
        self.pack = pack
        super().__init__(f"{LABELS.get(pack, pack)} isn't installed: download it in Settings > Modules, then restart rika!")


@dataclass(frozen=True)
class State:
    built_in: frozenset[str]
    #: Installed pack id -> its folder.
    installed: dict[str, Path] = field(default_factory=dict)

    def loaded(self, pack: str) -> bool:
        return pack in self.built_in or pack in self.installed


def _bundle() -> Path:
    """Where this program's own payloads are: the frozen bundle, or the checkout's desktop build."""
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parents[2] / "desktop/.build"


def _executable(path: str | Path | None) -> bool:
    return bool(path) and Path(path).is_file() and os.access(path, os.X_OK)


def _built_in() -> frozenset[str]:
    found = {pack for pack, (_dists, probe) in PYTHON.items() if importlib.util.find_spec(probe) is not None}
    if (_bundle() / "learnharness/server.js").is_file():
        found.add("learnharness")
    if _executable(os.environ.get("KITTY_HERDR", "").strip()):
        found.add("herdr")
    return frozenset(found)


def _installed() -> dict[str, Path]:
    try:
        named = json.loads(os.environ.get(ENVIRONMENT) or "{}")
    except ValueError:
        return {}
    if not isinstance(named, dict):
        return {}
    known = set(PYTHON) | PROGRAMS
    return {pack: Path(folder) for pack, folder in named.items()
            if pack in known and isinstance(folder, str) and Path(folder).is_absolute() and Path(folder).is_dir()}


@functools.cache
def state() -> State:
    """What this process has; the first call also puts the installed packs to use."""
    built_in = _built_in()
    installed = {pack: folder for pack, folder in _installed().items() if pack not in built_in}
    for pack, folder in installed.items():
        if pack in PYTHON and (folder / "python").is_dir():
            # After the bundle's own paths: the base's copy of any shared library wins.
            sys.path.append(str(folder / "python"))
        elif pack == "herdr" and _executable(folder / HERDR):
            os.environ["KITTY_HERDR"] = str(folder / HERDR)
    importlib.invalidate_caches()
    return State(built_in, {pack: folder for pack, folder in installed.items() if _payload_present(pack, folder)})


def _payload_present(pack: str, folder: Path) -> bool:
    if pack in PYTHON:
        return importlib.util.find_spec(PYTHON[pack][1]) is not None
    if pack == "learnharness":
        return (folder / "learnharness/server.js").is_file()
    return _executable(folder / HERDR)


def loaded(pack: str) -> bool:
    # herdr is whatever $KITTY_HERDR names now (Settings reads the same variable); the
    # installed pack's binary was put there by `state`.
    if pack == "herdr":
        state()
        return _executable(os.environ.get("KITTY_HERDR", "").strip())
    return state().loaded(pack)


def require(pack: str) -> None:
    """Raise ``PackMissing`` unless this process has `pack`."""
    if not loaded(pack):
        raise PackMissing(pack)


def missing(active) -> list[str]:
    """The packs the active modules need that this worker lacks (web packs are the page's to find)."""
    needed = [pack for module in active for pack in MODULE_PACKS.get(module, ()) if pack not in WEB]
    return [pack for pack in dict.fromkeys(needed) if not loaded(pack)]


def learnharness_dir() -> Path:
    """The LearnHarness payload: this program's own, else the installed pack's."""
    installed = state().installed.get("learnharness")
    return installed / "learnharness" if installed else _bundle() / "learnharness"


def built_in() -> list[str]:
    """The packs this program carries itself (the readiness line's ``packs.built_in``)."""
    return sorted(state().built_in)

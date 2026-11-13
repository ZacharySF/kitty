"""The base install without its module packs, and each worker pack built, verified, installed
and used (scripts/build-packs.py, server/src/packs.rs, kitty.packs; docs/rust-core-design.md,
milestone 3).

A "base" worker is the checkout's Python with every library a pack carries hidden (the frozen
backend's `backend_excludes`) and no LearnHarness or herdr: what the desktop installer ships.
The Python packs are built from the real locked libraries; LearnHarness and herdr from
synthetic payloads. Everything is signed with a throwaway key and served from loopback.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tarfile
import time
from pathlib import Path

import pytest

from kitty import packs as runtime
from kitty.core.discovery.jobspy_source import JobSpyAdapter
from kitty.modules import DEFAULT_MODULES, MODULE_PACKS
from pack_support import VERSION, Key, Release, build_packs
from terminal_support import front_site, make_root

REPO = Path(__file__).resolve().parents[1]
WORKER_PACKS = ["discovery", "math", "learnharness", "herdr", "voice"]


def default_root(tmp_path: Path):
    """A workspace with DEFAULT_MODULES on and ApplyAI's careers files from the public examples."""
    from kitty.core.bootstrap import copy_examples

    settings = make_root(tmp_path, DEFAULT_MODULES)
    copy_examples(settings, DEFAULT_MODULES)
    return settings


@pytest.fixture(scope="module")
def base_python(tmp_path_factory) -> list[str]:
    """The worker command of a base install: this Python with everything the packs carry hidden
    (a site-packages linking to everything else), no LearnHarness and no herdr."""
    import kitty

    hidden = set(build_packs.backend_excludes())
    site = next(Path(path) for path in sys.path if path.endswith("site-packages") and (Path(path) / "pydantic").is_dir())
    folder = tmp_path_factory.mktemp("base-site")
    for entry in site.iterdir():
        name = entry.name
        top = name.split(".", 1)[0].split("-", 1)[0]
        if name.endswith(".pth") or top in hidden or name.removesuffix(".libs") in hidden:
            continue
        (folder / name).symlink_to(entry)
    source = Path(kitty.__file__).resolve().parents[1]
    empty = tmp_path_factory.mktemp("no-bundle")
    # `python -S`: no site-packages but the filtered one; the checkout's LearnHarness build and
    # any $KITTY_HERDR are not this worker's (the desktop shell clears $KITTY_HERDR too).
    code = (f"import sys; sys.path[:0] = [{str(folder)!r}, {str(source)!r}]; import os; os.environ.pop('KITTY_HERDR', None); "
            f"import pathlib, kitty.packs; kitty.packs._bundle = lambda: pathlib.Path({str(empty)!r}); "
            "from kitty.web_server import main; main()")
    return [sys.executable, "-S", "-c", code]


@pytest.fixture
def release():
    serving = Release()
    yield serving
    serving.close()


def listing(site) -> dict[str, dict]:
    status, body = site.request("/api/packs")
    assert status == 200, body
    return {pack["id"]: pack for pack in body["packs"]}


def settle(site, pack: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while (state := listing(site)[pack])["state"] == "downloading":
        assert time.monotonic() < deadline, state
        time.sleep(0.1)
    return state


# ------------------------------------------------------------------ the base

def test_python_packs_carry_their_libraries_and_the_base_none_of_them():
    distributions = build_packs.python_distributions()
    base = build_packs.base_distributions()
    assert set(distributions) == set(runtime.PYTHON)
    for pack, (roots, _probe) in runtime.PYTHON.items():
        assert {build_packs._canonical(root) for root in roots} <= set(distributions[pack])
        assert base.isdisjoint(distributions[pack]), pack
    assert {"numpy", "pandas", "tls-client"} <= set(distributions["discovery"])
    assert {"ctranslate2", "av", "onnxruntime", "tokenizers", "numpy"} <= set(distributions["voice"])
    excluded = set(build_packs.backend_excludes())
    assert {"jobspy", "pandas", "numpy", "sympy", "mpmath", "faster_whisper", "ctranslate2", "av", "onnxruntime"} <= excluded
    # Small helpers stay wherever the base imports them; the base's own libraries always stay.
    assert excluded.isdisjoint({"packaging", "tqdm", "httpx", "pydantic", "kitty", "google", "PIL", "pymupdf"})


def test_the_base_worker_starts_without_any_pack_and_every_pack_route_says_so(tmp_path, base_python):
    """DEFAULT_MODULES on, no pack's payload: starts, lists the downloads, 409s their routes."""
    settings = default_root(tmp_path)
    with front_site(settings, worker=base_python) as site:
        status, modules = site.request("/api/modules")
        assert status == 200 and modules["enabled"] == list(DEFAULT_MODULES)
        assert modules["missing"] == WORKER_PACKS
        status, boot = site.request("/api/bootstrap")
        assert status == 200 and boot["modules"]["missing"] == WORKER_PACKS
        # No index is published here: every worker pack is a download, none built in.
        assert all(listing(site)[pack]["state"] == "unavailable" for pack in WORKER_PACKS)
        for path, pack in (("/api/math/topics", "Math engine"), ("/api/extensions/learnharness", "LearnHarness"),
                           ("/api/rika/voice", "Speech engine"), ("/api/terminal/agents", "herdr")):
            status, body = site.request(path)
            assert status == 409, (path, body)
            assert body["error"] == f"{pack} isn't installed: download it in Settings > Modules, then restart rika!"
        # Everything else runs: the base, ApplyAI without its aggregators, Workspace, Tern.
        for path in ("/api/dashboard", "/api/notes", "/api/calendar", "/api/terminal/tern"):
            status, body = site.request(path)
            assert status == 200, (path, body)


def test_jobspy_without_its_pack_is_an_error_never_an_empty_success(monkeypatch):
    monkeypatch.setattr(runtime, "loaded", lambda pack: pack != "discovery")
    ctx = type("Context", (), {})()
    track = type("Track", (), {"name": "t", "sources": ["jobspy"], "location_rules": None})()
    ctx.config = type("Config", (), {"sources": type("S", (), {"sources": {}})(),
                                     "tracks": type("T", (), {"tracks": [track]})()})()
    monkeypatch.setattr("kitty.core.discovery.jobspy_source.plan_searches", lambda tracks, options: ["one search"])
    result = JobSpyAdapter(ctx).fetch(None)
    assert result.status == "error" and result.jobs == []
    assert result.error == "Job aggregators isn't installed: download it in Settings > Modules, then restart rika!"


# ------------------------------------------------------------------ build, verify, install, use

def _program_payloads(folder: Path) -> Path:
    app = folder / "learnharness"
    (app / ".next").mkdir(parents=True)
    (app / "server.js").write_text("// synthetic LearnHarness\n")
    (app / ".next/BUILD_ID").write_text("synthetic\n")
    (app / "node").write_text("#!/bin/sh\nexit 0\n")
    (app / "node").chmod(0o755)
    (folder / "learnharness-legal").mkdir()
    (folder / "learnharness-legal/LICENSE").write_text("synthetic licence\n")
    (folder / "herdr").mkdir()
    (folder / "herdr/herdr").write_text("#!/bin/sh\nexit 0\n")
    (folder / "herdr/herdr").chmod(0o755)
    return folder


def test_program_packs_keep_their_layout_and_executables_and_build_reproducibly(tmp_path):
    payloads = _program_payloads(tmp_path / "build")
    archives = {}
    for run in ("one", "two"):
        members = {"learnharness": build_packs.learnharness_members(payloads), "herdr": build_packs.herdr_members(payloads)}
        archives[run] = {pack: build_packs.write_archive(tmp_path / f"{run}-{pack}.tar.gz", pack, VERSION, items)
                         for pack, items in members.items()}
    assert [entry["sha256"] for entry in archives["one"].values()] == [entry["sha256"] for entry in archives["two"].values()]
    with tarfile.open(tmp_path / "one-learnharness.tar.gz") as tar:
        modes = {member.name: member.mode for member in tar.getmembers()}
    assert modes == {"pack.json": 0o644, "learnharness/.next/BUILD_ID": 0o644, "learnharness/node": 0o755,
                     "learnharness/server.js": 0o644, "licenses/learnharness/LICENSE": 0o644}
    with tarfile.open(tmp_path / "one-herdr.tar.gz") as tar:
        assert {member.name: member.mode for member in tar.getmembers()} == {
            "pack.json": 0o644, "bin/herdr": 0o755, "licenses/herdr/LICENSE": 0o644}
        assert tar.extractfile("licenses/herdr/LICENSE").read() == (REPO / "desktop/third_party/herdr/LICENSE").read_bytes()
    with pytest.raises(ValueError, match="package_learnharness"):
        build_packs.learnharness_members(tmp_path / "nothing")
    with pytest.raises(ValueError, match="fetch_herdr"):
        build_packs.herdr_members(tmp_path / "nothing")


def _publish(tmp_path: Path, release: Release, key: Key, pack: str) -> dict:
    """Build `pack` as the release does (the Python ones from the real libraries) and publish it."""
    out = tmp_path / "packs-out"
    out.mkdir(exist_ok=True)
    if pack in runtime.PYTHON:
        members = build_packs.python_members(build_packs.python_distributions()[pack])
    else:
        payloads = _program_payloads(tmp_path / "payloads")
        members = build_packs.learnharness_members(payloads) if pack == "learnharness" else build_packs.herdr_members(payloads)
    entry = build_packs.write_archive(out / build_packs.archive_name(pack, VERSION), pack, VERSION, members, level=1)
    index = {"format": 1, "version": VERSION, "packs": [entry]}
    release.publish(key, index, {entry["file"]: (out / entry["file"]).read_bytes()})
    return entry


#: A route each pack's module serves once the pack is loaded (herdr's needs a running session,
#: so it is only checked to be let through).
USES = {"math": "/api/math/topics", "voice": "/api/rika/voice", "learnharness": "/api/extensions/learnharness",
        "herdr": "/api/terminal/agents", "discovery": None}


@pytest.mark.parametrize("pack", WORKER_PACKS)
def test_a_worker_pack_installs_verified_and_the_next_start_uses_it(tmp_path, release, base_python, pack):
    key = Key()
    entry = _publish(tmp_path, release, key, pack)
    settings = default_root(tmp_path)
    env = {"KITTY_PACKS_URL": release.url, "KITTY_PACKS_KEY": key.public}
    folder = settings.root / "xdg-data/kitty/packs"
    with front_site(settings, env, worker=base_python) as site:
        assert listing(site)[pack]["state"] == "available" and listing(site)[pack]["size"] == entry["size"]
        status, body = site.request("/api/packs/install", {"id": pack})
        assert status == 200, body
        installed = settle(site, pack)
        assert installed["state"] == "installed" and installed["error"] is None, installed
        # Loaded at the next start, not into this worker.
        assert pack in site.request("/api/modules")[1]["missing"]
    record = json.loads((folder / "installed.json").read_text())[pack]
    assert record["sha256"] == entry["sha256"] and record["installed_size"] == entry["installed_size"]
    tree = folder / record["dir"]
    assert json.loads((tree / "pack.json").read_text()) == {"id": pack, "version": VERSION}
    if pack == "herdr":
        assert stat.S_IMODE((tree / "bin/herdr").stat().st_mode) == 0o755
    if pack == "learnharness":
        assert stat.S_IMODE((tree / "learnharness/node").stat().st_mode) == 0o755
        assert (tree / "learnharness/.next/BUILD_ID").is_file()
    with front_site(settings, env, worker=base_python) as site:
        status, modules = site.request("/api/modules")
        assert modules["missing"] == [item for item in WORKER_PACKS if item != pack]
        assert listing(site)[pack]["state"] == "installed"
        if USES[pack]:
            status, body = site.request(USES[pack])
            assert status == 200 if pack != "herdr" else status != 409, (status, body)
        # Turned off and saved, the download can go; it is offered again from the index.
        selection = [module for module in DEFAULT_MODULES if pack not in MODULE_PACKS[module]]
        assert site.request("/api/modules", {"enabled": selection})[0] == 200
        status, body = site.request("/api/packs/uninstall", {"id": pack})
        assert status == 200 and next(item for item in body["packs"] if item["id"] == pack)["state"] == "available"
    assert not tree.exists()


def test_a_pack_folder_from_outside_the_front_is_ignored(tmp_path, monkeypatch):
    """$KITTY_PACKS names only absolute folders of known worker packs; anything else is not loaded."""
    stray = tmp_path / "stray"
    (stray / "python").mkdir(parents=True)
    monkeypatch.setenv("KITTY_PACKS", json.dumps({"brain": str(stray), "other": str(stray), "math": "relative/dir"}))
    assert runtime._installed() == {}
    monkeypatch.setenv("KITTY_PACKS", "not json")
    assert runtime._installed() == {}
    monkeypatch.setenv("KITTY_PACKS", json.dumps({"math": str(stray)}))
    assert runtime._installed() == {"math": stray}


def test_an_installed_python_pack_is_appended_after_the_bundle(tmp_path, monkeypatch):
    folder = tmp_path / "math-pack"
    (folder / "python/kitty_synthetic_probe").mkdir(parents=True)
    (folder / "python/kitty_synthetic_probe/__init__.py").write_text("VALUE = 1\n")
    monkeypatch.setitem(runtime.PYTHON, "math", (("sympy",), "kitty_synthetic_probe"))
    monkeypatch.setenv("KITTY_PACKS", json.dumps({"math": str(folder)}))
    monkeypatch.setattr(sys, "path", list(sys.path))
    runtime.state.cache_clear()
    try:
        current = runtime.state()
        assert current.installed == {"math": folder} and "math" not in current.built_in
        assert sys.path[-1] == str(folder / "python") and runtime.loaded("math")
    finally:
        runtime.state.cache_clear()
        sys.modules.pop("kitty_synthetic_probe", None)


def test_a_herdr_pack_becomes_the_pinned_herdr(tmp_path, monkeypatch):
    folder = tmp_path / "herdr-pack"
    (folder / "bin").mkdir(parents=True)
    (folder / "bin/herdr").write_text("#!/bin/sh\n")
    (folder / "bin/herdr").chmod(0o755)
    # Recorded so the variable state() sets is undone afterwards.
    monkeypatch.setenv("KITTY_HERDR", "")
    monkeypatch.setenv("KITTY_PACKS", json.dumps({"herdr": str(folder)}))
    runtime.state.cache_clear()
    try:
        assert runtime.loaded("herdr") and os.environ["KITTY_HERDR"] == str(folder / "bin/herdr")
        assert "herdr" not in runtime.built_in()
    finally:
        runtime.state.cache_clear()

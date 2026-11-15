"""Build the module packs of a release, and their index.

Every pack is `rika-pack-<id>-<version>.tar.gz` in --out: a `pack.json` naming the pack and
version, then its payload (byte-for-byte reproducible: sorted names, no owners or times,
only the executable bit kept). server/src/packs.rs installs them; kitty.packs uses them.

* Web packs (the Brain): `web/dist/pack-files.json` (web/vite.config.ts) lists each pack's
  files under the website; they go under `web/` and leave the website the installers carry.
* Python packs (`kitty.packs.PYTHON`): the named distributions and everything they need that
  the base does not, copied from the locked environment this runs in into `python/`.
  desktop/package_backend.py leaves the same modules out of the frozen backend
  (`backend_excludes`).
* Program packs: LearnHarness (`learnharness/` from desktop/package_learnharness.py, its
  licences and source under `licenses/`) and herdr (`bin/herdr` from desktop/fetch_herdr.py
  with its licence).

The index `packs.json` lists each archive's size, SHA-256 and unpacked size. The release
workflow signs it with the updater key (`tauri signer sign`, `packs.json.sig`) and
scripts/publish-desktop-release.py uploads all of them with the installers.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata as metadata
import io
import json
import os
import re
import sys
import tarfile
import tomllib
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = "packs.json"
FILES = "pack-files.json"
BUILD = ROOT / "desktop/.build"
sys.path.insert(0, str(ROOT / "src"))
from kitty.packs import HERDR, PYTHON  # noqa: E402

#: A member of an archive: its name and either its bytes or the file to read, and whether it runs.
Member = tuple[str, "bytes | Path", bool]


def archive_name(pack: str, version: str) -> str:
    return f"rika-pack-{pack}-{version}.tar.gz"


def _info(name: str, size: int, executable: bool) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o755 if executable else 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _size(content: bytes | Path) -> int:
    return len(content) if isinstance(content, bytes) else content.stat().st_size


def write_archive(target: Path, pack: str, version: str, members: Iterable[Member], *, level: int = 9) -> dict:
    """Write one pack archive (streamed: a payload can be hundreds of MB); return its index entry."""
    manifest = json.dumps({"id": pack, "version": version}, indent=2).encode() + b"\n"
    ordered = [("pack.json", manifest, False), *sorted(members, key=lambda member: member[0])]
    names = [name for name, _content, _executable in ordered]
    if len(set(names)) != len(names):
        raise ValueError(f"pack {pack}: a file is listed twice")
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=level) as compressed:
            # GNU format: some payload paths (Next.js, node_modules) are longer than ustar allows.
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as tar:
                for name, content, executable in ordered:
                    info = _info(name, _size(content), executable)
                    if isinstance(content, bytes):
                        tar.addfile(info, io.BytesIO(content))
                    else:
                        with content.open("rb") as handle:
                            tar.addfile(info, handle)
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {"id": pack, "version": version, "file": target.name, "size": target.stat().st_size,
            "sha256": digest.hexdigest(), "installed_size": sum(_size(content) for _n, content, _x in ordered)}


def _tree(folder: Path, prefix: str) -> list[Member]:
    """Every regular file under `folder` as `prefix/<relative path>`; links are refused."""
    members: list[Member] = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"{path} is a link; a pack holds regular files only")
        if path.is_file():
            members.append((f"{prefix}/{path.relative_to(folder).as_posix()}", path, os.access(path, os.X_OK)))
    return members


# ------------------------------------------------------------------ Python packs

def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requires(name: str) -> list[str]:
    """The distributions `name` needs on this platform (extras left out)."""
    from packaging.requirements import Requirement

    found = []
    for line in metadata.distribution(name).requires or ():
        requirement = Requirement(line)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            found.append(_canonical(requirement.name))
    return found


def closure(roots: Iterable[str]) -> set[str]:
    """`roots` and every installed distribution they need, transitively."""
    seen: set[str] = set()
    todo = [_canonical(root) for root in roots]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # an optional requirement this environment does not install
        seen.add(name)
        todo.extend(_requires(name))
    return seen


def base_distributions(root: Path = ROOT) -> set[str]:
    """What the frozen backend keeps: the project's dependencies without any pack's, transitively."""
    from packaging.requirements import Requirement

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    roots = {_canonical(dist) for dists, _probe in PYTHON.values() for dist in dists}
    return closure(name for name in (_canonical(Requirement(line).name) for line in project["dependencies"])
                   if name not in roots)


def python_distributions(root: Path = ROOT) -> dict[str, list[str]]:
    """Each Python pack's distributions: its own and those it needs that the base does not have.

    A distribution two packs need (numpy) goes into both, so each pack stands alone."""
    base = base_distributions(root)
    return {pack: sorted(closure(dists) - base) for pack, (dists, _probe) in PYTHON.items()}


def _site_files(name: str) -> list[tuple[str, Path]]:
    """A distribution's installed files under site-packages (its RECORD), bytecode left out."""
    distribution = metadata.distribution(name)
    files = []
    for entry in distribution.files or ():
        relative = entry.as_posix()
        if relative.startswith("../") or "/__pycache__/" in f"/{relative}" or relative.endswith(".pyc"):
            continue  # console scripts outside site-packages; caches are rebuilt
        path = Path(distribution.locate_file(entry))
        if path.is_file():
            files.append((relative, path))
    return files


def top_level_modules(names: Iterable[str]) -> set[str]:
    """The importable top-level names the distributions install."""
    found = set()
    for name in names:
        for relative, _path in _site_files(name):
            first, _, rest = relative.partition("/")
            if first.endswith((".dist-info", ".data", ".libs")) or first == "__pycache__":
                continue
            if rest:
                found.add(first)
            elif first.endswith(".py"):
                found.add(first[:-3])
            elif first.endswith(".so"):
                found.add(first.split(".", 1)[0])
    return found


#: A pack-only distribution at least this large leaves the frozen backend even if something in
#: the base imports it optionally (PIL tries numpy); a smaller helper (packaging, tqdm) stays
#: wherever PyInstaller finds it imported.
LARGE = 1 << 20


def backend_excludes() -> list[str]:
    """The modules the frozen backend leaves out: every Python pack's own (so a pack is never
    "built in" by accident) and the large libraries only packs need. A top-level name the base
    shares, such as a namespace package, stays."""
    roots = {_canonical(dist) for dists, _probe in PYTHON.values() for dist in dists}
    packed = {name for names in python_distributions().values() for name in names}
    large = {name for name in packed if sum(path.stat().st_size for _r, path in _site_files(name)) >= LARGE}
    return sorted(top_level_modules(roots | large) - top_level_modules(base_distributions()))


def python_members(names: Iterable[str]) -> list[Member]:
    return [(f"python/{relative}", path, os.access(path, os.X_OK)) for name in sorted(names) for relative, path in _site_files(name)]


# ------------------------------------------------------------------ program packs

def learnharness_members(build: Path = BUILD) -> list[Member]:
    app, legal = build / "learnharness", build / "learnharness-legal"
    if not (app / "server.js").is_file() or not (app / "node").is_file():
        raise ValueError("pack learnharness: run desktop/package_learnharness.py first")
    return [*_tree(app, "learnharness"), *_tree(legal, "licenses/learnharness")]


def herdr_members(build: Path = BUILD, root: Path = ROOT) -> list[Member]:
    binary = build / "herdr/herdr"
    if not binary.is_file():
        raise ValueError("pack herdr: run desktop/fetch_herdr.py first")
    return [(HERDR, binary, True), ("licenses/herdr/LICENSE", root / "desktop/third_party/herdr/LICENSE", False)]


# ------------------------------------------------------------------ the release

def build(dist: Path, out: Path, version: str, *, strip: bool = True, programs: Path | None = None,
          python: bool = False) -> dict:
    """Write every pack into `out`; return the index.

    Web packs come from `dist`'s pack-files.json (their files leave `dist` unless `strip` is
    off); with `python` the Python packs, and with `programs` (desktop/.build) LearnHarness and
    herdr too: a release builds all of them."""
    listed = json.loads((dist / FILES).read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    packs: dict[str, list[Member]] = {}
    for pack in sorted(listed):
        files = sorted(listed[pack])
        missing = [name for name in files if not (dist / name).is_file()]
        if missing or not files:
            raise ValueError(f"pack {pack}: missing from {dist} ({', '.join(missing) or 'no files'}); rebuild the website")
        packs[pack] = [(f"web/{name}", (dist / name).read_bytes(), False) for name in files]
    if python:
        for pack, names in python_distributions().items():
            packs[pack] = python_members(names)
    if programs is not None:
        packs["learnharness"] = learnharness_members(programs)
        packs["herdr"] = herdr_members(programs)
    entries = [write_archive(out / archive_name(pack, version), pack, version, members)
               for pack, members in sorted(packs.items())]
    if strip:
        for pack in listed:
            for name in listed[pack]:
                (dist / name).unlink()
    index = {"format": 1, "version": version, "packs": entries}
    (out / INDEX).write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dist", type=Path, default=ROOT / "web/dist")
    parser.add_argument("--out", type=Path, default=ROOT / "desktop/.build/packs")
    parser.add_argument("--version", help="defaults to desktop/tauri.conf.json's version")
    parser.add_argument("--keep", action="store_true", help="leave the packs' files in the website")
    parser.add_argument("--web-only", action="store_true", help="only the web packs (no Python or program packs)")
    args = parser.parse_args()
    version = args.version or json.loads((ROOT / "desktop/tauri.conf.json").read_text())["version"]
    index = build(args.dist, args.out, version, strip=not args.keep,
                  python=not args.web_only, programs=None if args.web_only else BUILD)
    for entry in index["packs"]:
        print(f"{entry['file']}: {entry['size']} bytes ({entry['installed_size']} unpacked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

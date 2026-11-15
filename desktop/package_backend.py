"""Freeze the locked Python project and built website into a relocatable backend directory.

The backend is a PyInstaller onedir, not a onefile: a onefile unpacks its whole payload
(~400 MB) into a temporary directory on every launch, which took ~10 s. The directory is
shipped unpacked (tauri.conf.json ``files``) and started in place by the desktop shell.

It is the base only (docs/rust-core-design.md, milestone 3): the module packs' Python
libraries (scripts/build-packs.py ``backend_excludes``), LearnHarness and herdr are downloads
that rika-server installs and kitty.packs loads, so they are left out here.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


def _build_packs(root: Path):
    spec = importlib.util.spec_from_file_location("build_packs", root / "scripts/build-packs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    desktop = root / "desktop"
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    # Resolve the installed entrypoint package after the parent rename. No alias
    # or developer-home path is written into runtime configuration.
    entrypoint = project["scripts"].get("kitty-web")
    if entrypoint is None:
        candidates = [value for name, value in project["scripts"].items() if name.endswith("-web")]
        if len(candidates) != 1:
            raise SystemExit("Define the kitty-web Python entrypoint before packaging")
        entrypoint = candidates[0]
    package = entrypoint.split(".", 1)[0]
    if not (root / "web/dist/index.html").is_file():
        raise SystemExit("Build the website before packaging the desktop backend")
    build = desktop / ".build"
    build.mkdir(exist_ok=True)
    (build / "backend-package.txt").write_text(package + "\n")
    examples = build / "examples"
    examples.mkdir(exist_ok=True)
    for source in (root / "config").glob("*.yaml.example"):
        shutil.copyfile(source, examples / source.name)
    binaries = desktop / "binaries"
    excluded = _build_packs(root).backend_excludes()
    command = [
        # Output: binaries/kitty-web/{kitty-web,_internal/}; sys._MEIPASS is _internal.
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "kitty-web", "--distpath", str(binaries),
        "--workpath", str(build / "work"), "--specpath", str(build),
        "--collect-all", package, "--recursive-copy-metadata", project["name"],
        "--add-data", f"{root / 'web/dist'}:website",
        "--add-data", f"{examples}:examples",
        "--add-data", f"{build / 'backend-package.txt'}:.",
        "--add-data", f"{root / 'third_party/grinstead-snell'}:grinstead-snell",
        # The kitty Tern plugin's source (kitty.core.tern_plugin copies it into a workspace).
        "--add-data", f"{root / 'integrations/tern'}:tern-plugin",
        *(item for module in excluded for item in ("--exclude-module", module)),
    ]
    # These adapters load data/native libraries dynamically. Optional packages
    # are collected only when present in the project's locked environment.
    for module in ("curl_cffi", "textual", "mcp", "google.auth", "google_auth_oauthlib", "fsrs", "icalendar", "pymupdf", "PIL", "zstandard"):
        if importlib.util.find_spec(module) is not None:
            command.extend(["--collect-all", module])
    command.append(str(desktop / "backend.py"))
    subprocess.run(command, cwd=root, check=True)
    target = binaries / "kitty-web"
    internal = target / "_internal"
    executable = target / "kitty-web"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit("Packaged backend lacks the executable kitty-web")
    if not (internal / "website/index.html").is_file():
        raise SystemExit("Packaged backend lacks the website")
    # A pack's payload in the base would make it "built in" and the download pointless.
    for leaked in ("learnharness", *excluded):
        if (internal / leaked).exists():
            raise SystemExit(f"Packaged backend carries {leaked}, which is a pack")
    # Tauri's .deb writer stores every entry by following links: a linked file becomes a
    # copy, but a linked directory would arrive empty.
    for folder, directories, _files in os.walk(target):
        for name in directories:
            if os.path.islink(os.path.join(folder, name)):
                raise SystemExit(f"Packaged backend has a linked directory: {os.path.join(folder, name)}")
    # The onefile sidecar this layout replaced, and the herdr sidecar that is now a pack, must
    # not be picked up by an installer.
    for stale in (*binaries.glob("kitty-web-*"), *binaries.glob("kitty-herdr-*")):
        stale.unlink()
    print(f"Packaged relocatable backend directory: {target}")


if __name__ == "__main__":
    main()

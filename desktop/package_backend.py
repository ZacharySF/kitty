"""Freeze the locked Python project and built website into a relocatable backend directory.

The backend is a PyInstaller onedir, not a onefile: a onefile unpacks its whole payload
(~400 MB) into a temporary directory on every launch, which took ~10 s. The directory is
shipped unpacked (tauri.conf.json ``files``) and started in place by the desktop shell.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


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
    learning = desktop / ".build/learnharness"
    if not (learning / "server.js").is_file() or not (learning / "node").is_file():
        raise SystemExit("Build the LearnHarness production payload before packaging")
    build = desktop / ".build"
    build.mkdir(exist_ok=True)
    (build / "backend-package.txt").write_text(package + "\n")
    examples = build / "examples"
    examples.mkdir(exist_ok=True)
    for source in (root / "config").glob("*.yaml.example"):
        shutil.copyfile(source, examples / source.name)
    binaries = desktop / "binaries"
    command = [
        # Output: binaries/kitty-web/{kitty-web,_internal/}; sys._MEIPASS is _internal.
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "kitty-web", "--distpath", str(binaries),
        "--workpath", str(build / "work"), "--specpath", str(build),
        "--collect-all", package, "--recursive-copy-metadata", project["name"],
        "--add-data", f"{root / 'web/dist'}:website",
        "--add-data", f"{examples}:examples",
        "--add-data", f"{build / 'backend-package.txt'}:.",
        "--add-data", f"{learning}:learnharness",
        "--add-data", f"{root / 'third_party/grinstead-snell'}:grinstead-snell",
        # The kitty Tern plugin's source (kitty.core.tern_plugin copies it into a workspace).
        "--add-data", f"{root / 'integrations/tern'}:tern-plugin",
        "--add-binary", f"{learning / 'node'}:learnharness",
    ]
    # These adapters load data/native libraries dynamically. Optional packages
    # are collected only when present in the project's locked environment.
    # faster_whisper, ctranslate2, av and tokenizers are Rika's on-device speech engine.
    for module in ("jobspy", "curl_cffi", "tls_client", "textual", "mcp", "google.auth", "google_auth_oauthlib", "fsrs", "sympy", "mpmath", "lark", "antlr4", "icalendar", "pymupdf", "PIL", "zstandard",
                   "faster_whisper", "ctranslate2", "av", "tokenizers"):
        if importlib.util.find_spec(module) is not None:
            command.extend(["--collect-all", module])
    command.append(str(desktop / "backend.py"))
    subprocess.run(command, cwd=root, check=True)
    target = binaries / "kitty-web"
    internal = target / "_internal"
    for required in (target / "kitty-web", internal / "learnharness/node"):
        if not required.is_file() or not os.access(required, os.X_OK):
            raise SystemExit(f"Packaged backend lacks the executable {required.relative_to(target)}")
    if not (internal / "website/index.html").is_file():
        raise SystemExit("Packaged backend lacks the website")
    # Tauri's .deb writer stores every entry by following links: a linked file becomes a
    # copy, but a linked directory would arrive empty.
    for folder, directories, _files in os.walk(target):
        for name in directories:
            if os.path.islink(os.path.join(folder, name)):
                raise SystemExit(f"Packaged backend has a linked directory: {os.path.join(folder, name)}")
    # The onefile sidecar this layout replaced must not be picked up by an installer.
    for stale in binaries.glob("kitty-web-*"):
        stale.unlink()
    print(f"Packaged relocatable backend directory: {target}")


if __name__ == "__main__":
    main()

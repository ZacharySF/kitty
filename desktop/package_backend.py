"""Freeze the locked Python project and built website into a relocatable sidecar."""
from __future__ import annotations

import importlib.util
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
    triple = subprocess.check_output(["rustc", "-vV"], text=True)
    triple = next(line.removeprefix("host: ") for line in triple.splitlines() if line.startswith("host: "))
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--name", "kitty-web", "--distpath", str(build / "dist"),
        "--workpath", str(build / "work"), "--specpath", str(build),
        "--collect-all", package, "--recursive-copy-metadata", project["name"],
        "--add-data", f"{root / 'web/dist'}:website",
        "--add-data", f"{examples}:examples",
        "--add-data", f"{build / 'backend-package.txt'}:.",
        "--add-data", f"{learning}:learnharness",
        # The kitty Tern plugin's source (kitty.core.tern_plugin copies it into a workspace).
        "--add-data", f"{root / 'integrations/tern'}:tern-plugin",
        "--add-binary", f"{learning / 'node'}:learnharness",
    ]
    # These adapters load data/native libraries dynamically. Optional packages
    # are collected only when present in the project's locked environment.
    for module in ("jobspy", "curl_cffi", "tls_client", "textual", "mcp", "google.auth", "google_auth_oauthlib", "fsrs", "sympy", "mpmath", "lark", "antlr4", "icalendar", "pymupdf", "PIL", "zstandard"):
        if importlib.util.find_spec(module) is not None:
            command.extend(["--collect-all", module])
    command.append(str(desktop / "backend.py"))
    subprocess.run(command, cwd=root, check=True)
    binaries = desktop / "binaries"
    binaries.mkdir(exist_ok=True)
    target = binaries / f"kitty-web-{triple}"
    shutil.copyfile(build / "dist/kitty-web", target)
    target.chmod(0o755)
    print(f"Packaged relocatable backend: {target.name}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Install a source-built Linux desktop app and launcher, without touching workspaces.

On NixOS invoke inside the build dependency shell. Only runtime library/schema
settings are captured, never API keys, shell setup or a reference to the checkout.

Loose files mirror the .deb layout under ``application/``: ``bin/kitty-workspace``,
``bin/rika-server`` and the unpacked backend in ``libexec/kitty-workspace/kitty-web/``,
where the desktop binary looks for them (desktop/src/main.rs ``sidecar`` and ``BACKEND``).
herdr, like every module's payload, is a pack the app downloads (server/src/packs.rs).
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

RUNTIME_ENVIRONMENT = (
    "LD_LIBRARY_PATH", "NIX_LD_LIBRARY_PATH", "NIX_LD", "GST_PLUGIN_SYSTEM_PATH_1_0",
    "GST_PLUGIN_PATH_1_0", "GIO_EXTRA_MODULES", "GI_TYPELIB_PATH", "XDG_DATA_DIRS",
    "GSETTINGS_SCHEMA_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
)


def desktop_quote(value: str) -> str:
    # Desktop Exec is not a shell; percent is its field-code escape character.
    value = value.replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    return '"' + value + '"'


def atomic_copy(source: Path, destination: Path) -> None:
    """Keep the installed executable intact if copying the replacement fails."""
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=f".{destination.name}-") as temporary:
        staged = Path(temporary) / destination.name
        shutil.copy2(source, staged)
        staged.chmod(0o755)
        os.replace(staged, destination)


def atomic_copy_tree(source: Path, destination: Path) -> None:
    """Swap in a complete copy of the backend; the replaced one stays as ``<name>.previous``."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=f".{destination.name}-") as temporary:
        staged = Path(temporary) / destination.name
        shutil.copytree(source, staged, symlinks=True)
        if destination.is_dir():
            previous = destination.with_name(destination.name + ".previous")
            shutil.rmtree(previous, ignore_errors=True)
            os.replace(destination, previous)
        os.replace(staged, destination)


def previous_appimage_launcher(launcher: Path, image: Path) -> tuple[list[str], list[str]]:
    """Read our generated launcher as data; never execute shell to recover settings."""
    lines = launcher.read_text().splitlines()
    # v0.2.1/v0.2.2 used exec; managed launchers keep the same command in a loop.
    command = next((line[5:] for line in lines if line.startswith("exec ")), None)
    if command is None and "while :; do" in lines:
        command = lines[lines.index("while :; do") + 1].strip()
    if command is None or not command.endswith(' "$@"'):
        raise ValueError("Existing launcher is not a generated rika! launcher; keep it and install separately.")
    tokens = shlex.split(command.removesuffix(' "$@"'))
    index = tokens.index(str(image))
    if index not in (0, 1) or tokens[index + 1:index + 2] not in ([], ["--workspace"]) or len(tokens[index + 1:]) not in (0, 2):
        raise ValueError("Existing launcher has custom arguments; keep it and install separately.")
    runtime = []
    generated = {
        "unset KITTY_HOME KITTY_DB", "export APPIMAGE_EXTRACT_AND_RUN=1",
        "export KITTY_DESKTOP_LAUNCHER=1", "while :; do", "result=$?",
        '[ "$result" -eq 42 ] || exit "$result"', "done", command, "exec " + command,
    }
    for line in lines:
        if line.startswith("export ") and line[7:].split("=", 1)[0] in RUNTIME_ENVIRONMENT:
            # Validate quoting without evaluating the shell expression.
            if len(shlex.split(line[7:])) != 1:
                raise ValueError("Invalid captured runtime setting in existing launcher.")
            runtime.append(line)
        elif line.strip() and not line.startswith("#") and line.strip() not in generated:
            raise ValueError("Existing launcher contains custom shell settings; keep it and install separately.")
    return runtime, tokens


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=root / "desktop/target/release/kitty-workspace")
    parser.add_argument("--appimage", type=Path, help="Install a signed published AppImage instead of loose build files")
    parser.add_argument("--backend", type=Path, help="Packaged backend directory (default: desktop/binaries/kitty-web)")
    parser.add_argument("--server", type=Path, help="rika-server front (default: sole desktop/binaries/rika-server-* file)")
    parser.add_argument("--icon", type=Path, default=root / "desktop/icons/icon.png")
    parser.add_argument("--workspace", type=Path, help="Reuse this workspace; omitted means the native default")
    parser.add_argument("--appimage-runner", type=Path, help="Launch the AppImage through this runner (NixOS: appimage-run)")
    args = parser.parse_args()
    if args.appimage is not None and (args.backend is not None or args.server is not None):
        parser.error("--appimage excludes --backend and --server")
    if args.appimage is None:
        if args.backend is None:
            args.backend = root / "desktop/binaries/kitty-web"
        for option, pattern, label in (("server", "rika-server-*", "staged rika-server"),):
            if getattr(args, option) is None:
                candidates = list((root / "desktop/binaries").glob(pattern))
                if len(candidates) != 1:
                    parser.error(f"pass --{option} when there is not exactly one {label}")
                setattr(args, option, candidates[0])
    executables = ([(args.appimage, "kitty.AppImage")] if args.appimage is not None else
                   [(args.binary, "bin/kitty-workspace"), (args.server, "bin/rika-server")])
    for source in [*(source for source, _ in executables), args.icon]:
        if not source.is_file():
            parser.error(f"missing build artifact: {source}")
    if args.appimage is None and not (args.backend / "kitty-web").is_file():
        parser.error(f"missing build artifact: {args.backend / 'kitty-web'}")
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser().resolve()
    bin_dir = Path(os.environ.get("XDG_BIN_HOME", home / ".local/bin")).expanduser().resolve()
    application = data / "kitty/application"
    applications = data / "applications"
    icon = data / "icons/hicolor/128x128/apps/com.zacharysf.kitty.png"
    for directory in (application, applications, bin_dir, icon.parent):
        directory.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "kitty-workspace"
    installed_image = application / "kitty.AppImage"
    runtime = [f"export {name}={shlex.quote(os.environ[name])}"
               for name in RUNTIME_ENVIRONMENT if name in os.environ]
    command = [str(application / ("kitty.AppImage" if args.appimage is not None else "bin/kitty-workspace"))]
    if args.appimage is not None and installed_image.is_file() and launcher.is_file():
        try:
            runtime, command = previous_appimage_launcher(launcher, installed_image)
        except ValueError as error:
            parser.error(str(error))
    if args.appimage_runner is not None:
        # Keep the GC-rooted link, not a resolved store path.
        command = command[command.index(str(installed_image)):]
        command.insert(0, str(args.appimage_runner.expanduser().absolute()))
    if args.workspace is not None:
        if "--workspace" in command:
            command = command[:command.index("--workspace")]
        command.extend(("--workspace", str(args.workspace.expanduser().resolve())))
    for source, name in executables:
        destination = application / name
        destination.parent.mkdir(exist_ok=True)
        if destination.is_file():
            atomic_copy(destination, destination.with_name(destination.name + ".previous"))
        atomic_copy(source, destination)
    if args.appimage is None:
        atomic_copy_tree(args.backend, application / "libexec/kitty-workspace/kitty-web")
        # The flat layout of the earlier onefile backend, and the herdr that is now a pack.
        for name in ("kitty-workspace", "kitty-web", "kitty-herdr", "bin/kitty-herdr"):
            for obsolete in (application / name, application / f"{name}.previous"):
                obsolete.unlink(missing_ok=True)
    shutil.copy2(args.icon, icon)
    lines = ["#!/bin/sh", "# Captured runtime dependencies; no build shell is needed at launch.", *runtime]
    # Never let an ambient developer workspace leak into a desktop installation.
    lines.append("unset KITTY_HOME KITTY_DB")
    if args.appimage is not None:
        lines.extend(["export APPIMAGE_EXTRACT_AND_RUN=1", "export KITTY_DESKTOP_LAUNCHER=1",
                      "# Exit 42 requests a restart outside the old AppImage/FHS environment.",
                      "while :; do", "  " + shlex.join(command) + ' "$@"',
                      "  result=$?", '  [ "$result" -eq 42 ] || exit "$result"', "done"])
    else:
        lines.append("exec " + shlex.join(command) + ' "$@"')
    launcher.write_text("\n".join(lines) + "\n")
    launcher.chmod(0o755)
    entry = applications / "com.zacharysf.kitty.desktop"
    entry.write_text("[Desktop Entry]\nType=Application\nName=rika!\n"
                     "Comment=Private local opportunities, learning and planning workspace\n"
                     f"Exec={desktop_quote(str(launcher))}\n"
                     f"Icon={str(icon).replace(chr(92), chr(92) * 2)}\n"
                     "Terminal=false\nCategories=Office;Utility;\nStartupNotify=true\n")
    database_updater = shutil.which("update-desktop-database")
    if database_updater:
        subprocess.run([database_updater, str(applications)], check=True)
    print(f"Installed rika! launcher: {entry}")
    print("Launch rika! from your application menu. Existing workspace data was not changed.")
    if args.appimage is None:
        print("This local source installation is not an auto-updatable AppImage or Debian package.")


if __name__ == "__main__":
    main()

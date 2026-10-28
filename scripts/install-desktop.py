#!/usr/bin/env python3
"""Install a source-built Linux desktop app and launcher, without touching workspaces.

On NixOS invoke inside the build dependency shell. Only runtime library/schema
settings are captured, never API keys, shell setup or a reference to the checkout.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
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


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=root / "desktop/target/release/kitty-workspace")
    parser.add_argument("--appimage", type=Path, help="Install a signed published AppImage instead of loose build files")
    parser.add_argument("--sidecar", type=Path, help="Packaged backend (default: sole desktop/binaries/kitty-web-* file)")
    parser.add_argument("--icon", type=Path, default=root / "desktop/icons/icon.png")
    parser.add_argument("--workspace", type=Path, help="Reuse this workspace; omitted means the native default")
    args = parser.parse_args()
    if args.appimage is not None and args.sidecar is not None:
        parser.error("--appimage and --sidecar are mutually exclusive")
    if args.appimage is None and args.sidecar is None:
        candidates = list((root / "desktop/binaries").glob("kitty-web-*"))
        if len(candidates) != 1:
            parser.error("pass --sidecar when there is not exactly one packaged backend")
        args.sidecar = candidates[0]
    executables = ([(args.appimage, "kitty.AppImage")] if args.appimage is not None else
                   [(args.binary, "kitty-workspace"), (args.sidecar, "kitty-web")])
    for source in [*(source for source, _ in executables), args.icon]:
        if not source.is_file():
            parser.error(f"missing build artifact: {source}")
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser().resolve()
    bin_dir = Path(os.environ.get("XDG_BIN_HOME", home / ".local/bin")).expanduser().resolve()
    application = data / "kitty/application"
    applications = data / "applications"
    icon = data / "icons/hicolor/128x128/apps/com.zacharysf.kitty.png"
    for directory in (application, applications, bin_dir, icon.parent):
        directory.mkdir(parents=True, exist_ok=True)
    for source, name in executables:
        destination = application / name
        shutil.copy2(source, destination)
        destination.chmod(0o755)
    shutil.copy2(args.icon, icon)
    launcher = bin_dir / "kitty-workspace"
    lines = ["#!/bin/sh", "# Captured runtime dependencies; no build shell is needed at launch."]
    for name in RUNTIME_ENVIRONMENT:
        if name in os.environ:
            lines.append(f"export {name}={shlex.quote(os.environ[name])}")
    # The native default remains authoritative unless explicitly selecting an
    # existing workspace; an ambient developer KITTY_HOME must not leak in.
    lines.append("unset KITTY_HOME KITTY_DB")
    if args.appimage is not None:
        lines.append("export APPIMAGE_EXTRACT_AND_RUN=1")
    command = [str(application / ("kitty.AppImage" if args.appimage is not None else "kitty-workspace"))]
    if args.workspace is not None:
        command.extend(("--workspace", str(args.workspace.expanduser().resolve())))
    lines.append("exec " + shlex.join(command) + ' "$@"')
    launcher.write_text("\n".join(lines) + "\n")
    launcher.chmod(0o755)
    entry = applications / "com.zacharysf.kitty.desktop"
    entry.write_text("[Desktop Entry]\nType=Application\nName=kitty!\n"
                     "Comment=Private local opportunities, learning and planning workspace\n"
                     f"Exec={desktop_quote(str(launcher))}\n"
                     f"Icon={str(icon).replace(chr(92), chr(92) * 2)}\n"
                     "Terminal=false\nCategories=Office;Utility;\nStartupNotify=true\n")
    database_updater = shutil.which("update-desktop-database")
    if database_updater:
        subprocess.run([database_updater, str(applications)], check=True)
    print(f"Installed kitty! launcher: {entry}")
    print("Launch kitty! from your application menu. Existing workspace data was not changed.")
    if args.appimage is None:
        print("This local source installation is not an auto-updatable AppImage or Debian package.")


if __name__ == "__main__":
    main()

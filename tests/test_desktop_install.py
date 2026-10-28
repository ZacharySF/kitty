"""Run an installed launcher with isolated XDG roots and synthetic executables."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_install_launcher_preserves_workspace_and_runtime_environment(tmp_path):
    build = tmp_path / "build files"
    build.mkdir()
    binary = build / "kitty-workspace"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$LD_LIBRARY_PATH" "$GST_PLUGIN_SYSTEM_PATH_1_0" "$@"\nexec "$(dirname "$0")/kitty-web"\n')
    binary.chmod(0o755)
    sidecar = build / "kitty-web-x86_64-unknown-linux-gnu"
    sidecar.write_text('#!/bin/sh\nprintf "sidecar started\\n"\n')
    sidecar.chmod(0o755)
    icon = build / "icon.png"
    icon.write_bytes(b"fixture icon")
    data = tmp_path / "user data"
    workspace = tmp_path / "existing workspace"
    workspace.mkdir()
    database = workspace / "apply.db"
    database.write_bytes(b"untouched database")
    default_workspace = data / "kitty/workspace"
    default_workspace.mkdir(parents=True)
    sentinel = default_workspace / "config.yaml"
    sentinel.write_text("keep user configuration")
    env = os.environ | {"XDG_DATA_HOME": str(data), "XDG_BIN_HOME": str(tmp_path / "user bin"),
                        "LD_LIBRARY_PATH": "/fixture/nix/lib", "GST_PLUGIN_SYSTEM_PATH_1_0": "/fixture/nix/gstreamer"}
    result = subprocess.run([sys.executable, str(ROOT / "scripts/install-desktop.py"),
                             "--binary", str(binary), "--sidecar", str(sidecar), "--icon", str(icon),
                             "--workspace", str(workspace)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    entry = (data / "applications/com.zacharysf.kitty.desktop").read_text()
    command = next(line.removeprefix("Exec=") for line in entry.splitlines() if line.startswith("Exec="))
    # This fixture uses spaces, not desktop-entry escape-sensitive shell syntax.
    launch = subprocess.run(shlex.split(command), env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert launch.returncode == 0, launch.stderr
    assert launch.stdout.splitlines() == ["/fixture/nix/lib", "/fixture/nix/gstreamer", "--workspace", str(workspace), "sidecar started"]
    assert database.read_bytes() == b"untouched database"
    assert sentinel.read_text() == "keep user configuration"
    assert "Terminal=false" in entry
    assert (data / "kitty/application/kitty-workspace").is_file()
    assert (data / "kitty/application/kitty-web").is_file()


def test_install_without_workspace_uses_native_default(tmp_path):
    binary = tmp_path / "kitty-workspace"
    binary.write_text('#!/bin/sh\nprintf "%s" "$#"\n')
    binary.chmod(0o755)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"icon")
    data = tmp_path / "data"
    env = os.environ | {"XDG_DATA_HOME": str(data), "XDG_BIN_HOME": str(tmp_path / "bin")}
    result = subprocess.run([sys.executable, str(ROOT / "scripts/install-desktop.py"), "--binary", str(binary),
                             "--sidecar", str(binary), "--icon", str(icon)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    launch = subprocess.run([str(tmp_path / "bin/kitty-workspace")], env=env, capture_output=True, text=True)
    assert launch.returncode == 0, launch.stderr
    assert launch.stdout == "0"


def test_installed_appimage_launches_replacement_without_source_checkout(tmp_path):
    image = tmp_path / "download.AppImage"
    image.write_text("#!/bin/sh\nexit 12\n")
    image.chmod(0o755)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"icon")
    data = tmp_path / "user data"
    env = os.environ | {"XDG_DATA_HOME": str(data), "XDG_BIN_HOME": str(tmp_path / "bin")}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/install-desktop.py"),
         "--appimage", str(image), "--icon", str(icon)],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    image.unlink()
    launcher = tmp_path / "bin/kitty-workspace"
    assert subprocess.run([str(launcher)], env=env).returncode == 12
    # The updater replaces the installed image, not the launcher or workspace.
    installed = data / "kitty/application/kitty.AppImage"
    replacement = installed.with_suffix(".new")
    replacement.write_text("#!/bin/sh\nexit 23\n")
    replacement.chmod(0o755)
    replacement.replace(installed)
    assert subprocess.run([str(launcher)], env=env).returncode == 23

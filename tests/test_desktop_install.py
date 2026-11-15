"""Run an installed launcher with isolated XDG roots and synthetic executables."""
from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_install_launcher_preserves_workspace_and_runtime_environment(tmp_path):
    build = tmp_path / "build files"
    build.mkdir()
    binary = build / "kitty-workspace"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$LD_LIBRARY_PATH" "$GST_PLUGIN_SYSTEM_PATH_1_0" "$@"\n'
                      'exec "$(dirname "$0")/rika-server"\n')
    binary.chmod(0o755)
    backend = build / "kitty-web"
    (backend / "_internal").mkdir(parents=True)
    (backend / "_internal/website.txt").write_text("bundled data")
    (backend / "kitty-web").write_text('#!/bin/sh\nprintf "sidecar started\\n"\n')
    (backend / "kitty-web").chmod(0o755)
    # The shell starts rika-server beside it, which starts the backend behind itself.
    server = build / "rika-server-x86_64-unknown-linux-gnu"
    server.write_text('#!/bin/sh\nprintf "front started\\n"\n'
                      'exec "$(dirname "$0")/../libexec/kitty-workspace/kitty-web/kitty-web"\n')
    server.chmod(0o755)
    icon = build / "icon.png"
    icon.write_bytes(b"fixture icon")
    data = tmp_path / "user data"
    # A flat onefile installation from before the unpacked backend.
    (data / "kitty/application/bin").mkdir(parents=True)
    (data / "kitty/application/kitty-web").write_bytes(b"old onefile")
    # herdr used to be installed beside the app; it is a pack now.
    (data / "kitty/application/bin/kitty-herdr").write_bytes(b"old herdr")
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
                             "--binary", str(binary), "--backend", str(backend), "--server", str(server),
                             "--icon", str(icon), "--workspace", str(workspace)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    entry = (data / "applications/com.zacharysf.kitty.desktop").read_text()
    command = next(line.removeprefix("Exec=") for line in entry.splitlines() if line.startswith("Exec="))
    # This fixture uses spaces, not desktop-entry escape-sensitive shell syntax.
    launch = subprocess.run(shlex.split(command), env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert launch.returncode == 0, launch.stderr
    assert launch.stdout.splitlines() == ["/fixture/nix/lib", "/fixture/nix/gstreamer", "--workspace", str(workspace),
                                          "front started", "sidecar started"]
    assert database.read_bytes() == b"untouched database"
    assert sentinel.read_text() == "keep user configuration"
    assert "Terminal=false" in entry
    application = data / "kitty/application"
    assert (application / "bin/kitty-workspace").is_file()
    assert (application / "libexec/kitty-workspace/kitty-web/_internal/website.txt").read_text() == "bundled data"
    assert not (application / "kitty-web").exists()
    assert not (application / "bin/kitty-herdr").exists()
    installed_server = application / "bin/rika-server"
    assert installed_server.read_bytes() == server.read_bytes() and os.access(installed_server, os.X_OK)
    # Reinstalling swaps in a whole new backend directory and keeps the replaced one.
    (backend / "_internal/website.txt").write_text("new data")
    again = subprocess.run([sys.executable, str(ROOT / "scripts/install-desktop.py"),
                            "--binary", str(binary), "--backend", str(backend), "--server", str(server),
                            "--icon", str(icon), "--workspace", str(workspace)], env=env, capture_output=True, text=True)
    assert again.returncode == 0, again.stderr
    assert (application / "libexec/kitty-workspace/kitty-web/_internal/website.txt").read_text() == "new data"
    assert (application / "libexec/kitty-workspace/kitty-web.previous/_internal/website.txt").read_text() == "bundled data"


def test_install_without_workspace_uses_native_default(tmp_path):
    binary = tmp_path / "kitty-workspace"
    binary.write_text('#!/bin/sh\nprintf "%s" "$#"\n')
    binary.chmod(0o755)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"icon")
    data = tmp_path / "data"
    env = os.environ | {"XDG_DATA_HOME": str(data), "XDG_BIN_HOME": str(tmp_path / "bin")}
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "kitty-web").write_bytes(binary.read_bytes())
    result = subprocess.run([sys.executable, str(ROOT / "scripts/install-desktop.py"), "--binary", str(binary),
                             "--backend", str(backend), "--server", str(binary), "--icon", str(icon)],
                            env=env, capture_output=True, text=True)
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


def test_appimage_reinstall_keeps_working_runtime_workspace_and_previous_image(tmp_path):
    image = tmp_path / "download.AppImage"
    image.write_text('#!/bin/sh\nprintf "old\\n"\n')
    image.chmod(0o755)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"icon")
    workspace = tmp_path / "selected workspace"
    workspace.mkdir()
    profile = workspace / "profile.yaml"
    profile.write_text("name: Synthetic update person\n")
    runner = tmp_path / "appimage-run"
    runner.write_text('#!/bin/sh\nimage="$1"; shift\nexec "$image" "$@"\n')
    runner.chmod(0o755)
    environment = os.environ | {
        "XDG_DATA_HOME": str(tmp_path / "data"), "XDG_BIN_HOME": str(tmp_path / "bin"),
        "LD_LIBRARY_PATH": "/working/runtime", "GST_PLUGIN_SYSTEM_PATH_1_0": "/working/media",
    }
    install = [sys.executable, str(ROOT / "scripts/install-desktop.py"),
               "--appimage", str(image), "--icon", str(icon)]
    first = subprocess.run(install + ["--appimage-runner", str(runner), "--workspace", str(workspace)],
                           env=environment, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    old_bytes = image.read_bytes()
    image.write_text('#!/bin/sh\nprintf "%s\\n" "$LD_LIBRARY_PATH" "$GST_PLUGIN_SYSTEM_PATH_1_0" "$@"\n')
    # Routine replacement from a different development dependency shell must
    # not recapture its libraries or silently switch to the default workspace.
    second = subprocess.run(install, env=environment | {"LD_LIBRARY_PATH": "/incompatible/build",
                            "GST_PLUGIN_SYSTEM_PATH_1_0": "/incompatible/media"},
                            capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    launch = subprocess.run([str(tmp_path / "bin/kitty-workspace")],
                            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
                            capture_output=True, text=True)
    assert launch.returncode == 0, launch.stderr
    assert launch.stdout.splitlines() == ["/working/runtime", "/working/media", "--workspace", str(workspace)]
    assert (tmp_path / "data/kitty/application/kitty.AppImage.previous").read_bytes() == old_bytes
    assert profile.read_text() == "name: Synthetic update person\n"


def test_appimage_restart_returns_through_runner_with_clean_environment(tmp_path):
    image = tmp_path / "download.AppImage"
    marker = tmp_path / "updated"
    image.write_text(f'''#!/bin/sh
if [ ! -e {shlex.quote(str(marker))} ]; then
    touch {shlex.quote(str(marker))}
    export LD_LIBRARY_PATH=/old/extracted/appimage
    exit 42
fi
printf "%s\\n" "$LD_LIBRARY_PATH" "$@"
''')
    image.chmod(0o755)
    runner = tmp_path / "appimage-run"
    runner.write_text('#!/bin/sh\nprintf "runner\\n"\nimage="$1"; shift\nexec "$image" "$@"\n')
    runner.chmod(0o755)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"icon")
    env = os.environ | {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_BIN_HOME": str(tmp_path / "bin"),
                        "LD_LIBRARY_PATH": "/working/runtime"}
    install = subprocess.run([sys.executable, str(ROOT / "scripts/install-desktop.py"),
                              "--appimage", str(image), "--appimage-runner", str(runner), "--icon", str(icon)],
                             env=env, capture_output=True, text=True)
    assert install.returncode == 0, install.stderr
    launch = subprocess.run([str(tmp_path / "bin/kitty-workspace"), "--workspace", str(tmp_path / "synthetic")],
                            env=env, capture_output=True, text=True, timeout=10)
    assert launch.returncode == 0, launch.stderr
    assert launch.stdout.splitlines() == ["runner", "runner", "/working/runtime", "--workspace", str(tmp_path / "synthetic")]


def test_legacy_appimage_launcher_is_upgraded_without_recapturing_runtime(tmp_path):
    data = tmp_path / "data"
    application = data / "kitty/application"
    application.mkdir(parents=True)
    installed = application / "kitty.AppImage"
    installed.write_text("#!/bin/sh\nexit 17\n")
    installed.chmod(0o755)
    launcher = tmp_path / "bin/kitty-workspace"
    launcher.parent.mkdir()
    runner = tmp_path / "runner"
    runner.write_text('#!/bin/sh\nprintf "runner\\n"\nimage="$1"; shift\nexec "$image" "$@"\n')
    runner.chmod(0o755)
    workspace = tmp_path / "legacy workspace"
    # The publicly shipped v0.2.1/v0.2.2 installer emitted this shell contract.
    launcher.write_text("#!/bin/sh\nexport LD_LIBRARY_PATH=/legacy/runtime\n"
                        "unset KITTY_HOME KITTY_DB\nexport APPIMAGE_EXTRACT_AND_RUN=1\nexec "
                        + shlex.join([str(runner), str(installed), "--workspace", str(workspace)]) + ' "$@"\n')
    launcher.chmod(0o755)
    replacement = tmp_path / "download.AppImage"
    replacement.write_text('#!/bin/sh\nprintf "%s\\n" "$LD_LIBRARY_PATH" "$@"\n')
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"icon")
    env = os.environ | {"XDG_DATA_HOME": str(data), "XDG_BIN_HOME": str(launcher.parent),
                        "LD_LIBRARY_PATH": "/incompatible/build"}
    result = subprocess.run([sys.executable, str(ROOT / "scripts/install-desktop.py"),
                             "--appimage", str(replacement), "--icon", str(icon)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    launch = subprocess.run([str(launcher)], env=env, capture_output=True, text=True)
    assert launch.returncode == 0, launch.stderr
    assert launch.stdout.splitlines() == ["runner", "/legacy/runtime", "--workspace", str(workspace)]
    assert subprocess.run([str(installed.with_name("kitty.AppImage.previous"))], env=env).returncode == 17


def test_failed_appimage_copy_leaves_current_executable_launchable(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("desktop_installer", ROOT / "scripts/install-desktop.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installed = tmp_path / "kitty.AppImage"
    installed.write_text("#!/bin/sh\nexit 17\n")
    installed.chmod(0o755)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"new image")

    def disk_full(_source, destination):
        Path(destination).write_bytes(b"incomplete copy")
        raise OSError("simulated disk full")

    monkeypatch.setattr(installer.shutil, "copy2", disk_full)
    with pytest.raises(OSError, match="simulated disk full"):
        installer.atomic_copy(replacement, installed)
    assert subprocess.run([str(installed)]).returncode == 17


def test_custom_launcher_settings_are_not_discarded_by_reinstallation(tmp_path):
    data = tmp_path / "data"
    application = data / "kitty/application"
    application.mkdir(parents=True)
    installed = application / "kitty.AppImage"
    installed.write_text("#!/bin/sh\nexit 17\n")
    installed.chmod(0o755)
    launcher = tmp_path / "bin/kitty-workspace"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\nexport CUSTOM_RUNTIME_SETTING=keep\nexec "
                        + shlex.quote(str(installed)) + ' "$@"\n')
    launcher.chmod(0o755)
    previous_launcher = launcher.read_bytes()
    replacement = tmp_path / "replacement.AppImage"
    replacement.write_text("#!/bin/sh\nexit 23\n")
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"icon")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/install-desktop.py"),
                             "--appimage", str(replacement), "--icon", str(icon)],
                            env=os.environ | {"XDG_DATA_HOME": str(data), "XDG_BIN_HOME": str(launcher.parent)},
                            capture_output=True, text=True)
    assert result.returncode != 0, "Reinstallation must not discard custom shell settings."
    assert launcher.read_bytes() == previous_launcher
    assert subprocess.run([str(launcher)]).returncode == 17

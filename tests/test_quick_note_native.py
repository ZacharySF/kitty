"""Opt-in compositor-level regression; never attaches to an existing app/display.

Set KITTY_NATIVE_BINARY to a freshly built kitty-workspace binary and put Xvfb,
openbox, xcompmgr, xdotool, ImageMagick 7 and dbus-run-session on PATH. Run inside
the desktop host-toolchain shell so WebKitGTK can load its native libraries.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image


BINARY = os.environ.get("KITTY_NATIVE_BINARY")
pytestmark = pytest.mark.skipif(not BINARY, reason="requires opt-in native desktop build")


def eventually(operation, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        value = operation()
        if value:
            return value
        assert time.monotonic() < deadline, "native UI did not become ready"
        time.sleep(0.1)


@pytest.fixture
def native_note(tmp_path):
    for tool in ("Xvfb", "openbox", "xcompmgr", "xdotool", "magick", "dbus-run-session"):
        assert shutil.which(tool), f"missing native test prerequisite: {tool}"
    home = tmp_path / "home"
    home.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("KITTY_", "XDG_", "DBUS_"))
           and key not in ("WAYLAND_DISPLAY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(runtime), GDK_BACKEND="x11")
    tool_env = {key: value for key, value in env.items() if key != "LD_LIBRARY_PATH"}
    processes = []
    with (tmp_path / "native.log").open("w") as log:
        try:
            display = subprocess.Popen(
                ["Xvfb", "-displayfd", "1", "-screen", "0", "2400x1100x24", "-ac", "-nolisten", "tcp"],
                stdout=subprocess.PIPE, stderr=log, env=tool_env, text=True,
            )
            processes.append(display)
            env["DISPLAY"] = ":" + display.stdout.readline().strip()
            tool_env["DISPLAY"] = env["DISPLAY"]
            for command in (["openbox", "--sm-disable"], ["xcompmgr", "-n"]):
                processes.append(subprocess.Popen(command, env=tool_env, stdout=log, stderr=log))
            time.sleep(0.5)
            app = subprocess.Popen(
                ["dbus-run-session", "--", str(Path(BINARY).resolve()),
                 "--workspace", str(tmp_path / "workspace"), "--quick-note"],
                env=env, stdout=log, stderr=log,
            )
            processes.append(app)

            def run(*args, check=True):
                result = subprocess.run(args, env=tool_env, capture_output=True, text=True)
                if check:
                    assert result.returncode == 0, f"{args}: {result.stderr}"
                return result

            def paint_backdrop(color):
                image = tmp_path / f"{color}.png"
                Image.new("RGB", (2400, 1100), color).save(image)
                process = subprocess.Popen(
                    ["magick", "display", "-title", f"Backdrop-{color}", "-geometry", "+0+0", str(image)],
                    env=tool_env, stdout=log, stderr=log,
                )
                processes.append(process)
                window = eventually(lambda: run("xdotool", "search", "--onlyvisible", "--name", f"^Backdrop-{color}$", check=False).stdout.strip()).splitlines()[0]
                # Above older backdrop windows but below both owned application windows.
                run("xdotool", "windowraise", window)
                run("xdotool", "windowraise", main)
                run("xdotool", "windowraise", note)
                time.sleep(0.5)

            def find_note():
                assert app.poll() is None, (tmp_path / "native.log").read_text()
                return run("xdotool", "search", "--onlyvisible", "--name", "^kitty! quick note$", check=False).stdout.strip()

            note = eventually(find_note).splitlines()[0]
            run("xdotool", "windowmove", note, "50", "60")
            run("xdotool", "windowsize", note, "380", "460")
            # Keep the main window beside, never underneath, the detached editor.
            main = eventually(lambda: run("xdotool", "search", "--onlyvisible", "--name", "^kitty!$", check=False).stdout.strip()).splitlines()[0]
            run("xdotool", "windowmove", main, "1200", "60")
            run("xdotool", "windowsize", main, "1000", "850")
            time.sleep(2)
            yield run, note, tmp_path, paint_backdrop
            run("xdotool", "windowactivate", "--sync", main)
            run("xdotool", "key", "ctrl+shift+q")
            app.wait(timeout=30)
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def test_detached_editor_reveals_native_backdrop(native_note):
    run, _note, root, paint_backdrop = native_note
    captures = []
    evidence = os.environ.get("KITTY_NATIVE_EVIDENCE_DIR")
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
    for color in ("black", "white"):
        paint_backdrop(color)
        shot = root / f"native-{color}.png"
        run("magick", "import", "-window", "root", str(shot))
        if evidence:
            shutil.copyfile(shot, Path(evidence) / shot.name)
        with Image.open(shot) as image:
            image = image.convert("RGB")
            backdrop = (0, 0, 0) if color == "black" else (255, 255, 255)
            assert image.getpixel((20, 420)) == backdrop, "backdrop positive control failed"
            assert image.getpixel((80, 110)) != backdrop, "note header did not render"
            # Blank lower editor, below the strongest part of the top color wash.
            captures.append(image.getpixel((240, 420)))
    transmitted = min(light - dark for dark, light in zip(*captures))
    assert transmitted >= 230, (
        f"Detached editor masks the desktop: black/white backdrop transmission "
        f"{transmitted}/255; pixels {captures}. Expected an unfilled lower editor."
    )

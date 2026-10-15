"""Frozen kitty-web sidecar; stdout is a private readiness channel to its owner."""
from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import os
import signal
import sys
import threading
from pathlib import Path


def prepare_workspace(root: Path, examples: Path) -> None:
    """Reuse selected workspaces in place; never copy databases or overwrite config."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = root / "config"
    config.mkdir(exist_ok=True, mode=0o700)
    for example in examples.glob("*.yaml.example"):
        destination = config / example.name.removesuffix(".example")
        try:
            with destination.open("x", encoding="utf-8") as output:
                output.write(example.read_text(encoding="utf-8"))
        except FileExistsError:
            pass
    for directory in ("data", "resumes", ".private"):
        (root / directory).mkdir(exist_ok=True, mode=0o700)


def serve(root: Path, assets: Path, package: str) -> None:
    server_module = importlib.import_module(f"{package}.web_server")
    settings_module = importlib.import_module(f"{package}.settings")
    workspace_module = importlib.import_module(f"{package}.core.workspace")
    settings = settings_module.Settings.load(root=root)
    with workspace_module.open_workspace(settings) as workspace:
        app = server_module.WebApplication(settings, workspace.config, assets)

    class DesktopHTTPServer(server_module.LocalHTTPServer):
        # A quit must drain accepted HTTP mutations before closing SQLite workers.
        daemon_threads = False
        block_on_close = True

    # Bind port zero directly: no probe-and-release race and no non-loopback mode.
    try:
        server = DesktopHTTPServer(app, 0)
    except BaseException:
        app.close()
        raise
    with server:
        stop = threading.Event()

        def request_stop(*_args: object) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        def owner_channel() -> None:
            # EOF also closes the backend when the desktop crashes. No HTTP quit route.
            for line in sys.stdin:
                if line.strip() == "quit":
                    break
            request_stop()

        def shutdown() -> None:
            stop.wait()
            server.shutdown()

        threading.Thread(target=owner_channel, daemon=True, name="kitty-owner").start()
        threading.Thread(target=shutdown, daemon=True, name="kitty-shutdown").start()
        print(json.dumps({"origin": server.origin}), flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            server.server_close()
            # Core owns scheduler, browser-worker and TaskRunner close ordering.
            # Browser tabs belong to the user and are never process-group-killed.
            app.close()


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    assets = bundle / "website"
    if not (assets / "index.html").is_file():
        parser.exit(1, "The packaged website is missing; reinstall kitty!.\n")
    root = args.workspace.expanduser().resolve()
    prepare_workspace(root, bundle / "examples")
    # Also serialize distinct desktop processes accidentally selecting the same root.
    with (root / ".private" / "desktop.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "This workspace already has a desktop backend.\n")
        try:
            package = (bundle / "backend-package.txt").read_text().strip()
            serve(root, assets, package)
        except Exception:
            # Config errors and SDK exceptions can contain secrets; don't print them.
            parser.exit(1, "Unable to open the private workspace; check its configuration.\n")


if __name__ == "__main__":
    main()

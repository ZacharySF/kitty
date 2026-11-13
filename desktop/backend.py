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


def prepare_workspace(root: Path, examples: Path, package: str) -> None:
    """Reuse selected workspaces in place; never copy databases or overwrite config."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = root / "config"
    config.mkdir(exist_ok=True, mode=0o700)
    settings_module = importlib.import_module(f"{package}.settings")
    bootstrap = importlib.import_module(f"{package}.core.bootstrap")
    settings = settings_module.Settings(root=root, db_path=root / "data" / "apply.db", demo=False)
    enabled = bootstrap.initialize_modules(settings)
    for example in examples.glob("*.yaml.example"):
        if "applyai" not in enabled:
            break
        destination = config / example.name.removesuffix(".example")
        try:
            with destination.open("x", encoding="utf-8") as output:
                output.write(example.read_text(encoding="utf-8"))
        except FileExistsError:
            pass
    directories = ("data", ".private", "resumes") if "applyai" in enabled else ("data", ".private")
    for directory in directories:
        (root / directory).mkdir(exist_ok=True, mode=0o700)


def serve(root: Path, assets: Path, package: str, *, safe: bool = False) -> None:
    server_module = importlib.import_module(f"{package}.web_server")
    settings_module = importlib.import_module(f"{package}.settings")
    workspace_module = importlib.import_module(f"{package}.core.workspace")
    desktop_module = importlib.import_module(f"{package}.web_desktop")
    endpoint_module = importlib.import_module(f"{package}.core.endpoint")
    project_module = importlib.import_module(f"{package}.core.builds.project")
    # Keep the owner pipe private: only owner messages use the original stdout;
    # stray library prints (Python or native) go to /dev/null instead.
    owner_output = os.fdopen(os.dup(1), "w", encoding="utf-8")
    discard = os.open(os.devnull, os.O_WRONLY)
    os.dup2(discard, 1)
    os.close(discard)

    def send(line: str) -> None:
        owner_output.write(line + "\n")
        owner_output.flush()

    owner = desktop_module.DesktopOwner(send)
    settings = settings_module.Settings.load(root=root)
    # A build saved with an error starts on the last good build, as kitty-web does.
    with workspace_module.open_workspace(settings, modules=project_module.startup_modules(settings)) as workspace:
        app = server_module.WebApplication(settings, workspace.config, assets, desktop=owner, safe=safe)

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
            # EOF also closes the backend when the desktop crashes. No HTTP quit route:
            # the page may only ask the shell, which answers with this pipe's quit.
            for line in sys.stdin:
                if line.strip() == "quit":
                    break
                owner.report(line)
            request_stop()

        def shutdown() -> None:
            stop.wait()
            server.shutdown()

        threading.Thread(target=owner_channel, daemon=True, name="kitty-owner").start()
        threading.Thread(target=shutdown, daemon=True, name="kitty-shutdown").start()
        try:
            # Paired programs (Q7) find this random port through the owner-only
            # .private/endpoint.json; it is removed again when serving stops.
            with endpoint_module.published(settings.root, server.origin):
                # The UI secret travels only on this private pipe: the shell injects it into
                # rika!'s own windows (docs/platform-design.md, "UI secret").
                send(json.dumps({"origin": server.origin, "secret": server.ui_auth.secret}))
                # Framed external-module servers (kitty.web_external_modules): their ports are
                # fixed at start-up, so the shell's navigation guard learns them before any page.
                for port in app.frame_ports():
                    owner.allow_frame(port)
                server.serve_forever(poll_interval=0.1)
        finally:
            server.server_close()
            # Core owns scheduler, browser-worker and TaskRunner close ordering.
            # Browser tabs belong to the user and are never process-group-killed.
            app.close()


def main() -> None:
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    if sys.argv[1:2] == ["agent-launch"]:
        # An Agent tab's shim (kitty.core.agent_sessions.launcher_command): confine, then exec the agent.
        package = (bundle / "backend-package.txt").read_text().strip()
        importlib.import_module(f"{package}.agent_launch").main(sys.argv[2:])
        return
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--safe", action="store_true",
                        help="Start every window without the build's custom CSS and theme.")
    args = parser.parse_args()
    assets = bundle / "website"
    if not (assets / "index.html").is_file():
        parser.exit(1, "The packaged website is missing; reinstall rika!.\n")
    root = args.workspace.expanduser().resolve()
    package = (bundle / "backend-package.txt").read_text().strip()
    prepare_workspace(root, bundle / "examples", package)
    # Also serialize distinct desktop processes accidentally selecting the same root.
    with (root / ".private" / "desktop.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "This workspace already has a desktop backend.\n")
        try:
            serve(root, assets, package, safe=args.safe)
        except Exception:
            # Config errors and SDK exceptions can contain secrets; don't print them.
            parser.exit(1, "Unable to open the private workspace; check its configuration.\n")


if __name__ == "__main__":
    # The math checker runs SymPy in a spawned worker process (kitty.core.math.cas).
    import multiprocessing

    multiprocessing.freeze_support()
    main()

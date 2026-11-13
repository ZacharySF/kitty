"""Own only the bundled external learning server, never a user's existing service."""
from __future__ import annotations

from pathlib import Path

from kitty.process_runtime import LoopbackProcess, loader_environment


class LearnHarnessRuntime(LoopbackProcess):
    label = "LearnHarness"

    def __init__(self, bundle: Path, data: Path, *, port: int = 3210):
        # A healthy service already on the fixed port (a user's own) is reused, not stopped.
        super().__init__(port, method="HEAD", healthy={200}, reuse=True)
        self.bundle = bundle
        self.data = data

    def prepare(self):
        node = self.bundle / "node"
        server = self.bundle / "server.js"
        if not node.is_file() or not server.is_file():
            raise RuntimeError("LearnHarness runtime is missing; reinstall rika! with the complete desktop package.")
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        home = self.data / "home"
        home.mkdir(exist_ok=True, mode=0o700)
        # No inherited provider keys, Kitty paths, NODE_OPTIONS, or user tool config.
        environment = {
            "HOME": str(home), "LANG": "C.UTF-8", "NODE_ENV": "production",
            "HOSTNAME": "127.0.0.1", "PORT": str(self.port),
            "LH_DATA_DIR": str(self.data), "LH_AUTH_FILE": str(self.data / "auth.json"),
            "NEXT_TELEMETRY_DISABLED": "1", **loader_environment(),
        }
        return [str(node), str(server)], self.bundle, environment

    def cannot_start(self) -> str:
        return "LearnHarness could not start; reinstall rika! for this system."

    def not_ready(self) -> str:
        return "LearnHarness did not become ready. Check that port 3210 is free, then try Start again."

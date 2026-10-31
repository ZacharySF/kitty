"""Build the pinned, separate AGPL learning app; never inspect a user's checkout."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

REVISION = "3dc88472eea41eed0d5e5f29c2f16f8d063c415b"
UPSTREAM = "https://github.com/amosblomqvist/learnharness"


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    target = root / "desktop/.build/learnharness"
    legal = target.parent / "learnharness-legal"
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        raise SystemExit("Node.js and npm are required to build the bundled learning runtime")
    with tempfile.TemporaryDirectory(prefix="kitty-learning-build-") as temporary:
        scratch = Path(temporary)
        source = scratch / "source"
        subprocess.run(["git", "clone", "--no-checkout", UPSTREAM, str(source)], check=True)
        subprocess.run(["git", "checkout", "--detach", REVISION], cwd=source, check=True)
        patch = root / "scripts/learnharness-dependencies.patch"
        subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
        environment = {name: os.environ[name] for name in (
            "PATH", "NIX_LD", "NIX_LD_LIBRARY_PATH", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "NIX_SSL_CERT_FILE",
        ) if name in os.environ}
        environment.update({"HOME": str(scratch / "home"), "LANG": "C.UTF-8",
            "NEXT_TELEMETRY_DISABLED": "1", "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"})
        for command in ([npm, "ci"], [npm, "run", "build"], [node, "scripts/build-server.mjs"]):
            subprocess.run(command, cwd=source, env=environment, check=True)
        standalone = source / ".next/standalone"
        # Upstream's build-server guard rejects data/auth/env leaks and missing prompts.
        # Start from a public clone, not a developer's possibly private build directory.
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(standalone, target)
        legal.mkdir(exist_ok=True)
        shutil.copyfile(source / "LICENSE", legal / "LICENSE")
        # Include corresponding source and the exact dependency change, not just a link.
        subprocess.run(["git", "archive", "--format=tar.gz", "-o", str(legal / "source.tar.gz"), REVISION],
                       cwd=source, check=True)
        shutil.copy2(node, target / "node")
        version = subprocess.check_output([node, "--version"], text=True).strip()
        license_url = f"https://raw.githubusercontent.com/nodejs/node/{version}/LICENSE"
        with urllib.request.urlopen(license_url, timeout=30) as response, (legal / "NODE-LICENSE").open("wb") as license_file:
            shutil.copyfileobj(response, license_file)
        shutil.copyfile(patch, legal / "learnharness-dependencies.patch")
        print(f"Packaged LearnHarness {REVISION}: {target}")


if __name__ == "__main__":
    main()

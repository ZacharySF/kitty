"""Stage rika!'s pinned herdr release binary as the desktop bundle's kitty-herdr sidecar.

herdr is Apache-2.0 (desktop/third_party/herdr/LICENSE ships in the bundle; the
upstream release has no NOTICE file). Only a binary whose SHA-256 matches the pin
below is staged; upgrading herdr means changing the version and both digests here.

    python desktop/fetch_herdr.py [--from <already downloaded herdr-linux-*>]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

HERDR_VERSION = "0.9.5"
RELEASE = "https://github.com/herdrdev/herdr/releases/download/v{version}/{asset}"
#: Rust target triple -> (release asset, SHA-256 GitHub publishes for that asset).
PINS: dict[str, tuple[str, str]] = {
    "x86_64-unknown-linux-gnu": (
        "herdr-linux-x86_64", "14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d"),
    "aarch64-unknown-linux-gnu": (
        "herdr-linux-aarch64", "17d7536f2da50859523680ef279dffcac8e4a235f4e46fe8da6c2ec44141897c"),
}


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def install(triple: str, binaries: Path, fetch: Callable[[str], bytes] = download) -> Path:
    """Stage ``kitty-herdr-<triple>`` in `binaries`, verified against the pin."""
    if triple not in PINS:
        raise SystemExit(f"There is no pinned herdr {HERDR_VERSION} release for {triple}")
    asset, digest = PINS[triple]
    target = binaries / f"kitty-herdr-{triple}"
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        return target
    data = fetch(RELEASE.format(version=HERDR_VERSION, asset=asset))
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f"herdr {HERDR_VERSION} {asset} does not match its pinned SHA-256; refusing to bundle it")
    binaries.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=binaries)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o755)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, help="Verify and stage this downloaded release asset")
    args = parser.parse_args()
    host = subprocess.check_output(["rustc", "-vV"], text=True)
    triple = next(line.removeprefix("host: ") for line in host.splitlines() if line.startswith("host: "))
    fetch = (lambda _url: args.source.read_bytes()) if args.source else download
    target = install(triple, Path(__file__).resolve().parent / "binaries", fetch)
    print(f"Staged pinned herdr {HERDR_VERSION}: {target.name}")


if __name__ == "__main__":
    main()

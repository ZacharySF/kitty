"""Build the module packs of a release from the web build, and their index.

`web/dist/pack-files.json` (web/vite.config.ts) lists each pack's files under the website. For
each pack this writes `rika-pack-<id>-<version>.tar.gz` into --out (a `pack.json` naming the pack
and version, then its files under `web/`; byte-for-byte reproducible), removes those files from
the website so the base install no longer carries them, and writes the index `packs.json` with
each archive's size, SHA-256 and unpacked size (server/src/packs.rs reads it). The release
workflow signs the index with the updater key (`tauri signer sign`, `packs.json.sig`) and
scripts/publish-desktop-release.py uploads all of them with the installers.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = "packs.json"
FILES = "pack-files.json"


def archive_name(pack: str, version: str) -> str:
    return f"rika-pack-{pack}-{version}.tar.gz"


def _member(name: str, data: bytes) -> tuple[tarfile.TarInfo, io.BytesIO]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info, io.BytesIO(data)


def build(dist: Path, out: Path, version: str, *, strip: bool = True) -> dict:
    """Write every pack listed by `dist`'s pack-files.json into `out`; return the index."""
    listed = json.loads((dist / FILES).read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for pack in sorted(listed):
        files = sorted(listed[pack])
        missing = [name for name in files if not (dist / name).is_file()]
        if missing or not files:
            raise ValueError(f"pack {pack}: missing from {dist} ({', '.join(missing) or 'no files'}); rebuild the website")
        manifest = json.dumps({"id": pack, "version": version}, indent=2).encode() + b"\n"
        members = [("pack.json", manifest), *((f"web/{name}", (dist / name).read_bytes()) for name in files)]
        buffer = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as tar:
                for name, data in members:
                    tar.addfile(*_member(name, data))
        data = buffer.getvalue()
        target = out / archive_name(pack, version)
        target.write_bytes(data)
        entries.append({
            "id": pack, "version": version, "file": target.name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "installed_size": sum(len(content) for _, content in members),
        })
        if strip:
            for name in files:
                (dist / name).unlink()
    index = {"format": 1, "version": version, "packs": entries}
    (out / INDEX).write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "web/dist")
    parser.add_argument("--out", type=Path, default=ROOT / "desktop/.build/packs")
    parser.add_argument("--version", help="defaults to desktop/tauri.conf.json's version")
    parser.add_argument("--keep", action="store_true", help="leave the packs' files in the website")
    args = parser.parse_args()
    version = args.version or json.loads((ROOT / "desktop/tauri.conf.json").read_text())["version"]
    index = build(args.dist, args.out, version, strip=not args.keep)
    for entry in index["packs"]:
        print(f"{entry['file']}: {entry['size']} bytes ({entry['installed_size']} unpacked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate version/tag before building, then signed release assets before publication.

Tauri's official action generates latest.json; this guard never rewrites that feed. The module
packs' index must verify against the updater key the app embeds, as rika-server checks it
(server/src/packs.rs), and list exactly the uploaded archives.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path


def verify_minisign(data: bytes, signature: str, public_key: str, file: str) -> None:
    """A `tauri signer sign` signature (base64 minisign, prehashed) of `file` by `public_key`, in
    tauri.conf.json's form; its trusted comment must name the file."""
    # The project environment's; the version check before the build needs no dependencies.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = base64.b64decode(base64.b64decode(public_key).decode().splitlines()[1])
        lines = base64.b64decode(signature).decode().splitlines()
        signed, overall = base64.b64decode(lines[1]), base64.b64decode(lines[3])
        comment = lines[2].removeprefix("trusted comment: ")
        if key[:2] != b"Ed" or signed[:2] != b"ED" or signed[2:10] != key[2:10]:
            raise ValueError("another key or algorithm")
        verifier = Ed25519PublicKey.from_public_bytes(key[10:42])
        verifier.verify(signed[10:], hashlib.blake2b(data, digest_size=64).digest())
        verifier.verify(overall, signed[10:] + comment.encode())
    except (ValueError, IndexError, UnicodeDecodeError, InvalidSignature) as error:
        raise ValueError(f"{file} is not signed by the updater key") from error
    if f"file:{file}" not in comment.split("\t"):
        raise ValueError(f"{file}'s signature names another file")


def validate_packs(config: dict, assets: Path, release: dict) -> None:
    """packs.json verifies against the embedded key, and every archive it lists was uploaded whole."""
    uploaded = {asset["name"]: asset for asset in release["assets"]}
    data = (assets / "packs.json").read_bytes()
    verify_minisign(data, (assets / "packs.json.sig").read_text(), config["plugins"]["updater"]["pubkey"], "packs.json")
    index = json.loads(data)
    if index["format"] != 1 or index["version"] != config["version"] or not index["packs"]:
        raise ValueError("the pack index must be format 1 for this version and list its packs")
    for entry in index["packs"]:
        local = assets / entry["file"]
        content = local.read_bytes() if local.is_file() else b""
        asset = uploaded.get(entry["file"])
        if (entry["version"] != config["version"] or len(content) != entry["size"] or asset is None
                or asset["size"] != entry["size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]):
            raise ValueError(f"missing, truncated or altered pack: {entry['file']}")


def validate(config_path: Path, tag: str, assets: Path | None, release_path: Path | None) -> None:
    config = json.loads(config_path.read_text())
    version = config["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or tag != f"v{version}":
        raise ValueError(f"tag {tag!r} must match stable application version v{version}")
    if assets is None and release_path is None:
        return
    if assets is None or release_path is None:
        raise ValueError("--assets and --release are required together")
    metadata = json.loads((assets / "latest.json").read_text())
    release = json.loads(release_path.read_text())
    if metadata["version"] != version or release["tag_name"] != tag:
        raise ValueError("configuration, updater version and release tag must agree")
    for extension, installer in (("AppImage", "appimage"), ("deb", "deb")):
        matching = [asset for asset in release["assets"]
                    if re.fullmatch(rf"kitty_{re.escape(version)}_(?:amd64|x86_64)\.{extension}", asset["name"])]
        if len(matching) != 1:
            raise ValueError(f"expected exactly one versioned x86_64 .{extension} asset")
        asset = matching[0]
        local = assets / asset["name"]
        if not local.is_file() or local.stat().st_size == 0 or local.stat().st_size != asset["size"]:
            raise ValueError(f"missing, empty or truncated asset: {asset['name']}")
        signature = (assets / (asset["name"] + ".sig")).read_text()
        target = f"linux-x86_64-{installer}"
        platform = metadata["platforms"][target]
        if not signature.strip() or platform["signature"] != signature:
            raise ValueError(f"signature missing or mismatched for {target}")
        allowed = {asset["url"], asset["browser_download_url"]}
        if platform["url"] not in allowed or not platform["url"].startswith("https://"):
            raise ValueError(f"updater URL does not point at the published {target} asset")
    validate_packs(config, assets, release)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("desktop/tauri.conf.json"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--release", type=Path)
    args = parser.parse_args()
    try:
        validate(args.config, args.tag, args.assets, args.release)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Invalid desktop release: {error}", file=sys.stderr)
        return 1
    print(f"Validated desktop release {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

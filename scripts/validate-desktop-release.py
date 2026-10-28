#!/usr/bin/env python3
"""Validate version/tag before building, then signed release assets before publication.

Tauri's official action generates latest.json; this guard never rewrites that feed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def validate(config: Path, tag: str, assets: Path | None, release_path: Path | None) -> None:
    version = json.loads(config.read_text())["version"]
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

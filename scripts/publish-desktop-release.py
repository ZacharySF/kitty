"""Publish signed desktop bundles to the public draft release and write its updater feed.

tauri-action reported large uploads as successful while none reached the release (v0.3.31), so
the workflow builds with tauri-action and publishes here: it stages the installers under the
pinned asset names, uploads them with gh, re-reads the release and refuses a missing or
truncated asset before writing latest.json against the uploaded files.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = "ZacharySF/kitty-releases"
#: (installer folder and extension, updater targets it serves)
INSTALLERS = (("appimage", "AppImage", ("linux-x86_64", "linux-x86_64-appimage")), ("deb", "deb", ("linux-x86_64-deb",)))


def asset_name(version: str, extension: str) -> str:
    return f"kitty_{version}_amd64.{extension}"


def stage(bundle: Path, version: str, out: Path) -> list[Path]:
    """Copies each installer and its signature into `out` under the pinned names."""
    out.mkdir(parents=True, exist_ok=True)
    staged = []
    for folder, extension, _ in INSTALLERS:
        found = sorted((bundle / folder).glob(f"*_{version}_amd64.{extension}"))
        if len(found) != 1:
            raise ValueError(f"expected exactly one {version} .{extension} bundle, found {len(found)}")
        signature = found[0].with_name(found[0].name + ".sig")
        if not signature.is_file() or not signature.read_text().strip():
            raise ValueError(f"missing signature for the .{extension} bundle")
        target = out / asset_name(version, extension)
        shutil.copyfile(found[0], target)
        shutil.copyfile(signature, out / (target.name + ".sig"))
        staged += [target, out / (target.name + ".sig")]
    return staged


def updater_manifest(version: str, notes: str, pub_date: str, release: dict, out: Path) -> dict:
    """latest.json for the uploaded installers; any missing or truncated upload is an error."""
    by_name = {asset["name"]: asset for asset in release["assets"]}
    platforms = {}
    for _, extension, targets in INSTALLERS:
        name = asset_name(version, extension)
        local = out / name
        asset = by_name.get(name)
        if asset is None or asset["size"] != local.stat().st_size:
            raise ValueError(f"{name} did not upload completely")
        signature = (out / (name + ".sig")).read_text()
        for target in targets:
            platforms[target] = {"signature": signature, "url": asset["browser_download_url"]}
    return {"version": version, "notes": notes, "pub_date": pub_date, "platforms": platforms}


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def release_json(tag: str) -> dict:
    # Draft releases are not addressable by tag; select from the authenticated list.
    for _ in range(10):
        releases = json.loads(gh("api", f"repos/{REPO}/releases"))
        found = [release for release in releases if release["tag_name"] == tag]
        if found:
            return found[0]
        time.sleep(3)
    raise ValueError(f"no release {tag} in {REPO}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--bundle", type=Path, default=Path("desktop/target/release/bundle"))
    parser.add_argument("--out", type=Path, default=Path("release-upload"))
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    version = args.tag.removeprefix("v")
    staged = stage(args.bundle, version, args.out)
    existing = [release for release in json.loads(gh("api", f"repos/{REPO}/releases")) if release["tag_name"] == args.tag]
    if not existing:
        gh("release", "create", args.tag, "--repo", REPO, "--draft", "--target", "main",
           "--title", args.title, "--notes-file", str(args.notes))
    gh("release", "upload", args.tag, "--repo", REPO, "--clobber", *map(str, staged))
    notes = args.notes.read_text().strip()
    pub_date = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest = updater_manifest(version, notes, pub_date, release_json(args.tag), args.out)
    (args.out / "latest.json").write_text(json.dumps(manifest, indent=2))
    gh("release", "upload", args.tag, "--repo", REPO, "--clobber", str(args.out / "latest.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

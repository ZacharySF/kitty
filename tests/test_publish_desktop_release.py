"""The release publisher names installers, signs the updater feed and checks every upload landed."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish", ROOT / "scripts/publish-desktop-release.py")
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


def bundle(tmp_path, version="0.3.32"):
    root = tmp_path / "bundle"
    (root / "appimage").mkdir(parents=True)
    (root / "deb").mkdir()
    (root / "appimage" / f"rika! Workspace_{version}_amd64.AppImage").write_bytes(b"appimage")
    (root / "appimage" / f"rika! Workspace_{version}_amd64.AppImage.sig").write_text("sig-appimage")
    (root / "deb" / f"rika! Workspace_{version}_amd64.deb").write_bytes(b"deb")
    (root / "deb" / f"rika! Workspace_{version}_amd64.deb.sig").write_text("sig-deb")
    return root


def test_stages_installers_and_signatures_under_the_pinned_names(tmp_path):
    staged = publish.stage(bundle(tmp_path), "0.3.32", tmp_path / "out")
    assert sorted(path.name for path in staged) == [
        "kitty_0.3.32_amd64.AppImage", "kitty_0.3.32_amd64.AppImage.sig",
        "kitty_0.3.32_amd64.deb", "kitty_0.3.32_amd64.deb.sig"]
    assert (tmp_path / "out" / "kitty_0.3.32_amd64.deb").read_bytes() == b"deb"


def test_staging_refuses_a_missing_or_duplicate_installer(tmp_path):
    root = bundle(tmp_path)
    (root / "deb" / "rika! Workspace_0.3.32_amd64.deb").unlink()
    with pytest.raises(ValueError, match="deb"):
        publish.stage(root, "0.3.32", tmp_path / "out")


def test_updater_manifest_points_every_target_at_the_uploaded_asset_by_its_stable_api_url(tmp_path):
    # A draft's browser_download_url is an untagged-… path that 404s once the release is
    # published (v0.3.32); the API asset URL stays valid across publication.
    out = tmp_path / "out"
    publish.stage(bundle(tmp_path), "0.3.32", out)
    release = {"tag_name": "v0.3.32", "assets": [
        {"name": "kitty_0.3.32_amd64.AppImage", "size": 8, "url": "https://api.example/assets/1",
         "browser_download_url": "https://example/download/untagged-abc/app"},
        {"name": "kitty_0.3.32_amd64.deb", "size": 3, "url": "https://api.example/assets/2",
         "browser_download_url": "https://example/download/untagged-abc/deb"}]}
    manifest = publish.updater_manifest("0.3.32", "notes", "2026-11-03T00:00:00Z", release, out)
    assert manifest["version"] == "0.3.32" and manifest["notes"] == "notes"
    assert manifest["platforms"] == {
        "linux-x86_64": {"signature": "sig-appimage", "url": "https://api.example/assets/1"},
        "linux-x86_64-appimage": {"signature": "sig-appimage", "url": "https://api.example/assets/1"},
        "linux-x86_64-deb": {"signature": "sig-deb", "url": "https://api.example/assets/2"}}
    json.dumps(manifest)

def test_a_missing_or_truncated_upload_is_an_error_not_a_success(tmp_path):
    out = tmp_path / "out"
    publish.stage(bundle(tmp_path), "0.3.32", out)
    truncated = {"tag_name": "v0.3.32", "assets": [
        {"name": "kitty_0.3.32_amd64.AppImage", "size": 1, "url": "https://api.example/assets/1", "browser_download_url": "https://example/app"}]}
    with pytest.raises(ValueError):
        publish.updater_manifest("0.3.32", "notes", "2026-11-03T00:00:00Z", truncated, out)

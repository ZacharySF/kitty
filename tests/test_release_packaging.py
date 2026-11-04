"""Exercise the release guard against real asset and metadata files, without GitHub."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release(tmp_path):
    config = tmp_path / "tauri.conf.json"
    config.write_text(json.dumps({"version": "0.1.1"}))
    assets = tmp_path / "assets"
    assets.mkdir()
    entries = []
    platforms = {}
    for number, (extension, installer) in enumerate((("AppImage", "appimage"), ("deb", "deb")), 1):
        name = f"kitty_0.1.1_amd64.{extension}"
        (assets / name).write_bytes(b"real fixture artifact")
        signature = f"signed fixture {installer}\n"
        (assets / (name + ".sig")).write_text(signature)
        url = f"https://api.github.com/repos/ZacharySF/kitty/releases/assets/{number}"
        entries.append({"id": number, "name": name, "size": 21, "url": url,
                        "browser_download_url": f"https://github.com/ZacharySF/kitty/releases/download/v0.1.1/{name}"})
        platforms[f"linux-x86_64-{installer}"] = {"url": url, "signature": signature}
    metadata = {"version": "0.1.1", "platforms": platforms}
    release_data = {"tag_name": "v0.1.1", "draft": True, "assets": entries}
    return config, assets, metadata, release_data


def run_guard(release):
    config, assets, metadata, release_data = release
    metadata_path = assets / "latest.json"
    metadata_path.write_text(json.dumps(metadata))
    release_path = assets.parent / "release.json"
    release_path.write_text(json.dumps(release_data))
    return subprocess.run([sys.executable, str(ROOT / "scripts/validate-desktop-release.py"),
                           "--config", str(config), "--tag", "v0.1.1", "--assets", str(assets),
                           "--release", str(release_path)], capture_output=True, text=True)


def test_valid_signed_installer_specific_release(release):
    result = run_guard(release)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fault", ["version", "tag", "target", "signature", "missing", "url", "empty", "asset-version"])
def test_reject_incomplete_or_mismatched_release(release, fault):
    config, assets, metadata, remote = release
    if fault == "version":
        metadata["version"] = "0.1.0"
    elif fault == "tag":
        remote["tag_name"] = "v9.0.0"
    elif fault == "target":
        metadata["platforms"].pop("linux-x86_64-deb")
    elif fault == "signature":
        metadata["platforms"]["linux-x86_64-deb"]["signature"] = "wrong signature"
    elif fault == "missing":
        (assets / "kitty_0.1.1_amd64.AppImage.sig").unlink()
    elif fault == "url":
        metadata["platforms"]["linux-x86_64-deb"]["url"] = "https://example.com/untrusted.deb"
    elif fault == "empty":
        (assets / "kitty_0.1.1_amd64.deb").write_bytes(b"")
    elif fault == "asset-version":
        remote["assets"][0]["name"] = "kitty_0.1.0_amd64.AppImage"
    result = run_guard(release)
    assert result.returncode == 1, result.stderr
    assert "Invalid desktop release:" in result.stderr


def test_rika_product_name_keeps_updater_and_package_identity():
    """The user-visible name is rika!; everything an installed app updates through stays kitty."""
    config = json.loads((ROOT / "desktop/tauri.conf.json").read_text())
    workflow = (ROOT / ".github/workflows/desktop-release.yml").read_text()
    assert config["productName"] == "rika! Workspace"
    assert config["identifier"] == "com.zacharysf.kitty"
    assert config["mainBinaryName"] == "kitty-workspace"
    assert config["plugins"]["updater"]["endpoints"] == [
        "https://github.com/ZacharySF/kitty-releases/releases/latest/download/latest.json"]
    # Asset names (parsed by the release guard) stay pinned, independent of productName: the
    # publish step uploads under kitty_<version>_amd64.* (tests/test_publish_desktop_release.py).
    assert "scripts/publish-desktop-release.py" in workflow
    assert '--title "rika! $RELEASE_TAG (Linux x86_64)"' in workflow
    # Tauri names the .deb package (kebab-case productName) and its /usr/lib folder after productName;
    # the new package takes over the old kitty-workspace package so dpkg -i updates in place.
    assert '= rika-workspace' in workflow
    deb = config["bundle"]["linux"]["deb"]
    assert "kitty-workspace" in deb["conflicts"] and "kitty-workspace" in deb["replaces"]
    assert "usr/lib/rika!\\ Workspace/licenses" in workflow and "kitty!" not in workflow

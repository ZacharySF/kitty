"""The desktop bundle's herdr binary is the pinned release, verified by SHA-256."""
from __future__ import annotations

import hashlib
import runpy
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fetcher(monkeypatch):
    module = runpy.run_path(str(ROOT / "desktop" / "fetch_herdr.py"))
    pins = module["PINS"]
    pins.clear()
    pins["test-linux-gnu"] = ("herdr-test", hashlib.sha256(b"pinned herdr").hexdigest())
    return module


def test_verified_release_binary_is_installed_executable(fetcher, tmp_path):
    requested = []
    target = fetcher["install"]("test-linux-gnu", tmp_path, lambda url: requested.append(url) or b"pinned herdr")
    assert target == tmp_path / "kitty-herdr-test-linux-gnu"
    assert target.read_bytes() == b"pinned herdr"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert requested == ["https://github.com/herdrdev/herdr/releases/download/v0.9.5/herdr-test"]

    def offline(url):
        raise AssertionError("an already verified binary is not downloaded again")

    assert fetcher["install"]("test-linux-gnu", tmp_path, offline) == target


def test_checksum_mismatch_is_refused_without_leaving_a_binary(fetcher, tmp_path):
    with pytest.raises(SystemExit, match="SHA-256"):
        fetcher["install"]("test-linux-gnu", tmp_path, lambda url: b"tampered herdr")
    assert list(tmp_path.iterdir()) == []


def test_a_modified_existing_binary_is_replaced_by_the_pinned_release(fetcher, tmp_path):
    stale = tmp_path / "kitty-herdr-test-linux-gnu"
    stale.write_bytes(b"locally modified")
    fetcher["install"]("test-linux-gnu", tmp_path, lambda url: b"pinned herdr")
    assert stale.read_bytes() == b"pinned herdr"


def test_targets_without_a_pinned_release_are_refused(fetcher, tmp_path):
    with pytest.raises(SystemExit, match="no pinned herdr"):
        fetcher["install"]("riscv64-unknown-linux-gnu", tmp_path, lambda url: b"pinned herdr")

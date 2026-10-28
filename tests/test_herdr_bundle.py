"""The desktop bundle's herdr binary is the pinned release, verified by SHA-256."""
from __future__ import annotations

import hashlib
import os
import runpy
import shutil
import stat
import struct
import subprocess
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


PATCHELF = ROOT / "desktop" / "appimage_patchelf.py"


def elf_with_segments(path: Path, *types: int) -> bytes:
    """A little-endian ELF64 executable carrying only the given program headers."""
    header = b"\x7fELF" + bytes([2, 1, 1]) + bytes(9) + struct.pack(
        "<HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0, 64, 56, len(types), 64, 0, 0)
    data = header + b"".join(struct.pack("<IIQQQQQQ", kind, 4, 0, 0, 0, 0, 0, 8) for kind in types)
    path.write_bytes(data)
    return data


def test_appimage_patchelf_leaves_static_pie_executables_byte_identical(tmp_path):
    # linuxdeploy gives every usr/bin ELF an $ORIGIN rpath; patchelf's rewrite crashes
    # a static-pie binary (PT_DYNAMIC but no PT_INTERP) such as the pinned herdr.
    herdr = tmp_path / "kitty-herdr"
    original = elf_with_segments(herdr, 1, 2)
    env = dict(os.environ, KITTY_PATCHELF=str(tmp_path / "absent-patchelf"))
    result = subprocess.run([str(PATCHELF), "--set-rpath", "$ORIGIN/../lib", str(herdr)],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert herdr.read_bytes() == original


def test_appimage_patchelf_still_sets_rpath_on_dynamically_loaded_executables(tmp_path):
    real = shutil.which("patchelf")
    if real is None:
        pytest.skip("patchelf is required to patch a dynamically loaded executable")
    executable = tmp_path / "kitty-workspace"
    shutil.copyfile(Path(real).resolve(), executable)
    executable.chmod(0o755)
    env = dict(os.environ, KITTY_PATCHELF=real)
    subprocess.run([str(PATCHELF), "--set-rpath", "$ORIGIN/../lib", str(executable)], env=env, check=True)
    printed = subprocess.run([str(PATCHELF), "--print-rpath", str(executable)], env=env,
                             check=True, capture_output=True, text=True)
    assert printed.stdout.strip() == "$ORIGIN/../lib"

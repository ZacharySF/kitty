#!/usr/bin/env python3
"""patchelf for Tauri's AppImage step that never rewrites executables without a loader.

linuxdeploy sets an ``$ORIGIN/../lib`` rpath on every ELF file in ``usr/bin`` and treats
static-pie executables (PT_DYNAMIC without PT_INTERP), such as the pinned herdr, as
dynamically linked. patchelf's rewrite makes those crash at startup. Only a dynamic
loader reads an rpath, so files without PT_INTERP are left byte-identical; every other
call goes to the real patchelf ($KITTY_PATCHELF, else ``patchelf`` on PATH).
linuxdeploy runs the program named by $PATCHELF.
"""
from __future__ import annotations

import os
import shutil
import struct
import sys

PT_INTERP = 3


def has_loader(path: str) -> bool:
    """Whether `path` is an ELF file naming a dynamic loader; non-ELF input counts as yes."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"\x7fELF" or data[4] not in (1, 2) or data[5] not in (1, 2):
        return True
    order = "<" if data[5] == 1 else ">"
    if data[4] == 2:
        offset, = struct.unpack_from(order + "Q", data, 0x20)
        size, count = struct.unpack_from(order + "HH", data, 0x36)
    else:
        offset, = struct.unpack_from(order + "I", data, 0x1C)
        size, count = struct.unpack_from(order + "HH", data, 0x2A)
    return any(struct.unpack_from(order + "I", data, offset + index * size)[0] == PT_INTERP
               for index in range(count))


def main(arguments: list[str]) -> int:
    if len(arguments) == 3 and arguments[0] == "--set-rpath" and not has_loader(arguments[2]):
        return 0
    real = os.environ.get("KITTY_PATCHELF") or shutil.which("patchelf")
    if real is None:
        print("appimage_patchelf: patchelf is required", file=sys.stderr)
        return 1
    os.execv(real, [real, *arguments])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

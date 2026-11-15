"""Unpack a module tarball so nothing can land, or point, outside its folder.

Every member is checked before anything is written: no absolute or ``..`` names, no
devices or FIFOs, links only to targets inside the archive, bounded size and count.
Extraction then uses Python's ``data`` filter as a second line of defence (it also
drops setuid/setgid bits and foreign ownership).
"""
from __future__ import annotations

import posixpath
import shutil
import tarfile
from pathlib import Path

MAX_MEMBERS = 50_000
MAX_TOTAL_BYTES = 2 * 1024 ** 3


class ArchiveError(ValueError):
    """The tarball is unsafe or unreadable; the message says why in plain words."""


def _inside(name: str) -> str | None:
    """`name` normalised inside the archive root, or None when it would leave it."""
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return None
    normal = posixpath.normpath(name)
    if normal == "..":
        return None
    if normal.startswith("../") or any(part == ".." for part in name.split("/")):
        return None
    return normal


def _check(members: list[tarfile.TarInfo]) -> None:
    if len(members) > MAX_MEMBERS:
        raise ArchiveError(f"The module archive has more than {MAX_MEMBERS} files")
    total = 0
    for member in members:
        name = _inside(member.name)
        if name is None:
            raise ArchiveError(f"The module archive contains {member.name!r}, which points outside the module folder")
        if member.isdev() or member.isfifo() or not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ArchiveError(f"The module archive contains {member.name!r}, which is not a regular file or folder")
        if member.issym() and (member.linkname.startswith("/")
                               or _inside(posixpath.join(posixpath.dirname(name), member.linkname)) is None):
            raise ArchiveError(f"The module archive links {member.name!r} outside the module folder")
        if member.islnk() and _inside(member.linkname) is None:
            raise ArchiveError(f"The module archive links {member.name!r} outside the module folder")
        total += max(member.size, 0)
        if total > MAX_TOTAL_BYTES:
            raise ArchiveError("The module archive is too large to install")


def extract(archive: Path, target: Path) -> None:
    """Unpack the gzip tarball `archive` into the new folder `target` (removed again on failure)."""
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            _check(members)
            target.mkdir(parents=True, mode=0o700)
            try:
                tar.extractall(target, filter="data")
            except BaseException:
                shutil.rmtree(target, ignore_errors=True)
                raise
    except ArchiveError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveError(f"The module archive could not be unpacked ({type(exc).__name__})") from None

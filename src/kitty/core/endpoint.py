"""Where a running kitty! listens, for paired outside programs (platform design Q7).

While the server runs, ``<workspace>/.private/endpoint.json`` holds its current origin
and the pairing path, nothing else (never a token). The desktop sidecar binds a random
port each start, so the kitty Tern plugin and other local programs read this file to
find it, then pair through ``PAIRING_PATH``.

The file is owner-only (0600) inside an owner-only ``.private`` (0700). It is written
under a temporary name and renamed into place, so a reader never sees a partial file;
every step goes through the directory's descriptor and never follows a symlink. On
shutdown the file is removed unless another server has replaced it since.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

NAME = "endpoint.json"
PAIRING_PATH = "/api/ingress/pair"


class EndpointError(OSError):
    """``.private`` is not a directory this user owns."""


def _private_dir(root: Path) -> int:
    """A descriptor for ``root/.private``, created or tightened to 0700; symlinks are refused."""
    private = root / ".private"
    try:
        private.mkdir(mode=0o700, exist_ok=True)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise EndpointError(f"{private} is not a private directory") from exc
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        raise EndpointError(f"{private} is not a directory owned by this user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.fchmod(descriptor, 0o700)
    return descriptor


def _content(origin: str) -> bytes:
    return (json.dumps({"origin": origin, "pairing": PAIRING_PATH}) + "\n").encode()


def write(root: Path, origin: str) -> Path:
    """Atomically publish `origin` as this workspace's endpoint."""
    directory = _private_dir(root)
    temporary = f".{NAME}.{secrets.token_hex(8)}"
    try:
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                         dir_fd=directory)
        try:
            os.fchmod(handle, 0o600)
            os.write(handle, _content(origin))
            os.fsync(handle)
        finally:
            os.close(handle)
        os.replace(temporary, NAME, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(directory)
    return root / ".private" / NAME


def _read_bytes(directory: int) -> bytes | None:
    try:
        handle = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    except OSError:
        return None
    try:
        return os.read(handle, 4096)
    finally:
        os.close(handle)


def read(root: Path) -> dict | None:
    """The published endpoint, or None when no server has published one."""
    try:
        directory = _private_dir(root)
    except EndpointError:
        return None
    try:
        data = _read_bytes(directory)
    finally:
        os.close(directory)
    try:
        return json.loads(data) if data else None
    except ValueError:
        return None


def remove(root: Path, origin: str) -> None:
    """Remove the endpoint file if it still names `origin`."""
    try:
        directory = _private_dir(root)
    except EndpointError:
        return
    try:
        if _read_bytes(directory) == _content(origin):
            os.unlink(NAME, dir_fd=directory)
    except FileNotFoundError:
        pass
    finally:
        os.close(directory)


@contextmanager
def published(root: Path, origin: str) -> Iterator[Path]:
    """Publish `origin` for the lifetime of the block."""
    path = write(root, origin)
    try:
        yield path
    finally:
        remove(root, origin)

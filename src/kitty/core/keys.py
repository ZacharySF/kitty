"""kitty Keys (platform design Q10): the one place named secrets are read and stored.

A key has a NAME (``OPENROUTER``, ``ANTHROPIC``, ``USAJOBS``, ``BRAVE`` or any
user-defined ``[A-Z][A-Z0-9_]*``). Reading tries, in order:

1. the OS keyring through the freedesktop Secret Service D-Bus API (gnome-keyring,
   KeePassXC), when one answers on the session bus;
2. an owner-only file under the workspace's ``.private/`` directory (OpenRouter keeps
   its existing ``.private/openrouter-key``; others live in ``.private/keys/NAME``);
3. the key's environment variable (``.env`` is loaded into the environment at start),
   except for employer account passwords (``APPLY_PW_*``), which are never read from
   or injected into an environment.

Writing goes to the keyring when one exists, otherwise to the owner-only file.

Values never appear in a return value meant for display, in an exception message or
in a log line: callers get status (set or unset and where it is stored) except from
:func:`read`, whose result must go only to the service that needs it.

``KITTY_KEYRING=none`` turns the keyring off for this process (tests set it so a test
never touches the real keyring). ``KITTY_KEYRING_SERVICE`` overrides the Secret
Service ``service`` attribute kitty files its entries under (default ``kitty``).

macOS: automic-vault deliberately never returns a secret to an app, so it cannot be a
backend here. It works as a launch wrapper instead: run kitty (or ``kitty keys run``'s
child) under automic-vault and the variables it injects reach kitty as environment
variables, the third source above.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kitty.settings import Settings

NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
#: A value is one line of printable ASCII (API keys, tokens), at most 4 KiB.
VALUE_RE = re.compile(r"[\x21-\x7e]{1,4096}")
DEFAULT_SERVICE = "kitty"
REDACTED = "[redacted]"
#: Generated per-tenant employer account passwords (kitty.core.apply.accounts).
ACCOUNT_PREFIX = "APPLY_PW_"
ACCOUNT_FILE_WARNING = (
    "No OS keyring is available, so employer account passwords are stored in "
    "owner-only files under .private/keys/. Unlock or install a keyring and move them there."
)


@dataclass(frozen=True)
class KeySpec:
    name: str
    label: str
    env: str
    #: File name under .private/ (None: .private/keys/NAME).
    legacy_file: str | None = None


KNOWN: dict[str, KeySpec] = {spec.name: spec for spec in (
    KeySpec("OPENROUTER", "OpenRouter API key", "OPENROUTER_API_KEY", "openrouter-key"),
    KeySpec("ANTHROPIC", "Anthropic API key", "ANTHROPIC_API_KEY"),
    KeySpec("USAJOBS", "USAJOBS API key", "USAJOBS_API_KEY"),
    KeySpec("BRAVE", "Brave Search API key", "BRAVE_SEARCH_API_KEY"),
)}


class KeysError(Exception):
    """A key operation failed; the message never contains a key value."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class Keyring(Protocol):
    """A secret store keyed by name; implementations must never log values."""

    label: str

    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> bool: ...
    def names(self) -> set[str]: ...
    def locked(self) -> bool: ...


class SecretServiceKeyring:
    """The freedesktop Secret Service via the ``keyring`` package's SecretService backend.

    Only items whose ``service`` attribute is ours are ever searched, read or deleted;
    the owner's other keyring entries are never listed.
    """

    label = "OS keyring (Secret Service)"

    def __init__(self, service: str = DEFAULT_SERVICE) -> None:
        from keyring.backends.SecretService import Keyring as Backend

        Backend.priority  # noqa: B018 - raises RuntimeError when no Secret Service answers
        self.service = service
        self._backend = Backend()

    def get(self, name: str) -> str | None:
        return self._backend.get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        self._backend.set_password(self.service, name, value)

    def delete(self, name: str) -> bool:
        from keyring.errors import PasswordDeleteError

        try:
            self._backend.delete_password(self.service, name)
        except PasswordDeleteError:
            return False
        return True

    def names(self) -> set[str]:
        from contextlib import closing

        collection = self._backend.get_preferred_collection()
        with closing(collection.connection):
            return {item.get_attributes().get("username", "") for item in collection.search_items({"service": self.service})}

    def locked(self) -> bool:
        """Whether the collection is locked, asked without the unlock prompt reads raise."""
        from contextlib import closing

        import secretstorage

        with closing(secretstorage.dbus_init()) as connection:
            path = getattr(self._backend, "preferred_collection", None)
            collection = (secretstorage.Collection(connection, path) if path
                          else secretstorage.get_default_collection(connection))
            return collection.is_locked()


def keyring() -> Keyring | None:
    """The OS keyring for this process, or None when none is reachable or it is turned off."""
    if os.environ.get("KITTY_KEYRING", "").strip().lower() in {"none", "off", "0"}:
        return None
    try:
        return SecretServiceKeyring(os.environ.get("KITTY_KEYRING_SERVICE", "").strip() or DEFAULT_SERVICE)
    except Exception:  # noqa: BLE001 - no D-Bus, no daemon, no keyring package: use the file
        return None


def canonical(name: str) -> str:
    """Upper-case NAME, or KeysError for anything that is not a key name."""
    if not isinstance(name, str):
        raise KeysError("A key name is required")
    upper = name.strip().upper()
    if not NAME_RE.fullmatch(upper):
        raise KeysError("Key names use A-Z, 0-9 and _ and start with a letter")
    return upper


def env_name(name: str) -> str:
    """The environment variable a key is read from and injected as."""
    spec = KNOWN.get(name)
    return spec.env if spec else name


def _check_value(value: object) -> str:
    if not isinstance(value, str):
        raise KeysError("Enter the key as text")
    value = value.strip()
    if not VALUE_RE.fullmatch(value):
        raise KeysError("A key is one line of printable characters without spaces (at most 4096)")
    return value


class Keys:
    """Named keys of one workspace. Construct with :func:`for_settings`."""

    def __init__(self, root: Path | None, *, store: Keyring | None, use_env: bool = True) -> None:
        self.root = root
        self.store = store
        self.use_env = use_env

    # ------------------------------------------------------------ the owner-only file

    def _private(self) -> Path:
        if self.root is None:
            raise KeysError("No workspace for private key files", 409)
        return self.root / ".private"

    def file_path(self, name: str) -> Path:
        spec = KNOWN.get(name)
        private = self._private()
        return private / spec.legacy_file if spec and spec.legacy_file else private / "keys" / name

    def _refuse_links(self, path: Path) -> None:
        private = self._private()
        for part in (private, path.parent, path):
            if part.is_symlink():
                raise KeysError("Private key storage must not be a symbolic link", 409)

    def _read_file(self, name: str) -> str | None:
        if self.root is None:
            return None
        path = self.file_path(name)
        self._refuse_links(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError:
            raise KeysError("Cannot read a private key file", 409) from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise KeysError("A private key path is not a regular file", 409)
            if info.st_mode & 0o077:
                raise KeysError("Private key files must be owner-only (chmod 600)", 409)
            if info.st_uid != os.getuid():
                raise KeysError("Private key files must belong to you", 409)
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(8192)
        finally:
            os.close(fd)
        try:
            value = raw.decode("ascii").strip()
        except UnicodeError:
            raise KeysError("A private key file is not plain text", 409) from None
        return value or None

    def _write_file(self, name: str, value: str) -> None:
        path = self.file_path(name)
        self._refuse_links(path)
        self._private().mkdir(mode=0o700, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        self._refuse_links(path)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".key-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _delete_file(self, name: str) -> bool:
        if self.root is None:
            return False
        path = self.file_path(name)
        self._refuse_links(path)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _file_names(self) -> set[str]:
        if self.root is None:
            return set()
        directory = self._private() / "keys"
        if directory.is_symlink() or not directory.is_dir():
            return set()
        return {path.name for path in directory.iterdir() if NAME_RE.fullmatch(path.name)}

    # ------------------------------------------------------------ the keyring

    def _keyring_get(self, name: str) -> str | None:
        if self.store is None:
            return None
        try:
            value = self.store.get(name)
        except Exception:  # noqa: BLE001 - backend errors may carry details; report none
            raise KeysError("The OS keyring did not answer (is it locked?)", 503) from None
        return value.strip() if value and value.strip() else None

    # ------------------------------------------------------------ public API

    def lookup(self, name: str) -> tuple[str, str] | None:
        """(value, backend) for NAME, trying keyring, file, then environment."""
        name = canonical(name)
        value = self._keyring_get(name)
        if value:
            return value, "keyring"
        value = self._read_file(name)
        if value:
            return value, "file"
        if self.use_env and not name.startswith(ACCOUNT_PREFIX):
            value = os.environ.get(env_name(name), "").strip()
            if value:
                return value, "env"
        return None

    def read(self, name: str) -> str:
        """NAME's value, or "" when unset. Hand the result only to the service that needs it."""
        found = self.lookup(name)
        return found[0] if found else ""

    def backend(self) -> str:
        return "keyring" if self.store is not None else "file"

    def locked(self) -> bool:
        """True when a keyring exists but is locked (or cannot say): background runs
        hand off instead of waiting on an unlock prompt nobody sees."""
        if self.store is None:
            return False
        try:
            return bool(self.store.locked())
        except Exception:  # noqa: BLE001 - an unreadable lock state is not "unlocked"
            return True

    def set(self, name: str, value: object) -> str:
        """Store NAME in the keyring when one exists, else the owner-only file; returns where."""
        name = canonical(name)
        value = _check_value(value)
        if self.store is not None:
            try:
                self.store.set(name, value)
            except Exception:  # noqa: BLE001
                raise KeysError("The OS keyring refused the key (is it locked?)", 503) from None
            self._delete_file(name)  # the keyring copy would shadow a stale file copy
            return "keyring"
        self._write_file(name, value)
        return "file"

    def remove(self, name: str) -> bool:
        """Remove NAME from the keyring and the file; an environment variable stays."""
        name = canonical(name)
        removed = False
        if self.store is not None:
            try:
                removed = self.store.delete(name)
            except Exception:  # noqa: BLE001
                raise KeysError("The OS keyring refused the removal (is it locked?)", 503) from None
        return self._delete_file(name) or removed

    def move_to_keyring(self, name: str) -> None:
        """Move NAME's owner-only file copy into the keyring, verified before the file goes."""
        name = canonical(name)
        if self.store is None:
            raise KeysError("No OS keyring is available", 409)
        value = self._read_file(name)
        if not value:
            raise KeysError("That key has no file copy to move", 404)
        try:
            self.store.set(name, value)
            stored = self.store.get(name)
        except Exception:  # noqa: BLE001
            raise KeysError("The OS keyring refused the key (is it locked?)", 503) from None
        if stored is None or stored.strip() != value:
            raise KeysError("The OS keyring did not keep the key; the file copy stays", 503)
        self._delete_file(name)

    def names(self) -> list[str]:
        """Known names first, then user-defined ones stored anywhere."""
        custom = self._file_names()
        if self.store is not None:
            try:
                custom |= self.store.names()
            except Exception:  # noqa: BLE001, S110 - a locked keyring still lists known names
                pass
        return [*KNOWN, *sorted(name for name in custom if name not in KNOWN and NAME_RE.fullmatch(name))]

    def status(self) -> dict:
        """What the Keys screen and ``kitty keys list`` show: never a value."""
        rows = []
        accounts = {"count": 0, "in_file": 0}
        for name in self.names():
            if name.startswith(ACCOUNT_PREFIX):
                # Employer account passwords are listed per tenant in Settings → Apply
                # → Accounts; here only how many there are and where they live.
                accounts["count"] += 1
                try:
                    accounts["in_file"] += bool(self._read_file(name))
                except KeysError:
                    pass
                continue
            spec = KNOWN.get(name)
            try:
                found = self.lookup(name)
                error = None
            except KeysError as exc:
                found, error = None, str(exc)
            in_file = False
            try:
                in_file = bool(self._read_file(name)) if self.root is not None else False
            except KeysError:
                pass
            rows.append({
                "name": name, "label": spec.label if spec else name, "env": env_name(name),
                "custom": spec is None, "set": found is not None,
                "backend": found[1] if found else None, "in_file": in_file, "error": error,
            })
        return {
            "keys": rows, "keyring": self.store is not None,
            "keyring_label": self.store.label if self.store is not None else None,
            "store": self.backend(),
            "account_passwords": accounts,
            "warnings": [ACCOUNT_FILE_WARNING] if accounts["in_file"] else [],
        }

    def environment(self, names: Iterable[str]) -> dict[str, str]:
        """{ENV_VAR: value} for the named keys; KeysError naming any unset one."""
        result: dict[str, str] = {}
        for raw in names:
            name = canonical(raw)
            if name.startswith(ACCOUNT_PREFIX):
                raise KeysError("Employer account passwords are never injected into other programs", 403)
            value = self.read(name)
            if not value:
                raise KeysError(f"Key {name} is not set (kitty keys set {name})", 404)
            result[env_name(name)] = value
        return result


def for_settings(settings: Settings | None) -> Keys:
    """The workspace's keys. Demo mode uses only its own throwaway files: never the
    OS keyring or the environment."""
    if settings is None:
        return Keys(None, store=None, use_env=True)
    if settings.demo:
        return Keys(settings.root, store=None, use_env=False)
    return Keys(settings.root, store=keyring(), use_env=True)


def read(settings: Settings | None, name: str) -> str:
    """NAME's value or "" (shortcut for consumers)."""
    return for_settings(settings).read(name)


def redact(text: str, *values: str) -> str:
    """`text` with every occurrence of the given secret values replaced."""
    for value in values:
        if value and len(value) >= 4:
            text = text.replace(value, REDACTED)
    return text

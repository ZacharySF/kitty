"""The application-account credentials, read only when login automation needs them.

They live in `.private/application-credentials.env` under the project root, as two
lines: APPLICATION_ACCOUNT_EMAIL=... and APPLICATION_ACCOUNT_PASSWORD=... The file
is never exported to the environment, never interpolated, and must be a regular
file only its owner can read (chmod 600). Errors name the problem, never a value
or a line of the file. `redact` keeps the values out of anything shown or stored.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import stat
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, quote_plus

from applyai.settings import Settings

#: Where the credentials live, relative to the project root.
CREDENTIALS_FILE = Path(".private") / "application-credentials.env"

EMAIL_KEY = "APPLICATION_ACCOUNT_EMAIL"
PASSWORD_KEY = "APPLICATION_ACCOUNT_PASSWORD"

#: Stands in for a credential in redacted text.
REDACTED = "[redacted]"

_MAX_BYTES = 4096
_BINDING_KEY = secrets.token_bytes(32)


class CredentialError(RuntimeError):
    """A fixed, value-free explanation of why the credentials cannot be used."""


@dataclass(frozen=True)
class ApplicationCredentials:
    """The one email/password pair used for employer application accounts."""

    email: str = field(repr=False)
    password: str = field(repr=False)

    def redact(self, text: str) -> str:
        return redact(text, (self.email, self.password))


def credential_binding(credentials: ApplicationCredentials) -> str:
    """Process-local identity token for reviewed runs, never persisted or exposed publicly."""
    payload = json.dumps([credentials.email, credentials.password], separators=(",", ":")).encode()
    return hmac.new(_BINDING_KEY, payload, hashlib.sha256).hexdigest()


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace each secret, raw or in common URL / JSON / HTML encodings, by [redacted]."""
    forms: set[str] = set()
    for secret in secrets:
        if not secret:
            continue
        forms.update({
            secret, quote(secret, safe=""), quote_plus(secret, safe=""),
            json.dumps(secret, ensure_ascii=False)[1:-1], json.dumps(secret)[1:-1],
            html.escape(secret, quote=True),
        })
    for form in sorted(forms, key=len, reverse=True):
        if form:
            text = text.replace(form, REDACTED)
    return text


def load_credentials(settings: Settings) -> ApplicationCredentials:
    """Read and validate the credentials file; raise CredentialError on any problem."""
    path = settings.root / CREDENTIALS_FILE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        raise CredentialError(f"login: credentials file {CREDENTIALS_FILE} is missing") from None
    except OSError:
        raise CredentialError(
            f"login: cannot open {CREDENTIALS_FILE} (a symlink or unreadable file is refused)"
        ) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CredentialError(f"login: {CREDENTIALS_FILE} must be a regular file")
        if info.st_uid != os.geteuid():
            raise CredentialError(f"login: {CREDENTIALS_FILE} must be owned by you")
        if info.st_mode & 0o077:
            raise CredentialError(
                f"login: {CREDENTIALS_FILE} is readable by other users; run chmod 600 on it"
            )
        if info.st_size > _MAX_BYTES:
            raise CredentialError(f"login: {CREDENTIALS_FILE} is too large to be a credentials file")
        raw = os.read(fd, _MAX_BYTES + 1)
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError(f"login: {CREDENTIALS_FILE} is not UTF-8 text") from None
    values = _parse(text)
    email, password = values.get(EMAIL_KEY, ""), values.get(PASSWORD_KEY, "")
    if not email or not password:
        raise CredentialError(f"login: {CREDENTIALS_FILE} needs both {EMAIL_KEY} and {PASSWORD_KEY}")
    local, at, domain = email.partition("@")
    if not (local and at and "." in domain and "@" not in domain) or any(c.isspace() for c in email):
        raise CredentialError(f"login: {EMAIL_KEY} in {CREDENTIALS_FILE} is not an email address")
    return ApplicationCredentials(email=email, password=password)


def _parse(text: str) -> dict[str, str]:
    """KEY=value lines; blank lines and # comments skipped; optional quotes, no escapes."""
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        key, eq, value = stripped.partition("=")
        key = key.strip()
        if not eq or key not in (EMAIL_KEY, PASSWORD_KEY):
            raise CredentialError(f"login: line {number} of {CREDENTIALS_FILE} is not a known KEY=value line")
        if key in values:
            raise CredentialError(f"login: {key} appears twice in {CREDENTIALS_FILE}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif value[:1] in ("'", '"') or value[-1:] in ("'", '"'):
            raise CredentialError(f"login: line {number} of {CREDENTIALS_FILE} has unbalanced quotes")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise CredentialError(f"login: line {number} of {CREDENTIALS_FILE} has control characters")
        values[key] = value
    return values

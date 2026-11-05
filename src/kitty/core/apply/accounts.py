"""Employer accounts, unattended (proposal-07 Step 4): registry, grants, secrets, caps.

A tenant is one ATS origin (``https://acme.wd5.myworkdayjobs.com``). When the
owner has granted its family (kitty.core.apply.grants; Settings → Apply →
Accounts or ``kitty apply accounts grant``), kitty may create an account there,
read the tenant's listed verification sender, sign in and reset the password.

Registry (``apply_accounts``, migration 055): family, tenant origin, login email,
state ``unknown | created | verified | active | rejected | locked | manual``,
timestamps and the password-policy profile used. It never holds a secret.

Secrets: each tenant gets its own generated password (``secrets`` module, 16+
characters with every class), stored through kitty Keys as
``APPLY_PW_<sha1-16 of origin>``: the OS keyring, or the owner-only file when no
keyring exists (with a visible warning); never the environment. A refused
password is replaced by a stricter profile, never retried verbatim. The shared
credential file supplies the base email; its password is used only for tenants
in ``authentication.existing_account_origins``. The base email is the login
email unless the family is listed in ``authentication.plus_alias_families``.

Caps: at most 3 new tenant accounts per rolling day and 3 sign-in tries per
tenant per rolling day. ``locked`` and ``manual`` tenants are never attempted.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kitty.config import (
    AuthenticationSettings, Config, EmailVerificationScope, EmailVerificationSettings,
    _authentication_origin,
)
from kitty.core import keys as keys_core
from kitty.core.apply.credentials import ApplicationCredentials, CredentialError, load_credentials
from kitty.core.apply.grants import FAMILIES, family_of
from kitty.db.connection import transaction
from kitty.settings import Settings

STATES = ("unknown", "created", "verified", "active", "rejected", "locked", "manual")
PROFILES = ("standard", "strict", "shared")
#: States whose stored password is expected to sign in.
SIGN_IN_STATES = frozenset({"created", "verified", "active"})
NEW_ACCOUNTS_PER_DAY = 3
SIGN_INS_PER_TENANT_PER_DAY = 3
_DAY = timedelta(hours=24)

#: Typed handoff prefixes (kitty.core.apply.attempts maps them to causes).
ACCOUNT_PROBLEM = "account_problem: "
ACCOUNT_CAP = "account_cap: "
KEYRING_LOCKED = "keyring_locked: "
KEYRING_LOCKED_NOTE = (KEYRING_LOCKED + "the OS keyring that holds employer account passwords is "
                       "locked; unlock it, then run again (nothing was opened)")

# Password profiles: (length, minimum of each class, symbols).
_PROFILE_RULES = {
    "standard": (20, 1, "!#$%&*+-=?@^_"),
    # Stricter, for a tenant that refused the standard one: fits a 16-character
    # maximum, two of each class, only the most widely accepted symbols, starts
    # with a letter, no repeated neighbours, no 3-character runs.
    "strict": (16, 2, "!@#$%"),
}


class AccountError(RuntimeError):
    """A typed, value-free reason kitty cannot use a tenant account now."""


def key_name(origin: str) -> str:
    """The Keys name of a tenant's password: APPLY_PW_ + 16 hex of SHA-1(origin)."""
    return keys_core.ACCOUNT_PREFIX + hashlib.sha1(origin.encode()).hexdigest()[:16].upper()


def _runs(password: str) -> bool:
    for a, b, c in zip(password, password[1:], password[2:]):
        if (ord(b) - ord(a)) == (ord(c) - ord(b)) and abs(ord(b) - ord(a)) == 1:
            return True
    return False


def meets_profile(password: str, profile: str, *, avoid: str = "") -> bool:
    """Whether `password` satisfies `profile` (tests and the stored-key check use it)."""
    length, each, symbols = _PROFILE_RULES[profile]
    classes = (string.ascii_lowercase, string.ascii_uppercase, string.digits, symbols)
    if len(password) < max(16, length) or any(char not in "".join(classes) for char in password):
        return False
    if any(sum(char in group for char in password) < each for group in classes):
        return False
    if profile == "strict":
        if len(password) != length or not password[0].isalpha():
            return False
        if any(a == b for a, b in zip(password, password[1:])) or _runs(password.lower()):
            return False
        parts = [part for part in re.split(r"[^a-z0-9]+", avoid.lower()) if len(part) >= 3]
        if any(part in password.lower() for part in parts):
            return False
    return True


def generate_password(profile: str = "standard", *, avoid: str = "") -> str:
    """A fresh random password for `profile`; `avoid` (the login email) never appears in it."""
    length, each, symbols = _PROFILE_RULES[profile]
    classes = (string.ascii_lowercase, string.ascii_uppercase, string.digits, symbols)
    alphabet = "".join(classes)
    shuffle = secrets.SystemRandom().shuffle
    while True:
        chars = [secrets.choice(group) for group in classes for _ in range(each)]
        chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
        shuffle(chars)
        password = "".join(chars)
        if meets_profile(password, profile, avoid=avoid):
            return password


# ------------------------------------------------------------------ registry


@dataclass(frozen=True)
class Account:
    id: int
    family: str
    origin: str
    login_email: str
    state: str
    password_profile: str
    created_at: str
    updated_at: str
    account_created_at: str | None
    verified_at: str | None
    last_sign_in_at: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _row(row: sqlite3.Row | None) -> Account | None:
    return Account(**{key: row[key] for key in row.keys()}) if row is not None else None


def get_account(conn: sqlite3.Connection, origin: str) -> Account | None:
    return _row(conn.execute("SELECT * FROM apply_accounts WHERE origin=?", (origin,)).fetchone())


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    return [_row(row) for row in conn.execute("SELECT * FROM apply_accounts ORDER BY family, origin")]


def ensure_account(conn: sqlite3.Connection, family: str, origin: str, login_email: str,
                   profile: str) -> Account:
    """The tenant's row, created in state ``unknown`` on first use (login email fixed then)."""
    now = _now().isoformat()
    conn.execute(
        "INSERT INTO apply_accounts(family, origin, login_email, state, password_profile, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?) ON CONFLICT(origin) DO NOTHING",
        (family, origin, login_email, "unknown", profile, now, now),
    )
    return get_account(conn, origin)


_STAMPS = {"created": "account_created_at", "verified": "verified_at", "active": "last_sign_in_at"}


def set_state(conn: sqlite3.Connection, origin: str, state: str, *, profile: str | None = None,
              event: str | None = None, stamp: bool = True) -> Account:
    """Move a tenant to `state` (and record `event`), stamping its timestamp column
    (``stamp=False``: a reset makes the account usable without being a sign-in)."""
    if state not in STATES or (profile is not None and profile not in PROFILES):
        raise ValueError("unknown account state or profile")
    now = _now().isoformat()
    sets = ["state=?", "updated_at=?"]
    values: list[object] = [state, now]
    if profile is not None:
        sets.append("password_profile=?")
        values.append(profile)
    if stamp and (column := _STAMPS.get(state)):
        sets.append(f"{column}=?")
        values.append(now)
    with transaction(conn):
        conn.execute(f"UPDATE apply_accounts SET {', '.join(sets)} WHERE origin=?", (*values, origin))
        if event:
            conn.execute("INSERT INTO apply_account_events(origin, kind, at) VALUES (?,?,?)", (origin, event, now))
    return get_account(conn, origin)


def record_event(conn: sqlite3.Connection, origin: str, kind: str) -> None:
    conn.execute("INSERT INTO apply_account_events(origin, kind, at) VALUES (?,?,?)",
                 (origin, kind, _now().isoformat()))


def count_events(conn: sqlite3.Connection, kind: str, *, origin: str | None = None,
                 now: datetime | None = None) -> int:
    """`kind` events in the rolling day before `now` (all tenants unless `origin`)."""
    since = ((now or _now()) - _DAY).isoformat()
    if origin is None:
        query, args = "SELECT COUNT(*) FROM apply_account_events WHERE kind=? AND at>?", (kind, since)
    else:
        query = "SELECT COUNT(*) FROM apply_account_events WHERE kind=? AND origin=? AND at>?"
        args = (kind, origin, since)
    return conn.execute(query, args).fetchone()[0]


def create_cap_blocker(conn: sqlite3.Connection) -> str | None:
    if count_events(conn, "create") >= NEW_ACCOUNTS_PER_DAY:
        return (ACCOUNT_CAP + f"kitty already created {NEW_ACCOUNTS_PER_DAY} new employer accounts "
                "in the last 24 hours; this one waits for a later run")
    return None


def sign_in_cap_blocker(conn: sqlite3.Connection, origin: str) -> str | None:
    if count_events(conn, "sign_in", origin=origin) >= SIGN_INS_PER_TENANT_PER_DAY:
        return (ACCOUNT_CAP + f"kitty already tried to sign in to this employer {SIGN_INS_PER_TENANT_PER_DAY} "
                "times in the last 24 hours; it tries again later")
    return None


# ------------------------------------------------------------------ grants


def granted_families(conn: sqlite3.Connection) -> dict[str, str]:
    """{family: granted_at} of the owner's ATS-family grants."""
    return {row[0]: row[1] for row in conn.execute("SELECT family, granted_at FROM apply_family_grants")
            if row[0] in FAMILIES}


def set_grant(conn: sqlite3.Connection, family: str, granted: bool) -> None:
    """Grant or revoke one family (the owner's action; agents never reach this)."""
    if family not in FAMILIES:
        raise ValueError(f"unknown ATS family {family!r}")
    if granted:
        conn.execute("INSERT INTO apply_family_grants(family, granted_at) VALUES (?,?)"
                     " ON CONFLICT(family) DO NOTHING", (family, _now().isoformat()))
    else:
        conn.execute("DELETE FROM apply_family_grants WHERE family=?", (family,))


def granted_family(conn: sqlite3.Connection, url: str) -> str | None:
    """The granted family `url` lies in, or None (ungranted or outside every pattern)."""
    family = family_of(url)
    return family if family is not None and family in granted_families(conn) else None


# ------------------------------------------------------------------ tenants


def base_email(settings: Settings, config: Config) -> str:
    """The shared credential's email, or the profile email when no credential file exists."""
    try:
        return load_credentials(settings).email
    except CredentialError:
        email = (config.profile.email or "").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise AccountError(ACCOUNT_PROBLEM + "no base email: save the application credentials "
                               "or a profile email first") from None
        return email


def login_email_for(base: str, family: str, origin: str, policy: AuthenticationSettings) -> str:
    """The base email, or ``local+tenant@domain`` for a family flagged as accepting '+'."""
    if family not in policy.plus_alias_families:
        return base
    local, _, domain = base.partition("@")
    host = origin.removeprefix("https://").split(":")[0]
    tenant = re.sub(r"[^a-z0-9]", "", host.split(".")[0].lower())[:24] or "tenant"
    return f"{local.split('+')[0]}+{tenant}@{domain}"


@dataclass(frozen=True)
class Tenant:
    """A granted tenant's account, policy and in-process credentials (never persisted)."""

    family: str
    origin: str
    account: Account
    credentials: ApplicationCredentials
    policy: AuthenticationSettings
    shared: bool

    @property
    def mode(self) -> str:
        return self.policy.account_mode


def _keys(settings: Settings) -> keys_core.Keys:
    return keys_core.for_settings(settings)


def _scope(policy: AuthenticationSettings, family: str, origin: str) -> EmailVerificationScope | None:
    scopes = policy.email_verification.scopes_for([origin])
    if scopes:
        return scopes[0]
    sender = policy.family_senders.get(family)
    return EmailVerificationScope(origin=origin, sender_email=sender) if sender else None


def tenant_policy(base: AuthenticationSettings, family: str, origin: str, *, mode: str,
                  shared: bool) -> AuthenticationSettings:
    """The run policy a family grant gives one tenant: its origin, its listed sender."""
    scope = _scope(base, family, origin)
    verification = EmailVerificationSettings(
        enabled=scope is not None, scopes=[scope] if scope else [],
        max_wait_seconds=base.email_verification.max_wait_seconds,
        max_age_seconds=base.email_verification.max_age_seconds,
    )
    policy = AuthenticationSettings(
        enabled=True, allow_account_creation=not shared, allow_account_terms=True,
        approved_origins=[origin], existing_account_origins=[origin] if shared else [],
        manual_account_origins=base.manual_account_origins, account_mode=mode,
        email_verification=verification, plus_alias_families=base.plus_alias_families,
        family_senders=base.family_senders,
    )
    policy._family = family
    return policy


def tenant_for(conn: sqlite3.Connection, settings: Settings, config: Config, url: str) -> Tenant | None:
    """The granted tenant at `url` with its credentials, or None when no grant covers it.

    Raises AccountError with a typed handoff (keyring locked, no base email,
    locked/manual account) instead of returning something unusable.
    """
    base = config.apply.authentication
    origin = _authentication_origin(url)
    family = granted_family(conn, url)
    if origin is None or family is None or base.account_mode == "google":
        return None
    if origin in base.manual_account_origins:
        raise AccountError(ACCOUNT_PROBLEM + "this employer account is set to manual sign-in; continue yourself")
    shared = origin in base.existing_account_origins
    if shared:
        try:
            credentials = load_credentials(settings)
        except CredentialError as exc:
            raise AccountError(str(exc)) from None
        account = ensure_account(conn, family, origin, credentials.email, "shared")
        return Tenant(family, origin, account, ApplicationCredentials(credentials.email, credentials.password, origin),
                      tenant_policy(base, family, origin, mode="login", shared=True), True)
    account = get_account(conn, origin) or ensure_account(
        conn, family, origin, login_email_for(base_email(settings, config), family, origin, base), "standard")
    if account.state in ("locked", "manual"):
        raise AccountError(ACCOUNT_PROBLEM + (
            "the employer reported this account locked; sign in yourself" if account.state == "locked"
            else "this employer account is set to manual sign-in; continue yourself"))
    store = _keys(settings)
    if store.locked():
        raise AccountError(KEYRING_LOCKED_NOTE)
    try:
        password = store.read(key_name(origin))
    except keys_core.KeysError as exc:
        raise AccountError(KEYRING_LOCKED + str(exc) if exc.status == 503 else ACCOUNT_PROBLEM + str(exc)) from None
    mode = "login" if password and account.state in SIGN_IN_STATES else "create"
    # No password yet: AccountStage.create generates and stores one right before use.
    return Tenant(family, origin, account, ApplicationCredentials(account.login_email, password, origin),
                  tenant_policy(base, family, origin, mode=mode, shared=False), False)


def new_password(conn: sqlite3.Connection, settings: Settings, tenant: Tenant, profile: str) -> Tenant:
    """Generate, store (before any page sees it) and return the tenant with a new password."""
    if tenant.shared:
        raise AccountError(ACCOUNT_PROBLEM + "the shared credential's password is never replaced automatically")
    password = generate_password(profile, avoid=tenant.account.login_email)
    try:
        _keys(settings).set(key_name(tenant.origin), password)
    except keys_core.KeysError as exc:
        raise AccountError((KEYRING_LOCKED if exc.status == 503 else ACCOUNT_PROBLEM) + str(exc)) from None
    account = set_state(conn, tenant.origin, tenant.account.state, profile=profile)
    return Tenant(tenant.family, tenant.origin, account,
                  ApplicationCredentials(account.login_email, password, tenant.origin),
                  tenant.policy, False)


def with_mode(tenant: Tenant, conn: sqlite3.Connection, mode: str) -> Tenant:
    """The same tenant with a fresh registry row and run policy in `mode`."""
    policy = tenant.policy.model_copy(update={"account_mode": mode})
    policy._family = tenant.family
    return Tenant(tenant.family, tenant.origin, get_account(conn, tenant.origin), tenant.credentials,
                  policy, tenant.shared)


class Memory:
    """apply_accounts-backed account facts for auth.authenticate (kitty.core.apply.auth.AccountMemory).

    A sign-in the employer rejected marks the tenant ``rejected`` (``locked`` when it
    says so): the same password is never submitted there again. For the shared
    credential that holds until the credentials file changes; a granted tenant
    leaves ``rejected`` only through a password reset. An observed existing account
    for the shared credential is recorded as ``created`` so later runs sign in.
    """

    def __init__(self, conn: sqlite3.Connection, settings: Settings) -> None:
        self.conn = conn
        self.settings = settings

    def _shared_changed_since(self, stamp: str) -> bool:
        from kitty.core.apply.credentials import CREDENTIALS_FILE

        try:
            changed = datetime.fromtimestamp((self.settings.root / CREDENTIALS_FILE).stat().st_mtime, timezone.utc)
        except OSError:
            return False
        return changed > datetime.fromisoformat(stamp)

    def _row(self, origin: str, credentials: ApplicationCredentials) -> Account:
        family = family_of(origin + "/") or "other"
        return get_account(self.conn, origin) or ensure_account(
            self.conn, family, origin, credentials.email, "shared" if credentials.origin is None else "standard")

    def rejected(self, origin: str | None, credentials: ApplicationCredentials) -> bool:
        account = get_account(self.conn, origin) if origin else None
        if account is None or account.state not in ("rejected", "locked"):
            return False
        if account.login_email.casefold() != credentials.email.casefold():
            return False
        if credentials.origin is None:
            return not self._shared_changed_since(account.updated_at)
        return True

    def reject(self, origin: str, credentials: ApplicationCredentials, *, locked: bool) -> None:
        self._row(origin, credentials)
        set_state(self.conn, origin, "locked" if locked else "rejected",
                  event="locked" if locked else "sign_in_rejected")

    def exists(self, origin: str | None, email: str) -> bool:
        account = get_account(self.conn, origin) if origin else None
        return bool(account and account.login_email.casefold() == email.casefold()
                    and account.password_profile == "shared"
                    and account.state in ("created", "verified", "active", "rejected", "locked"))

    def mark_exists(self, origin: str, email: str) -> None:
        account = self._row(origin, ApplicationCredentials(email, "", None))
        # A granted tenant's states belong to kitty.core.apply.account_flow.
        if account.password_profile == "shared" and account.state == "unknown" and \
                account.login_email.casefold() == email.casefold():
            set_state(self.conn, origin, "created")


def keyring_blocker(conn: sqlite3.Connection, settings: Settings) -> str | None:
    """Before a run: a typed keyring_locked handoff when grants need a locked keyring."""
    if not granted_families(conn):
        return None
    return KEYRING_LOCKED_NOTE if _keys(settings).locked() else None


def listing(conn: sqlite3.Connection, settings: Settings, config: Config) -> dict:
    """Settings → Apply → Accounts: registry rows and grants, never a secret."""
    store = _keys(settings)
    status = store.status()
    granted = granted_families(conn)
    policy = config.apply.authentication
    rows = []
    for account in list_accounts(conn):
        try:
            found = store.lookup(key_name(account.origin)) if account.password_profile != "shared" else None
            stored = found[1] if found else None
        except keys_core.KeysError:
            stored = "unavailable"
        rows.append({
            "family": account.family, "origin": account.origin, "login_email": account.login_email,
            "state": account.state, "password_profile": account.password_profile,
            "password_stored": "shared file" if account.password_profile == "shared" else stored,
            "created_at": account.created_at, "updated_at": account.updated_at,
            "account_created_at": account.account_created_at, "verified_at": account.verified_at,
            "last_sign_in_at": account.last_sign_in_at,
            "sign_ins_today": count_events(conn, "sign_in", origin=account.origin),
        })
    return {
        "accounts": rows,
        "grants": [{
            "family": family.id, "label": family.label, "patterns": list(family.patterns),
            "granted": family.id in granted, "granted_at": granted.get(family.id),
            "plus_alias": family.id in policy.plus_alias_families,
            "sender": "<tenant>@otp.workday.com" if family.id == "workday" else policy.family_senders.get(family.id),
        } for family in FAMILIES.values()],
        "caps": {
            "new_accounts_per_day": NEW_ACCOUNTS_PER_DAY, "new_accounts_today": count_events(conn, "create"),
            "sign_ins_per_tenant_per_day": SIGN_INS_PER_TENANT_PER_DAY,
        },
        "store": status["store"], "keyring": status["keyring"], "keyring_locked": store.locked(),
        "warnings": status["warnings"],
    }

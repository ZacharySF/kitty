"""Unattended employer accounts (proposal-07 Step 4 + Step 5 link rules).

A local HTTPS fixture tenant behaves like a Workday candidate site: sign-in page
with Create Account and Forgot password, account creation with an emailed
activation link, password policy, "email already exists", an emailed reset link
and a new-password form. Mail is a synthetic outbox read through the same rules
as Gmail (no mailbox, no network, no real account). Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import re
import secrets
import ssl
import threading
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from kitty.config import AuthenticationSettings
from kitty.core import jobs as jobs_core
from kitty.core import keys as keys_core
from kitty.core.apply import account_flow, accounts, auth, email_verification, filler, grants
from kitty.core.apply.accounts import key_name
from kitty.core.apply.credentials import CREDENTIALS_FILE
from kitty.core.apply.filler import fill_job
from kitty.core.apply.verification_mail import VerificationMessage
from test_application_auth import firefox, page  # noqa: F401 - shared headless Firefox fixtures

x509 = pytest.importorskip("cryptography.x509")

EMAIL = "pat.synthetic@example.invalid"
SHARED_PASSWORD = "Shared-Synthetic#1"      # the credential file's password: never sent to a tenant
OWNER_UNKNOWN = "Owner-Forgot-It#9"         # a pre-existing account's password kitty does not know
SENDER = "accounts@tenant.example.invalid"

SIGN_IN = """<!doctype html><title>Sign In</title><body>
<h1>Sign In</h1>{notice}{alert}
<form method="post" action="/session">
  <label>Email Address <input type="email" name="email"></label>
  <label>Password <input type="password" name="password"></label>
  <button type="submit">Sign In</button>
</form>
<p><a href="/create">Create Account</a></p>
<p><a href="/forgot">Forgot your password?</a></p></body>"""

CREATE = """<!doctype html><title>Create Account</title><body>
<h1>Create Account</h1>{alert}
<form method="post" action="/create">
  <label>Email Address <input type="email" name="email" value="{email}"></label>
  <label>Password <input type="password" name="password" autocomplete="new-password"></label>
  <label>Verify New Password <input type="password" name="confirm" autocomplete="new-password"></label>
  <button type="submit">Create Account</button>
</form>
<p><a href="/login">Sign In</a></p></body>"""

VERIFY = """<!doctype html><title>Verify</title><body><h1>Verify your email</h1>
<p>We sent an email to your address. Check your email and open the verification link to activate your account.</p></body>"""

FORGOT = """<!doctype html><title>Forgot Password</title><body><h1>Forgot Password</h1>
<form method="post" action="/forgot">
  <label>Email Address <input type="email" name="email"></label>
  <button type="submit">Reset Password</button>
</form></body>"""

SENT = """<!doctype html><title>Check your email</title><body><h1>Check your email</h1>
<p>We have sent a password reset link to your email address.</p></body>"""

RESET = """<!doctype html><title>Reset Password</title><body><h1>Reset Password</h1>{alert}
<form method="post" action="/reset-password">
  <input type="hidden" name="token" value="{token}">
  <label>New Password <input type="password" name="password"></label>
  <label>Confirm New Password <input type="password" name="confirm"></label>
  <button type="submit">Reset Password</button>
</form></body>"""

APPLICATION = """<!doctype html><title>Apply</title><body><h1>My Information</h1>
<form id="application" method="post" action="/apply">
  <label>Preferred name <input name="preferred_name"></label>
  <button type="submit">Submit application</button>
</form></body>"""

_STRICT = re.compile(r"[A-Za-z0-9!@#$%]{8,16}")


class Tenant:
    """The fixture tenant's server-side state and what it received."""

    def __init__(self) -> None:
        self.origin = ""
        self.reset()

    def reset(self) -> None:
        self.accounts: dict[str, dict] = {}
        self.sessions: set[str] = set()
        self.tokens: dict[str, tuple[str, str]] = {}
        self.used: set[str] = set()
        self.outbox: list[dict] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.strict_policy = False
        self.lock_on_failure = False

    def mail(self, kind: str, to: str, path: str) -> None:
        token = secrets.token_urlsafe(16)
        self.tokens[token] = (kind, to)
        self.outbox.append({"id": f"synthetic-{len(self.outbox) + 1}", "to": to, "kind": kind,
                            "link": f"{self.origin}{path}?token={token}", "token": token,
                            "received_at": dt.datetime.now(dt.timezone.utc)})

    def posts(self, path: str | None = None) -> list[dict]:
        return [fields for method, p, fields in self.requests if method == "POST" and (path is None or p == path)]


def _handler(tenant: Tenant):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: str, status: int = 200, location: str | None = None, cookie: str | None = None):
            data = body.encode()
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            if cookie:
                self.send_header("Set-Cookie", f"sid={cookie}; Path=/; Secure; HttpOnly")
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _signed_in(self) -> bool:
            cookie = self.headers.get("Cookie") or ""
            return any(part.strip().removeprefix("sid=") in tenant.sessions
                       for part in cookie.split(";") if part.strip().startswith("sid="))

        def _token(self, query: dict, kind: str) -> str | None:
            token = (query.get("token") or [""])[0]
            found = tenant.tokens.get(token)
            return token if found and found[0] == kind and token not in tenant.used else None

        def do_GET(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            tenant.requests.append(("GET", parts.path, query))
            if parts.path == "/job/apply":
                self._send(APPLICATION if self._signed_in() else SIGN_IN.format(notice="", alert=""))
            elif parts.path == "/login":
                notice = "<p>Your password has been reset.</p>" if "reset" in query else ""
                self._send(SIGN_IN.format(notice=notice, alert=""))
            elif parts.path == "/create":
                self._send(CREATE.format(alert="", email=""))
            elif parts.path == "/forgot":
                self._send(FORGOT)
            elif parts.path == "/activate":
                token = self._token(query, "activate")
                if token is None:
                    self._send("<h1>Link expired</h1><p>This link has expired or is invalid.</p>", 400)
                    return
                tenant.used.add(token)
                tenant.accounts[tenant.tokens[token][1]]["verified"] = True
                self._send("", 303, "/login?activated=1")
            elif parts.path == "/reset-password":
                token = self._token(query, "reset")
                if token is None:
                    self._send("<h1>Link expired</h1><p>This link has expired or is invalid.</p>", 400)
                    return
                self._send(RESET.format(alert="", token=token))
            else:
                self._send("<p>not here</p>", 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            fields = {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode()).items()}
            tenant.requests.append(("POST", self.path, fields))
            email, password = fields.get("email", ""), fields.get("password", "")
            refused = '<p role="alert">Password must be at most 16 characters and use only !@#$% as symbols.</p>'
            if self.path == "/create":
                if email in tenant.accounts:
                    self._send(CREATE.format(email=email, alert=(
                        '<p role="alert">An account with this email address already exists.</p>')))
                elif tenant.strict_policy and not _STRICT.fullmatch(password):
                    self._send(CREATE.format(email=email, alert=refused))
                elif password != fields.get("confirm"):
                    self._send(CREATE.format(email=email, alert='<p role="alert">Passwords do not match.</p>'))
                else:
                    tenant.accounts[email] = {"password": password, "verified": False}
                    tenant.mail("activate", email, "/activate")
                    self._send(VERIFY)
            elif self.path == "/session":
                account = tenant.accounts.get(email)
                if account and account["password"] == password and account["verified"]:
                    sid = secrets.token_hex(8)
                    tenant.sessions.add(sid)
                    self._send("", 303, "/job/apply", cookie=sid)
                else:
                    alert = ("Your account is locked." if tenant.lock_on_failure
                             else "Incorrect email or password.")
                    self._send(SIGN_IN.format(notice="", alert=f'<p role="alert">{alert}</p>'))
            elif self.path == "/forgot":
                if email in tenant.accounts:
                    tenant.mail("reset", email, "/reset-password")
                self._send(SENT)
            elif self.path == "/reset-password":
                token = self._token({"token": [fields.get("token", "")]}, "reset")
                if token is None:
                    self._send("<h1>Link expired</h1><p>This link has expired or is invalid.</p>", 400)
                elif tenant.strict_policy and not _STRICT.fullmatch(password):
                    self._send(RESET.format(alert=refused, token=token))
                else:
                    tenant.used.add(token)
                    tenant.accounts[tenant.tokens[token][1]].update(password=password, verified=True)
                    self._send("", 303, "/login?reset=1")
            else:
                self._send("<p>not here</p>", 404)

    return Handler


@pytest.fixture(scope="module")
def tenant_server(tmp_path_factory):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    folder = tmp_path_factory.mktemp("tenant-tls")
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    state = Tenant()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.origin = f"https://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


class Outbox:
    """The synthetic mail reader: the tenant's outbox, filtered like the Gmail reader."""

    def __init__(self, tenant: Tenant) -> None:
        self.tenant = tenant
        self.override = None          # callable(message) -> message, to forge bad mail
        self.reads = 0

    def __call__(self, bidi, request, *, checkpoint=None):
        self.reads += 1
        found = []
        for item in self.tenant.outbox:
            if item["to"].casefold() != request.recipient.casefold() or item["received_at"] < request.requested_at:
                continue
            message = VerificationMessage(
                id=item["id"], sender=SENDER, recipients=(item["to"],), received_at=item["received_at"],
                subject="Reset your password" if item["kind"] == "reset" else "Activate your account",
                text="Open this link to continue.", links=(item["link"],), authenticated=True)
            found.append(self.override(message) if self.override else message)
        return found[-1:]


@pytest.fixture
def tenant(tenant_server, firefox, monkeypatch):  # noqa: F811 - fixture injection
    """A fresh tenant, granted under the Workday family pattern for its loopback origin."""
    tenant_server.reset()
    monkeypatch.setitem(grants.FAMILIES, "workday", grants.Family(
        "workday", "Workday", (tenant_server.origin + "/*",)))
    monkeypatch.setattr(auth, "_AUTH_WAIT_SECONDS", 4.0)
    monkeypatch.setattr(account_flow, "_RESET_WAIT_SECONDS", 6.0)
    outbox = Outbox(tenant_server)
    monkeypatch.setattr(email_verification, "read_verification_mail", outbox)
    tenant_server.mailbox = outbox
    return tenant_server


def _space(workspace, tenant, *, grant=True, **policy):
    path = workspace.settings.root / CREDENTIALS_FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nAPPLICATION_ACCOUNT_PASSWORD={SHARED_PASSWORD}\n")
    path.chmod(0o600)
    settings = AuthenticationSettings(**({"family_senders": {"workday": SENDER}} | policy))
    workspace.config = replace(workspace.config, apply=workspace.config.apply.model_copy(
        update={"authentication": settings}))
    if grant:
        accounts.set_grant(workspace.conn, "workday", True)
    return workspace


def _job(space, tenant):
    job, _ = jobs_core.upsert_job(space.conn, company="Synthetic Tenant Co", title="Intern",
                                  url=tenant.origin + "/job/apply", tracks=["software"])
    return job


def _stored_password(space, tenant) -> str:
    return keys_core.for_settings(space.settings).read(key_name(tenant.origin))


def _state(space, tenant) -> str:
    return accounts.get_account(space.conn, tenant.origin).state


# ---------------------------------------------------------------- end to end


def test_new_tenant_create_verify_sign_in_fill_then_second_run_signs_in_from_registry(workspace, tenant, page):
    space = _space(workspace, tenant)
    job = _job(space, tenant)
    report = fill_job(page, space.conn, space.settings, space.config, job)

    password = _stored_password(space, tenant)
    assert accounts.meets_profile(password, "standard")
    creates = tenant.posts("/create")
    assert creates == [{"email": EMAIL, "password": password, "confirm": password}]
    assert tenant.posts("/session") == [{"email": EMAIL, "password": password}]
    assert tenant.accounts[EMAIL]["verified"]
    assert page.main.url == tenant.origin + "/job/apply"
    assert not tenant.posts("/apply")                                  # never submitted
    assert not report.note or not report.note.startswith(("login", "account", "verification")), report.note
    account = accounts.get_account(space.conn, tenant.origin)
    assert (account.state, account.family, account.login_email, account.password_profile) == (
        "active", "workday", EMAIL, "standard")
    assert account.account_created_at and account.verified_at and account.last_sign_in_at
    assert SHARED_PASSWORD not in json.dumps(tenant.requests)          # shared password stays home

    # Second run: a signed-out browser signs in with the stored password; nothing is created.
    tenant.sessions.clear()
    tenant.requests.clear()
    second = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))
    assert tenant.posts() == [{"email": EMAIL, "password": password}]
    assert page.main.url == tenant.origin + "/job/apply"
    assert not (second.note or "").startswith(("login", "account")), second.note
    assert accounts.count_events(space.conn, "create") == 1
    assert accounts.count_events(space.conn, "sign_in", origin=tenant.origin) == 2


def test_refused_password_gets_a_stricter_one_never_the_same(workspace, tenant, page):
    tenant.strict_policy = True
    space = _space(workspace, tenant)
    fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))

    first, second = (fields["password"] for fields in tenant.posts("/create"))
    assert first != second
    assert accounts.meets_profile(first, "standard") and not _STRICT.fullmatch(first)
    assert accounts.meets_profile(second, "strict", avoid=EMAIL)
    assert _stored_password(space, tenant) == second
    assert tenant.accounts[EMAIL]["password"] == second
    assert accounts.get_account(space.conn, tenant.origin).password_profile == "strict"
    assert accounts.count_events(space.conn, "password_refused", origin=tenant.origin) == 1
    assert accounts.count_events(space.conn, "create") == 1            # only the accepted one counts
    assert tenant.posts("/session") == [{"email": EMAIL, "password": second}]


def test_email_in_use_resets_through_the_emailed_link_then_signs_in(workspace, tenant, page):
    tenant.accounts[EMAIL] = {"password": OWNER_UNKNOWN, "verified": True}
    space = _space(workspace, tenant)
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))

    new = _stored_password(space, tenant)
    assert new and new != OWNER_UNKNOWN and accounts.meets_profile(new, "standard")
    assert [p for m, p, _ in tenant.requests if m == "POST"] == [
        "/create", "/forgot", "/reset-password", "/session"]
    assert tenant.posts("/forgot") == [{"email": EMAIL}]
    reset = tenant.posts("/reset-password")[0]
    assert reset["password"] == reset["confirm"] == new
    assert tenant.posts("/session") == [{"email": EMAIL, "password": new}]
    assert tenant.accounts[EMAIL]["password"] == new
    assert page.main.url == tenant.origin + "/job/apply"
    assert not (report.note or "").startswith(("login", "account")), report.note
    assert _state(space, tenant) == "active"
    assert accounts.count_events(space.conn, "create") == 0            # no new account was made
    rows = space.conn.execute("SELECT state FROM application_email_verification_requests").fetchall()
    assert [row[0] for row in rows] == ["verified"]                    # the link is used up


@pytest.mark.parametrize("forge, why", [
    (lambda m: replace(m, sender="accounts@attacker.example"), "sender"),
    (lambda m: replace(m, authenticated=False), "DKIM"),
    (lambda m: replace(m, recipients=("someone.else@example.invalid",)), "recipient"),
    (lambda m: replace(m, links=("https://attacker.example/reset-password?token=x",)), "host"),
    (lambda m: replace(m, links=(m.links[0].replace("127.0.0.1", "localhost"),)), "other origin"),
])
def test_reset_links_failing_host_sender_dkim_or_recipient_rules_are_never_opened(
        workspace, tenant, page, forge, why):
    tenant.accounts[EMAIL] = {"password": OWNER_UNKNOWN, "verified": True}
    tenant.mailbox.override = forge
    space = _space(workspace, tenant)
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))

    assert report.status == "blocked" and report.note.startswith("account_problem:"), (why, report.note)
    assert not any(path == "/reset-password" for _, path, _ in tenant.requests), why
    assert tenant.accounts[EMAIL]["password"] == OWNER_UNKNOWN
    assert not tenant.posts("/session")
    attempt = space.conn.execute("SELECT cause FROM apply_attempts ORDER BY id DESC").fetchone()
    assert attempt[0] == "account_problem"


def test_used_reset_email_is_refused_on_a_later_reset(workspace, tenant, page):
    tenant.accounts[EMAIL] = {"password": OWNER_UNKNOWN, "verified": True}
    space = _space(workspace, tenant)
    fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))
    first = tenant.outbox[-1]
    # The account is later refused again; the next reset only finds the old email.
    tenant.accounts[EMAIL]["password"] = OWNER_UNKNOWN
    tenant.sessions.clear()
    tenant.requests.clear()
    tenant.mailbox.override = lambda m: replace(m, id=first["id"], received_at=first["received_at"],
                                                links=(first["link"],))
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))
    assert report.note.startswith("account_problem:"), report.note
    assert not any(path == "/reset-password" for _, path, _ in tenant.requests)


def test_sign_in_and_new_account_caps(workspace, tenant, page):
    space = _space(workspace, tenant)
    for _ in range(accounts.NEW_ACCOUNTS_PER_DAY):
        accounts.record_event(space.conn, "https://other.example.invalid", "create")
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))
    assert report.note.startswith("account_cap:") and not tenant.posts(), report.note
    assert space.conn.execute("SELECT cause FROM apply_attempts").fetchone()[0] == "account_cap"

    # An existing tenant account: at most 3 sign-in tries a day.
    space.conn.execute("DELETE FROM apply_account_events")
    keys_core.for_settings(space.settings).set(key_name(tenant.origin), "Known-Synthetic#123456")
    accounts.set_state(space.conn, tenant.origin, "active")
    for _ in range(accounts.SIGN_INS_PER_TENANT_PER_DAY):
        accounts.record_event(space.conn, tenant.origin, "sign_in")
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))
    assert report.note.startswith("account_cap:") and not tenant.posts(), report.note


def test_locked_account_is_never_tried_again(workspace, tenant, page):
    tenant.accounts[EMAIL] = {"password": "Something-Else#1", "verified": True}
    tenant.lock_on_failure = True
    space = _space(workspace, tenant)
    keys_core.for_settings(space.settings).set(key_name(tenant.origin), "Known-Synthetic#123456")
    accounts.ensure_account(space.conn, "workday", tenant.origin, EMAIL, "standard")
    accounts.set_state(space.conn, tenant.origin, "active")
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))
    assert report.note.startswith("account_problem:") and len(tenant.posts("/session")) == 1, report.note
    assert _state(space, tenant) == "locked"
    tenant.requests.clear()
    again = fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))
    assert again.note.startswith("account_problem:") and not tenant.posts()


def test_secrets_never_reach_logs_db_recovery_reports_or_ai_bodies(workspace, tenant, page, monkeypatch):
    generated: list[str] = []
    original = accounts.generate_password

    def recording(profile="standard", *, avoid=""):
        generated.append(original(profile, avoid=avoid))
        return generated[-1]

    monkeypatch.setattr(accounts, "generate_password", recording)
    tenant.strict_policy = True                       # standard refused -> strict: two passwords
    space = _space(workspace, tenant)
    reports = [fill_job(page, space.conn, space.settings, space.config, _job(space, tenant))]
    # A forgotten password: reset by emailed link (a third password, a reset token).
    tenant.accounts[EMAIL]["password"] = OWNER_UNKNOWN
    tenant.sessions.clear()
    reports.append(fill_job(page, space.conn, space.settings, space.config, _job(space, tenant)))
    assert _state(space, tenant) == "active" and len(generated) >= 3

    # The AI request body the recovery planner would send for this page.
    from decimal import Decimal

    from kitty.core.apply.preflight import inspect_page
    from kitty.core.apply.recovery import Blocker, ModelBudget, OpenRouterPlanner, Action
    from kitty.core.apply.recovery_runtime import JobRecovery

    page.goto(tenant.origin + "/login")
    page.main.call("(value) => { document.querySelector('[name=password]').value = value; }",
                   generated[-1], page_realm=True)
    recovery = JobRecovery(space.conn, space.settings, space.config, _job(space, tenant), page)
    observation = replace(recovery.observe(inspect_page(page)), blocker=Blocker.UNKNOWN)
    bodies: list[str] = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            import json as json_module
            bodies.append(json_module.dumps(json))
            raise RuntimeError("offline test")

    monkeypatch.setattr("kitty.core.apply.recovery.httpx.Client", Client)
    planner = OpenRouterPlanner(endpoint="https://openrouter.invalid/api/v1/chat/completions", api_key="test",
                                model="synthetic/model", approved=True, budget=ModelBudget(
                                    max_requests=4, max_input_tokens=100000, max_output_tokens=1000,
                                    max_usd=Decimal("1"), input_price_per_million=Decimal("1"),
                                    output_price_per_million=Decimal("1")))
    with pytest.raises(Exception):
        planner.plan(observation, (Action.WAIT, Action.HANDOFF), 1.0)
    assert bodies

    links = [item["link"] for item in tenant.outbox]
    tokens = [item["token"] for item in tenant.outbox]
    secrets_ = [*generated, *links, *tokens, SHARED_PASSWORD]
    stored = "\n".join(space.conn.iterdump())
    registry = {row[0] for row in space.conn.execute("SELECT login_email FROM apply_accounts")}
    elsewhere = "\n".join(line for line in space.conn.iterdump() if "apply_accounts" not in line)
    files = []
    for path in space.settings.root.rglob("*"):
        if path.is_file() and ".private" not in path.parts and path.suffix not in (".db", ".db-wal", ".db-shm"):
            files.append(path.read_text(errors="ignore"))
    texts = {
        "db": stored, "files": "\n".join(files), "ai": "\n".join(bodies),
        "reports": json.dumps([asdict(report) for report in reports], default=str),
    }
    for where, text in texts.items():
        for value in secrets_:
            assert value not in text, (where, "secret leaked")
    assert registry == {EMAIL}
    for where, text in {**texts, "db": elsewhere}.items():
        assert EMAIL not in text, (where, "login email outside the registry")


# ---------------------------------------------------------------- units


def test_password_key_names_fit_keys_and_profiles_hold():
    name = key_name("https://acme.wd5.myworkdayjobs.com")
    assert name == key_name("https://acme.wd5.myworkdayjobs.com") and keys_core.NAME_RE.fullmatch(name)
    assert re.fullmatch(r"APPLY_PW_[0-9A-F]{16}", name) and name != key_name("https://other.wd5.myworkdayjobs.com")
    for _ in range(50):
        standard = accounts.generate_password("standard", avoid=EMAIL)
        strict = accounts.generate_password("strict", avoid=EMAIL)
        assert len(standard) == 20 and accounts.meets_profile(standard, "standard")
        assert len(strict) == 16 and accounts.meets_profile(strict, "strict", avoid=EMAIL)
        assert _STRICT.fullmatch(strict) and keys_core.VALUE_RE.fullmatch(standard)
        assert "synthetic" not in strict.lower()


@pytest.mark.parametrize("url, family", [
    ("https://acme.wd5.myworkdayjobs.com/en-US/External/login", "workday"),
    ("https://acme.wd103.myworkdaysite.com/recruiting/acme/External", "workday"),
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1", "oracle"),
    ("https://acme.eightfold.ai/careers/apply?pid=1", "eightfold"),
    ("https://careers-acme.icims.com/jobs/1/login", "icims"),
    # Outside every pattern: other paths, http, ports, userinfo, look-alikes, dot segments.
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/Other/job/1", None),
    ("https://acme.eightfold.ai/admin", None),
    ("http://acme.wd5.myworkdayjobs.com/login", None),
    ("https://acme.wd5.myworkdayjobs.com:8443/login", None),
    ("https://user@acme.wd5.myworkdayjobs.com/login", None),
    ("https://acme.wd5.myworkdayjobs.com.attacker.example/login", None),
    ("https://myworkdayjobs.com/login", None),
    ("https://careers.icims.com/jobs", None),
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/../../admin", None),
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/%2e%2e/admin", None),
])
def test_family_grant_patterns_are_narrow_host_and_path(url, family):
    assert grants.family_of(url) == family


def test_granted_policy_keeps_redirects_inside_the_family():
    base = AuthenticationSettings()
    policy = accounts.tenant_policy(base, "workday", "https://acme.wd5.myworkdayjobs.com", mode="login", shared=False)
    assert policy.approves("https://acme.wd5.myworkdayjobs.com/en-US/External/job/X")
    assert policy.approves("https://acme.wd1.myworkdayjobs.com/en-US/External/login")       # same family
    assert not policy.approves("https://accounts.google.com/signin")
    assert not policy.approves("https://careers-acme.icims.com/jobs/1")                     # other family
    assert not base.approves("https://acme.wd5.myworkdayjobs.com/")                         # no grant, no approval


def test_plus_alias_only_for_flagged_families():
    origin = "https://acme.wd5.myworkdayjobs.com"
    assert accounts.login_email_for(EMAIL, "workday", origin, AuthenticationSettings()) == EMAIL
    flagged = AuthenticationSettings(plus_alias_families=["workday"])
    assert accounts.login_email_for(EMAIL, "workday", origin, flagged) == "pat.synthetic+acme@example.invalid"
    assert accounts.login_email_for(EMAIL, "icims", origin, flagged) == EMAIL
    with pytest.raises(ValueError):
        AuthenticationSettings(plus_alias_families=["nope"])


def test_account_passwords_never_come_from_the_environment_and_file_fallback_warns(tmp_path, monkeypatch):
    store = keys_core.Keys(tmp_path, store=None, use_env=True)
    name = key_name("https://acme.wd5.myworkdayjobs.com")
    monkeypatch.setenv(name, "From-The-Environment#1")
    monkeypatch.setenv("SYNTHETIC_TOOL", "tool-value")
    assert store.lookup(name) is None and store.read("SYNTHETIC_TOOL") == "tool-value"
    assert store.status()["warnings"] == []
    assert store.set(name, "Stored-In-File#12345") == "file"
    assert store.lookup(name) == ("Stored-In-File#12345", "file")
    assert (tmp_path / ".private" / "keys" / name).stat().st_mode & 0o777 == 0o600
    status = store.status()
    assert status["warnings"] == [keys_core.ACCOUNT_FILE_WARNING]
    assert status["account_passwords"] == {"count": 1, "in_file": 1}
    assert name not in {row["name"] for row in status["keys"]}
    assert "Stored-In-File" not in json.dumps(status)
    with pytest.raises(keys_core.KeysError):
        store.environment([name])


class _LockedKeyring:
    label = "Synthetic locked keyring"

    def locked(self):
        return True

    def get(self, name):
        raise AssertionError("a locked keyring is never read (that would raise an unlock prompt)")

    set = delete = get

    def names(self):
        return set()


def test_locked_keyring_is_a_typed_handoff_before_any_browser_opens(workspace, monkeypatch):
    monkeypatch.setattr(keys_core, "keyring", lambda: _LockedKeyring())
    space = _space(workspace, Tenant())
    job, _ = jobs_core.upsert_job(space.conn, company="Synthetic Tenant Co", title="Intern",
                                  url="https://acme.wd5.myworkdayjobs.com/External/job/X_R1", tracks=["software"])
    monkeypatch.setattr(filler, "headless_firefox", lambda *a, **k: pytest.fail("no browser with a locked keyring"))
    monkeypatch.setattr(filler.resolve_core, "before_attempt", lambda *a, **k: [])
    assert accounts.keyring_blocker(space.conn, space.settings).startswith("keyring_locked:")
    (report,) = filler.fill_jobs(space.settings, space.config, [job.id], headless=True)
    assert report.status == "blocked" and report.note.startswith("keyring_locked:")
    assert tuple(space.conn.execute("SELECT cause, outcome FROM apply_attempts").fetchone()) == (
        "keyring_locked", "needs_owner")
    with pytest.raises(accounts.AccountError, match="keyring_locked"):
        accounts.tenant_for(space.conn, space.settings, space.config, job.url)
    # Without any grant, nothing needs the keyring.
    accounts.set_grant(space.conn, "workday", False)
    assert accounts.keyring_blocker(space.conn, space.settings) is None


def test_registry_listing_and_web_grant_route_show_no_secrets(workspace):
    from kitty import web_apply_accounts

    space = _space(workspace, Tenant(), grant=False)
    origin = "https://acme.wd5.myworkdayjobs.com"
    accounts.ensure_account(space.conn, "workday", origin, EMAIL, "standard")
    keys_core.for_settings(space.settings).set(key_name(origin), "Listed-Never#123456")

    class App:
        settings, config = space.settings, space.config

        def _no_query(self, query):
            assert not query

    listing = web_apply_accounts.post(App(), space.conn, "/api/apply/accounts/grant",
                                      {"family": "workday", "granted": True})
    assert next(g for g in listing["grants"] if g["family"] == "workday")["granted"]
    assert listing == web_apply_accounts.get(App(), space.conn, "/api/apply/accounts", {})
    (row,) = listing["accounts"]
    assert (row["origin"], row["state"], row["password_stored"]) == (origin, "unknown", "file")
    assert listing["warnings"] == [keys_core.ACCOUNT_FILE_WARNING]
    assert "Listed-Never" not in json.dumps(listing) and SHARED_PASSWORD not in json.dumps(listing)
    from kitty.web_server import APIError
    with pytest.raises(APIError):
        web_apply_accounts.post(App(), space.conn, "/api/apply/accounts/grant", {"family": "evil", "granted": True})


def test_link_rules_refuse_other_hosts_senders_and_unauthenticated_mail():
    origin = "https://acme.wd5.myworkdayjobs.com"
    good = origin + "/en-US/External/passwordreset?token=x"
    assert email_verification._link((good,), origin, family="workday", purpose="reset") == good
    for link in ("https://acme.wd1.myworkdayjobs.com/en-US/External/passwordreset?token=x",   # another tenant
                 "https://attacker.example/passwordreset?token=x",
                 "http://acme.wd5.myworkdayjobs.com/passwordreset?token=x",
                 origin + "/en-US/External/apply"):                                          # not a reset link
        assert email_verification._link((link,), origin, family="workday", purpose="reset") is None, link
    assert email_verification._link((good, good + "2"), origin, family="workday", purpose="reset") is None
    now = dt.datetime.now(dt.timezone.utc)
    message = VerificationMessage(id="m", sender=SENDER, recipients=(EMAIL,), received_at=now - dt.timedelta(seconds=1),
                                  subject="", text="", links=(good,), authenticated=True)
    window = dict(sender=SENDER, recipient=EMAIL, requested_at=now - dt.timedelta(seconds=5),
                  expires_at=now + dt.timedelta(minutes=5), link=True)
    assert email_verification.message_refusal(message, **window) is None
    assert "DKIM" in email_verification.message_refusal(replace(message, authenticated=False), **window)
    assert email_verification.message_refusal(replace(message, sender="x@attacker.example"), **window)
    assert email_verification.message_refusal(replace(message, recipients=("x@example.invalid",)), **window)
    assert email_verification.message_refusal(
        replace(message, received_at=now - dt.timedelta(minutes=1)), **window)

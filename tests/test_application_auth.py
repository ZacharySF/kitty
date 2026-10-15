"""Sign-in automation in headless Firefox against a local HTTPS site with synthetic credentials.

The site records every request it receives, so the tests can see exactly what was
sent where: one sign-in POST at most, nothing to unapproved origins, no password
in a URL, and no application submit.
"""

from __future__ import annotations

import datetime
import ipaddress
import ssl
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from kitty.config import AuthenticationSettings
from kitty.core import jobs as jobs_core
from kitty.core.apply import bidi as bidi_module
from kitty.core.apply.auth import authenticate
from kitty.core.apply.bidi import open_tab
from kitty.core.apply.browser import headless_firefox
from kitty.core.apply.credentials import CREDENTIALS_FILE, load_credentials
from kitty.core.apply.filler import fill_job
from kitty.settings import Settings, find_firefox

x509 = pytest.importorskip("cryptography.x509")

EMAIL = "pat.synthetic@example.invalid"
PASSWORD = "Synthetic-Pass#42"

LOGIN = """<!doctype html><title>Sign in</title><body>
<h1>Candidate sign in</h1>{extra}
<form method="{method}" action="/session">
  <label>Email address <input type="email" name="email"></label>
  <label>Password <input type="password" name="password"></label>
  <button type="submit">Sign in</button>
</form><a href="/reset">Forgot password?</a></body>"""

SIGNUP = """<!doctype html><title>Create account</title><body>
<form method="post" action="/session">
  <label>Email address <input type="email" name="email"></label>
  <label>Password <input type="password" name="password" autocomplete="new-password"></label>
  <label>Verify new password <input type="password" name="confirm" autocomplete="new-password"></label>
  <button type="submit">Create Account</button>
</form></body>"""

APPLICATION = """<!doctype html><title>Apply</title><body>
<form id="application" method="post" action="/apply">
  <label>Preferred name <input name="preferred_name"></label>
  <button type="submit">Submit application</button>
</form></body>"""


class _Site:
    """What the HTTPS test site serves and what it has received."""

    def __init__(self) -> None:
        self.login_page = LOGIN.format(extra="", method="post")
        self.accept = True
        self.requests: list[tuple[str, str, dict]] = []   # (method, path, fields)
        self.pages: dict[str, str] = {}


def _handler(site: _Site):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):          # keep test output quiet
            pass

        def _send(self, body: str, status: int = 200, location: str | None = None):
            data = body.encode()
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parts = urlsplit(self.path)
            site.requests.append(("GET", parts.path, parse_qs(parts.query)))
            if parts.path == "/redirect":
                self._send("", 303, site.origin.replace("127.0.0.1", "localhost") + "/signup")
                return
            page = site.pages.get(parts.path) or {
                "/login": site.login_page, "/signup": SIGNUP, "/application": APPLICATION,
            }.get(parts.path)
            self._send(page or "<p>not here</p>", 200 if page else 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            fields = parse_qs(self.rfile.read(length).decode())
            site.requests.append(("POST", self.path, fields))
            if self.path == "/session" and site.accept:
                self._send("", 303, "/application")
            else:
                self._send(site.login_page)

    return Handler


@pytest.fixture(scope="module")
def https_site(tmp_path_factory):
    """A local HTTPS server with a throwaway self-signed certificate for 127.0.0.1."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    folder = tmp_path_factory.mktemp("tls")
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "cert.pem", folder / "key.pem")

    site = _Site()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(site))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    site.origin = f"https://127.0.0.1:{server.server_address[1]}"
    yield site
    server.shutdown()


@pytest.fixture(scope="module")
def firefox(tmp_path_factory):
    """Headless Firefox that accepts the test site's self-signed certificate (tests only)."""
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    original = bidi_module.Bidi.command

    def command(self, method, params, **kwargs):
        if method == "session.new":
            params["capabilities"]["alwaysMatch"]["acceptInsecureCerts"] = True
        return original(self, method, params, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bidi_module.Bidi, "command", command)
        settings = Settings.load(root=tmp_path_factory.mktemp("firefox"))
        with headless_firefox(settings) as bidi:
            yield bidi


@pytest.fixture
def page(firefox):
    tab = open_tab(firefox)
    yield tab
    tab.close()


@pytest.fixture
def site(https_site):
    https_site.login_page = LOGIN.format(extra="", method="post")
    https_site.accept = True
    https_site.requests.clear()
    https_site.pages.clear()
    return https_site


def _space(workspace, site, **policy):
    """Workspace with synthetic credentials and an authentication policy for the site."""
    path = workspace.settings.root / CREDENTIALS_FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nAPPLICATION_ACCOUNT_PASSWORD={PASSWORD}\n")
    path.chmod(0o600)
    settings = AuthenticationSettings(**({"enabled": True, "approved_origins": [site.origin]} | policy))
    workspace.config = replace(workspace.config, apply=workspace.config.apply.model_copy(
        update={"authentication": settings}))
    return workspace


def _job(space, site, path="/login"):
    job, _ = jobs_core.upsert_job(space.conn, company="Synthetic Co", title="Intern",
                                  url=site.origin + path, tracks=["software"])
    return job


def _posts(site):
    return [(path, fields) for method, path, fields in site.requests if method == "POST"]


def _value(page, selector):
    return page.main.call("(s) => document.querySelector(s)?.value ?? null", selector, page_realm=True)


def _stored_text(space) -> str:
    rows = space.conn.execute("SELECT * FROM jobs").fetchall()
    rows += space.conn.execute("SELECT * FROM screening_questions").fetchall()
    return " ".join(str(value) for row in rows for value in tuple(row))


def test_signs_in_once_then_prepares_the_application_without_submitting(workspace, site, page):
    space = _space(workspace, site)
    job = _job(space, site)
    report = fill_job(page, space.conn, space.settings, space.config, job)

    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD]})]
    assert page.main.url == site.origin + "/application"
    assert report.pages == 1 and not report.note.startswith(("login", "captcha", "consent")), report.note
    assert not any(path == "/apply" for _, path, _ in site.requests)          # nothing submitted
    assert not any(PASSWORD in str(fields) for method, _, fields in site.requests if method == "GET")
    assert PASSWORD not in _stored_text(space) and PASSWORD not in str(report)


def test_rejected_sign_in_is_not_retried(workspace, site, page):
    site.accept = False
    space = _space(workspace, site)
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, site))

    assert len(_posts(site)) == 1
    assert report.status == "blocked" and "did not advance" in report.note
    assert PASSWORD not in _stored_text(space)


def test_unapproved_origin_gets_no_credentials(workspace, site, page):
    space = _space(workspace, site, approved_origins=["https://127.0.0.1:1"])
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, site))

    assert report.status == "blocked" and report.note.startswith("login:")
    assert _posts(site) == []
    assert _value(page, "[name=email]") == "" and _value(page, "[name=password]") == ""


def test_disabled_policy_keeps_the_manual_login_gate(workspace, site, page):
    space = _space(workspace, site, enabled=False, approved_origins=[])
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, site))
    assert report.status == "blocked" and report.note.startswith("login:")
    assert _posts(site) == [] and _value(page, "[name=password]") == ""


@pytest.mark.parametrize(("extra", "method", "blocker"), [
    ('<div class="grecaptcha-badge" style="width:50px;height:20px">reCAPTCHA</div>', "post", "captcha:"),
    ("<p>By signing in, you agree to our Terms of Use.</p>", "post", "consent:"),
    ('<label><input type="checkbox" name="terms" required> I accept the terms</label>', "post", "consent:"),
    ("", "get", "login:"),
])
def test_challenges_consent_and_get_forms_stop_before_any_credential(workspace, site, page,
                                                                      extra, method, blocker):
    site.login_page = LOGIN.format(extra=extra, method=method)
    if "checkbox" in extra:           # a terms box inside the form itself
        site.login_page = site.login_page.replace("<button", extra + "<button")
    space = _space(workspace, site)
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, site))

    assert report.status == "blocked" and report.note.startswith(blocker), report.note
    assert _posts(site) == []
    assert _value(page, "[name=password]") == ""
    assert not any(PASSWORD in str(fields) for _, _, fields in site.requests)


def test_account_creation_needs_its_own_opt_in(workspace, site, page):
    space = _space(workspace, site)
    page.goto(site.origin + "/signup")
    credentials = load_credentials(space.settings)
    result = authenticate(page, space.config.apply.authentication, credentials)
    assert not result.acted and "account creation is off" in result.blocker
    assert _posts(site) == [] and _value(page, "[name=password]") == ""

    allowed = AuthenticationSettings(enabled=True, allow_account_creation=True, approved_origins=[site.origin])
    page.goto(site.origin + "/signup")
    assert authenticate(page, allowed, credentials).acted
    deadline = time.monotonic() + 10           # the click submits asynchronously
    while not _posts(site) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]})]


def test_existing_different_value_is_not_overwritten(workspace, site, page):
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    page.main.call("() => { document.querySelector('[name=email]').value = 'someone.else@example.invalid'; }",
                   page_realm=True)
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and "already holds other values" in result.blocker
    assert _posts(site) == [] and _value(page, "[name=password]") == ""


@pytest.mark.parametrize("entry", [
    '<a href="/signup">Create account</a>',
    '<button type="button" onclick="location.href=\'/signup\'">Create account</button>',
])
def test_explicit_new_account_choice_enters_registration_once(workspace, site, page, entry):
    site.login_page = LOGIN.format(extra=entry, method="post")
    space = _space(workspace, site, allow_account_creation=True, account_mode="create")
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None  # request sent, not a success assertion
    deadline = time.monotonic() + 10
    while not _posts(site) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]})]
    assert not any(path == "/apply" for _, path, _ in site.requests)


def test_explicit_login_choice_enters_login_not_registration(workspace, site, page):
    space = _space(workspace, site, account_mode="login")
    page.goto(site.origin + "/application")
    page.main.call("""() => { document.body.innerHTML =
        '<a href="/login">Sign in</a><a href="/signup">Create account</a>'; }""", page_realm=True)
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted
    deadline = time.monotonic() + 10
    while not _posts(site) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD]})]
    assert not any(path == "/signup" for _, path, _ in site.requests)


@pytest.mark.parametrize("extra", [
    '<p>By creating an account you agree to the terms.</p><a href="/signup">Create account</a>',
    '<label><input type="checkbox">I accept terms</label><a href="/signup">Create account</a>',
    '<div class="grecaptcha-badge">Challenge</div><a href="/signup">Create account</a>',
    '<p>Enter your verification code</p><a href="/signup">Create account</a>',
    '<button>Submit application</button><a href="/signup">Create account</a>',
    '<a href="https://unapproved.invalid/signup">Create account</a>',
    '<form method="post" action="/signup"><button>Create account</button></form>',
    '<a href="/signup">Create account</a><a href="/signup">Register</a>',
])
def test_registration_entry_refuses_unsafe_or_ambiguous_landing(workspace, site, page, extra):
    site.login_page = "<!doctype html><body>" + extra + "</body>"
    space = _space(workspace, site, allow_account_creation=True, account_mode="create")
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker
    assert _posts(site) == []
    assert not any(path == "/signup" for _, path, _ in site.requests)


def test_registration_entry_redirect_rechecks_origin_before_credentials(workspace, site, page):
    site.login_page = '<!doctype html><body><a href="/redirect">Create account</a></body>'
    space = _space(workspace, site, allow_account_creation=True, account_mode="create")
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker
    assert _posts(site) == []
    assert not any(PASSWORD in str(fields) for _, _, fields in site.requests)


@pytest.mark.parametrize("change", ["action", "frame", "submit", "disabled", "unapproved"])
def test_registration_boundaries_send_no_credentials(workspace, site, page, change):
    policy = {"allow_account_creation": True, "account_mode": "create"}
    if change == "disabled":
        policy.update(enabled=False, allow_account_creation=False)
    elif change == "unapproved":
        policy["approved_origins"] = ["https://unapproved.invalid"]
    space = _space(workspace, site, **policy)
    if change == "action":
        site.login_page = SIGNUP.replace('action="/session"', 'action="https://unapproved.invalid/session"')
    elif change == "frame":
        other = site.origin.replace("127.0.0.1", "localhost")
        site.login_page = f'<iframe src="{other}/signup"></iframe>'
    elif change == "submit":
        site.login_page = SIGNUP.replace("</form>", "<button>Submit application</button></form>")
    else:
        site.login_page = SIGNUP
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker
    assert _posts(site) == []
    assert not any(PASSWORD in str(fields) for _, _, fields in site.requests)


def test_failed_registration_request_never_falls_back_to_login(workspace, site, page):
    site.accept = False
    site.login_page = LOGIN.format(extra='<a href="/signup">Create account</a>', method="post")
    space = _space(workspace, site, allow_account_creation=True, account_mode="create")
    report = fill_job(page, space.conn, space.settings, space.config, _job(space, site))
    assert len(_posts(site)) == 1 and "confirm" in _posts(site)[0][1]
    assert report.status == "blocked" and "did not advance" in report.note


def test_login_choice_never_submits_an_already_open_registration(workspace, site, page):
    space = _space(workspace, site, allow_account_creation=True, account_mode="login")
    page.goto(site.origin + "/signup")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker
    assert _posts(site) == [] and _value(page, "[name=password]") == ""


def test_legacy_recognized_mode_does_not_guess_a_new_account(workspace, site, page):
    site.login_page = '<!doctype html><body><a href="/signup">Create account</a></body>'
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker
    assert _posts(site) == []
    assert not any(path == "/signup" for _, path, _ in site.requests)


def test_website_review_registers_from_account_landing_then_fills(workspace, site, page):
    from types import SimpleNamespace
    from kitty import web_automation

    # Saved credentials alone do not enable authentication globally.
    space = _space(workspace, site, enabled=False, approved_origins=[])
    site.login_page = ('<!doctype html><body><h1>Candidate account</h1>'
                       '<a href="/signup">Create account</a>'
                       '<a href="/login">Sign in</a></body>')
    job = _job(space, site)
    captured = []
    app = SimpleNamespace(
        settings=space.settings, config=space.config,
        automation=SimpleNamespace(start=lambda config, *a, **k: captured.append(config)),
    )
    body = {"mode": "selected", "job_ids": [job.id], "authentication_mode": "create"}
    plan = web_automation.post(app, space.conn, "/api/automation/preflight", body)
    assert plan["authentication"]["credentials_available"]
    assert plan["authentication"]["approved_origins"] == [site.origin]
    web_automation.post(app, space.conn, "/api/automation/start", {
        **body, "confirmed": True, "authorize_authentication": True, "fingerprint": plan["fingerprint"],
    })
    report = fill_job(page, space.conn, space.settings, captured[0], job)
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]})]
    assert page.main.url == site.origin + "/application"
    assert not report.note.startswith(("login", "captcha", "consent")), report.note
    assert not any(path == "/apply" for _, path, _ in site.requests)
    assert not space.config.apply.authentication.enabled
    assert PASSWORD not in _stored_text(space) and PASSWORD not in str(report)


def _google(space, site, monkeypatch, *, chooser=None, popup=False):
    """Only tests substitute Google's exact origin with a local HTTPS host."""
    from kitty.core.apply import auth
    google = site.origin.replace("127.0.0.1", "localhost")
    monkeypatch.setattr(auth, "_GOOGLE_ORIGIN", google)
    space.config = replace(space.config, profile=space.config.profile.model_copy(update={"email": EMAIL}))
    policy = AuthenticationSettings(enabled=True, approved_origins=[site.origin], account_mode="google")
    policy._google_email = EMAIL
    space.config = replace(space.config, apply=space.config.apply.model_copy(update={"authentication": policy}))
    site.pages["/application"] = APPLICATION.replace("Preferred name", "Email").replace("preferred_name", "email")
    site.login_page = ('<h1>Sign in</h1><a href="' + google + '/chooser"' +
                       (' target="_blank" rel="opener"' if popup else '') + '>Continue with Google</a>')
    if popup:
        # Cross-origin OAuth popups notify their opener; they cannot read/write
        # the opener's location.href property in Firefox.
        site.login_page += (
            "<script>addEventListener('message', e => {"
            f"if (e.origin === '{google}' && e.data === 'fixture-signed-in') "
            "location.href = '/application';"
            "});</script>"
        )
    if chooser is None:
        chooser = f'<button type="button" data-identifier="{EMAIL}" onclick="location.href=\'/selected\'">{EMAIL}</button>'
    site.pages["/chooser"] = "<h1>Choose an account</h1>" + chooser
    site.pages["/selected"] = (
        f"<script>window.opener.postMessage('fixture-signed-in', '{site.origin}'); window.close()</script>"
        if popup else f"<script>location.href='{site.origin}/application'</script>"
    )
    return policy


@pytest.mark.parametrize("popup", [False, True])
def test_google_existing_exact_account_returns_to_application_without_password(
    workspace, site, page, monkeypatch, popup,
):
    _google(workspace, site, monkeypatch, popup=popup)
    report = fill_job(page, workspace.conn, workspace.settings, workspace.config, _job(workspace, site))
    assert not report.error
    assert _value(page, 'input[name="email"]') == EMAIL
    assert any(path == "/selected" for _, path, _ in site.requests)
    assert not _posts(site)
    assert all(PASSWORD not in str(row) for row in site.requests)
    assert page.main.url == site.origin + "/application"


@pytest.mark.parametrize("chooser", [
    '<button type="button">Use another account</button>',
    '<button type="button" data-identifier="wrong@example.invalid">Wrong account</button>',
    f'<button data-identifier="{EMAIL}">One</button><button data-identifier="{EMAIL}">Two</button>',
    f'<button data-identifier="{EMAIL}">{EMAIL} Signed out</button>',
    f'<button data-identifier="{EMAIL}">{EMAIL}</button><input type="password">',
    f'<button data-identifier="{EMAIL}">{EMAIL}</button><input autocomplete="one-time-code">',
    f'<p>App wants access to your Google Account</p><button data-identifier="{EMAIL}">{EMAIL}</button><button>Allow</button>',
    f'<p>This browser may not be secure</p><button data-identifier="{EMAIL}">{EMAIL}</button>',
])
def test_google_unsafe_chooser_hands_off_without_clicking(
    workspace, site, page, monkeypatch, chooser,
):
    from kitty.core.apply.auth import google_session
    policy = _google(workspace, site, monkeypatch, chooser=chooser)
    page.goto(site.origin + "/login")
    result = google_session(page, policy, EMAIL)
    assert not result.acted
    assert not any(path == "/selected" for _, path, _ in site.requests)
    assert not _posts(site)
    assert _value(page, 'input[type=password]') in (None, "")


@pytest.mark.parametrize("case", ["origin", "ambiguous", "drive", "consent", "identity"])
def test_google_entry_requires_reviewed_unambiguous_signin(
    workspace, site, page, monkeypatch, case,
):
    from kitty.core.apply.auth import google_session
    policy = _google(workspace, site, monkeypatch)
    if case == "origin":
        policy.approved_origins = ["https://unapproved.example.invalid"]
    elif case == "ambiguous":
        site.login_page += site.login_page
    elif case == "drive":
        site.login_page = "<h1>Sign in</h1><button>Google Drive</button>"
    elif case == "consent":
        site.login_page += "<p>By continuing you agree to the terms.</p>"
    else:
        policy._google_email = "changed@example.invalid"
    page.goto(site.origin + "/login")
    result = google_session(page, policy, EMAIL)
    assert not result.acted
    assert not any(path == "/chooser" for _, path, _ in site.requests)
    assert not _posts(site)


def test_google_manual_login_reuses_original_tab_and_preserves_fields(
    workspace, site, page, monkeypatch,
):
    from kitty.core.apply.filler import _retained_tab
    _google(workspace, site, monkeypatch, chooser="<button>Use another account</button>")
    job = _job(workspace, site, "/login?gh_jid=4321")
    first = fill_job(page, workspace.conn, workspace.settings, workspace.config, job)
    assert first.status == "blocked"
    # Synthetic manual completion, never a real Google authentication.
    page.goto(site.origin + "/application?gh_jid=4321")
    page.main.call("() => { document.querySelector('input').value = 'Manually retained'; }")
    saved = jobs_core.get_job(workspace.conn, job.id)
    retained = _retained_tab(page.bidi, saved)
    assert retained is not None and retained.context == page.context
    report = fill_job(retained, workspace.conn, workspace.settings, workspace.config, saved, resume_tab=True)
    assert report.resumed
    assert _value(page, "input") == "Manually retained"
    assert not _posts(site)
    page.goto(site.origin + "/application?gh_jid=9876")
    assert _retained_tab(page.bidi, saved) is None
    page.goto(site.origin + "/application")
    assert _retained_tab(page.bidi, saved) is None


def test_google_unrelated_existing_tab_is_never_selected(workspace, site, page, monkeypatch):
    from kitty.core.apply.auth import google_session
    policy = _google(workspace, site, monkeypatch)
    unrelated = open_tab(page.bidi)
    try:
        unrelated.goto(site.origin.replace("127.0.0.1", "localhost") + "/chooser")
        # The employer's actual flow has no signed-in account.
        site.pages["/chooser"] = "<h1>Choose an account</h1><button>Use another account</button>"
        page.goto(site.origin + "/login")
        result = google_session(page, policy, EMAIL)
        assert not result.acted
        assert unrelated.main.url.endswith("/chooser")
        assert not any(path == "/selected" for _, path, _ in site.requests)
    finally:
        unrelated.close()


def test_google_chooser_never_runs_on_unapproved_origin(workspace, site, page, monkeypatch):
    from kitty.core.apply.auth import _GOOGLE_CLICK
    _google(workspace, site, monkeypatch)
    page.goto(site.origin + "/chooser")
    result = page.main.call(_GOOGLE_CLICK, [site.origin], "https://accounts.google.com", EMAIL, True)
    assert result is not True
    assert not any(path == "/selected" for _, path, _ in site.requests)


@pytest.mark.parametrize("mode", ["google", "recognized"])
def test_employer_password_never_sent_to_google_origin(workspace, site, page, monkeypatch, mode):
    from kitty.core.apply import auth
    from kitty.core.apply.credentials import ApplicationCredentials

    monkeypatch.setattr(auth, "_GOOGLE_ORIGIN", site.origin)
    policy = AuthenticationSettings(
        enabled=True, approved_origins=[site.origin], account_mode=mode,
    )
    page.goto(site.origin + "/login")
    result = authenticate(page, policy, ApplicationCredentials(EMAIL, PASSWORD))
    assert not result.acted
    assert _posts(site) == []
    assert _value(page, "[name=email]") == ""
    assert _value(page, "[name=password]") == ""

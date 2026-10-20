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
        self.post_responses: list[str] = []


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
            if site.post_responses:
                self._send(site.post_responses.pop(0))
            elif self.path == "/session" and site.accept:
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
    https_site.post_responses.clear()
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
    assert report.status == "blocked"
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
    ('<label>Security code<input autocomplete="one-time-code"></label>', "post", "verification:"),
    ("", "get", "login:"),
])
def test_challenges_and_get_forms_stop_before_any_credential(workspace, site, page,
                                                           extra, method, blocker):
    site.login_page = LOGIN.format(extra=extra, method=method)
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


@pytest.mark.parametrize("label", ["Continue with email", "Sign in with email"])
@pytest.mark.parametrize("entry", [
    '<a href="{other_origin}/email-signin">{label}</a>',
    '<a href="{http_origin}/email-signin">{label}</a>',
    '<a href="{userinfo_origin}/email-signin">{label}</a>',
    '<a href="/email-signin" download>{label}</a>',
    '<a href="/email-signin" target="_blank">{label}</a>',
    '<a href="/email-signin" aria-label="Submit application">{label}</a>',
    '<form method="post" action="/final"><button type="submit">{label}</button></form>',
])
def test_email_account_entry_uses_common_destination_guards(workspace, site, page, entry, label):
    entry = entry.format(
        label=label, other_origin=site.origin.replace("127.0.0.1", "localhost"),
        http_origin=site.origin.replace("https:", "http:"),
        userinfo_origin=site.origin.replace("https://", "https://unexpected@"),
    )
    site.pages["/email-signin"] = LOGIN.format(extra="", method="post")
    site.login_page = """<!doctype html><body><h1>Candidate sign in</h1>""" + entry + """
      <input type="email" name="email" style="display:none">
      <input type="password" name="password" style="display:none">
      <script>document.querySelector('a, button').addEventListener('click', () => {
        document.body.dataset.entryClicked = 'yes';
      });</script></body>"""
    space = _space(workspace, site, account_mode="login")
    page.goto(site.origin + "/login")
    contexts = {c["context"] for c in page.bidi.command("browsingContext.getTree", {})["contexts"]}
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and not result.filled and result.blocker.startswith("login:")
    assert page.main.url == site.origin + "/login"
    assert not page.main.call("() => document.body.dataset.entryClicked")
    assert _value(page, "[name=email]") == _value(page, "[name=password]") == ""
    assert contexts == {
        c["context"] for c in page.bidi.command("browsingContext.getTree", {})["contexts"]
    }
    assert not _posts(site)
    assert not any(path == "/email-signin" for _, path, _ in site.requests)
    assert not any(EMAIL in str(fields) or PASSWORD in str(fields) for _, _, fields in site.requests)


@pytest.mark.parametrize("entry", [
    '<a href="/email-signin">Continue with email</a>',
    '<button type="button" onclick="location.href=\'/email-signin\'">Sign in with email</button>',
])
def test_safe_email_account_entry_preserves_login_without_application_submit(workspace, site, page, entry):
    site.login_page = "<!doctype html><body><h1>Candidate sign in</h1>" + entry + "</body>"
    site.pages["/email-signin"] = LOGIN.format(extra="", method="post")
    space = _space(workspace, site, account_mode="login")
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    assert result.filled == ("email", "password")
    deadline = time.monotonic() + 10
    while not _posts(site) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD]})]
    assert sum(path == "/email-signin" for _, path, _ in site.requests) == 1
    assert not any(path == "/apply" for _, path, _ in site.requests)


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
    space = _space(workspace, site, allow_account_creation=True, account_mode="recognized")
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
    assert _value(page, 'input[name="email"]') == EMAIL, (report.status, report.note)
    assert any(path == "/selected" for _, path, _ in site.requests)
    assert not _posts(site)
    assert all(PASSWORD not in str(row) for row in site.requests)
    assert page.main.url == site.origin + "/application"


@pytest.mark.parametrize("popup", [False, True])
@pytest.mark.parametrize("lost_reply", ["entry", "chooser"])
def test_google_navigation_with_lost_click_reply_waits_without_repeating(
    workspace, site, page, monkeypatch, popup, lost_reply,
):
    from kitty.core.apply import auth

    _google(workspace, site, monkeypatch, popup=popup)
    original = bidi_module.Frame.call
    attempts = {"entry": 0, "chooser": 0}

    def call(self, function, *args, **kwargs):
        if function != auth._GOOGLE_CLICK:
            return original(self, function, *args, **kwargs)
        step = "chooser" if args[-1] else "entry"
        attempts[step] += 1
        result = original(self, function, *args, **kwargs)
        if step == lost_reply and result is True:
            # The navigation happened, but its old script realm disappeared
            # before Firefox could acknowledge the click.
            raise bidi_module.BidiError("script.callFunction: document was unloaded")
        return result

    monkeypatch.setattr(bidi_module.Frame, "call", call)
    report = fill_job(page, workspace.conn, workspace.settings, workspace.config, _job(workspace, site))
    assert _value(page, 'input[name="email"]') == EMAIL, (report.status, report.note)
    assert not report.error and attempts == {"entry": 1, "chooser": 1}
    assert page.main.call("() => location.href") == site.origin + "/application"
    assert not _posts(site)
    assert all(PASSWORD not in str(row) for row in site.requests)


@pytest.mark.parametrize("popup", [False, True])
def test_google_return_retries_detached_document_observation(
    workspace, site, page, monkeypatch, popup,
):
    from kitty.core.apply import preflight

    _google(workspace, site, monkeypatch, popup=popup)
    original = preflight.inspect_page
    interrupted = False

    def inspect(tab):
        nonlocal interrupted
        if not interrupted and any(path == "/selected" for _, path, _ in site.requests):
            interrupted = True
            raise bidi_module.BidiError("script.callFunction: document was unloaded")
        return original(tab)

    monkeypatch.setattr(preflight, "inspect_page", inspect)
    report = fill_job(page, workspace.conn, workspace.settings, workspace.config, _job(workspace, site))
    assert interrupted
    assert _value(page, 'input[name="email"]') == EMAIL, (report.status, report.note)
    assert not report.error
    assert not _posts(site)


def test_google_pending_employer_url_is_not_an_approved_application_document(
    workspace, site, page, monkeypatch,
):
    from kitty.core.apply.auth import google_session

    policy = _google(workspace, site, monkeypatch)
    google = site.origin.replace("127.0.0.1", "localhost")
    # Even an application-shaped intermediate Google document cannot authorize
    # success just because getTree already advertises the employer destination.
    site.pages["/selected"] = site.pages["/application"] + (
        f"<script>setTimeout(() => location.href = '{site.origin}/application', 1000)</script>"
    )
    original = bidi_module.Bidi.command
    provisional_reads = 0

    def command(self, method, params, **kwargs):
        nonlocal provisional_reads
        result = original(self, method, params, **kwargs)
        if method == "browsingContext.getTree":
            for context in result["contexts"]:
                if context["context"] == page.context and context["url"] == google + "/selected":
                    provisional_reads += 1
                    context["url"] = site.origin + "/application"
        return result

    monkeypatch.setattr(bidi_module.Bidi, "command", command)
    page.goto(site.origin + "/login")
    result = google_session(page, policy, EMAIL)
    assert provisional_reads > 0
    assert result.acted, result.blocker
    assert page.main.call("() => location.href") == site.origin + "/application"
    assert _value(page, 'input[name="email"]') == ""
    assert not _posts(site)


def test_google_unacknowledged_choice_times_out_without_retry_or_success(
    workspace, site, page, monkeypatch,
):
    from types import SimpleNamespace
    from kitty.core.apply import auth

    policy = _google(workspace, site, monkeypatch)
    original = bidi_module.Frame.call
    choices = 0

    def call(self, function, *args, **kwargs):
        nonlocal choices
        if function == auth._GOOGLE_CLICK and args[-1]:
            choices += 1
            # A failed acknowledgement cannot prove that the choice happened.
            # Leave the chooser in place and require a bounded manual handoff.
            raise bidi_module.BidiError("script.callFunction: document was unloaded")
        return original(self, function, *args, **kwargs)

    monkeypatch.setattr(bidi_module.Frame, "call", call)
    ticks = iter(range(14))
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    page.goto(site.origin + "/login")
    result = auth.google_session(page, policy, EMAIL)
    assert choices == 1
    assert not result.acted and "has not returned" in result.blocker
    assert "manually" in result.blocker
    assert not any(path == "/selected" for _, path, _ in site.requests)
    assert not _posts(site)


@pytest.mark.parametrize("chooser", [
    '<button type="button">Use another account</button>',
    '<button type="button" data-identifier="wrong@example.invalid">Wrong account</button>',
    f'<button data-identifier="{EMAIL}">One</button><button data-identifier="{EMAIL}">Two</button>',
    f'<button data-identifier="{EMAIL}">{EMAIL} Signed out</button>',
    f'<button data-identifier="{EMAIL}">{EMAIL}</button><input type="password">',
    f'<button data-identifier="{EMAIL}">{EMAIL}</button><input autocomplete="one-time-code">',
    f'<div class="g-recaptcha">Challenge</div><button data-identifier="{EMAIL}">{EMAIL}</button>',
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
    assert result.blocker and "manually" in result.blocker
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


@pytest.mark.parametrize("entry", ["login", "signup"])
def test_auto_detects_required_account_flow_without_mode_selection(workspace, site, page, entry):
    space = _space(workspace, site, allow_account_creation=True)
    site.pages["/account"] = '<h1>Candidate account</h1>' + (
        '<a href="/login">Sign in</a><a href="/signup">Create account</a>'
        if entry == "login" else '<a href="/signup">Create account</a>')
    page.goto(site.origin + "/account")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    deadline = time.monotonic() + 10
    while page.main.url != site.origin + "/application" and time.monotonic() < deadline:
        time.sleep(0.1)
    posts = _posts(site)
    assert len(posts) == 1
    assert posts[0][1]["email"] == [EMAIL]
    assert posts[0][1]["password"] == [PASSWORD]
    assert ("confirm" in posts[0][1]) == (entry == "signup")
    assert page.main.url == site.origin + "/application"


def test_auto_creates_only_after_explicit_account_not_found(workspace, site, page):
    space = _space(workspace, site, allow_account_creation=True)
    site.post_responses.append(LOGIN.format(method="post", extra=(
        '<p role="alert">No account found for this email.</p>'
        '<a href="/signup">Create account</a>')))
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    deadline = time.monotonic() + 10
    while page.main.url != site.origin + "/application" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [
        ("/session", {"email": [EMAIL], "password": [PASSWORD]}),
        ("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
    ]
    assert page.main.url == site.origin + "/application"
    assert not any(path == "/apply" for _, path, _ in site.requests)


def test_auto_never_infers_new_account_from_incorrect_password(workspace, site, page):
    space = _space(workspace, site, allow_account_creation=True)
    site.post_responses.append(LOGIN.format(method="post", extra=(
        '<p role="alert">Incorrect email or password.</p><a href="/signup">Create account</a>')))
    job = _job(space, site)
    report = fill_job(page, space.conn, space.settings, space.config, job)
    assert report.status == "blocked"
    continued = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert continued.status == "blocked"
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD]})]
    assert _value(page, "[name=email]") == "" and _value(page, "[name=password]") == ""
    assert not any(path == "/signup" for _, path, _ in site.requests)


def _phenom(site):
    href = "/us/en/hvhapply?jobSeqNo=SITEJR7001EXTERNAL"
    link = f'<a data-ph-at-id="apply-link" data-ph-at-job-seqno-text="SITEJR7001EXTERNAL" href="{href}">Apply Now</a>'
    site.pages["/us/en/job/JR7001"] = f"<h1>Example role</h1>{link}{link}"
    site.pages["/us/en/hvhapply"] = """<h1>Contact info</h1>
      <input class="phw-form-control" form-field="firstName" label="What is your first name?">
      <input class="phw-form-control" form-field="lastName" label="What is your last name?">
      <input class="phw-form-control" form-field="email" label="What is your email address?">
      <fieldset><legend>Opt in to job marketing emails?</legend>
        <label>Yes<input type="radio" name="email-optin" value="true"></label>
        <label>No<input type="radio" name="email-optin" value="false"></label></fieldset>
      <button type="submit" onclick="fetch('/apply',{method:'POST'})">Next</button>"""


def test_phenom_follows_posting_and_fills_contact_without_consent_or_submission(workspace, site, page):
    _phenom(site)
    workspace.config = replace(workspace.config, profile=workspace.config.profile.model_copy(update={
        "name": "Pat Tester", "first_name": "Pat", "last_name": "Tester", "email": EMAIL,
    }))
    job = _job(workspace, site, "/us/en/job/JR7001")
    report = fill_job(page, workspace.conn, workspace.settings, workspace.config, job)
    assert _value(page, '[form-field=firstName]') == "Pat"
    assert _value(page, '[form-field=lastName]') == "Tester"
    assert _value(page, '[form-field=email]') == EMAIL
    assert page.main.call("() => [...document.querySelectorAll('input[type=radio]')].every(e => !e.checked)")
    assert report.pages == 1 and report.status == "partial"
    assert _posts(site) == []


@pytest.mark.parametrize("change", ["other-job", "external-origin", "final-action", "changed-after-review"])
def test_phenom_rejects_ambiguous_or_changed_entry(workspace, site, page, change):
    from kitty.core.apply.preflight import inspect_page, advance
    from kitty.core.apply.page import UnsafeAction
    _phenom(site)
    page.goto(site.origin + "/us/en/job/JR7001")
    reviewed = inspect_page(page)
    assert reviewed.entry == "phenom-apply"
    page.main.call("""change => {
      for (const link of document.querySelectorAll('a')) {
        if (change === 'other-job') link.href = '/us/en/hvhapply?jobSeqNo=SITEJR9999EXTERNAL';
        else if (change === 'external-origin') link.href = 'https://example.invalid/us/en/hvhapply?jobSeqNo=SITEJR7001EXTERNAL';
        else if (change === 'final-action') link.textContent = 'Submit application';
        else link.dataset.phAtJobSeqnoText = 'SITEJR9999EXTERNAL';
      }
    }""", change, page_realm=True)
    with pytest.raises(UnsafeAction):
        advance(reviewed, "other")
    assert _posts(site) == []
    assert not any(path == "/us/en/hvhapply" for _, path, _ in site.requests)


@pytest.mark.parametrize("entry", ["login", "signup"])
def test_website_default_account_flow_needs_review_not_mode_selection(workspace, site, page, entry):
    from types import SimpleNamespace
    from kitty import web_automation

    space = _space(workspace, site, enabled=False, approved_origins=[])
    site.pages["/account"] = '<h1>Candidate account</h1>' + (
        '<a href="/login">Sign in</a><a href="/signup">Create account</a>'
        if entry == "login" else '<a href="/signup">Create account</a>')
    job = _job(space, site, "/account")
    captured = []
    app = SimpleNamespace(
        settings=space.settings, config=space.config,
        automation=SimpleNamespace(start=lambda config, *args, **kwargs: captured.append(config)),
    )
    body = {"mode": "selected", "job_ids": [job.id]}
    plan = web_automation.post(app, space.conn, "/api/automation/preflight", body)
    assert plan["authentication"]["mode"] == "auto"
    assert plan["authentication"]["credentials_available"]
    assert plan["authentication"]["allow_account_creation"]
    assert plan["authentication"]["approved_origins"] == [site.origin]
    assert not captured and _posts(site) == []
    web_automation.post(app, space.conn, "/api/automation/start", {
        **body, "confirmed": True, "authorize_authentication": True, "fingerprint": plan["fingerprint"],
    })
    assert captured[0].apply.authentication.account_mode == "auto"
    report = fill_job(page, space.conn, space.settings, captured[0], job)
    posts = _posts(site)
    assert len(posts) == 1
    assert posts[0][1]["email"] == [EMAIL]
    assert posts[0][1]["password"] == [PASSWORD]
    assert ("confirm" in posts[0][1]) == (entry == "signup")
    assert page.main.url == site.origin + "/application"
    assert report.pages == 1
    assert not any(path == "/apply" for _, path, _ in site.requests)
    assert not space.config.apply.authentication.enabled
    assert PASSWORD not in _stored_text(space) and PASSWORD not in str(report)


@pytest.mark.parametrize(("markup", "fields"), [
    # Native login submit, with a registration navigation in the same form.
    (LOGIN.format(extra="", method="post").replace(
        "</form>", '<button type="button" onclick="location.href=\'/signup\'">Create account</button></form>'),
     {"email": [EMAIL], "password": [PASSWORD]}),
    # Registration already open: the alternate flow is not the credential action.
    (SIGNUP.replace("</form>", '<button type="button" onclick="location.href=\'/login\'">Sign in</button></form>'),
     {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
    # Non-native, form-less account component; email is outside the password row.
    ("""<section><label>Username<input name="username" autocomplete="username"></label>
       <div><label>Password<input type="password" name="password"></label>
       <div role="button" aria-label="Log in" tabindex="0"
         onclick="fetch('/session', {method:'POST', body:new URLSearchParams({
           username:document.querySelector('[name=username]').value,
           password:document.querySelector('[name=password]').value})})
           .then(() => location.href='/application')"></div></div></section>
       <style>[role=button] {width:100px;height:30px}</style>""",
     {"username": [EMAIL], "password": [PASSWORD]}),
    # Semantically labelled fields and an accessibility proxy over a hidden
    # submit, as observed on Workday, without relying on employer selectors.
    (SIGNUP.replace(
        '<button type="submit">Create Account</button>',
        """<div role="button" aria-label="Create Account" tabindex="0"
           style="width:100px;height:30px"
           onclick="this.nextElementSibling.click()"></div>
           <button type="submit" aria-hidden="true">Create Account</button>"""),
     {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
])
def test_account_action_is_distinguished_from_navigation_across_structures(
    workspace, site, page, markup, fields,
):
    space = _space(workspace, site, allow_account_creation=True)
    site.login_page = markup
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    deadline = time.monotonic() + 10
    while not _posts(site) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [("/session", fields)]
    assert not any(path in ("/signup", "/apply") for _, path, _ in site.requests)


def test_unrelated_cookie_agreement_does_not_block_an_open_account_form(workspace, site, page):
    cookie = """<aside role="dialog"><p>By clicking “Accept All Cookies,” you agree
      to the storing of cookies on your device.</p>
      <button onclick="fetch('/cookies', {method:'POST'})">Accept Cookies</button></aside>"""
    site.login_page = cookie + SIGNUP
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    assert result.filled == ("email", "password", "passwordConfirm")
    assert _posts(site) == [
        ("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
    ]
    assert not any(path == "/cookies" for _, path, _ in site.requests)


@pytest.mark.parametrize("consent", ["inside-text", "outside-text", "described", "checkbox", "radio",
                                   "create-terms", "late-terms", "uncertain-continuing"])
def test_account_consent_allows_safe_population_but_never_a_request(
    workspace, site, page, consent,
):
    markup = SIGNUP if consent == "create-terms" else LOGIN.format(extra="", method="post")
    if consent == "inside-text":
        markup = markup.replace("<button", "<p>By signing in, you agree to our terms.</p><button")
    elif consent == "outside-text":
        markup = "<p>By signing in, you agree to our terms.</p>" + markup
    elif consent == "described":
        markup = '<p id="terms">By continuing, you accept our privacy terms.</p>' + markup.replace(
            '<form ', '<form aria-describedby="terms" ')
    elif consent in ("checkbox", "radio"):
        markup = markup.replace("<button", (
            f'<label><input type="{consent}" name="terms" required>I accept the terms</label><button'))
    elif consent == "create-terms":
        markup = markup.replace("<button", """<p>By creating an external account, you are
            agreeing to the Terms and Conditions.</p><label><input type="checkbox" name="terms">
            I acknowledge the terms of use.</label><button""")
    elif consent == "late-terms":
        markup += """<script>document.querySelector('[name=email]').addEventListener('input', () => {
            const p = document.createElement('p');
            p.textContent = 'By signing in, you agree to our terms.';
            document.querySelector('form').append(p);
          }, {once:true});</script>"""
    else:
        markup = "<p>By continuing, you agree to the site's terms.</p>" + markup
    site.login_page = markup
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker.startswith("consent:")
    expected = ("email", "password", "passwordConfirm") if consent == "create-terms" else ("email", "password")
    assert result.filled == expected
    assert _value(page, "[name=email]") == EMAIL
    assert _value(page, "[name=password]") == PASSWORD
    assert page.main.call("() => [...document.querySelectorAll('input[type=checkbox], input[type=radio]')].every(e => !e.checked)")
    assert _posts(site) == []
    assert PASSWORD not in str(result) and EMAIL not in str(result)

@pytest.mark.parametrize("kind", ["native", "aria", "implicit"])
def test_authorized_creation_accepts_only_routine_required_account_terms(workspace, site, page, kind):
    if kind == "native":
        choice = '<label><input type="checkbox" name="terms" required>I accept the terms of service</label>'
    elif kind == "aria":
        choice = ('<div role="checkbox" aria-checked="false" aria-required="true" tabindex="0" '
                  'onclick="this.setAttribute(\'aria-checked\', \'true\')">I accept the privacy policy</div>')
    else:
        choice = '<p>By creating an account, you agree to the Terms and Conditions.</p>'
    site.login_page = SIGNUP.replace("<button", choice + "<button")
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    assert page.main.url == site.origin + "/application"
    assert not any(path == "/apply" for _, path, _ in site.requests)


@pytest.mark.parametrize("hazard", [
    None, "missing-binding", "optional", "extra-choice", "certification", "marketing", "biometric",
])
def test_generic_agreement_label_requires_one_bound_routine_required_account_checkbox(
    workspace, site, page, hazard,
):
    explanation = "By selecting the checkbox, you are agreeing to the Terms and Conditions."
    if hazard == "missing-binding":
        explanation = "Read the Terms and Conditions."
    elif hazard in ("certification", "marketing", "biometric"):
        explanation += {
            "certification": " I certify my application is accurate.",
            "marketing": " I accept promotional marketing.",
            "biometric": " I consent to biometric processing.",
        }[hazard]
    choice = ('<label><input type="checkbox" name="terms" '
              + ("" if hazard == "optional" else "required") + '>I agree</label>')
    if hazard == "extra-choice":
        choice += '<label><input type="checkbox" required>Another choice</label>'
    site.login_page = SIGNUP.replace("<button", f"<p>{explanation}</p>{choice}<button")
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))

    if hazard is None:
        assert result.acted and result.blocker is None
        assert _posts(site) == [("/session", {
            "email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD], "terms": ["on"],
        })]
    else:
        assert not result.acted and result.blocker.startswith("consent:")
        assert not page.main.call("() => document.querySelector('[name=terms]').checked")
        assert not _posts(site)


@pytest.mark.parametrize("extra", [
    "I certify the information is accurate",
    "I agree to the terms of service and promotional marketing",
    "I accept the privacy policy and waive arbitration rights",
])
def test_account_creation_authority_excludes_certifications_and_extra_consent(workspace, site, page, extra):
    site.login_page = SIGNUP.replace(
        "<button", f'<label><input type="checkbox" required>{extra}</label><button')
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker.startswith("consent:")
    assert not page.main.call("() => document.querySelector('input[type=checkbox]').checked")
    assert not _posts(site)



@pytest.mark.parametrize("change", ["ambiguous-submits", "readonly-password", "extra-field",
                                   "unapproved-proxy-action", "get-proxy-method", "external-extra-field"])
def test_uncertain_account_structure_is_not_permission_to_fill(workspace, site, page, change):
    markup = LOGIN.format(extra="", method="post")
    if change == "ambiguous-submits":
        markup = markup.replace("</form>", "<button type='submit'>Create account</button></form>")
    elif change == "readonly-password":
        markup = markup.replace('type="password"', 'type="password" readonly')
    elif change == "extra-field":
        markup = markup.replace("</form>", "<label>Security answer<input name='answer'></label></form>")
    elif change == "external-extra-field":
        markup = markup.replace("<form ", "<form id='account' ") + (
            "<label>Security answer<input name='answer' form='account'></label>")
    else:
        override = 'formaction="https://unapproved.invalid/session"' if change == "unapproved-proxy-action" else 'formmethod="get"'
        markup = markup.replace(
            '<button type="submit">Sign in</button>',
            '<div role="button" aria-label="Sign in" style="width:100px;height:30px" '
            'onclick="this.nextElementSibling.click()"></div>'
            f'<button type="submit" aria-hidden="true" {override}>Sign in</button>')
    site.login_page = markup
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker
    assert result.filled == ()
    assert _value(page, "[name=email]") == "" and _value(page, "[name=password]") == ""
    assert _posts(site) == []
    assert not any(path == "/signup" for _, path, _ in site.requests)


@pytest.mark.parametrize("change", ["destination", "captcha", "other-value"])
def test_each_credential_write_rechecks_security_and_existing_values(workspace, site, page, change):
    mutations = {
        "destination": "document.querySelector('form').action = 'https://unapproved.invalid/session'",
        "captcha": """const c = document.createElement('div'); c.className = 'grecaptcha-badge';
                      c.textContent = 'Challenge'; document.body.append(c)""",
        "other-value": "document.querySelector('[name=password]').value = 'Existing unrelated password'",
    }
    site.login_page += """<script>document.querySelector('[name=email]').addEventListener('input',
      () => {""" + mutations[change] + """}, {once:true});</script>"""
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker
    assert result.filled == ("email",)
    assert _value(page, "[name=email]") == EMAIL
    assert _value(page, "[name=password]") == ("Existing unrelated password" if change == "other-value" else "")
    assert _posts(site) == []


def test_existing_confirmation_value_prevents_all_account_writes(workspace, site, page):
    site.login_page = SIGNUP.replace('name="confirm"', 'name="confirm" value="Existing unrelated password"')
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and "already holds other values" in result.blocker
    assert result.filled == ()
    assert _value(page, "[name=email]") == "" and _value(page, "[name=password]") == ""
    assert _value(page, "[name=confirm]") == "Existing unrelated password"
    assert _posts(site) == []


def test_auto_switches_to_login_only_on_explicit_existing_account_error(workspace, site, page):
    site.login_page = SIGNUP
    site.post_responses.append(SIGNUP.replace("</form>", (
        '<p role="alert">Email is already registered.</p>'
        '<button type="button" onclick="location.href=\'/signin\'">Sign in</button></form>')))
    site.pages["/signin"] = LOGIN.format(extra="", method="post")
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    deadline = time.monotonic() + 10
    while len(_posts(site)) < 2 and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [
        ("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
        ("/session", {"email": [EMAIL], "password": [PASSWORD]}),
    ]


@pytest.mark.parametrize("mode", ["auto", "create"])
def test_created_account_can_require_a_separate_sign_in(workspace, site, page, mode):
    site.login_page = SIGNUP
    site.post_responses = [LOGIN.format(extra="", method="post")]
    space = _space(workspace, site, allow_account_creation=True, account_mode=mode)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))

    assert result.acted and result.blocker is None
    creation = ("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]})
    if mode == "auto":
        assert _posts(site) == [creation, ("/session", {"email": [EMAIL], "password": [PASSWORD]})]
        assert any(path == "/application" for _, path, _ in site.requests)
    else:
        assert _posts(site) == [creation]
        assert _value(page, "[name=email]") == _value(page, "[name=password]") == ""


def test_post_creation_sign_in_rejection_does_not_retry_or_recreate(workspace, site, page):
    site.login_page = SIGNUP
    site.post_responses = [
        LOGIN.format(extra="", method="post"),
        LOGIN.format(extra='<p role="alert">Incorrect email or password.</p>', method="post"),
    ]
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))

    assert result.acted and result.blocker and result.blocker.startswith("login:")
    assert _posts(site) == [
        ("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
        ("/session", {"email": [EMAIL], "password": [PASSWORD]}),
    ]
    assert not any(path == "/application" for _, path, _ in site.requests)


def test_open_create_form_does_not_switch_to_login_without_account_exists_evidence(workspace, site, page):
    site.login_page = SIGNUP.replace("</form>", (
        '<p>Already have an account?</p>'
        '<button type="button" onclick="location.href=\'/signin\'">Sign in</button></form>'))
    site.pages["/signin"] = LOGIN.format(extra="", method="post")
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    assert _posts(site) == [
        ("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
    ]
    assert not any(path == "/signin" for _, path, _ in site.requests)


def test_conditional_existing_account_hint_is_not_evidence_to_switch(workspace, site, page):
    site.login_page = SIGNUP
    site.post_responses.append(SIGNUP.replace("</form>", (
        '<p role="alert">If you already have an account, sign in instead.</p>'
        '<button type="button" onclick="location.href=\'/signin\'">Sign in</button></form>')))
    site.pages["/signin"] = LOGIN.format(extra="", method="post")
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    assert _posts(site) == [
        ("/session", {"email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD]}),
    ]
    assert not any(path == "/signin" for _, path, _ in site.requests)


@pytest.mark.parametrize("enables", [False, True])
def test_disabled_account_action_is_filled_but_only_clicked_if_it_becomes_enabled(
    workspace, site, page, enables,
):
    site.login_page = LOGIN.format(extra="", method="post").replace(
        '<button type="submit">', '<button type="submit" disabled>')
    if enables:
        site.login_page += """<script>document.querySelector('[name=password]').addEventListener('input',
            () => { document.querySelector('button').disabled = false; });</script>"""
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.filled == ("email", "password")
    if enables:
        assert result.acted and result.blocker is None
        assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD]})]
    else:
        assert not result.acted and "disabled" in result.blocker
        assert _value(page, "[name=email]") == EMAIL and _value(page, "[name=password]") == PASSWORD
        assert _posts(site) == []


def test_explicit_login_uses_local_navigation_without_accepting_create_terms(workspace, site, page):
    site.login_page = '<header><button>Sign in</button></header><section>' + SIGNUP.replace(
        "</form>", """<label><input type="checkbox" name="terms">I accept the terms</label>
          </form><button type="button" onclick="location.href='/signin'">Sign in</button>""") + "</section>"
    site.pages["/signin"] = LOGIN.format(extra="", method="post")
    space = _space(workspace, site, account_mode="login")
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    deadline = time.monotonic() + 10
    while not _posts(site) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD]})]


@pytest.mark.parametrize("transport", ["default-get", "empty-override", "proxy-empty-override"])
def test_native_credential_transport_cannot_default_to_get(workspace, site, page, transport):
    markup = LOGIN.format(extra="", method="post")
    if transport == "default-get":
        markup = markup.replace('method="post" action="/session"', "")
    else:
        markup = markup.replace('type="submit"', 'type="submit" formmethod=""')
        if transport == "proxy-empty-override":
            markup = markup.replace('<button type="submit"', (
                '<div role="button" aria-label="Sign in" style="width:100px;height:30px" '
                'onclick="this.nextElementSibling.click()"></div><button aria-hidden="true" type="submit"'))
    site.login_page = markup
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted
    assert _value(page, "[name=email]") == _value(page, "[name=password]") == ""
    assert not _posts(site)
    assert not any(PASSWORD in str(fields) for method, _, fields in site.requests if method == "GET")


@pytest.mark.parametrize(("label", "verb"), [("Create My Account", "clicking"), ("Sign in", "selecting")])
def test_external_agreement_matches_the_actual_account_action(workspace, site, page, label, verb):
    markup = SIGNUP.replace("Create Account", label) if label.startswith("Create") else LOGIN.format(extra="", method="post")
    site.login_page = f"<p>By {verb} {label}, you agree to our Terms and Privacy Policy.</p>" + markup
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker.startswith("consent:")
    assert _value(page, "[name=email]") == EMAIL
    assert _value(page, "[name=password]") == PASSWORD
    assert not _posts(site)


@pytest.mark.parametrize("choices", [
    '<input id="terms" type="checkbox"><label for="terms">I accept the terms</label>'
    '<input id="remember" type="checkbox"><label for="remember">Remember me</label>',
    '<input id="remember" type="checkbox" aria-required="true"><label for="remember">Remember me</label>',
])
def test_remember_me_does_not_exempt_other_or_required_account_choices(workspace, site, page, choices):
    site.login_page = LOGIN.format(extra="", method="post").replace("<button", choices + "<button")
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker.startswith("consent:")
    assert _value(page, "[name=email]") == EMAIL
    assert page.main.call("() => [...document.querySelectorAll('input[type=checkbox]')].every(e => !e.checked)")
    assert not _posts(site)


@pytest.mark.parametrize("destination", ["same-posting", "another-posting", "another-origin"])
def test_manual_account_consent_handoff_reuses_only_the_approved_posting(workspace, site, page, destination):
    from kitty.core.apply.filler import _retained_tab
    space = _space(workspace, site, allow_account_creation=True)
    site.login_page = SIGNUP.replace("<button", '<label><input type="checkbox" name="terms">I accept terms</label><button')
    job = _job(space, site, "/login?gh_jid=4321")
    first = fill_job(page, space.conn, space.settings, space.config, job)
    assert first.status == "partial" and first.note.startswith("consent:")
    assert "account email" in first.filled
    assert _value(page, "[name=email]") == EMAIL
    assert not _posts(site)
    origin = site.origin.replace("127.0.0.1", "localhost") if destination == "another-origin" else site.origin
    page.goto(origin + "/application?gh_jid=" + ("9999" if destination == "another-posting" else "4321"))
    page.main.call("() => document.querySelector('input').value='Manually retained name'")
    retained = _retained_tab(page.bidi, jobs_core.get_job(space.conn, job.id))
    if destination != "same-posting":
        assert retained is None
    else:
        assert retained is not None
        result = fill_job(retained, space.conn, space.settings, space.config,
                          jobs_core.get_job(space.conn, job.id), resume_tab=True)
        assert result.resumed
        assert _value(page, "input") == "Manually retained name"
    assert not _posts(site)


def test_component_proxy_without_method_has_a_post_only_native_fallback(workspace, site, page):
    site.login_page = SIGNUP.replace('method="post" ', "").replace(
        '<button type="submit">Create Account</button>',
        '<div role="button" aria-label="Create Account" style="width:100px;height:30px"></div>'
        '<button type="submit" aria-hidden="true" '
        'onclick="if (!event.isTrusted) event.preventDefault()">Create Account</button>',
    )
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    assert _posts(site) == [("/session", {
        "email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD],
    })]
    assert not any(PASSWORD in str(fields) for method, _, fields in site.requests if method == "GET")


@pytest.mark.parametrize(("mode", "label"), [
    ("login", "Submit"), ("login", "Sign In"), ("create", "Create Account"),
])
def test_accessibility_overlay_uses_hidden_native_account_semantics_and_real_pointer(
    workspace, site, page, mode, label,
):
    markup = SIGNUP if mode == "create" else LOGIN.format(extra="", method="post")
    native_label = "Create Account" if mode == "create" else "Sign in"
    markup = markup.replace(f'<button type="submit">{native_label}</button>', f"""
      <div style="position:relative;width:180px;height:40px">
        <div role="button" aria-label="{label}" tabindex="0"
          style="position:absolute;inset:0;z-index:1"
          onclick="if(event.isTrusted) this.nextElementSibling.click()"></div>
        <button type="submit" aria-hidden="true" tabindex="-2"
          style="position:absolute;inset:0">{native_label}</button>
      </div>""").replace('method="post" ', "")
    site.login_page = markup
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))

    assert result.acted and result.blocker is None
    fields = {"email": [EMAIL], "password": [PASSWORD]}
    if mode == "create":
        fields["confirm"] = [PASSWORD]
    assert _posts(site) == [("/session", fields)]
    assert page.main.url == site.origin + "/application"
    assert not any(path == "/apply" for _, path, _ in site.requests)
    assert not any(PASSWORD in str(values) for method, _, values in site.requests if method == "GET")


@pytest.mark.parametrize("hazard", [
    "application-native", "unassociated-native", "additional-submit", "get-method",
    "get-override", "unapproved-action", "extra-field", "conflicting-account-label",
])
def test_generic_submit_proxy_never_authorizes_an_uncertain_or_unsafe_account_action(
    workspace, site, page, hazard,
):
    native = '<button type="submit" aria-hidden="true">Sign in</button>'
    proxy = '<div role="button" aria-label="Submit" style="width:180px;height:40px"></div>'
    if hazard == "application-native":
        native = native.replace("Sign in", "Submit application")
    elif hazard == "unassociated-native":
        native = "<section>" + native + "</section>"
    elif hazard == "get-override":
        native = native.replace('type="submit"', 'type="submit" formmethod="get"')
    elif hazard == "unapproved-action":
        native = native.replace('type="submit"',
                                'type="submit" formaction="https://unapproved.invalid/session"')
    elif hazard == "conflicting-account-label":
        proxy = proxy.replace('aria-label="Submit"', 'aria-label="Create Account"')
    markup = LOGIN.format(extra="", method="get" if hazard == "get-method" else "post").replace(
        '<button type="submit">Sign in</button>', proxy + native)
    if hazard == "additional-submit":
        markup = markup.replace("</form>", '<button type="submit">Submit application</button></form>')
    elif hazard == "extra-field":
        markup = markup.replace("</form>", '<label>Security answer<input name="answer"></label></form>')
    site.login_page = markup
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))

    assert not result.acted and result.blocker and not result.filled
    assert _value(page, "[name=email]") == _value(page, "[name=password]") == ""
    assert not _posts(site)


def test_application_resume_survives_new_sessions_without_guessing_other_tabs(
    workspace, site, firefox, tmp_path,
):
    from kitty.core.apply.bidi import open_session
    from kitty.core.apply.filler import _retained_tab

    space = _space(workspace, site)
    job = _job(space, site, "/application?gh_jid=4321")
    site.pages["/application"] = APPLICATION.replace("</form>", """
      <input id="native-choice" type="checkbox" style="display:none">
      <label id="native-label" for="native-choice">I accept the privacy policy</label>
      <div id="aria-choice" role="checkbox" aria-checked="false" tabindex="0">
        <span>I certify the application is accurate</span>
      </div>
      <label>Manual dropdown<select id="native-select">
        <option value="">Choose...</option><option value="alpha">Alpha</option><option value="beta">Beta</option>
      </select></label>
      <button type="button" id="custom" role="combobox" aria-label="Manual custom dropdown"
        aria-controls="custom-list" aria-expanded="false">Choose...</button>
      <div role="listbox" id="custom-list" hidden>
        <div role="option" id="custom-alpha" aria-selected="false">Alpha</div>
        <div role="option" id="custom-beta" aria-selected="false">Beta</div>
      </div>
      <input id="locked" readonly value="Keep read-only">
      <input id="disabled" disabled value="Keep disabled">
      </form>
      <script>
        window.manualTrusted=[];
        document.querySelector('#native-choice').onclick=e => window.manualTrusted.push(e.isTrusted);
        document.querySelector('#aria-choice').onclick=e => {
          window.manualTrusted.push(e.isTrusted);
          const box=e.currentTarget;
          box.setAttribute('aria-checked', String(box.getAttribute('aria-checked')!=='true'));
        };
        const combo=document.querySelector('#custom'), list=document.querySelector('#custom-list');
        combo.onclick=() => {
          list.hidden=!list.hidden; combo.setAttribute('aria-expanded', String(!list.hidden));
        };
        for (const option of list.children) option.onclick=e => {
          window.manualTrusted.push(e.isTrusted);
          for (const other of list.children) other.setAttribute('aria-selected', String(other===option));
          combo.textContent=option.textContent; list.hidden=true; combo.setAttribute('aria-expanded', 'false');
        };
      </script>
    """)

    def manual_controls(tab, value, checked):
        frame = tab.main
        for selector in ("#native-label", "#aria-choice span"):
            target = frame.node("(s) => document.querySelector(s)", selector)
            assert target is not None
            frame.click(target)
        frame.call("() => document.querySelector('#native-select').focus()")
        frame.type(value[0] + "\ue004")
        for selector in ("#custom", f"#custom-{value}"):
            target = frame.node("(s) => document.querySelector(s)", selector)
            assert target is not None
            frame.click(target)
        assert frame.call("""() => [
          document.querySelector('#native-choice').checked,
          document.querySelector('#aria-choice').getAttribute('aria-checked') === 'true',
          document.querySelector('#native-select').value,
          document.querySelector('#custom-list [aria-selected=true]').id,
          document.querySelector('#locked').readOnly,
          document.querySelector('#disabled').disabled
        ]""") == [checked, checked, value, f"custom-{value}", True, True]
        assert frame.call("() => window.manualTrusted.every(Boolean)", page_realm=True)

    with headless_firefox(Settings.load(root=tmp_path / "resume-browser")) as first:
        port = first._ws.socket.getpeername()[1]
        session = first
        try:
            original = open_tab(first)
            fill_job(original, space.conn, space.settings, space.config, job)
            original.main.call("() => document.querySelector('input').value='Manually retained name'")
            manual_controls(original, "beta", True)
            unrelated = open_tab(first)
            unrelated.goto(job.url)
            saved = jobs_core.get_job(space.conn, job.id)
            first.close()
            session = open_session(port)
            retained = _retained_tab(session, saved)
            assert retained is not None and retained.context != saved.fill_context_id
            assert _value(retained, "input") == "Manually retained name"
            report = fill_job(retained, space.conn, space.settings, space.config, saved, resume_tab=True)
            assert report.resumed
            assert _value(retained, "input") == "Manually retained name"
            assert _value(retained, "#native-select") == "beta"
            assert retained.main.call("() => document.querySelector('#native-choice').checked")
            manual_controls(retained, "alpha", False)
            assert not _posts(site)

            # Copied storage is not enough to choose between two candidate tabs.
            copied = open_tab(session)
            copied.goto(job.url)
            saved = jobs_core.get_job(space.conn, job.id)
            copied.main.call(
                "context => sessionStorage.setItem('kitty.application.context', context)",
                saved.fill_context_id,
            )
            session.close()
            session = open_session(port)
            assert _retained_tab(session, saved) is None
        finally:
            session.close()


def test_account_activation_email_stops_before_any_credential_write(workspace, site, page):
    from kitty.core.apply.preflight import inspect_page

    site.login_page = LOGIN.format(extra=(
        "<p>An email has been sent to you. Please verify your account.</p>"
    ), method="post")
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    assert inspect_page(page).blocker.startswith("verification:")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert not result.acted and result.blocker.startswith("verification:")
    assert _value(page, "[name=email]") == _value(page, "[name=password]") == ""
    assert not _posts(site)


@pytest.mark.parametrize("mode", ["auto", "create"])
def test_account_creation_returns_activation_handoff_without_another_account_attempt(
    workspace, site, page, mode,
):
    site.login_page = SIGNUP
    site.post_responses = [LOGIN.format(extra=(
        "<p>An email has been sent to you. Please verify your account.</p>"
    ), method="post")]
    space = _space(workspace, site, allow_account_creation=True, account_mode=mode)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))

    assert result.acted and result.blocker.startswith("verification:")
    assert _posts(site) == [("/session", {
        "email": [EMAIL], "password": [PASSWORD], "confirm": [PASSWORD],
    })]
    assert _value(page, "[name=email]") == _value(page, "[name=password]") == ""


@pytest.mark.parametrize("native", [False, True])
def test_account_certification_handoff_leaves_choices_manually_operable(workspace, site, page, native):
    choice = (
        '<input id="certification" type="checkbox" required>'
        '<label id="manual-choice" for="certification">I certify all application information is accurate</label>'
        if native else
        '<div id="certification" role="checkbox" aria-checked="false" aria-required="true" tabindex="0" '
        'onclick="this.setAttribute(\'aria-checked\', this.getAttribute(\'aria-checked\') !== \'true\')">'
        '<span id="manual-choice">I certify all application information is accurate</span></div>'
    )
    site.login_page = SIGNUP.replace("<button", choice + "<button")
    space = _space(workspace, site, allow_account_creation=True)
    page.goto(site.origin + "/login")
    credentials = load_credentials(space.settings)
    result = authenticate(page, space.config.apply.authentication, credentials)
    assert not result.acted and result.blocker.startswith("consent:")
    assert result.filled == ("email", "password", "passwordConfirm")
    assert not _posts(site)
    target = page.main.node("() => document.querySelector('#manual-choice')")
    assert target is not None
    page.main.click(target)
    assert page.main.call("""() => {
      const box=document.querySelector('#certification');
      return !!box.checked || box.getAttribute('aria-checked') === 'true';
    }""")
    # A manually reviewed factual certification must not turn into authorization
    # for an automatic credential request on the next automation attempt.
    result = authenticate(page, space.config.apply.authentication, credentials)
    assert not result.acted and result.blocker.startswith("consent:")
    assert not _posts(site)


@pytest.mark.parametrize("notice, expected", [
    ("", None),
    ("<h1>This job is no longer available</h1>", "closed:"),
    ("<p role='alert'>Enter your verification code</p>", "verification:"),
    ("<div role='dialog'>Please complete the captcha</div>", "captcha:"),
    ("<div role='dialog'>Cookie preferences: choose your consent</div>", "consent:"),
])
def test_preflight_recognizes_nested_open_shadow_application_and_gates(site, page, notice, expected):
    from kitty.core.apply.preflight import inspect_page

    site.pages["/shadow"] = "<!doctype html><body><section id='host'></section>"
    page.goto(site.origin + "/shadow")
    page.main.call("""notice => {
      const root = document.querySelector('#host').attachShadow({mode:'open'});
      root.innerHTML = '<div id="nested"></div>';
      const nested = root.querySelector('#nested').attachShadow({mode:'open'});
      nested.innerHTML = `<form id="application">
        <div role="group" aria-required="true">
          <label>Name<input name="name" required></label>
        </div>
        <button type="submit">Submit application</button>
      </form>` + notice;
    }""", notice, page_realm=True)
    state = inspect_page(page)
    assert state.has_fields and state.boundary
    assert not state.unsupported_required
    assert state.errors >= 1
    assert (state.blocker is None) if expected is None else state.blocker.startswith(expected)
    assert not state.entry and not state.safe_next
    assert not _posts(site)


def test_required_shadow_host_is_represented_only_by_real_supported_controls(site, page):
    from kitty.core.apply.preflight import inspect_page

    site.pages["/shadow"] = "<!doctype html><body><div id='host' aria-required='true'></div>"
    page.goto(site.origin + "/shadow")
    page.main.call("""() => {
      document.querySelector('#host').attachShadow({mode:'open'}).innerHTML =
        '<form id="application"><button type="submit">Submit application</button></form>';
    }""", page_realm=True)
    assert inspect_page(page).unsupported_required
    page.main.call("""() => {
      document.querySelector('#host').shadowRoot.querySelector('form').insertAdjacentHTML(
        'afterbegin', '<label>First name<input name="firstName" required></label>');
    }""", page_realm=True)
    assert not inspect_page(page).unsupported_required
    assert not _posts(site)


@pytest.mark.parametrize("obstruction", [
    '<form id="application"><input name="name"><button type="button">Apply</button></form>',
    '<label>Name<input name="candidate" value="Already answered"></label>',
    '<h1>Review your application</h1>',
    '<div role="dialog">Cookie consent preferences</div>',
    '<fieldset disabled><a data-ph-at-id="apply-link" '
    'data-ph-at-job-seqno-text="SITEJR7001EXTERNAL" '
    'href="/us/en/hvhapply?jobSeqNo=SITEJR7001EXTERNAL">Apply Now</a></fieldset>',
])
def test_posting_entry_never_reinterprets_application_or_gated_state(site, page, obstruction):
    from kitty.core.apply.page import UnsafeAction
    from kitty.core.apply.preflight import advance, inspect_page

    _phenom(site)
    site.pages["/us/en/job/JR7001"] += obstruction
    page.goto(site.origin + "/us/en/job/JR7001")
    state = inspect_page(page)
    with pytest.raises(UnsafeAction):
        advance(state, "other")
    assert not _posts(site)
    assert not any(path == "/us/en/hvhapply" for _, path, _ in site.requests)


def test_posting_entry_rechecks_even_changed_valid_same_posting_destinations(site, page):
    from kitty.core.apply.page import UnsafeAction
    from kitty.core.apply.preflight import advance, inspect_page

    _phenom(site)
    page.goto(site.origin + "/us/en/job/JR7001")
    state = inspect_page(page)
    page.main.call("""() => {
      for (const link of document.querySelectorAll('a')) {
        link.href = '/us/en/hvhapply?jobSeqNo=OTHERJR7001EXTERNAL';
        link.dataset.phAtJobSeqnoText = 'OTHERJR7001EXTERNAL';
      }
    }""", page_realm=True)
    with pytest.raises(UnsafeAction):
        advance(state, "other")
    assert not _posts(site)
    assert not any(path == "/us/en/hvhapply" for _, path, _ in site.requests)


@pytest.mark.parametrize("url, expected", [
    ("https://hdnn.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/114233", "oracle"),
    ("https://egup.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/20278847", "oracle"),
    ("https://jobs.dayforcehcm.com/en-US/yss/CANDIDATEPORTAL/jobs/1129", "dayforce"),
    ("https://performancemanager.successfactors.com/career?company=example", "successfactors"),
    ("https://careers.leonardodrs.com/job/example/1427797200/", "other"),
    ("https://www.databricks.com/company/careers/example-7011263002?gh_jid=7011263002", "greenhouse"),
    ("https://oraclecloud.com.example.invalid/hcmUI/CandidateExperience/en/sites/CX/job/114233", "other"),
    ("https://jobs.dayforcehcm.com.example.invalid/en-US/yss/CANDIDATEPORTAL/jobs/1129", "other"),
    ("https://greenhouse.io.example.invalid/jobs/7011263002", "other"),
    ("https://example.invalid/?redirect=gh_jid=7011263002", "other"),
])
def test_provider_detection_uses_domain_boundaries_and_explicit_posting_ids(url, expected):
    from kitty.core.apply.urls import detect_ats

    assert detect_ats(url) == expected


def test_posting_entry_click_is_trusted_and_reaches_nested_shadow_link(site, page):
    from kitty.core.apply.preflight import advance, inspect_page, wait_for_page

    _phenom(site)
    page.goto(site.origin + "/us/en/job/JR7001")
    page.main.call("""() => {
      const link = document.querySelector('a').cloneNode(true);
      document.querySelectorAll('a').forEach(e => e.remove());
      const host = document.createElement('section');
      document.body.append(host);
      const nested = document.createElement('div');
      host.attachShadow({mode:'open'}).append(nested);
      nested.attachShadow({mode:'open'}).append(link);
      link.addEventListener('click', event => {
        if (!event.isTrusted) event.preventDefault();
      });
    }""", page_realm=True)
    before = inspect_page(page)
    assert before.entry == "phenom-apply"
    advance(before, "other")
    after = wait_for_page(page, previous=before.signature)
    assert after.step == "Application details"
    assert page.main.url == site.origin + "/us/en/hvhapply?jobSeqNo=SITEJR7001EXTERNAL"
    assert not _posts(site)


def test_hidden_credential_error_and_help_text_do_not_block_valid_sign_in(workspace, site, page):
    site.login_page = LOGIN.format(method="post", extra=(
        '<p role="alert" style="display:none">Incorrect email or password.</p>'
        '<p>If you enter the wrong password repeatedly your account might be locked.</p>'))
    space = _space(workspace, site)
    page.goto(site.origin + "/login")
    result = authenticate(page, space.config.apply.authentication, load_credentials(space.settings))
    assert result.acted and result.blocker is None
    deadline = time.monotonic() + 10
    while page.main.url != site.origin + "/application" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert page.main.url == site.origin + "/application"
    assert _posts(site) == [("/session", {"email": [EMAIL], "password": [PASSWORD]})]

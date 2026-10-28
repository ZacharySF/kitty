"""Synthetic Workday ownership and account-stage regressions; no live browser."""

import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from kitty.config import AuthenticationSettings
from kitty.core.apply import auth, preflight
from kitty.core.apply.credentials import ApplicationCredentials
from kitty.core.apply.recovery_runtime import JobRecovery, PreparationStopped


ORIGIN = "https://synthetic.wd1.myworkdayjobs.com"
SITE = "/en-US/External_Careers"
POSTING = SITE + "/job/Example%2C-CA/Engineering-Intern_R123456-1"
APPLICATION = ORIGIN + POSTING + "/apply/applyManually"
SAVED_POSTING = ORIGIN + POSTING.replace("/en-US", "").replace("%2C", "")


def _login(target=POSTING + "/apply/applyManually", *, origin=ORIGIN, site=SITE):
    return origin + site + "/login?redirect=" + quote(target, safe="")


def _runtime(url=APPLICATION, *, job_url=SAVED_POSTING):
    page = SimpleNamespace(context="owned-tab", main=SimpleNamespace(url=url))
    policy = SimpleNamespace(max_actions=6, max_recovery_seconds=45,
                             max_job_seconds=300, max_stall_seconds=20)
    config = SimpleNamespace(apply=SimpleNamespace(recovery=policy))
    job = SimpleNamespace(id=1, apply_url=None, url=job_url)
    return JobRecovery(None, None, config, job, page), page


@pytest.mark.parametrize("url", [
    ORIGIN + POSTING, APPLICATION, _login(), _login(APPLICATION),
    ORIGIN + POSTING.replace("%2C", ",") + "/apply",
    SAVED_POSTING, _login(POSTING.replace("en-US", "fr-FR") + "/apply"),
])
def test_workday_binding_retains_exact_tenant_site_and_posting(url):
    assert auth.workday_application_binding(url) == auth.workday_application_binding(APPLICATION)


@pytest.mark.parametrize("url", [
    _login("https://hostile.example.invalid" + POSTING + "/apply"),
    _login("//hostile.example.invalid" + POSTING + "/apply"),
    _login(APPLICATION.replace("synthetic.wd1", "other.wd1")),
    _login(POSTING.replace("External_Careers", "Other_Careers") + "/apply"),
    _login(SITE + "/login?redirect=" + quote(POSTING, safe="")),
    _login() + "&redirect=" + quote(POSTING, safe=""),
    ORIGIN + SITE + "/login",
    _login() + "#" + POSTING,
    _login(POSTING + "/apply?redirect=https://hostile.example.invalid"),
    _login(POSTING.replace("/job/", "/job/../job/") + "/apply"),
    _login(POSTING.replace("Example%2C-CA", "Example%2FCA") + "/apply"),
    _login(POSTING.replace("Example%2C-CA", "%252e%252e") + "/apply"),
    _login(POSTING.replace("Example%2C-CA", "Example%5CCA") + "/apply"),
    _login(POSTING.replace("Example%2C-CA", "Example%00CA") + "/apply"),
    _login(POSTING.replace("Example%2C-CA", "Example%FFCA") + "/apply"),
    _login(origin="http://synthetic.wd1.myworkdayjobs.com"),
    _login(origin="https://synthetic.wd1.myworkdayjobs.com.hostile.invalid"),
    _login(origin="https://hostile@synthetic.wd1.myworkdayjobs.com"),
    APPLICATION + "?redirect=https://hostile.example.invalid",
])
def test_workday_binding_rejects_unsafe_or_unbound_redirect(url):
    assert auth.workday_application_binding(url) is None


def test_checkpoint_accepts_bound_login_before_accepted_navigation_without_progress():
    runtime, page = _runtime()
    before = runtime.last_progress
    page.main.url = _login()
    assert runtime.checkpoint()
    assert runtime.last_progress == before
    assert runtime.owned_url == APPLICATION
    runtime.accepted_navigation()
    page.main.url = APPLICATION
    assert runtime.checkpoint()


@pytest.mark.parametrize("changed", [
    _login(POSTING.replace("R123456-1", "R999999-1") + "/apply"),
    _login(POSTING.replace("Engineering-Intern", "Different-Title") + "/apply"),
    _login(origin=ORIGIN.replace("synthetic.wd1", "other.wd1")),
    _login(POSTING.replace("External_Careers", "Other_Careers") + "/apply"),
    ORIGIN + SITE + "/login",
    _login() + "&redirect=" + quote(POSTING, safe=""),
    _login("https://hostile.example.invalid" + POSTING),
    ORIGIN + SITE + "/candidateHome",
])
@pytest.mark.parametrize("operation", ["checkpoint", "accepted_navigation"])
def test_navigation_cannot_adopt_mismatched_workday_posting(changed, operation):
    runtime, page = _runtime()
    page.main.url = changed
    with pytest.raises(PreparationStopped):
        getattr(runtime, operation)()
    assert runtime.owned_url == APPLICATION


@pytest.mark.parametrize("operation", ["checkpoint", "accepted_navigation"])
def test_same_posting_in_another_context_is_not_owned(operation):
    runtime, page = _runtime()
    page.context = "unowned-tab"
    page.main.url = _login()
    with pytest.raises(PreparationStopped):
        getattr(runtime, operation)()


def test_initial_retained_workday_login_requires_the_reviewed_posting():
    runtime, _ = _runtime(_login())
    assert runtime.checkpoint()
    with pytest.raises(PreparationStopped):
        _runtime(_login(POSTING.replace("R123456-1", "R000000-1")))


def test_bound_login_is_still_a_protected_fixer_boundary(monkeypatch):
    runtime, page = _runtime()
    page.main.url = _login()
    monkeypatch.setattr(auth, "authentication_pending", lambda _: True)
    with pytest.raises(PreparationStopped, match="cannot repeat an account submission"):
        runtime.guard()


class _Bidi:
    def protect_secrets(self, *values):
        pass


class _AccountFrame:
    def __init__(self, url):
        self.url = url
        self.mode = "create"
        self.finished = False
        self.writes = []

    def call(self, script, *args):
        if script == "() => ({url: location.href, loading: document.readyState === 'loading'})":
            return {"url": self.url, "loading": False}
        if script == auth._SNAPSHOT_JS:
            return {
                "top": True, "url": self.url, "blocker": None, "rejected": False,
                "hasPassword": not self.finished, "accountStatus": None,
                "stage": None if self.finished else "password", "mode": self.mode,
                "signature": self.url + "|" + self.mode, "roles": ["email", "password"],
                "action": ORIGIN + "/session",
            }
        if script == auth._ENTRY_RECEIPT_JS:
            return self.url + "|" + self.mode
        if script == auth._WRITE_JS:
            self.writes.append(args[-1])
            return True
        if script == "() => location.href":
            return self.url
        if script == "() => { document.activeElement?.blur?.(); }":
            return None
        if "location.href === url" in script or "const target = (" in script:
            return True
        raise AssertionError("Unexpected account operation")


def _account_page(monkeypatch, url):
    frame = _AccountFrame(url)
    page = SimpleNamespace(context="owned-tab", main=frame, bidi=_Bidi(), frames=lambda: [frame])
    monkeypatch.setattr(auth, "form_frames", lambda _: [frame])
    monkeypatch.setattr(auth, "oracle_email_gate", lambda *_: None)
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=time.monotonic,
                                                     sleep=lambda _: None))
    return page, frame


def test_synthetic_runtime_signs_into_bound_posting_once_using_known_account(monkeypatch):
    runtime, _ = _runtime()
    page, frame = _account_page(monkeypatch, _login())
    runtime.page = page
    calls = []

    def click(target, script, *args, **kwargs):
        calls.append(script)
        if script == auth._ENTRY_TARGET_JS:
            assert args[1] == "login"
            frame.mode = "login"
        elif script == auth._ACCOUNT_TARGET_JS:
            assert frame.mode == "login"
            frame.finished = True
            frame.url = APPLICATION
        else:
            raise AssertionError("Unexpected account click")

    monkeypatch.setattr(auth, "_guarded_click", click)
    monkeypatch.setattr(preflight, "inspect_page", lambda _: SimpleNamespace(
        blocker=None, has_fields=True, boundary=False, step="My Information", url=APPLICATION))
    policy = AuthenticationSettings(enabled=True, approved_origins=[ORIGIN],
                                    account_mode="auto", allow_account_creation=True,
                                    existing_account_origins=[ORIGIN])
    assert runtime.checkpoint()
    result = auth.authenticate(page, policy, ApplicationCredentials("synthetic@example.invalid", "Synthetic-Pass#42"),
                               application_url=SAVED_POSTING)
    assert result.acted and result.blocker is None
    assert calls == [auth._ENTRY_TARGET_JS, auth._ACCOUNT_TARGET_JS]
    assert frame.writes == ["email", "password"]
    runtime.accepted_navigation()
    assert runtime.checkpoint()
    assert page.main.url == APPLICATION


@pytest.mark.parametrize("url", [
    _login(POSTING.replace("R123456-1", "R999999-1")),
    _login("https://hostile.example.invalid" + POSTING),
    _login() + "&redirect=" + quote(POSTING, safe=""),
])
def test_authentication_rejects_mismatched_redirect_before_credential_writes(monkeypatch, url):
    page, frame = _account_page(monkeypatch, url)
    policy = AuthenticationSettings(enabled=True, approved_origins=[ORIGIN])
    monkeypatch.setattr(auth, "_guarded_click", lambda *_a, **_k: pytest.fail("No click is authorized"))
    result = auth.authenticate(page, policy, ApplicationCredentials("synthetic@example.invalid", "Synthetic-Pass#42"),
                               application_url=APPLICATION)
    assert not result.acted and not result.filled
    assert "ownership changed" in result.blocker
    assert frame.writes == []

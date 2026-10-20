"""Oracle's bounded email entry against isolated HTTPS, with synthetic account data.

The fixture deliberately has no Knockout handler: the native fallback must POST,
never put the account alias in a GET URL. Browser fixtures use throwaway profiles,
not the operator's Firefox. Real-site email ownership remains a manual boundary.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from kitty.config import AuthenticationSettings
from kitty.core.apply import oracle_email
from kitty.core.apply.bidi import Frame, Tab
from kitty.core.apply.credentials import ApplicationCredentials

# Reuse the existing isolated HTTPS server and headless-browser convention.
from test_application_auth import firefox, https_site, page  # noqa: F401

ACCOUNT_ALIAS = "account-alias@example.invalid"
PROFILE_EMAIL = "profile-email@example.invalid"
CREDENTIALS = ApplicationCredentials(ACCOUNT_ALIAS, "Never-Sent-Password#42")
PATH = "/hcmUI/CandidateExperience/en/sites/CX/job/114233/apply/email"
VERIFICATION = '<h1>Verify your email</h1><input autocomplete="one-time-code"><button>Verify</button>'
APPLICATION = '<h1>Application</h1><form method="post" action="/final"><button>Submit application</button></form>'
TERMS = '''<input type="checkbox" id="legal-disclaimer-checkbox" required
 aria-labelledby="legal-disclaimer-checkbox-label" style="position:absolute;opacity:0">
<label for="legal-disclaimer-checkbox" id="legal-disclaimer-checkbox-label">I agree with the terms and conditions</label>'''
FORM = '''<!doctype html><title>Oracle email entry</title><body>
<form novalidate data-bind="submit: next, setBodyScrollTo: {{ to: 0 }}" {attrs}>
<input type="email" id="primary-email-0" name="primary-email" autocomplete="email" aria-required="true" {email_attrs}>
<input name="honey-pot" aria-hidden="true" tabindex="-1" style="display:none">
{terms}{extra}
<button type="button" aria-label="Cancel">CANCEL</button>
<button type="submit" aria-label="Next" data-bind="enable :$parent.isNextButtonEnabled" {next_attrs}>NEXT</button>
</form>{script}</body>'''


@pytest.fixture
def oracle_site(https_site, monkeypatch):
    site = https_site
    site.requests.clear()
    site.pages.clear()
    site.post_responses[:] = [VERIFICATION]
    # Only the fixture's loopback hostname substitutes for the fixed production
    # Oracle host grammar; exact HTTPS origin and component guards remain intact.
    monkeypatch.setattr(oracle_email, "_ORACLE_HOST", re.compile(r"127\.0\.0\.1"))
    return site


def _open(page, site, *, attrs="", email_attrs="", terms="", extra="", next_attrs="", script=""):
    site.pages[PATH] = FORM.format(attrs=attrs, email_attrs=email_attrs, terms=terms,
                                   extra=extra, next_attrs=next_attrs, script=script)
    page.goto(site.origin + PATH)


def _policy(site, **overrides):
    return AuthenticationSettings(**({"enabled": True, "allow_account_creation": True,
                                      "approved_origins": [site.origin]} | overrides))


def _value(page, selector):
    return page.main.call("s => document.querySelector(s)?.value ?? null", selector)


def _posts(site):
    return [(path, fields) for method, path, fields in site.requests if method == "POST"]


@pytest.fixture
def oracle_navigation(monkeypatch):
    """Exercise Tab.main's getTree URL snapshots without a browser timing race."""
    origin = "https://hdnn.fa.us6.oraclecloud.com"
    nav = SimpleNamespace(
        origin=origin, entry=origin + PATH,
        destination=origin + PATH.removesuffix("email") + "application",
        states=[], polls=0, inspections=[], writes=0, targets=0, next_clicks=0, now=0.0,
    )
    nav.policy = _policy(nav)

    def stage(context_url, **state):
        nav.states.append(
            {"context_url": context_url, "url": context_url, "ready": True, "blocker": None} | state
        )

    def command(method, params):
        assert method == "browsingContext.getTree"
        assert params == {"root": "oracle"}
        node = {"context": "oracle", "url": nav.entry}
        if nav.next_clicks:
            assert nav.states
            nav.current = nav.states[min(nav.polls, len(nav.states) - 1)]
            nav.polls += 1
            if nav.current["context_url"] is None:
                node.pop("url")
            else:
                node["url"] = nav.current["context_url"]
        return {"contexts": [node]}

    def call(frame, function, *args):
        if nav.next_clicks:
            assert function == oracle_email._SNAPSHOT_JS
            assert args == (nav.policy.approved_origins, frame.url, None)
            nav.inspections.append(nav.polls)
            return {key: nav.current[key] for key in ("url", "ready", "blocker")}
        assert frame.url == nav.entry
        assert args == (nav.policy.approved_origins, nav.entry, ACCOUNT_ALIAS)
        if function == oracle_email._WRITE_JS:
            nav.writes += 1
            return True
        assert function == oracle_email._SNAPSHOT_JS
        return {"blocker": None, "pendingTerms": False, "disabled": False}

    def node(frame, function, *args):
        assert not nav.next_clicks and frame.url == nav.entry
        assert function == oracle_email._TARGET_JS
        assert args == (nav.policy.approved_origins, nav.entry, ACCOUNT_ALIAS, "next")
        nav.targets += 1
        return "oracle-next"

    def click(frame, target):
        assert frame.url == nav.entry and target == "oracle-next"
        nav.next_clicks += 1

    def sleep(seconds):
        nav.now += seconds

    monkeypatch.setattr(Frame, "call", call)
    monkeypatch.setattr(Frame, "node", node)
    monkeypatch.setattr(Frame, "click", click)
    monkeypatch.setattr(
        oracle_email, "time", SimpleNamespace(monotonic=lambda: nav.now, sleep=sleep),
    )
    nav.stage = stage
    nav.page = Tab(SimpleNamespace(command=command), "oracle")
    return nav


def test_saved_account_alias_not_profile_email_and_honeypot_untouched(page, oracle_site):
    _open(page, oracle_site)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.filled == ("email",)
    assert result.blocker.startswith("verification:")
    posts = _posts(oracle_site)
    assert len(posts) == 1
    assert posts[0][0] == PATH
    assert posts[0][1] == {"primary-email": [ACCOUNT_ALIAS]}
    assert all(not fields for method, _, fields in oracle_site.requests if method == "GET")
    assert PROFILE_EMAIL not in str(oracle_site.requests)
    assert CREDENTIALS.password not in str(oracle_site.requests)


def test_routine_required_terms_use_trusted_label_pointer(page, oracle_site):
    # A DOM .click() would produce isTrusted=false and leave the button disabled.
    script = '''<script>
    document.querySelector('[type=submit]').disabled = true;
    document.querySelector('#legal-disclaimer-checkbox').addEventListener('change', e => {
      if (e.isTrusted) document.querySelector('[type=submit]').disabled = false;
    });
    </script>'''
    _open(page, oracle_site, terms=TERMS, script=script)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.blocker.startswith("verification:")
    assert len(_posts(oracle_site)) == 1


@pytest.mark.parametrize("overrides", [
    {"allow_account_creation": False}, {"account_mode": "login"}, {"account_mode": "google"},
    {"approved_origins": ["https://unapproved.example.invalid"]},
])
def test_policy_blocks_before_any_write(page, oracle_site, overrides):
    _open(page, oracle_site, terms=TERMS)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site, **overrides), CREDENTIALS)
    assert result.blocker and not result.acted and not result.filled
    assert _value(page, '[name="primary-email"]') == ""
    assert _value(page, '[name="honey-pot"]') == ""
    assert not _posts(oracle_site)
    assert not page.main.call("() => document.querySelector('[type=checkbox]').checked")


@pytest.mark.parametrize("changes", [
    {"attrs": 'method="get"'},
    {"attrs": 'method="dialog"'},
    {"attrs": 'action="https://unapproved.example.invalid/steal"'},
    {"attrs": 'action="/different"'},
    {"attrs": 'target="_blank"'},
    {"next_attrs": 'formmethod="get"'},
    {"next_attrs": 'formaction="/different"'},
    {"next_attrs": 'disabled'},
    {"extra": '<input name="firstName" required>'},
    {"extra": '<input type="password" name="password">'},
    {"extra": '<button type="submit">Submit application</button>'},
    {"email_attrs": 'value="different@example.invalid"'},
    {"extra": '<input type="checkbox" name="marketing">Marketing'},
    {"script": '<input name="outside-account-detail">'},
    {"extra": '<div role="checkbox" aria-checked="false">Marketing</div>'},
    {"terms": TERMS.replace("terms and conditions", "biometric consent")},
    {"terms": TERMS.replace("required", "")},
    {"terms": TERMS.replace("terms and conditions", "certification that my answers are accurate")},
])
def test_unsafe_or_unrecognized_entry_never_acts(page, oracle_site, changes):
    _open(page, oracle_site, **changes)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.blocker and not result.acted
    assert not _posts(oracle_site)
    assert _value(page, '[name="honey-pot"]') == ""


def test_explicit_post_is_not_rewritten(page, oracle_site):
    _open(page, oracle_site, attrs='method="post"')
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.blocker.startswith("verification:")
    assert len(_posts(oracle_site)) == 1


@pytest.mark.parametrize("event", ["onfocus", "oninput", "onblur"])
def test_changed_value_is_not_overwritten_or_submitted(page, oracle_site, event):
    _open(page, oracle_site, email_attrs=f"{event}=\"this.value='changed@example.invalid'\"")
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.blocker and not result.acted
    assert _value(page, '[name="primary-email"]') == "changed@example.invalid"
    assert not _posts(oracle_site)


def test_nonempty_honeypot_is_preserved_and_blocks_entry(page, oracle_site):
    _open(page, oracle_site)
    page.main.call("() => {document.querySelector('[name=\"honey-pot\"]').value = 'untouched';}")
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.blocker and not result.acted and not result.filled
    assert _value(page, '[name="honey-pot"]') == "untouched"
    assert _value(page, '[name="primary-email"]') == ""
    assert not _posts(oracle_site)


def test_rechecks_changed_destination_after_terms_pointer(page, oracle_site):
    script = '''<script>document.querySelector('[type=checkbox]').addEventListener('change', () => {
      document.querySelector('form').action = 'https://unapproved.example.invalid/steal';
    });</script>'''
    _open(page, oracle_site, terms=TERMS, script=script)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.blocker and not result.acted
    assert not _posts(oracle_site)


@pytest.mark.parametrize("extra", [
    '<p>Verify your email</p><input autocomplete="one-time-code">',
    '<h1>Authenticate Your Identity</h1><input name="lastName">',
    '<h1>Confirm Your Identity</h1><input name="dateOfBirth">',
    '<div class="g-recaptcha">Complete the CAPTCHA</div>',
])
def test_verification_or_captcha_stops_before_writes(page, oracle_site, extra):
    _open(page, oracle_site, extra=extra)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.blocker.startswith(("verification:", "captcha:"))
    assert not result.acted and not result.filled
    assert _value(page, '[name="primary-email"]') == ""
    assert not _posts(oracle_site)


def test_never_final_submits_after_email_entry(page, oracle_site):
    destination = PATH.removesuffix("email") + "application"
    oracle_site.pages[destination] = APPLICATION
    # The first real pointer event advances to the application; no further click.
    script = f'''<script>document.querySelector('form').addEventListener('submit', e => {{
      e.preventDefault(); location.href = {repr(destination)};
    }});</script>'''
    _open(page, oracle_site, script=script)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.blocker is None
    assert page.main.url == oracle_site.origin + destination
    assert not _posts(oracle_site)
    assert sum(method == "GET" and path == destination for method, path, _ in oracle_site.requests) == 1


def test_permanent_blank_after_next_times_out_without_application_submit(page, oracle_site):
    script = '''<script>document.querySelector('form').addEventListener('submit', e => {
      e.preventDefault(); location.href = 'about:blank';
    });</script>'''
    _open(page, oracle_site, script=script)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.blocker == oracle_email._UNCONFIRMED
    assert result.filled == ("email",)
    assert page.main.url == "about:blank"
    assert not _posts(oracle_site)


def test_unapproved_redirect_after_next_never_submits_application(page, oracle_site):
    destination = PATH.removesuffix("email") + "application"
    oracle_site.pages[destination] = APPLICATION
    other_origin = oracle_site.origin.replace("127.0.0.1", "localhost")
    script = f'''<script>document.querySelector('form').addEventListener('submit', e => {{
      e.preventDefault(); location.href = {repr(other_origin + destination)};
    }});</script>'''
    _open(page, oracle_site, script=script)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.blocker == oracle_email._LEFT_ORIGIN
    assert result.filled == ("email",)
    assert not _posts(oracle_site)


@pytest.mark.parametrize("context_url", [None, "", "about:blank"])
def test_provisional_context_url_rechecks_before_inspecting_approved_navigation(oracle_navigation, context_url):
    nav = oracle_navigation
    nav.stage(context_url, url="", ready=False)
    nav.stage(nav.destination)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker is None and result.filled == ("email",)
    assert nav.polls == 2 and nav.inspections == [2]
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


@pytest.mark.parametrize("document", ["empty", "blank", "loading", "previous"])
def test_pending_document_rechecks_without_rewriting_email_or_reclicking_next(oracle_navigation, document):
    nav = oracle_navigation
    url = {"empty": "", "blank": "about:blank", "loading": nav.destination, "previous": nav.entry}[document]
    nav.stage(nav.destination, url=url, ready=document == "previous")
    nav.stage(nav.destination)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker is None
    assert nav.polls == 2 and nav.inspections == [1, 2]
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


@pytest.mark.parametrize("context_url", [None, "", "about:blank"])
def test_pending_context_url_times_out_without_inspection_or_next_retry(oracle_navigation, context_url):
    nav = oracle_navigation
    nav.stage(context_url, url="", ready=False)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker == oracle_email._UNCONFIRMED
    assert result.filled == ("email",) and nav.now >= 10
    assert not nav.inspections
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


@pytest.mark.parametrize("destination", [
    "https://unapproved.example.invalid/application",
    "http://hdnn.fa.us6.oraclecloud.com/application",
    "about:blank#not-a-provisional-context",
])
def test_concrete_unapproved_context_stops_without_document_inspection(oracle_navigation, destination):
    nav = oracle_navigation
    nav.stage("", url="", ready=False)
    nav.stage(destination, ready=False)
    nav.stage(nav.destination)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker == oracle_email._LEFT_ORIGIN
    assert nav.polls == 2 and not nav.inspections
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


@pytest.mark.parametrize("ready", [False, True])
def test_unapproved_live_document_stops_before_readiness_or_tree_recheck(oracle_navigation, ready):
    nav = oracle_navigation
    nav.stage(nav.destination, url="https://unapproved.example.invalid/application", ready=ready)
    nav.stage(nav.destination)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker == oracle_email._LEFT_ORIGIN
    assert nav.polls == 1 and nav.inspections == [1] and nav.now == 0
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


@pytest.mark.parametrize("ready", [False, True])
def test_blank_live_document_never_completes_handoff(oracle_navigation, ready):
    nav = oracle_navigation
    nav.stage(nav.destination, url="about:blank", ready=ready)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker == oracle_email._UNCONFIRMED
    assert result.filled == ("email",) and nav.now >= 10
    assert nav.inspections
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


def test_loading_approved_document_times_out_without_application_handoff(oracle_navigation):
    nav = oracle_navigation
    nav.stage(nav.destination, ready=False)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker == oracle_email._UNCONFIRMED
    assert result.filled == ("email",) and nav.now >= 10
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


@pytest.mark.parametrize("blocker", [
    "verification: complete Oracle email ownership verification yourself",
    "captcha: complete the Oracle CAPTCHA yourself",
])
def test_pending_navigation_preserves_manual_security_challenge(oracle_navigation, blocker):
    nav = oracle_navigation
    nav.stage("", url="", ready=False)
    nav.stage(nav.destination, ready=False)
    nav.stage(nav.destination, blocker=blocker)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker == blocker
    assert nav.polls == 3 and nav.inspections == [2, 3]
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


def test_email_entry_after_next_is_not_success_and_never_retries(oracle_navigation):
    nav = oracle_navigation
    nav.stage(nav.entry)
    result = oracle_email.oracle_email_gate(nav.page, nav.policy, CREDENTIALS)
    assert result.acted and result.blocker.startswith("login: Oracle remained on email entry")
    assert nav.now >= 10
    assert (nav.writes, nav.targets, nav.next_clicks) == (1, 1, 1)


def test_wrong_host_or_non_entry_path_is_not_recognized(page, oracle_site, monkeypatch):
    _open(page, oracle_site)
    monkeypatch.setattr(oracle_email, "_ORACLE_HOST", re.compile(r"other\.fa\.us2\.oraclecloud\.com"))
    assert oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS) is None
    assert _value(page, '[name="primary-email"]') == ""
    assert not _posts(oracle_site)


@pytest.mark.parametrize("url", [
    "http://hdnn.fa.us6.oraclecloud.com" + PATH,
    "https://hdnn.fa.us6.oraclecloud.com.attacker.invalid" + PATH,
    "https://user@hdnn.fa.us6.oraclecloud.com" + PATH,
    "https://hdnn.fa.us6.oraclecloud.com" + PATH + "?email=secret",
    "https://hdnn.fa.us6.oraclecloud.com" + PATH.replace("114233", "not-numeric"),
    "https://hdnn.fa.us6.oraclecloud.com" + PATH.replace("/email", "/submit"),
])
def test_recognition_is_bounded(url):
    assert not oracle_email._recognized(url)


def test_real_oracle_entry_urls_are_recognized():
    assert oracle_email._recognized("https://hdnn.fa.us6.oraclecloud.com" + PATH)
    assert oracle_email._recognized("https://egup.fa.us2.oraclecloud.com" + PATH.replace("114233", "20278847"))


@pytest.mark.parametrize("extra", [
    '<captcha class="captcha-email-verification"></captcha>',
    '<div class="grecaptcha-badge" style="width:256px;height:60px"></div>',
    '''<oj-dialog class="standard-apply-flow-agreement__dialog" role="dialog"
        aria-label="Terms and Conditions" style="display:none">
        <button aria-label="Close" data-bind="click: onClose"></button>
        <button data-bind="click: accept">Agree</button>
       </oj-dialog>''',
])
def test_empty_captcha_placeholder_passive_badge_and_inactive_terms_dialog(page, oracle_site, extra):
    _open(page, oracle_site, extra=extra)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.blocker.startswith("verification:")
    assert len(_posts(oracle_site)) == 1


def test_oracle_visual_checkbox_avoids_the_terms_link_at_label_center(page, oracle_site):
    terms = '''<label for="legal-disclaimer-checkbox" style="display:inline-block;width:300px">
      <input type="checkbox" id="legal-disclaimer-checkbox" required
        aria-labelledby="legal-disclaimer-checkbox-label" style="display:none">
      <span id="legal-disclaimer-checkbox-label" style="display:inline-flex;width:300px">
        <span class="apply-flow-input-checkbox__button" data-bind="click: toggleAccepted, css: {}"
          style="display:inline-block;width:22px;height:22px;flex-shrink:0"></span>
        <span>I agree with the <a href="/unsafe-terms-navigation">terms and conditions</a></span>
      </span>
    </label>'''
    script = '''<script>
      document.querySelector('[type=submit]').disabled = true;
      document.querySelector('[type=checkbox]').addEventListener('change', e => {
        if (e.isTrusted) document.querySelector('[type=submit]').disabled = false;
      });
    </script>'''
    _open(page, oracle_site, terms=terms, script=script)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.acted and result.blocker.startswith("verification:")
    assert len(_posts(oracle_site)) == 1
    assert all(path != "/unsafe-terms-navigation" for _, path, _ in oracle_site.requests)


def test_inactive_terms_dialog_still_cannot_override_native_method(page, oracle_site):
    extra = '''<oj-dialog class="standard-apply-flow-agreement__dialog" role="dialog"
      aria-label="Terms and Conditions" style="display:none">
      <button data-bind="click: accept" formmethod="get">Agree</button>
    </oj-dialog>'''
    _open(page, oracle_site, extra=extra)
    result = oracle_email.oracle_email_gate(page, _policy(oracle_site), CREDENTIALS)
    assert result.blocker and not result.acted and not result.filled
    assert _value(page, '[name="primary-email"]') == ""
    assert not _posts(oracle_site)

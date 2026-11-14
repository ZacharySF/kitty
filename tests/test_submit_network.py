"""The network tripwire (L2), its release, orphan recovery and passive learning
(research note N3 §4.4-4.6, §6-§7; proposal-07 Step 2d).

Every fixture is served by a SubmitTrap, the ground truth of what left the
browser. Adversarial fixtures run with the layer on and off: off (an unarmed tab,
or L1 without the intercept), the page's own request reaches the trap; on, it is
failed, the trap stays empty and the tab's network log names the rule. All
fixture pages, jobs and values are synthetic; the crash fixtures restart only
the throwaway headless Firefox the test itself started.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from kitty.core import jobs as jobs_core
from kitty.core.apply import bidi as bidi_module
from kitty.core.apply import browser, endpoints, filler, guard
from kitty.core.apply import watch as watch_module
from kitty.core.apply.bidi import Frame, open_tab
from kitty.core.apply.filler import fill_job, fill_jobs
from kitty.core.apply.guard import FIELD, UnsafeAction
from kitty.core.apply.watch import ConfirmationRule, SubmitLearner, Watch, WatchedTab, look, watch_tabs
from submit_trap import SubmitTrap, owner_click
from test_apply import firefox, space  # noqa: F401  (a headless Firefox; the synthetic workspace)

LAYERS = pytest.mark.parametrize("layer", ["on", "off"])
SUBMIT = ("POST", "/trap/submit")


@pytest.fixture(scope="module")
def trap():
    trap = SubmitTrap()
    yield trap
    trap.close()


@pytest.fixture
def tab(firefox, trap):  # noqa: F811
    trap.reset()
    tab = open_tab(firefox)
    yield tab
    tab.release()
    tab.close()


def _serve(trap, tab, body: str, *, layer: str = "on", path: str = "/fixture", family="custom", allowlist=None):
    """Serve a synthetic page and open it, armed (L1 + L2) before navigation when the layer is on."""
    trap.pages[path] = "<!doctype html><html><body>" + body + "</body></html>"
    if layer == "on":
        tab.arm(family=family, allowlist=allowlist)
    tab.goto(trap.url(path))
    return tab.main


def _node(frame: Frame, selector: str) -> str:
    node = frame.node("(s) => document.querySelector(s)", selector)
    assert node is not None, selector
    return node


def _page(frame: Frame, script: str, *args):
    return frame.call(script, *args, page_realm=True)


def _blocked(tab) -> list[tuple[str, str, str]]:
    return [(decision.method, decision.path, decision.rule) for decision in tab.network_blocks]


FETCH_ALL = """async (requests) => {
    const out = [];
    for (const [method, path] of requests) {
        try { await fetch(path, {method, body: method === 'GET' ? undefined : 'synthetic'}); out.push('ok'); }
        catch (error) { out.push('failed'); }
    }
    return out;
}"""


# ------------------------------------------------------------ pure parts


def test_path_templates_keep_the_endpoint_shape_never_its_ids():
    assert guard.path_template("/api/candidates/12345/applications") == "/api/candidates/{n}/applications"
    assert guard.path_template("/wday/cxs/acme/External/job/Austin/Intern_R-123/submit") == (
        "/wday/cxs/acme/External/job/Austin/{id}/submit")
    assert guard.path_template("/u/pat@example.invalid/v2/0123456789abcdef0123/x") == "/u/{id}/v2/{hex}/x"
    assert guard.path_template("/a/123e4567-e89b-12d3-a456-426614174000/") == "/a/{uuid}/"


def test_only_the_inferred_allowlists_are_observed_during_the_survey():
    assert {family for family in guard.PROFILES if guard.allowlist_mode(family) == "observe"} == {
        "greenhouse", "lever", "smartrecruiters"}
    assert guard.allowlist_mode("workday") == guard.allowlist_mode("other") == "enforce"


def test_evidence_files_keep_method_path_template_rule_and_decision_only(space):  # noqa: F811
    observed = guard.Decision(guard.CONTINUE, "greenhouse.unlisted", "POST", "/v1/boards/acme/files/99881", True)
    blocked = guard.Decision(guard.FAIL, "generic.submit", "POST", "/api/applications")
    assert endpoints.record_fill(space.settings, "greenhouse", observed=[observed], blocked=[blocked]) == 2
    endpoints.record_learned(space.settings, "lever", "post", "/v0/postings/acme/0f8b3c2e-1d4a-4b6c-9e7f-1a2b3c4d5e6f/apply")
    rows = endpoints.read(space.settings, endpoints.OBSERVED)
    assert [{key: row[key] for key in row if key != "at"} for row in rows] == [
        {"family": "greenhouse", "method": "POST", "path": "/v1/boards/acme/files/{n}",
         "endpoint": "greenhouse.unlisted", "decision": "observed"},
        {"family": "greenhouse", "method": "POST", "path": "/api/applications",
         "endpoint": "generic.submit", "decision": "fail"},
    ]
    (learned,) = endpoints.read(space.settings, endpoints.LEARNED)
    assert set(learned) == {"at", "family", "method", "path", "source"}
    assert (learned["method"], learned["path"]) == ("POST", "/v0/postings/acme/{uuid}/apply")
    folder = space.settings.data_dir / endpoints.DIRECTORY
    assert oct(folder.stat().st_mode & 0o777) == "0o700"
    assert oct((folder / endpoints.OBSERVED).stat().st_mode & 0o777) == "0o600"


def test_the_typed_causes_classify_the_two_new_stops():
    from kitty.core.apply import attempts

    assert attempts.classify("blocked", guard.network_note(guard.Decision(
        guard.FAIL, "generic.submit", "POST", "/trap/submit")), None, "unsupported_controls") == "submit_blocked_network"
    assert attempts.classify("blocked", filler.restart_note(2), None, "human_boundary") == "restart_firefox"
    assert attempts.outcome_of("restart_firefox") == "needs_owner"
    assert attempts.classify("blocked", filler.LOST_NOTE, "lost the connection to Firefox during x: closed",
                             "browser_error") == "browser_connection"


# ------------------------------------------------------------ X: the page's own requests


X1 = """<fieldset><legend>Are you legally authorized to work in the United States?</legend>
  <label><input type="radio" name="auth" id="yes" value="Yes"> Yes</label>
  <label><input type="radio" name="auth" id="no" value="No"> No</label></fieldset>
<script>
  for (const radio of document.querySelectorAll('[name=auth]'))
    radio.addEventListener('change', () => fetch('/trap/submit', {method: 'POST', keepalive: true, body: 'synthetic'}));
</script>"""

X2 = """<label>First name <input id="first" name="first"></label>
<label>Last name <input id="last" name="last"></label>
<script>
  document.querySelector('#first').addEventListener('blur', () => navigator.sendBeacon('/trap/submit', 'synthetic'));
</script>"""

X3 = """<form method="post" action="/trap/apply">
  <label>Start term <select name="term" onchange="this.form.submit()">
    <option value="">Choose</option><option value="fall">Fall</option></select></label></form>"""

X4 = """<div id="host"></div><script>
  const root = document.querySelector('#host').attachShadow({mode: 'open'});
  root.innerHTML = '<form method="post" action="/trap/apply"><input type="checkbox" id="agree" ' +
    'style="width:30px;height:30px"><label for="agree">I agree</label></form>';
  root.querySelector('#agree').addEventListener('change', () => root.querySelector('form').requestSubmit());
</script>"""

X5 = """<button type="button" id="add" style="width:160px;height:30px">Add another school</button>
<script>
  document.querySelector('#add').addEventListener('click', () =>
    setTimeout(() => fetch('/trap/submit', {method: 'POST', body: 'synthetic'}), 1500));
</script>"""


def _x1(frame):
    frame.click(_node(frame, "#yes"), intent=FIELD)


def _x2(frame):
    first = _node(frame, "#first")
    frame.call("() => document.querySelector('#first').focus()")
    frame.type("Pat", into=first)
    frame.click(_node(frame, "#last"), intent=FIELD)          # the blur sends the beacon


def _x3(frame):
    # kitty's own choice write: set the value and fire change, as page.choose_select does
    frame.call("""() => { const s = document.querySelector('select'); s.value = 'fall';
                         s.dispatchEvent(new Event('change', {bubbles: true})); }""")


def _x4(frame):
    frame.click(frame.node("() => document.querySelector('#host').shadowRoot.querySelector('#agree')"),
                intent=FIELD)


def _x5(frame):
    frame.click(_node(frame, "#add"), intent=FIELD)
    time.sleep(2.0)                                           # the delayed POST fires after the click


_X = {
    # X1: a radio's change handler POSTs with fetch (keepalive)
    "x1 fetch on radio change": (X1, _x1, SUBMIT, "generic.submit"),
    # X2: sendBeacon when the field loses focus
    "x2 beacon on blur": (X2, _x2, SUBMIT, "generic.submit"),
    # X3: form.submit() from a select's change handler: no submit event (P2), a navigation POST
    "x3 form.submit on select change": (X3, _x3, ("POST", "/trap/apply"), "navigation_post"),
    # X4: requestSubmit() on a form inside a shadow root: its submit event never reaches window (P3)
    "x4 shadow requestSubmit": (X4, _x4, ("POST", "/trap/apply"), "navigation_post"),
    # X5: a delayed POST two seconds after a kitty click
    "x5 delayed POST": (X5, _x5, SUBMIT, "generic.submit"),
}


@LAYERS
@pytest.mark.parametrize("case", list(_X))
def test_x_the_pages_own_submit_requests_are_failed_by_the_network_guard(tab, trap, layer, case):
    body, act, request, rule = _X[case]
    frame = _serve(trap, tab, body, layer=layer)
    if layer == "off":
        act(frame)                                            # the same kitty action, nothing armed
        assert trap.wait_for_write() == [request]
        return
    with contextlib.suppress(guard.SubmitBlockedNetwork):     # the action itself may already see it
        act(frame)
    assert trap.wait_for_write(1.0) == []
    assert _blocked(tab) == [(*request, rule)]
    with pytest.raises(guard.SubmitBlockedNetwork):           # the fill stops at its next step
        frame.click(_node(frame, "body"), intent=FIELD)
    assert tab.vetoes == []                                   # L1 saw nothing to veto: L2 alone held it


@LAYERS
def test_x6_a_dedicated_workers_post_is_held_too(tab, trap, layer):
    """N3 §8 R1 expected worker requests to escape L2 (no browsing context). Observed on
    Firefox 155: a dedicated worker's fetch is attributed to its tab and failed. If a
    Firefox change lets it through, this fails (R1 then applies; L0/L1 still stop the
    click that would start such a worker)."""
    frame = _serve(trap, tab, "<p>worker</p>", layer=layer)
    _page(frame, """(url) => { const source = "fetch(" + JSON.stringify(url) + ", {method: 'POST', body: 'synthetic'})";
                              new Worker(URL.createObjectURL(new Blob([source], {type: 'text/javascript'}))); }""",
          trap.url("/trap/submit"))
    if layer == "off":
        assert trap.wait_for_write() == [SUBMIT]
        return
    time.sleep(1.5)
    assert trap.writes() == []
    assert _blocked(tab) == [(*SUBMIT, "generic.submit")]


# ------------------------------------------------------------ profiles, staging and the log


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_greenhouse_unknown_non_gets_are_recorded_not_failed_while_its_allowlist_is_observed(tab, trap, mode):
    frame = _serve(trap, tab, "<p>Synthetic Greenhouse form</p>", family="greenhouse",
                   allowlist=None if mode == "observe" else "enforce")
    results = _page(frame, FETCH_ALL, [["POST", "/v1/boards/acme/drafts"]])
    if mode == "observe":
        assert results == ["ok"] and trap.writes() == [("POST", "/v1/boards/acme/drafts")]
        assert [(d.path, d.rule, d.observed) for d in tab.network_observed] == [
            ("/v1/boards/acme/drafts", "greenhouse.unlisted", True)]
        assert tab.network_blocks == []
    else:
        assert results == ["failed"] and trap.writes() == []
        assert _blocked(tab) == [("POST", "/v1/boards/acme/drafts", "greenhouse.unlisted")]


def test_deny_rules_are_live_while_an_allowlist_is_only_observed(tab, trap):
    frame = _serve(trap, tab, "<p>Synthetic Lever form</p>", family="lever")
    assert _page(frame, FETCH_ALL, [["POST", "/v0/postings/acme/1/apply"]]) == ["failed"]
    assert trap.writes() == []
    assert _blocked(tab) == [("POST", "/v0/postings/acme/1/apply", "lever.apply")]


def test_request_events_keep_method_path_rule_and_decision_only(tab, trap):
    frame = _serve(trap, tab, "<p>Synthetic form</p>")
    results = _page(frame, FETCH_ALL, [
        ["POST", "/api/draft?token=synthetic-secret&email=pat.synthetic%40example.invalid"],
        ["GET", "/asset.css?v=synthetic-version"],
        ["POST", "/api/finalize?step=synthetic-step"],
    ])
    assert results == ["ok", "ok", "failed"]
    assert tab.network_log == [
        {"method": "POST", "path": "/api/draft", "endpoint": "custom.unlisted", "decision": "continue"},
        {"method": "POST", "path": "/api/finalize", "endpoint": "generic.submit", "decision": "fail"},
    ]
    kept = repr((tab.network_log, tab.network_blocks, tab.network_observed))
    assert "synthetic-secret" not in kept and "example.invalid" not in kept and "synthetic-step" not in kept
    assert ("POST", "/api/draft", "token=synthetic-secret&email=pat.synthetic%40example.invalid", 9) in trap.received


def test_workday_fails_every_non_get_while_its_review_page_shows(tab, trap):
    frame = _serve(trap, tab, "<p>Synthetic Workday step</p>", family="workday")
    tab.observe_review(True)                                  # inspect_page's snapshot said Review
    assert _page(frame, FETCH_ALL, [["POST", "/wday/cxs/acme/External/job/x/step"]]) == ["failed"]
    assert _blocked(tab) == [("POST", "/wday/cxs/acme/External/job/x/step", "workday.review")]
    assert trap.writes() == []


def test_a_credential_token_covers_its_own_origin_only_for_its_window(tab, trap, monkeypatch):
    monkeypatch.setattr(bidi_module, "NETWORK_TOKEN_SECONDS", 0.5)
    frame = _serve(trap, tab, """<form method="post" action="/account/login">
        <label>Email <input name="email" value="pat.synthetic@example.invalid"></label></form>""")
    with frame.granting(guard.CREDENTIAL):
        _page(frame, "() => { document.forms[0].submit(); }")
        assert trap.wait_for_write() == [("POST", "/account/login")]
    trap.reset()
    time.sleep(0.8)                                           # the token's window is over
    frame = tab.main
    _page(frame, "() => { document.body.innerHTML = '<form method=post action=/account/login></form>';"
                 " document.forms[0].submit(); }")
    assert trap.wait_for_write(1.0) == []
    assert _blocked(tab)[-1] == ("POST", "/account/login", "navigation_post")


# ------------------------------------------------------------ release (N3 §7)


APPLICATION = """<form id="application" method="post" action="/trap/submit">
  <label>First name <input name="first_name" autocomplete="given-name"></label>
  <button id="submit" type="submit">Submit application</button>
</form>"""


def test_release_answers_then_removes_the_intercept_before_the_veto(tab, trap, monkeypatch):
    _serve(trap, tab, APPLICATION)
    calls: list[str] = []
    command, wait = tab.bidi.command, tab.bidi.wait_answered
    monkeypatch.setattr(tab.bidi, "command", lambda method, params, **kw: calls.append(method) or command(method, params, **kw))
    monkeypatch.setattr(tab.bidi, "wait_answered", lambda timeout: calls.append("wait_answered") or wait(timeout))
    tab.release()
    steps = [call for call in calls if call in ("wait_answered", "network.removeIntercept", "session.unsubscribe",
                                                "script.removePreloadScript", "script.callFunction")]
    steps = steps[steps.index("wait_answered"):]             # a heartbeat may have renewed the lease first
    assert steps[:4] == ["wait_answered", "network.removeIntercept", "session.unsubscribe",
                         "script.removePreloadScript"]
    assert "script.callFunction" in steps[4:]                 # then every frame's veto is disarmed
    assert tab.network_released and not tab.armed
    monkeypatch.undo()
    assert _page(tab.main, FETCH_ALL, [["POST", "/trap/submit"]]) == ["ok"]   # nothing intercepts it now
    assert trap.writes() == [SUBMIT]


FETCH_SUBMIT = """<label>First name <input name="first_name" autocomplete="given-name"></label>
<button id="submit" type="button">Submit application</button>
<script>
  document.querySelector('#submit').addEventListener('click', () =>
    fetch('/trap/submit', {method: 'POST', body: 'synthetic'}));
</script>"""


def _job(space, trap, body: str, path: str = "/apply"):  # noqa: F811
    trap.pages[path] = "<!doctype html><html><body>" + body + "</body></html>"
    job, _ = jobs_core.upsert_job(space.conn, company="Synthetic Co", title=f"Guarded intern {path}",
                                  url=trap.url(path), tracks=[space.track.name],
                                  location="Arlington, TX, United States", country="US")
    return job


@pytest.mark.parametrize("page", ["form post", "fetch"])
def test_lift_after_a_fill_the_owners_submit_reaches_the_server_exactly_once(space, tab, trap, page):  # noqa: F811
    report = fill_job(tab, space.conn, space.settings, space.config,
                      _job(space, trap, APPLICATION if page == "form post" else FETCH_SUBMIT))
    assert report.guard_released and not tab.armed and tab.network_released
    assert tab.network_blocks == [] and trap.writes() == []
    owner_click(tab.main, _node(tab.main, "#submit"))
    assert trap.wait_for_write() == [SUBMIT]
    time.sleep(0.5)
    assert trap.writes() == [SUBMIT]


X1_FORM = """<form id="application" method="post" action="/trap/submit">
  <label>First name <input name="first_name" autocomplete="given-name" required></label>
  <fieldset><legend>Are you legally authorized to work in the United States?</legend>
    <label><input type="radio" name="authorized" value="Yes" required> Yes</label>
    <label><input type="radio" name="authorized" value="No"> No</label></fieldset>
  <label>How did you hear about this position? <input name="source" required></label>
  <button type="submit">Submit application</button>
</form>
<script>
  for (const radio of document.querySelectorAll('[name=authorized]'))
    radio.addEventListener('change', () => fetch('/trap/submit', {method: 'POST', keepalive: true, body: 'synthetic'}));
</script>"""


def test_a_fill_that_trips_the_network_guard_stops_as_submit_blocked_network(space, tab, trap):  # noqa: F811
    job = _job(space, trap, X1_FORM)
    report = fill_job(tab, space.conn, space.settings, space.config, job)
    time.sleep(0.5)
    assert trap.writes() == []
    assert report.status == "blocked" and report.note.startswith("network:") and report.error is None
    assert "POST /trap/submit" in report.note and "generic.submit" in report.note
    cause = space.conn.execute("SELECT cause FROM apply_attempts WHERE id = ?", (report.attempt_id,)).fetchone()[0]
    assert cause == guard.SUBMIT_BLOCKED_NETWORK
    assert jobs_core.get_job(space.conn, job.id).fill_blocker.startswith("network:")
    assert report.guard_released and not tab.armed           # the tab is kept for the owner
    assert _page(tab.main, "() => document.querySelector('[name=first_name]').value") == "Pat"
    evidence = endpoints.read(space.settings, endpoints.OBSERVED)
    assert [(row["method"], row["path"], row["endpoint"], row["decision"]) for row in evidence] == [
        ("POST", "/trap/submit", "generic.submit", "fail")]


# Workday answered HTTP 521 (Cloudflare "Web server is down") to every client during
# the 2026-11-14 trial: an outage, not an unsupported page or a closed posting.
SERVER_ERROR_PAGE = "<h1>An error occurred.</h1><h2>Web server is down</h2>"


@pytest.mark.parametrize("status", [521, 503])
def test_an_employer_server_error_is_a_site_error_not_an_unsupported_page(space, tab, trap, status):  # noqa: F811
    job = _job(space, trap, SERVER_ERROR_PAGE)
    trap.statuses["/apply"] = status
    report = fill_job(tab, space.conn, space.settings, space.config, job)
    assert report.status == "blocked", report.note
    assert report.note.startswith("site_error:") and f"HTTP {status}" in report.note
    assert report.cause == "unavailable"                                # not "unsupported controls"
    assert tuple(space.conn.execute("SELECT cause, outcome FROM apply_attempts WHERE id = ?",
                                    (report.attempt_id,)).fetchone()) == ("site_error", "failed")
    assert jobs_core.get_job(space.conn, job.id).closed_at is None     # an outage never closes a posting
    assert trap.writes() == []


def test_error_wording_on_a_page_that_loaded_is_not_a_site_error(space, tab, trap):  # noqa: F811
    report = fill_job(tab, space.conn, space.settings, space.config, _job(space, trap, SERVER_ERROR_PAGE))
    assert report.status == "blocked" and not report.note.startswith("site_error:")


# ------------------------------------------------------------ orphaned session (proposal-07 Step 2)


CRASH_PAGE = """<form id="application" method="post" action="/trap/submit">
  <label>First name <input name="first_name" autocomplete="given-name"></label>
  <button id="submit" type="submit">Submit application</button></form>"""

CAPTCHA_PAGE = """<h1>Security check</h1><p>Please verify you are human to continue.</p>
<form id="application" method="post" action="/trap/submit">
  <label>First name <input name="first_name" autocomplete="given-name"></label></form>"""

READY_PAGE = """<form id="application" method="post" action="/trap/submit">
  <label>First Name <input name="first_name" autocomplete="given-name" required></label>
  <label>Resume <input name="resume" type="file" required></label>
  <button type="submit">Submit application</button></form>"""


def test_background_reporting_does_not_override_a_successful_fill(space, tab, trap, monkeypatch):  # noqa: F811
    # Substitute only the reporting service's origin with a local receiver:
    # the real policy, Firefox interception, form filler and evidence writer run.
    monkeypatch.setattr(guard, "_CSP_REPORT_ORIGIN", trap.origin, raising=False)
    monkeypatch.setattr(filler, "detect_ats", lambda url: "workday")
    body = READY_PAGE + """<script>
        window.reportDone = fetch('/cdn-cgi/script_monitor/report', {
            method: 'POST', body: 'synthetic-report'
        }).then(() => 'sent', () => 'blocked');
    </script>"""
    job = _job(space, trap, body)
    report = fill_job(tab, space.conn, space.settings, space.config, job)
    assert report.status == "ready", report.note
    assert _page(tab.main, "() => window.reportDone") == "blocked"
    assert _page(tab.main, "() => document.querySelector('[name=first_name]').value") == "Pat"
    assert _page(tab.main, "() => document.querySelector('[name=resume]').files[0].name") == "resume.pdf"
    assert trap.writes() == []
    assert report.guard_released and tab.network_released
    assert jobs_core.get_job(space.conn, job.id).fill_state == "ready"
    assert space.conn.execute("SELECT outcome FROM apply_attempts WHERE id = ?",
                              (report.attempt_id,)).fetchone()[0] == "ready"
    assert [(row["path"], row["decision"]) for row in endpoints.read(space.settings, endpoints.OBSERVED)] == [
        ("/cdn-cgi/script_monitor/report", "fail")]


@pytest.mark.parametrize("review", [False, True])
def test_reporting_drops_preserve_input_and_cannot_hide_a_later_submit(tab, trap, monkeypatch, review):
    monkeypatch.setattr(guard, "_CSP_REPORT_ORIGIN", trap.origin, raising=False)
    frame = _serve(trap, tab, '<label>First name <input name="first"></label>', family="workday")
    tab.observe_review(review)
    assert _page(frame, FETCH_ALL, [["POST", "/cdn-cgi/script_monitor/report"]]) == ["failed"]
    first = _node(frame, '[name="first"]')
    frame.call("() => document.querySelector('input').focus()")
    frame.type("Pat", into=first)
    assert _page(frame, "() => document.querySelector('input').value") == "Pat"
    assert _page(frame, FETCH_ALL, [["POST", "/wday/cxs/acme/External/job/x/submit"]]) == ["failed"]
    # Diagnostic history is bounded. Background traffic must not evict the
    # safety stop and let automation resume after a denied submission.
    assert _page(frame, FETCH_ALL, [["POST", "/cdn-cgi/script_monitor/report"]] * 205) == ["failed"] * 205
    assert trap.writes() == []
    with pytest.raises(guard.SubmitBlockedNetwork, match="workday.submit"):
        tab.check_network()
    with pytest.raises(guard.SubmitBlockedNetwork):
        frame.type("Unsafe continuation", into=first)


def _crash_mid_fill(monkeypatch, crash_path: str, ports: list[int]):
    """The BiDi WebSocket dies mid-fill of `crash_path`, after its page scheduled a POST.

    That POST is then held by the orphaned intercept (nobody can answer it), the
    state N3 §7.4 describes. A restart must drop it, never send it.
    """
    real = filler.fill_form

    def fill_form(page, *args, **kwargs):
        if not ports and page.main.url.endswith(crash_path):
            ports.append(page.bidi.port)
            _page(page.main, "() => { setTimeout(() => fetch('/trap/submit', {method: 'POST', keepalive: true, "
                             "body: 'synthetic'}), 300); }")
            page.bidi._ws.close()                 # deliberate lost transport, throwaway Firefox only
            time.sleep(1.0)
        return real(page, *args, **kwargs)

    monkeypatch.setattr(filler, "fill_form", fill_form)


def _firefoxes(monkeypatch) -> list[int]:
    """Count the headless Firefoxes fill_jobs starts (a restart starts a second one)."""
    started: list[int] = []
    real = filler.headless_firefox

    @contextlib.contextmanager
    def counted(settings):
        with real(settings) as bidi:
            started.append(bidi.port)
            yield bidi

    monkeypatch.setattr(filler, "headless_firefox", counted)
    return started


def _held_post_never_arrives(trap) -> None:
    time.sleep(1.5)
    assert trap.writes() == [], "a request the orphaned session held was sent"


def test_crash_with_no_tab_waiting_restarts_firefox_and_fills_the_job_again(space, trap, monkeypatch):  # noqa: F811
    trap.reset()
    job = _job(space, trap, CRASH_PAGE)
    crashed: list[int] = []
    _crash_mid_fill(monkeypatch, "/apply", crashed)
    started = _firefoxes(monkeypatch)
    messages: list[str] = []
    (report,) = fill_jobs(space.settings, space.config, [job.id], headless=True, progress=messages.append, pause=0)
    assert crashed and len(started) == 2 and started[0] == crashed[0] != started[1]
    assert any("restarting the rika! Firefox" in message for message in messages)
    assert not (report.note or "").startswith("restart_firefox:")
    assert "first name" in [name.casefold() for name in report.filled]
    causes = [row[0] for row in space.conn.execute(
        "SELECT cause FROM apply_attempts WHERE job_id = ? ORDER BY id", (job.id,))]
    assert causes[0] == "browser_connection" and len(causes) == 2 and "restart_firefox" not in causes
    assert not own_firefox_left(crashed[0])
    _held_post_never_arrives(trap)


def own_firefox_left(port: int) -> bool:
    return bool(browser.own_firefox_pids(port))


@pytest.mark.parametrize("waiting", ["captcha", "ready"])
def test_crash_with_a_tab_waiting_raises_one_restart_item_and_restarts_nothing(space, trap, monkeypatch, waiting):  # noqa: F811
    trap.reset()
    first = _job(space, trap, CAPTCHA_PAGE if waiting == "captcha" else READY_PAGE, "/first")
    second = _job(space, trap, CRASH_PAGE, "/second")
    crashed: list[int] = []
    _crash_mid_fill(monkeypatch, "/second", crashed)
    started = _firefoxes(monkeypatch)
    reports = fill_jobs(space.settings, space.config, [first.id, second.id], headless=True, pause=0,
                        watch=Watch(hours=0.0001))          # keep the tabs open, as a visible run does
    assert crashed and len(started) == 1                    # no restart under the owner's tab
    held, item = reports
    assert held.status == ("ready" if waiting == "ready" else "blocked")
    if waiting == "captcha":
        assert held.note.startswith("captcha:")
    assert [report.job_id for report in reports if (report.note or "").startswith("restart_firefox:")] == [second.id]
    assert "the 1 tab waiting for you" in item.note and item.error is None
    assert item.cause == "human_boundary"
    assert jobs_core.get_job(space.conn, second.id).fill_blocker.startswith("restart_firefox:")
    causes = [row[0] for row in space.conn.execute("SELECT cause FROM apply_attempts ORDER BY id")]
    assert causes.count("restart_firefox") == 1
    _held_post_never_arrives(trap)


def test_a_direct_fill_that_loses_its_connection_is_the_typed_handoff(space, trap, monkeypatch):  # noqa: F811
    """Without kitty's own restartable Firefox (a direct fill), nothing restarts or reloads:
    the tab becomes the one `restart_firefox:` handoff and its held request stays unsent."""
    trap.reset()
    job = _job(space, trap, CRASH_PAGE)
    crashed: list[int] = []
    _crash_mid_fill(monkeypatch, "/apply", crashed)
    with pytest.raises(bidi_module.SessionEndError):        # the orphaned session cannot be ended
        with browser.headless_firefox(space.settings) as own:
            page = open_tab(own)
            report = fill_job(page, space.conn, space.settings, space.config, job)
            assert report.note.startswith("restart_firefox: ") and not report.restart
            assert "Restart the rika! Firefox" in report.note and report.error is None
            assert not page.network_released
            _held_post_never_arrives(trap)
    _held_post_never_arrives(trap)                            # killed, not quit: still never sent


# ------------------------------------------------------------ passive learning (OWNER RULING R-PASSIVE)


CONFIRMING = """<h1>Synthetic application</h1>
<button id="submit" type="button">Submit application</button>
<button id="other" type="button">Show details</button>
<script>
  document.querySelector('#submit').addEventListener('click', async () => {
    await fetch('/api/candidates/12345/applications?token=synthetic-secret', {
      method: 'POST', headers: {'X-Synthetic-Header': 'synthetic-header'},
      body: JSON.stringify({email: 'pat.synthetic@example.invalid'})});
    navigator.sendBeacon('/api/analytics/event', 'synthetic');
    location.href = '/jobs/4242/confirmation';
  });
  document.querySelector('#other').addEventListener('click', () =>
    fetch('/api/candidates/12345/draft', {method: 'PUT', body: 'synthetic'}));
</script>"""


def _learning(space, tab, trap, monkeypatch, *, click_submit: bool):  # noqa: F811
    monkeypatch.setitem(watch_module.RULES, "fixture", ConfirmationRule(host="127.0.0.1", path=r"/confirmation/?$"))
    trap.pages["/jobs/4242/confirmation"] = "<!doctype html><p>Thank you for applying.</p>"
    job = _job(space, trap, CONFIRMING, "/jobs/4242/apply")
    tab.goto(job.url)
    watch = Watch(hours=0.02, tabs=[WatchedTab(
        job_id=job.id, context=tab.context, ats="fixture", form_url=job.url, label="Synthetic Co - intern",
        resume_id=None, track=None, marker=tab.context)])
    learned: list[tuple[str, str, str]] = []
    learner = SubmitLearner(tab.bidi, lambda family, method, path: (
        learned.append((family, method, path)), endpoints.record_learned(space.settings, family, method, path)))

    def owner() -> None:
        """The owner, in his own tab, while the watcher (this test's thread) watches it."""
        deadline = time.monotonic() + 10
        while not tab.main.call("() => globalThis.kittySubmitClick !== undefined") and time.monotonic() < deadline:
            time.sleep(0.05)
        owner_click(tab.main, _node(tab.main, "#other"))     # not a Submit: never the learned endpoint
        time.sleep(0.3)
        if click_submit:
            owner_click(tab.main, _node(tab.main, "#submit"))
        else:
            _page(tab.main, """() => { fetch('/api/candidates/12345/applications', {method: 'POST'})
                                         .then(() => { location.href = '/jobs/4242/confirmation'; }); }""")

    hand = threading.Thread(target=owner, daemon=True)
    hand.start()
    watch_tabs(lambda watched: look(tab.bidi, watched), space.conn, space.config, watch, lambda message: None,
               poll_seconds=0.2, learner=learner)
    hand.join(10)
    assert watch.tabs == []                                   # confirmed and recorded
    return learned


def test_passive_learning_records_only_method_and_path_of_the_owners_submit(space, tab, trap, monkeypatch):  # noqa: F811
    learned = _learning(space, tab, trap, monkeypatch, click_submit=True)
    assert learned == [("fixture", "POST", "/api/candidates/12345/applications")]
    (row,) = endpoints.read(space.settings, endpoints.LEARNED)
    assert {key: row[key] for key in row if key != "at"} == {
        "family": "fixture", "method": "POST", "path": "/api/candidates/{n}/applications", "source": "owner_submit"}
    stored = (space.settings.data_dir / endpoints.DIRECTORY / endpoints.LEARNED).read_text()
    for secret in ("synthetic-secret", "synthetic-header", "X-Synthetic", "example.invalid", "token"):
        assert secret not in stored
    # read-only: the owner's request left unmodified, once, and nothing was held
    assert [entry for entry in trap.received if entry[1] == "/api/candidates/12345/applications"] == [
        ("POST", "/api/candidates/12345/applications", "token=synthetic-secret",
         len(json.dumps({"email": "pat.synthetic@example.invalid"}, separators=(",", ":"))))]
    assert trap.hits("/jobs/4242/confirmation") == ["GET"]
    assert not tab.armed


def test_passive_learning_learns_nothing_without_the_owners_submit_click(space, tab, trap, monkeypatch):  # noqa: F811
    assert _learning(space, tab, trap, monkeypatch, click_submit=False) == []
    assert endpoints.read(space.settings, endpoints.LEARNED) == []


def test_passive_learning_never_touches_a_tab_kitty_still_holds(space, tab, trap):  # noqa: F811
    _serve(trap, tab, "<p>armed</p>")
    learner = SubmitLearner(tab.bidi, lambda *args: pytest.fail("learned from an armed tab"))
    learner.start([SimpleNamespace(context=tab.context)])
    try:
        assert learner._subscriptions == [] and learner._preloads == []
    finally:
        learner.stop()


def test_kitty_refuses_input_in_a_tab_once_the_network_guard_failed_a_request(tab, trap):
    frame = _serve(trap, tab, "<label>City <input id='city'></label>")
    assert _page(frame, FETCH_ALL, [["POST", "/trap/submit"]]) == ["failed"]
    city = _node(frame, "#city")
    frame.call("() => document.querySelector('#city').focus()")
    with pytest.raises(guard.SubmitBlockedNetwork):
        frame.type("Austin", into=city)
    tab.release()
    with pytest.raises(UnsafeAction, match="released"):
        frame.type("Austin", into=city)


def test_crash_control_without_the_intercept_the_pages_post_is_sent(space, trap, monkeypatch):  # noqa: F811
    """Sensitivity control for the crash fixtures: with L1 only, the same POST leaves."""
    trap.reset()
    job = _job(space, trap, CRASH_PAGE)
    monkeypatch.setattr(bidi_module.Tab, "_arm_network", lambda self, guard: None)
    crashed: list[int] = []
    _crash_mid_fill(monkeypatch, "/apply", crashed)
    fill_jobs(space.settings, space.config, [job.id], headless=True, pause=0)
    assert crashed and trap.wait_for_write() == [SUBMIT]


def test_a_get_navigation_to_an_application_page_is_reading_not_sending():
    """Custom sites: a GET navigation is failed only for a submit verb (N3 §8 R11), never for
    a page whose path ends in application(s), where forms are read and filled."""
    def navigate(url):
        return guard.policy({"request": {"method": "GET", "url": url}, "navigation": "n1", "context": "t"},
                            "other", ())

    assert navigate("https://careers.example.invalid/jobs/42/application").verdict == guard.CONTINUE
    assert navigate("https://careers.example.invalid/my/applications").verdict == guard.CONTINUE
    blocked = navigate("https://careers.example.invalid/apply/submitApplication?id=42")
    assert (blocked.verdict, blocked.rule) == (guard.FAIL, "generic.submit")
    post = guard.policy({"request": {"method": "POST", "url": "https://careers.example.invalid/api/applications"},
                         "context": "t"}, "other", ())
    assert (post.verdict, post.rule) == (guard.FAIL, "generic.submit")

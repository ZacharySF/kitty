"""BiDi event dispatch: events reach handlers, blocked requests are answered at once.

Prerequisite for the no-submit layer's network tripwire (N3 §4.4 transport).
These tests arm a `network.addIntercept` by hand in a throwaway headless
Firefox against a local recording server; real fills do not arm it yet.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

import pytest

from kitty.core.apply.bidi import (
    Bidi,
    BidiError,
    ConnectionLost,
    SessionEndError,
    Tab,
    open_tab,
)
from kitty.core.apply.browser import headless_firefox
from kitty.core.apply.guard import CONTINUE, FAIL, policy
from kitty.settings import Settings, find_firefox

PAGE = b"""<!doctype html><title>Guard transport fixture</title>
<form id="top" method="post" action="/trap/form"><input name="q" value="synthetic"></form>
"""
FRAME = b"""<!doctype html><title>Frame</title>
<form id="inner" method="post" action="/trap/frame-form"><input name="q" value="synthetic"></form>
"""


class Trap:
    """A local HTTP server that records (method, path) of every request it receives."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.arrived = threading.Condition()
        trap = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                with trap.arrived:
                    trap.requests.append((self.command, self.path))
                    trap.arrived.notify_all()
                body = FRAME if self.path == "/frame" else PAGE
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = _record

            def log_message(self, *_args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def writes(self) -> list[tuple[str, str]]:
        with self.arrived:
            return [request for request in self.requests if request[0] != "GET"]

    def wait_for(self, request: tuple[str, str], timeout: float) -> bool:
        with self.arrived:
            return self.arrived.wait_for(lambda: request in self.requests, timeout)


@pytest.fixture(scope="module")
def firefox(tmp_path_factory):
    """One headless Firefox for the module's browser tests (skips when not installed)."""
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    settings = Settings.load(root=tmp_path_factory.mktemp("firefox"))
    with headless_firefox(settings) as bidi:
        yield bidi


@pytest.fixture
def trap():
    with Trap() as server:
        yield server


@pytest.fixture
def page(firefox):
    tab = open_tab(firefox)
    yield tab
    firefox.set_request_decider(None)
    tab.close()


def _js(tab: Tab, expression: str, *args, timeout: float = 30.0):
    return tab.main.call(expression, *args, page_realm=True, timeout=timeout)


def _arm(tab: Tab) -> tuple[str, str]:
    """Hold every request of this tab at beforeRequestSent (what L2 will do per fill)."""
    subscription = tab.bidi.command("session.subscribe", {
        "events": ["network.beforeRequestSent"], "contexts": [tab.context]})["subscription"]
    intercept = tab.bidi.command("network.addIntercept", {
        "phases": ["beforeRequestSent"], "contexts": [tab.context]})["intercept"]
    return intercept, subscription


def _disarm(tab: Tab, armed: tuple[str, str]) -> None:
    intercept, subscription = armed
    tab.bidi.command("network.removeIntercept", {"intercept": intercept})
    tab.bidi.command("session.unsubscribe", {"subscriptions": [subscription]})


FETCH_ALL = """async (requests) => {
    const out = [];
    for (const [method, path] of requests) {
        try {
            await fetch(path, {method, body: method === 'GET' ? undefined : 'synthetic'});
            out.push('ok');
        } catch (error) {
            out.push('failed');
        }
    }
    return out;
}"""


def test_events_reach_the_handlers_registered_for_them(page, trap):
    seen: list[dict] = []
    other: list[dict] = []
    stop = page.bidi.on("browsingContext.load", seen.append)
    page.bidi.on("log.entryAdded", other.append)()  # registered, then removed at once
    subscription = page.bidi.command("session.subscribe", {
        "events": ["browsingContext.load", "log.entryAdded"], "contexts": [page.context]})["subscription"]
    try:
        page.goto(trap.origin + "/page")
        _js(page, "() => console.log('synthetic log line')")
        deadline = time.monotonic() + 10
        while not any(e.get("url") == trap.origin + "/page" for e in seen) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert any(e["context"] == page.context and e["url"] == trap.origin + "/page" for e in seen)
        assert other == []

        stop()
        count = len(seen)
        page.goto(trap.origin + "/page?again")
        time.sleep(0.3)
        assert len(seen) == count  # a removed handler no longer receives events
    finally:
        page.bidi.command("session.unsubscribe", {"subscriptions": [subscription]})


def test_blocked_requests_are_answered_inside_the_command_that_caused_them(page, trap):
    page.goto(trap.origin + "/page")
    decisions = []

    def decide(event: dict) -> str:
        decision = policy(event, "other", ())
        decisions.append(decision.log_entry())
        return decision.verdict

    page.bidi.set_request_decider(decide)
    intercept = _arm(page)
    try:
        # One script.callFunction command: its own reply can only arrive after
        # kitty answered every request the script made while it ran.
        results = _js(page, FETCH_ALL, [["GET", "/data"], ["POST", "/draft/save"], ["POST", "/trap/submit"]])
        assert results == ["ok", "ok", "failed"]
        assert decisions == [
            {"method": "GET", "path": "/data", "endpoint": "safe_method", "decision": CONTINUE},
            {"method": "POST", "path": "/draft/save", "endpoint": "custom.unlisted", "decision": CONTINUE},
            {"method": "POST", "path": "/trap/submit", "endpoint": "generic.submit", "decision": FAIL},
        ]
        assert page.bidi.unanswered_requests == 0
    finally:
        _disarm(page, intercept)
    assert trap.writes() == [("POST", "/draft/save")]
    assert ("GET", "/data") in trap.requests


def test_form_posts_are_navigations_in_the_top_document_and_in_iframes(page, trap):
    page.goto(trap.origin + "/page")
    _js(page, """(src) => new Promise(resolve => {
        const frame = document.createElement('iframe');
        frame.onload = () => resolve(true);
        frame.src = src;
        document.body.append(frame);
    })""", trap.origin + "/frame")
    decisions = []

    def decide(event: dict) -> str:
        decision = policy(event, "other", ())
        decisions.append(decision.log_entry())
        return decision.verdict

    page.bidi.set_request_decider(decide)
    intercept = _arm(page)
    try:
        _js(page, "() => document.querySelector('iframe').contentDocument.forms.inner.submit()")
        _js(page, "() => document.forms.top.submit()")
        deadline = time.monotonic() + 10
        while len(decisions) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
    finally:
        _disarm(page, intercept)
    assert {"method": "POST", "path": "/trap/frame-form", "endpoint": "navigation_post",
            "decision": FAIL} in decisions
    assert {"method": "POST", "path": "/trap/form", "endpoint": "navigation_post",
            "decision": FAIL} in decisions
    assert trap.writes() == []


def test_requests_are_answered_while_no_command_is_waiting(page, trap):
    page.goto(trap.origin + "/page")
    page.bidi.set_request_decider(lambda event: policy(event, "other", ()).verdict)
    intercept = _arm(page)
    try:
        _js(page, """() => { setTimeout(() => fetch('/later', {method: 'POST', body: 'synthetic'}), 300); }""")
        # No BiDi command is in flight while this waits: only the reader can answer.
        assert trap.wait_for(("POST", "/later"), timeout=10)
    finally:
        _disarm(page, intercept)


def test_a_blocked_request_without_a_decider_fails_closed(page, trap):
    page.goto(trap.origin + "/page")
    page.bidi.set_request_decider(None)
    intercept = _arm(page)
    try:
        assert _js(page, FETCH_ALL, [["GET", "/data"], ["POST", "/draft/save"]]) == ["failed", "failed"]
    finally:
        _disarm(page, intercept)
    assert trap.writes() == []
    assert ("GET", "/data") not in trap.requests


def test_a_decider_that_raises_or_waits_for_a_reply_fails_the_request(page, trap):
    page.goto(trap.origin + "/page")
    errors: list[Exception] = []

    def decide(event: dict) -> str:
        try:
            page.bidi.command("session.status", {})
        except BidiError as exc:
            errors.append(exc)
            raise
        return CONTINUE

    page.bidi.set_request_decider(decide)
    intercept = _arm(page)
    try:
        assert _js(page, FETCH_ALL, [["POST", "/draft/save"]]) == ["failed"]
        page.bidi.set_request_decider(lambda event: "maybe")
        assert _js(page, FETCH_ALL, [["POST", "/draft/save"]]) == ["failed"]
    finally:
        _disarm(page, intercept)
    assert len(errors) == 1 and "event reader" in str(errors[0])
    assert trap.writes() == []


def test_session_end_waits_for_a_pending_decision_and_holds_nothing(tmp_path, trap):
    """Ending a session continues every request still blocked (BiDi cleanup, P10).

    A decision still being made when close() is called must be sent before
    session.end; otherwise Firefox would send the request kitty meant to fail.
    """
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    entered = threading.Event()

    def slow_fail(event: dict) -> str:
        if event["request"]["url"].endswith("/trap/slow"):
            entered.set()
            time.sleep(1.5)
            return FAIL
        return CONTINUE

    with headless_firefox(Settings.load(root=tmp_path)) as owner:
        tab = open_tab(owner)
        tab.goto(trap.origin + "/page?held")
        owner.set_request_decider(slow_fail)
        _arm(tab)
        _js(tab, """() => {
            window.slowResult = 'pending';
            fetch('/trap/slow', {method: 'POST', body: 'synthetic'})
                .then(() => { window.slowResult = 'sent'; }, () => { window.slowResult = 'failed'; });
        }""")
        assert entered.wait(10)
        owner.close()  # called while the decision is still being made
        assert owner.session_ended
        assert owner.unanswered_requests == 0

        replacement = Bidi(owner.url)
        try:
            # Context ids are per session: find the same tab by its document.
            retained = Tab(replacement, next(
                node["context"] for node in
                replacement.command("browsingContext.getTree", {})["contexts"]
                if node["url"] == trap.origin + "/page?held"
            ))
            deadline = time.monotonic() + 5
            while _js(retained, "() => window.slowResult") == "pending" and time.monotonic() < deadline:
                time.sleep(0.05)
            assert _js(retained, "() => window.slowResult") == "failed"
            # The intercept ended with its session: nothing is held for the next controller.
            assert _js(retained, FETCH_ALL, [["POST", "/draft/after"]], timeout=10) == ["ok"]
        finally:
            replacement.close()
    assert ("POST", "/trap/slow") not in trap.requests
    assert ("POST", "/draft/after") in trap.requests


def test_a_lost_connection_wakes_a_waiting_command_and_keeps_end_error_semantics(tmp_path):
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    with pytest.raises(SessionEndError) as failed:
        with headless_firefox(Settings.load(root=tmp_path)) as owner:
            tab = open_tab(owner)
            session_id = owner.session_id
            closer = threading.Timer(0.5, owner._ws.close)  # deliberate loss, throwaway Firefox only
            closer.start()
            started = time.monotonic()
            with pytest.raises(ConnectionLost):
                _js(tab, "() => new Promise(resolve => setTimeout(resolve, 20000))", timeout=25)
            assert time.monotonic() - started < 10
            closer.join()
            with pytest.raises(ConnectionLost):
                owner.command("session.status", {})
    assert failed.value.session_id == session_id
    assert not failed.value.end_acknowledged
    assert not owner.session_ended

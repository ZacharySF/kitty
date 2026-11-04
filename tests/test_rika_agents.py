"""Rika's worker agents against synthetic herdr and Tern: approval before start, the task
brief, the merged roster and Open in a Tern window. Never a real herdr, Tern or agent."""
from __future__ import annotations

import os
import shutil
import time

import pytest

from tern_support import FakeTern
from terminal_support import FakeHerdr, make_root, site

SESSION = "kitty-rika-test"


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    (tmp_path / "herdr").mkdir()
    herdr = FakeHerdr(tmp_path / "herdr")
    (tmp_path / "tern").mkdir()
    tern = FakeTern(tmp_path / "tern")
    monkeypatch.setenv("HOME", str(herdr.home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(herdr.home / ".config"))
    monkeypatch.setenv("KITTY_HERDR", str(herdr.binary))
    monkeypatch.setenv("KITTY_HERDR_SESSION", SESSION)
    monkeypatch.setenv("PATH", f"{tern.bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tern.runtime))
    monkeypatch.delenv("TERN_DAEMON_SOCKET", raising=False)
    monkeypatch.setattr("kitty.core.tern.FHS_HOST_ETC", tmp_path / "no-host-etc")
    try:
        yield herdr, tern
    finally:
        herdr.cleanup(SESSION)
        tern.cleanup()
        shutil.rmtree(herdr.home, ignore_errors=True)
        shutil.rmtree(tern.runtime, ignore_errors=True)


def wait_for(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not (found := check()):
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)
    return found


def latest(running, kind):
    items = running.request("/api/rika/history")[1]["items"]
    return [item for item in items if item["kind"] == kind]


def test_a_worker_starts_only_after_approval_gets_its_brief_and_opens_in_tern(tmp_path, fakes):
    herdr, tern = fakes
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        assert running.request("/api/rika/agents") == (200, {"agents": []})
        running.request("/api/rika/messages", {"text": "start claude to fix the parser"})
        approval = wait_for(lambda: latest(running, "approval"))[0]
        assert approval["status"] == "pending" and approval["tool"] == "start_worker"
        assert str(settings.root) in approval["detail"] and "fix the parser" in approval["detail"]
        assert herdr.invocations() == []  # nothing starts before the tap
        running.request(f"/api/rika/approvals/{approval['id']}", {"decision": "approve"})
        action = wait_for(lambda: latest(running, "action"))[0]
        assert action["status"] == "done", action
        calls = [item["argv"][2:] for item in herdr.invocations()]
        assert calls[0] == ["server"]
        assert ["workspace", "create", "--cwd", str(settings.root), "--label", "Rika", "--focus"] in calls
        start = next(call for call in calls if call[:2] == ["agent", "start"])
        assert start[3:5] == ["--kind", "claude"]
        prompt = next(call for call in calls if call[:2] == ["agent", "prompt"])
        assert prompt[2] == start[6] and prompt[3].startswith("Task from Rika, the rika! agent: fix the parser")

        # A coding agent the user runs in a Tern pane joins the roster beside herdr's.
        running.app.tern.run_tab(["codex"], settings.root)
        agents = running.request("/api/rika/agents")[1]["agents"]
        herdr_agent, tern_agent = agents
        assert herdr_agent["where"] == {"herdr_pane": prompt[2]} and herdr_agent["kind"] == "claude"
        assert herdr_agent["status"] == "idle" and herdr_agent["task"] == "fix the parser"
        assert tern_agent["kind"] == "codex" and set(tern_agent["where"]) == {"tern_pane"}
        # The fake herdr never runs the launcher shim: no exchange, so neither card is mediated.
        assert herdr_agent["guarantee"] == "none" and tern_agent["guarantee"] == "none"

        # Open shows the herdr pane through a Tern tab attached to rika!'s session, once.
        for _ in range(2):
            status, opened = running.request(f"/api/rika/agents/{herdr_agent['id']}/open", {})
            assert (status, opened) == (200, {"ok": True, "window": "tern", "view": None})
        attached = [call for call in tern.calls() if call[:2] == ["new", "tab"] and str(herdr.binary) in call]
        assert len(attached) == 1 and attached[0][-2:] == ["--session", SESSION]
        assert ["agent", "focus", prompt[2]] in [item["argv"][2:] for item in herdr.invocations()]
        assert running.request(f"/api/rika/agents/{tern_agent['id']}/open", {})[1]["window"] == "tern"
        assert ["focus", str(tern_agent["where"]["tern_pane"])] in tern.calls()
        assert running.request("/api/rika/agents/tern:999/open", {})[0] == 404
    # Quit stops the headless session Rika started.
    assert not herdr.listening(SESSION)


def test_a_rejected_worker_never_touches_herdr(tmp_path, fakes):
    herdr, _tern = fakes
    settings = make_root(tmp_path, ("workspace", "terminal"))
    with site(settings) as running:
        running.request("/api/rika/messages", {"text": "start omp"})
        approval = wait_for(lambda: latest(running, "approval"))[0]
        running.request(f"/api/rika/approvals/{approval['id']}", {"decision": "reject"})
        reply = wait_for(lambda: [item for item in latest(running, "message") if item["role"] == "rika"
                                  and item["status"] == "done"])[0]
        assert reply["text"].startswith("Not done: Start omp")
    assert herdr.invocations() == []


def test_worker_tools_are_absent_without_the_terminal_module(tmp_path, fakes):
    herdr, _tern = fakes
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        assert running.request("/api/rika/agents") == (200, {"agents": []})
        running.request("/api/rika/messages", {"text": "start omp to tidy up"})
        reply = wait_for(lambda: [item for item in latest(running, "message") if item["role"] == "rika"
                                  and item["status"] != "pending"])[0]
        assert latest(running, "approval") == []
        assert "OpenRouter API key" in reply["text"]
    assert herdr.invocations() == []

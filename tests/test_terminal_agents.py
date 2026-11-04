"""Q2/Q9 agent launch and attachment contracts, using only synthetic sessions."""
from __future__ import annotations

import json
import os

import pytest

from terminal_support import FrameReader, make_root, site
from test_web_terminal import herdr, refused  # shared isolated executable fixture

from kitty.core import agent_sessions


def attach(running):
    status, _, connection, rest = running.connect(running.token())
    assert status == 101
    FrameReader(connection, rest).until(b"FAKE-HERDR")
    return connection


def test_agent_launcher_is_disabled_with_the_module(tmp_path, herdr):
    with site(make_root(tmp_path, ())) as running:
        assert refused(*running.request("/api/terminal/agents"))
        assert refused(*running.request("/api/terminal/agents", {"kind": "omp"}))
    assert herdr.invocations() == []


def test_external_attachment_instructions_are_explicit_and_do_not_start_a_session(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        status, info = running.request("/api/terminal/agents")
        assert status == 200
        assert info["session"] == "kitty-test"
        assert info["binary"] == str(herdr.binary)
        assert info["cwd"] == str(settings.root)
        assert info["default"] == "omp"
        assert {"omp", "claude", "codex"} <= set(info["kinds"])
    assert herdr.invocations() == []


def test_agent_launch_needs_an_attached_terminal_and_same_origin_proof(tmp_path, herdr):
    with site(make_root(tmp_path, ("terminal",))) as running:
        assert running.request("/api/terminal/agents", {"kind": "omp"}, headers={"X-Kitty-Request": None})[0] == 403
        assert running.request("/api/terminal/agents", {"kind": "omp"}, headers={"Origin": "https://example.org"})[0] == 403
        assert running.request("/api/terminal/agents", {"kind": "omp"})[0] == 409
    assert herdr.invocations() == []


@pytest.mark.parametrize("body", [{"kind": "sh"}, {"kind": "omp; touch /tmp/no"}, {"kind": []}, {"kind": "omp", "command": "anything"}])
def test_invalid_agent_requests_cannot_create_a_tab(tmp_path, herdr, body):
    with site(make_root(tmp_path, ("terminal",))) as running:
        assert running.request("/api/terminal/agents", body)[0] == 400
    assert herdr.invocations() == []


@pytest.mark.parametrize("kind", ["omp", "claude", "codex"])
def test_agent_has_its_own_labelled_tab_and_the_bundled_skill(tmp_path, herdr, kind):
    settings = make_root(tmp_path, ("terminal",))
    herdr.activity_file.write_text(json.dumps({"workspaces": [{"workspace_id": "w7", "label": "Sample", "focused": True}]}))
    with site(settings) as running:
        connection = attach(running)
        try:
            status, launched = running.request("/api/terminal/agents", {"kind": kind})
            assert status == 200, launched
            assert launched["tab"] == "w7:t2"
            assert launched["pane"] == "w7:p2"
            assert launched["kind"] == kind
            # The activity strip shows herdr's agent name, so it names the coding agent.
            assert launched["name"].startswith(f"{kind}-")
            # The fake herdr never runs the pane's shim, so the launch exchange never happens:
            # the tab gets no token and says so (kitty.core.agent_sessions).
            mediation = launched["mediation"]
            assert (mediation["state"], mediation["guarantee"]) == ("pending", "none")
            assert "no token" in mediation["message"]
            activity = running.request("/api/terminal/activity")[1]
            [agent] = activity["workspaces"][0]["agents"]
            assert {key: agent[key] for key in ("pane", "name", "status")} == {"pane": "w7:p2", "name": launched["name"], "status": "idle"}
            # A new agent has no task or output yet; the dock card times it from `since`.
            assert (agent["task"], agent["line"], agent["revision"]) == ("", "", 0)
            assert isinstance(agent["since"], int)
        finally:
            connection.close()
    creates = [call for call in herdr.invocations() if call["argv"][2:4] == ["tab", "create"]]
    assert len(creates) == 1
    args = creates[0]["argv"]
    assert args[args.index("--workspace") + 1] == "w7"
    assert args[args.index("--cwd") + 1] == str(settings.root)
    assert args[args.index("--label") + 1] == "Agent"
    env = dict(item.split("=", 1) for item in (args[index + 1] for index, value in enumerate(args) if value == "--env"))
    assert len(env["KITTY_LAUNCH_NONCE"]) == 43 and env["KITTY_LAUNCH_ORIGIN"].startswith("http://127.0.0.1:")
    bin_dir = settings.root / ".private" / "herdr" / "bin"
    assert env["PATH"].split(os.pathsep)[0] == str(bin_dir) == env["KITTY_LAUNCH_SHIMS"]
    shim = (bin_dir / kind).read_text()
    assert "kitty.agent_launch" in shim and f"--kind {kind}" in shim and (bin_dir / kind).stat().st_mode & 0o777 == 0o700
    renames = [call["argv"][2:] for call in herdr.invocations() if call["argv"][2:4] == ["tab", "rename"]]
    assert renames == [["tab", "rename", "w7:t2", "Agent · Not mediated"]]
    starts = [call for call in herdr.invocations() if call["argv"][2:4] == ["agent", "start"]]
    assert len(starts) == 1
    args = starts[0]["argv"]
    assert args[args.index("--pane") + 1] == "w7:p2"
    assert args[args.index("--kind") + 1] == kind
    # The skill is an emitted interface: herdr's own skill reaches the agent where its
    # socket stays reachable; a Full tab (socket closed) gets rika!'s mediation note.
    skill = settings.root / ".private" / "herdr" / "SKILL.md"
    if agent_sessions.host_guarantee()["guarantee"] == "full":
        assert skill.read_text() == agent_sessions.MEDIATION_NOTE
    else:
        assert skill.read_text() == herdr.skill + "\n\n" + agent_sessions.MEDIATION_NOTE
    assert skill.stat().st_mode & 0o777 == 0o600
    assert str(skill) in " ".join(args[args.index("--") + 1:])


def test_startup_failure_preserves_the_agent_tab_and_reports_its_pane(tmp_path, herdr):
    settings = make_root(tmp_path, ("terminal",))
    herdr.activity_file.write_text(json.dumps({"workspaces": [{"workspace_id": "w7", "label": "Sample", "focused": True}]}))
    herdr.agent_error.write_text("agent_not_ready")
    with site(settings) as running:
        connection = attach(running)
        try:
            status, failure = running.request("/api/terminal/agents", {"kind": "omp"})
            assert status == 503
            assert "w7:p2" in failure["error"]
            assert "agent_not_ready" in failure["error"]
            assert "not retried" in failure["error"]
        finally:
            connection.close()
    assert not any(call["argv"][2:4] == ["tab", "close"] for call in herdr.invocations())

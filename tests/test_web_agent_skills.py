"""Agent skills over HTTP (kitty.web_agent_skills) against synthetic herdr and a fake
HOME: the opt-in, the catalogue, Open file through the desktop owner pipe, the launch
checklist on both agent starts, the now state on Rika's roster, and nothing stored."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

from kitty.web_desktop import DesktopOwner
from terminal_support import make_root, site
from test_terminal_agents import attach
from test_web_terminal import herdr, refused  # noqa: F401 - shared isolated executable fixture

FIXTURES = Path(__file__).parent / "fixtures" / "agent_skills"
PATH = "/api/terminal/agent-skills"


def skill(root: Path, name: str, description: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\nBody.\n")
    return path


def rows(db: Path) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}


def test_the_catalogue_needs_the_terminal_module(tmp_path, herdr):
    with site(make_root(tmp_path, ())) as running:
        assert refused(*running.request(PATH))
        assert refused(*running.request(PATH + "/settings", {"kind": "omp", "enabled": True}))


def test_catalogue_is_off_per_kind_until_enabled_then_names_only(tmp_path, herdr):
    home = herdr.home
    lavish = skill(home / ".omp/agent/skills", "lavish", "Plans as HTML.")
    skill(home / ".claude/skills", "loopy", "Loops.")
    (home / ".omp/agent/mcp.json").write_text(json.dumps({"mcpServers": {"figma": {"env": {"KEY": "sk-live-SECRET"}}}}))
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        before = rows(settings.db_path)
        status, catalog = running.request(PATH)
        assert status == 200 and catalog["can_open"] is False
        assert [(kind["kind"], kind["label"], kind["enabled"], kind["skills"]) for kind in catalog["kinds"]] == [
            ("omp", "omp", False, []), ("claude", "Claude Code", False, []), ("codex", "Codex", False, [])]
        status, catalog = running.request(PATH + "/settings", {"kind": "omp", "enabled": True})
        assert status == 200
        omp, claude, _ = catalog["kinds"]
        assert omp["enabled"] and omp["skills"] == [{"name": "lavish", "description": "Plans as HTML.", "path": str(lavish)}]
        assert omp["mcp"] == [{"name": "figma", "enabled": True}] and "SECRET" not in json.dumps(catalog)
        assert not claude["enabled"] and claude["skills"] == []
        for body in ({"kind": "sh", "enabled": True}, {"kind": "omp", "enabled": "yes"}, {"kind": "omp"}):
            assert running.request(PATH + "/settings", body)[0] == 400
        # Nothing about agent skills is kept in the database.
        assert rows(settings.db_path) == before
    saved = json.loads((settings.root / ".private/agents/settings.json").read_text())
    assert saved == {"enabled": {"claude": False, "codex": False, "omp": True}}


def test_open_file_goes_through_the_desktop_opener_for_listed_skills_only(tmp_path, herdr):
    lavish = skill(herdr.home / ".omp/agent/skills", "lavish", "Plans.")
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        running.request(PATH + "/settings", {"kind": "omp", "enabled": True})
        body = {"kind": "omp", "path": str(lavish)}
        assert running.request(PATH + "/open", body) == (409, {"error": "Open file needs the rika! desktop app; use Copy path instead"})
        lines: list[str] = []
        running.app.desktop = DesktopOwner(lines.append)
        assert running.request(PATH)[1]["can_open"] is True
        assert running.request(PATH + "/open", body) == (200, {"ok": True})
        assert [json.loads(line) for line in lines] == [{"command": "open-skill", "path": str(lavish)}]
        for other in ({"kind": "omp", "path": "/etc/passwd"}, {"kind": "claude", "path": str(lavish)},
                      {"kind": "omp", "path": str(lavish.parent / "notes.md")}):
            assert running.request(PATH + "/open", other)[0] == 404
        assert running.request(PATH + "/open", {"kind": "omp"})[0] == 400
        assert len(lines) == 1


def test_new_agent_checklist_and_hook_become_launch_flags(tmp_path, herdr):
    home = herdr.home
    for name in ("grill-me", "lavish", "loopy"):
        skill(home / ".omp/agent/skills", name, "A skill.")
    skill(home / ".codex/skills/.system", "imagegen", "Images.")
    settings = make_root(tmp_path, ("terminal",))
    herdr.activity_file.write_text(json.dumps({"workspaces": [{"workspace_id": "w7", "label": "Sample", "focused": True}]}))
    with site(settings) as running:
        connection = attach(running)
        try:
            # Off: no hook, and no skill can be switched off.
            assert running.request("/api/terminal/agents", {"kind": "omp", "skills_off": ["lavish"]})[0] == 400
            assert running.request("/api/terminal/agents", {"kind": "omp"})[0] == 200
            running.request(PATH + "/settings", {"kind": "omp", "enabled": True})
            running.request(PATH + "/settings", {"kind": "codex", "enabled": True})
            assert running.request("/api/terminal/agents", {"kind": "omp", "skills_off": ["nope"]})[0] == 400
            assert running.request("/api/terminal/agents", {"kind": "omp", "skills_off": "lavish"})[0] == 400
            status, launched = running.request("/api/terminal/agents", {"kind": "omp", "skills_off": ["lavish"]})
            assert status == 200 and "notice" not in launched
            status, launched = running.request("/api/terminal/agents", {"kind": "codex", "skills_off": ["imagegen"]})
            assert status == 200 and "/hooks" in launched["notice"]
        finally:
            connection.close()
    starts = [call["argv"] for call in herdr.invocations() if call["argv"][2:4] == ["agent", "start"]]
    plain, omp, codex = (start[start.index("--") + 1:] for start in starts)
    assert plain == ["--append-system-prompt", str(settings.root / ".private/herdr/SKILL.md")]
    extension = settings.root / ".private/agents/kitty-activity.ts"
    assert omp[2:] == ["--extension", str(extension), "--skills", "grill-me,loopy"]
    assert codex[2] == "-c" and str(settings.root / ".private/agents/kitty-activity-hook") in codex[3]
    assert codex[4:] == ["-c", 'skills.config=[{name="imagegen",enabled=false}]']
    assert extension.stat().st_mode & 0o777 == 0o600


def test_rika_roster_carries_now_from_herdr_tokens_and_the_log(tmp_path, herdr):
    home = herdr.home
    log = home / ".claude/projects/-work/s.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text((FIXTURES / "claude.jsonl").read_text())
    herdr.activity_file.write_text(json.dumps({
        "workspaces": [{"workspace_id": "w1", "label": "Main", "focused": True}],
        "agents": [{"workspace_id": "w1", "pane_id": "w1:p1", "agent": "omp", "name": "omp-1", "agent_status": "working",
                    "tokens": {"kitty-tool": "read", "kitty-skill": "lavish"}},
                   {"workspace_id": "w1", "pane_id": "w1:p2", "agent": "claude", "name": "claude-1", "agent_status": "blocked",
                    "agent_session": {"source": "herdr:claude", "agent": "claude", "kind": "path", "value": str(log)}}]}))
    settings = make_root(tmp_path, ("terminal",))
    with site(settings) as running:
        attach(running).close()
        agents = running.request("/api/rika/agents")[1]["agents"]
        assert [agent["now"] for agent in agents] == [None, None]
        running.request(PATH + "/settings", {"kind": "omp", "enabled": True})
        running.request(PATH + "/settings", {"kind": "claude", "enabled": True})
        omp, claude = running.request("/api/rika/agents")[1]["agents"]
        assert (omp["now"]["label"], omp["now"]["via"]) == ("lavish · read", "hook")
        assert (claude["now"]["label"], claude["now"]["via"]) == ("no-mistakes · Bash", "log")
        # herdr's raw signals never reach the page's activity route.
        activity = running.request("/api/terminal/activity")[1]
        assert all("signals" not in agent for space in activity["workspaces"] for agent in space["agents"])
        # Rika's own tools hand the model no now state.
        from kitty.core.rika.outward import _list_agents
        from kitty.core.rika.tools import Context
        result = running.app.rika.agents.list()
        listed = _list_agents(Context(running.app, None), {}).data["agents"]
        assert all("now" in agent for agent in result) and all("now" not in agent for agent in listed)


def test_demo_catalogue_and_now_are_scripted(tmp_path, herdr):
    settings = dataclasses.replace(make_root(tmp_path, ("workspace", "terminal")), demo=True)
    with site(settings) as running:
        running.request(PATH + "/settings", {"kind": "omp", "enabled": True})
        omp = running.request(PATH)[1]["kinds"][0]
        assert omp["enabled"] and {item["name"] for item in omp["skills"]} >= {"lavish", "no-mistakes"}
        assert all(item["path"].startswith("/home/demo/") for item in omp["skills"])
        agents = {agent["id"]: agent["now"] for agent in running.request("/api/rika/agents")[1]["agents"]}
        assert agents["herdr:demo:w1:p2"]["label"] == "no-mistakes · bash"
        assert agents["tern:demo:6"] is None  # claude is still off
    assert not (settings.root / ".private/agents").exists()

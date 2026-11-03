"""Agent skills (docs/agent-skills-design.md): the per-agent session-log parsers on fixture
logs, names-only config parsing with secrets in the fixtures, the installed scan, the now
sources in order, the per-launch hook arguments, the activity hook itself, and the
per-kind opt-in gate (with it off no file under ~/.omp, ~/.claude or ~/.codex is opened).
Never a real agent, herdr or home folder."""
from __future__ import annotations

import builtins
import io
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitty.core import agent_skills as skills
from kitty.web_agent_skills import ASSETS, AgentSkills

FIXTURES = Path(__file__).parent / "fixtures" / "agent_skills"
SECRETS = ("sk-live-SECRET", "Bearer TOKEN-SECRET", "ghp_SECRET", "https://user:pass@secret.example", "--api-key=SECRET")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def skill(root: Path, name: str, front: str) -> Path:
    return write(root / name / "SKILL.md", f"---\n{front}\n---\n# {name}\nBody text.\n")


# Session-log parsers ---------------------------------------------------------------

def test_omp_log_gives_last_tool_its_intent_and_the_last_skill_read():
    found = skills.parse_log("omp", (FIXTURES / "omp.jsonl").read_text())
    assert found["tool"] == "bash" and found["skill"] == "lavish"
    assert found["intent"].startswith("Running the unit tests") and len(found["intent"]) == skills.INTENT_MAX
    assert "private reasoning" not in json.dumps(found) and "secret file body" not in json.dumps(found)


def test_claude_log_gives_last_tool_and_the_skill_tool_input():
    assert skills.parse_log("claude", (FIXTURES / "claude.jsonl").read_text()) == {"tool": "Bash", "skill": "no-mistakes"}


def test_codex_log_gives_last_tool_and_the_skill_file_an_exec_read():
    assert skills.parse_log("codex", (FIXTURES / "codex.jsonl").read_text()) == {"tool": "apply_patch", "skill": "no-mistakes"}


@pytest.mark.parametrize("kind", skills.KINDS)
def test_unrecognised_or_empty_logs_answer_nothing(kind):
    assert skills.parse_log(kind, "") == {}
    assert skills.parse_log(kind, '{"type":"future_format","x":1}\nnot json\n{"type":"message"}\n') == {}


def test_tail_reads_at_most_64_kb_from_a_whole_line(tmp_path):
    log = tmp_path / "big.jsonl"
    early = json.dumps({"type": "custom", "customType": "tool_execution_start", "data": {"toolName": "early"}})
    filler = json.dumps({"type": "model_usage", "pad": "x" * 200})
    log.write_text(early + "\n" + "\n".join([filler] * 600) + "\n")
    text = skills.tail(log)
    assert len(text.encode()) <= skills.TAIL_BYTES
    assert all(line.startswith("{") for line in text.splitlines())
    assert skills.parse_log("omp", text) == {}


# Installed -------------------------------------------------------------------------

def test_installed_skills_scan_each_kinds_roots_with_precedence(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    skill(home / ".omp/agent/skills", "lavish", "name: lavish\ndescription: >\n  Plans and reports\n  as HTML.")
    skill(home / ".omp/agent/skills", "grill-me", 'name: "grill-me"\ndescription: "Pressure-test a plan."')
    skill(home / ".agents/skills", "no-mistakes", "name: no-mistakes\ndescription: Ship safely.")
    skill(work / ".omp/skills", "lavish", "name: lavish\ndescription: The workspace copy wins.")
    skill(home / ".claude/skills", "loopy", "description: No name: the folder names it.")
    skill(home / ".claude/skills/synced/acct", "pdf", "name: pdf\ndescription: PDFs.")
    skill(home / ".claude/plugins/cache/mkt/review/1.0/skills", "code-review", "name: code-review\ndescription: Review.")
    skill(home / ".codex/skills/.system", "imagegen", "name: imagegen\ndescription: Images.")
    write(home / ".omp/agent/skills/broken/SKILL.md", "no frontmatter")
    omp = {item["name"]: item for item in skills.installed_skills("omp", work, home)}
    assert sorted(omp) == ["broken", "grill-me", "lavish", "no-mistakes"]
    assert omp["lavish"]["description"] == "The workspace copy wins."
    assert omp["lavish"]["path"] == str(work / ".omp/skills/lavish/SKILL.md")
    assert omp["grill-me"]["description"] == "Pressure-test a plan."
    assert omp["broken"]["description"] == ""
    claude = {item["name"] for item in skills.installed_skills("claude", work, home)}
    assert claude == {"loopy", "pdf", "code-review"}
    codex = {item["name"] for item in skills.installed_skills("codex", work, home)}
    assert codex == {"no-mistakes", "imagegen"}


def test_frontmatter_folds_block_scalars():
    assert skills.frontmatter("---\nname: x\ndescription: |\n  one\n  two\nother: y\n---\n") == {"name": "x", "description": "one two"}
    assert skills.frontmatter("# no frontmatter") == {}


def test_mcp_and_plugin_configs_keep_names_and_flags_only(tmp_path):
    home, work = tmp_path / "home", tmp_path / "work"
    server = {"command": "npx", "args": ["--api-key=SECRET", "server"], "env": {"API_KEY": "sk-live-SECRET"},
              "headers": {"Authorization": "Bearer TOKEN-SECRET"}, "url": "https://user:pass@secret.example"}
    write(home / ".omp/agent/mcp.json", json.dumps({"mcpServers": {"figma": server, "off": {**server, "enabled": False}},
                                                    "disabledServers": ["gone"], "token": "ghp_SECRET"}))
    write(work / ".mcp.json", json.dumps({"mcpServers": {"gone": server, "local": server}}))
    write(home / ".claude.json", json.dumps({"oauthAccount": {"emailAddress": "ghp_SECRET"}, "mcpServers": {"github": server},
                                             "projects": {str(work): {"mcpServers": {"project-db": server},
                                                                      "disabledMcpjsonServers": ["local"]}}}))
    write(home / ".claude/settings.json", json.dumps({"env": {"TOKEN": "ghp_SECRET"}, "enabledPlugins": {"review@mkt": False}}))
    write(home / ".claude/plugins/installed_plugins.json", json.dumps(
        {"version": 2, "plugins": {"review@mkt": [{"installPath": "/x", "secret": "ghp_SECRET"}], "docs@mkt": []}}))
    write(home / ".codex/config.toml", '[mcp_servers.linear]\ncommand = "npx"\nenv = { LINEAR_API_KEY = "sk-live-SECRET" }\n'
                                        '[mcp_servers.quiet]\nurl = "https://user:pass@secret.example"\nenabled = false\n'
                                        '[plugins."sites@openai-curated-remote"]\nenabled = false\n')
    (home / ".codex/plugins/cache/openai-curated-remote/sites/1.0").mkdir(parents=True)
    (home / ".codex/plugins/cache/openai-curated-remote/work-pets").mkdir(parents=True)
    found = {kind: skills.installed(kind, work, home) for kind in skills.KINDS}
    dumped = json.dumps(found)
    assert not any(secret in dumped for secret in SECRETS)
    assert found["omp"]["mcp"] == [{"name": "figma", "enabled": True}, {"name": "gone", "enabled": False},
                                   {"name": "local", "enabled": True}, {"name": "off", "enabled": False}]
    assert found["claude"]["mcp"] == [{"name": "github", "enabled": True}, {"name": "gone", "enabled": True},
                                      {"name": "local", "enabled": False}, {"name": "project-db", "enabled": True}]
    assert found["codex"]["mcp"] == [{"name": "linear", "enabled": True}, {"name": "quiet", "enabled": False}]
    assert found["claude"]["plugins"] == [{"name": "docs", "enabled": True}, {"name": "review", "enabled": False}]
    assert found["codex"]["plugins"] == [{"name": "sites", "enabled": False}, {"name": "work-pets", "enabled": True}]


def test_json_values_are_dropped_while_parsing(tmp_path):
    seen = []
    original = skills._names_only

    def spy(pairs):
        kept = original(pairs)
        seen.append(kept)
        return kept

    path = write(tmp_path / "mcp.json", json.dumps({"mcpServers": {"a": {"env": {"K": "sk-live-SECRET"}, "args": ["ghp_SECRET"]}}}))
    skills._names_only = spy
    try:
        data = skills._json_names(path)
    finally:
        skills._names_only = original
    assert data == {"mcpServers": {"a": {"env": {"K": None}, "args": None}}}
    assert not any(secret in repr(seen) for secret in SECRETS)


# Now -------------------------------------------------------------------------------

def test_now_takes_hook_tokens_then_the_extension_report_then_the_log(tmp_path):
    home = tmp_path / "home"
    reports = tmp_path / "reports"
    log = home / ".omp/agent/sessions/-work/2026_s1.jsonl"
    write(log, (FIXTURES / "omp.jsonl").read_text())
    agent = {"kind": "omp", "pane": "w1:p2", "session": {"kind": "path", "value": str(log)}}
    from_log = skills.now(agent, home=home, reports=reports)
    assert (from_log["skill"], from_log["tool"], from_log["via"], from_log["label"]) == ("lavish", "bash", "log", "lavish · bash")
    write(skills.report_path(reports, "w1:p2"), json.dumps({"tool": "read", "skill": "grill-me", "intent": "Reading the grill-me skill"}))
    assert skills.now(agent, home=home, reports=reports) | {"via": None} == {
        "skill": "grill-me", "tool": "read", "intent": "Reading the grill-me skill", "via": None, "label": "grill-me · read"}
    hooked = skills.now({**agent, "tokens": {"kitty-tool": "eval", "kitty-skill": "loopy", "other": "x"}}, home=home, reports=reports)
    assert (hooked["skill"], hooked["tool"], hooked["intent"], hooked["via"]) == ("loopy", "eval", "Reading the grill-me skill", "hook")


def test_now_is_a_dash_when_nothing_answers(tmp_path):
    found = skills.now({"kind": "claude", "pane": "w1:p1", "tokens": {}}, home=tmp_path, reports=tmp_path)
    assert found == {"skill": None, "tool": None, "intent": None, "via": None, "label": "—"}


def test_stale_extension_reports_are_ignored(tmp_path):
    report = write(skills.report_path(tmp_path, "w1:p1"), json.dumps({"tool": "read"}))
    old = time.time() - skills.REPORT_TTL - 5
    os.utime(report, (old, old))
    assert skills.now({"kind": "omp", "pane": "w1:p1"}, home=tmp_path / "h", reports=tmp_path)["label"] == "—"


def test_session_files_resolve_only_inside_each_agents_folder(tmp_path):
    home = tmp_path / "home"
    outside = write(tmp_path / "elsewhere.jsonl", "{}\n")
    assert skills.session_file("omp", {"kind": "path", "value": str(outside)}, None, home) is None
    assert skills.session_file("claude", {"kind": "path", "value": str(home / ".claude/projects/../../x.jsonl")}, None, home) is None
    claude = write(home / ".claude/projects/-work-app/abc.jsonl", "{}\n")
    assert skills.session_file("claude", {"kind": "path", "value": str(claude)}, None, home) == claude
    # Without herdr's agent_session: the newest log for the pane's working directory.
    assert skills.session_file("claude", None, "/work/app", home) == claude
    omp_home = write(home / ".omp/agent/sessions/-projects-kitty!/2026_a.jsonl", "{}\n")
    assert skills.session_file("omp", None, str(home / "projects/kitty!"), home) == omp_home
    omp_tmp = write(home / ".omp/agent/sessions/-tmp/2026_b.jsonl", "{}\n")
    assert skills.session_file("omp", None, "/tmp", home) == omp_tmp
    ident = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
    codex = write(home / f".codex/sessions/2026/11/01/rollout-2026-11-01T10-00-00-{ident}.jsonl",
                  (FIXTURES / "codex.jsonl").read_text())
    assert skills.session_file("codex", {"kind": "id", "value": ident}, None, home) == codex
    assert skills.session_file("codex", None, "/work", home) == codex
    assert skills.session_file("codex", {"kind": "id", "value": "../../etc"}, None, home) is None


# Launch arguments ------------------------------------------------------------------

def test_omp_launch_loads_the_extension_and_filters_skills(tmp_path):
    extension = tmp_path / "kitty-activity.ts"
    names = ["grill-me", "lavish", "loopy"]
    assert skills.launch_arguments("omp", skills=names, extension=extension) == ["--extension", str(extension)]
    assert skills.launch_arguments("omp", skills=names, off=["lavish"], extension=extension) == [
        "--extension", str(extension), "--skills", "grill-me,loopy"]
    assert skills.launch_arguments("omp", skills=names, off=names) == ["--no-skills"]


def test_claude_launch_passes_hooks_and_skill_overrides_in_one_settings_json(tmp_path):
    hook = tmp_path / "dir with space" / "kitty-activity-hook"
    arguments = skills.launch_arguments("claude", skills=["a", "b"], off=["b"], hook=hook)
    assert arguments[0] == "--settings" and len(arguments) == 2
    settings = json.loads(arguments[1])
    assert settings["skillOverrides"] == {"b": "off"}
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "*"
    command = entry["hooks"][0]["command"]
    assert shlex.split(command)[:3] == ["python3", str(hook), "claude"] and command.endswith("|| true")
    assert skills.launch_arguments("claude") == []


def test_codex_launch_overrides_parse_as_toml(tmp_path):
    hook = tmp_path / "kitty-activity-hook"
    arguments = skills.launch_arguments("codex", skills=["imagegen", "no-mistakes"], off=["imagegen"], hook=hook)
    assert arguments[0] == "-c" and arguments[2] == "-c"
    parsed = [tomllib.loads(value) for value in arguments[1::2]]
    assert parsed[0] == {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": skills.hook_command(hook, "codex"), "timeout": 5}]}]}}
    assert parsed[1] == {"skills": {"config": [{"name": "imagegen", "enabled": False}]}}


def test_launch_ignores_names_that_are_not_skill_names():
    assert skills.launch_arguments("claude", off=["ok", "$(rm -rf /)", "a,b"]) == ["--settings", '{"skillOverrides":{"ok":"off"}}']


# The activity hook ---------------------------------------------------------------------

@pytest.fixture
def hook(tmp_path):
    folder = tmp_path / "agents"
    folder.mkdir()
    script = folder / "kitty-activity-hook"
    script.write_text((ASSETS / "kitty-activity-hook").read_text())
    log = tmp_path / "herdr.log"
    herdr = write(tmp_path / "herdr", f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
    herdr.chmod(0o755)

    def run(kind, event, pane="w1:p3"):
        env = {"PATH": os.environ["PATH"], "HERDR_BIN_PATH": str(herdr)}
        if pane:
            env["HERDR_PANE_ID"] = pane
        subprocess.run([sys.executable, str(script), kind], input=json.dumps(event), text=True, env=env, check=True, timeout=10)
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return run


def test_claude_hook_reports_the_tool_and_skill_as_pane_tokens(hook):
    calls = hook("claude", {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": "no-mistakes"},
                            "session_id": "s", "transcript_path": "/x", "cwd": "/w"})
    assert calls == [["pane", "report-metadata", "w1:p3", "--source", "kitty:activity", "--token", "kitty-tool=Skill",
                      "--ttl-ms", "600000", "--token", "kitty-skill=no-mistakes"]]
    # The same tool and skill again within two seconds is not reported twice.
    assert len(hook("claude", {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": "no-mistakes"}})) == 1
    calls = hook("claude", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cat secret"}})
    assert calls[-1] == ["pane", "report-metadata", "w1:p3", "--source", "kitty:activity", "--token", "kitty-tool=Bash", "--ttl-ms", "600000"]


def test_codex_hook_names_the_skill_file_an_exec_reads(hook):
    calls = hook("codex", {"hook_event_name": "PreToolUse", "tool_name": "exec",
                           "tool_input": {"command": "cat ~/.codex/skills/.system/imagegen/SKILL.md"}})
    assert calls[-1][-2:] == ["--token", "kitty-skill=imagegen"]


def test_hook_is_silent_outside_herdr_and_on_other_events(hook):
    assert hook("claude", {"tool_name": "Bash"}, pane=None) == []
    assert hook("claude", {"hook_event_name": "PostToolUse", "tool_name": "Bash"}) == []


# The opt-in gate -----------------------------------------------------------------------

def _opened_under_agent_folders(home: Path, monkeypatch) -> list[str]:
    opened: list[str] = []
    roots = [str(home / name) for name in (".omp", ".claude", ".codex", ".claude.json", ".agents")]

    def track(original):
        def wrapper(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = os.fsdecode(file)
                if any(path == root or path.startswith(root + os.sep) for root in roots):
                    opened.append(path)
            return original(file, *args, **kwargs)
        return wrapper

    monkeypatch.setattr(builtins, "open", track(builtins.open))
    monkeypatch.setattr(io, "open", track(io.open))
    monkeypatch.setattr(os, "open", track(os.open))
    monkeypatch.setattr(os, "scandir", track(os.scandir))
    return opened


def _agent_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    skill(home / ".omp/agent/skills", "lavish", "name: lavish\ndescription: Reports.")
    skill(home / ".claude/skills", "loopy", "name: loopy\ndescription: Loops.")
    skill(home / ".codex/skills/.system", "imagegen", "name: imagegen\ndescription: Images.")
    write(home / ".claude.json", json.dumps({"mcpServers": {"github": {}}}))
    write(home / ".codex/config.toml", "[mcp_servers.linear]\ncommand = 'x'\n")
    write(home / ".omp/agent/sessions/-work/s.jsonl", (FIXTURES / "omp.jsonl").read_text())
    write(home / ".claude/projects/-work/s.jsonl", (FIXTURES / "claude.jsonl").read_text())
    return home


def test_with_the_option_off_no_agent_file_is_opened(tmp_path, monkeypatch):
    home = _agent_home(tmp_path)
    service = AgentSkills(SimpleNamespace(demo=False, root=tmp_path / "work"), home=home)
    opened = _opened_under_agent_folders(home, monkeypatch)
    catalog = service.catalog()
    assert [(kind["kind"], kind["enabled"], kind["skills"]) for kind in catalog["kinds"]] == [
        ("omp", False, []), ("claude", False, []), ("codex", False, [])]
    for kind in skills.KINDS:
        assert service.now({"id": "herdr:w1:p1", "kind": kind, "pane": "w1:p1", "cwd": "/work",
                            "session": {"kind": "path", "value": str(home / ".omp/agent/sessions/-work/s.jsonl")}}) is None
        assert service.launch(kind, []) == ([], None)
        with pytest.raises(ValueError, match="Turn on agent skills"):
            service.launch(kind, ["lavish"])
        with pytest.raises(LookupError):
            service.skill_path(kind, str(home / ".omp/agent/skills/lavish/SKILL.md"))
    assert opened == []
    # The control: once a kind is on, its files are read.
    service.set_enabled("omp", True)
    assert [item["name"] for item in service.catalog()["kinds"][0]["skills"]] == ["lavish"]
    assert service.now({"id": "herdr:w1:p1", "kind": "omp", "pane": "w1:p1", "cwd": "/work"})["label"] == "lavish · bash"
    assert any(path.startswith(str(home / ".omp")) for path in opened)
    assert not any(path.startswith(str(home / ".claude")) or path.startswith(str(home / ".codex")) for path in opened)


def test_the_opt_in_is_per_kind_off_by_default_and_kept_privately(tmp_path):
    root = tmp_path / "work"
    service = AgentSkills(SimpleNamespace(demo=False, root=root), home=tmp_path / "home")
    assert [service.enabled(kind) for kind in skills.KINDS] == [False, False, False]
    service.set_enabled("claude", True)
    saved = root / ".private/agents/settings.json"
    assert json.loads(saved.read_text()) == {"enabled": {"claude": True, "codex": False, "omp": False}}
    assert saved.stat().st_mode & 0o777 == 0o600 and (root / ".private/agents").stat().st_mode & 0o777 == 0o700
    again = AgentSkills(SimpleNamespace(demo=False, root=root), home=tmp_path / "home")
    assert [again.enabled(kind) for kind in skills.KINDS] == [False, True, False]


def test_launch_writes_the_hook_privately_and_checks_the_checklist(tmp_path):
    home = _agent_home(tmp_path)
    root = tmp_path / "work"
    service = AgentSkills(SimpleNamespace(demo=False, root=root), home=home)
    for kind in skills.KINDS:
        service.set_enabled(kind, True)
    arguments, notice = service.launch("omp", ["lavish"])
    assert arguments == ["--extension", str(root / ".private/agents/kitty-activity.ts"), "--no-skills"] and notice is None
    assert (root / ".private/agents/kitty-activity.ts").read_text() == (ASSETS / "kitty-activity.ts").read_text()
    arguments, notice = service.launch("codex", [])
    assert arguments[0] == "-c" and str(root / ".private/agents/kitty-activity-hook") in arguments[1]
    assert "/hooks" in notice
    assert (root / ".private/agents/kitty-activity-hook").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="Unknown Claude Code skill: nope"):
        service.launch("claude", ["nope"])

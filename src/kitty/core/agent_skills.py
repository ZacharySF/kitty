"""Agent skills (docs/agent-skills-design.md): what each coding agent can load, and what a
running agent is using now. Pure and read-only: nothing here writes a file, starts a
process or stores anything, and no agent's own configuration is ever changed.

``installed(kind, workspace)`` scans the folders that agent kind loads and returns three
groups, names only::

    {"skills": [{name, description, path}], "mcp": [{name, enabled}], "plugins": [{name, enabled}]}

Skills come from ``<root>/<name>/SKILL.md`` frontmatter (``name``, ``description``) under
the agent's known roots. MCP servers and plugins are names and enabled flags only: every
other value (commands, arguments, ``env``, ``headers``, URLs, tokens) is dropped while the
config is parsed. Credentials files are never in any list (``~/.claude/.credentials.json``,
``~/.codex/auth.json``, omp's ``agent.db``).

``now(agent)`` reads, in order, the herdr pane tokens kitty!'s launch hooks report
(``kitty-tool``, ``kitty-skill``), the omp extension's report (it adds omp's one-line
intent, longer than herdr's 80-character token values), then the last 64 KB of the
agent's session log, reduced to the last tool name, the last skill name and omp's intent
(at most 120 characters). Each field takes the first source that answers; with none the
label is "—".

``launch_arguments`` builds the per-launch flags for agents kitty! or Rika starts: the
activity hook or extension and the skill checklist (omp ``--skills``, Claude
``--settings`` ``skillOverrides``, Codex ``-c skills.config``).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
import tomllib
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

KINDS = ("omp", "claude", "codex")
LABELS = {"omp": "omp", "claude": "Claude Code", "codex": "Codex"}
#: The session-log tail now() reads.
TAIL_BYTES = 64 * 1024
#: omp's intent line, at most.
INTENT_MAX = 120
#: herdr caps a pane token value at 80 characters (herdr 0.9.5).
TOKEN_MAX = 80
DESCRIPTION_MAX = 300
#: Bytes of a SKILL.md read for its frontmatter.
FRONTMATTER_BYTES = 8192
#: Largest agent config parsed for MCP and plugin names.
CONFIG_MAX = 8 * 1024 * 1024
SKILLS_MAX = 500
#: The herdr metadata source and tokens the hooks and the omp extension report.
TOKEN_SOURCE = "kitty:activity"
TOKEN_TOOL = "kitty-tool"
TOKEN_SKILL = "kitty-skill"
#: How long an omp extension report counts as current.
REPORT_TTL = 600.0
#: The label when nothing answers.
UNKNOWN = "—"

_NAME = re.compile(r"[^\w.:@/+-]+")
_SKILL_NAME = re.compile(r"[A-Za-z0-9][\w.-]{0,79}")
_SKILL_FILE = re.compile(r"/skills/(?:\.system/)?([A-Za-z0-9][\w.-]{0,79})/SKILL\.md")
_CODEX_ID = re.compile(r"[0-9a-fA-F-]{8,64}")
#: Lists of names kept while parsing a config; every other list is dropped.
_NAME_LISTS = frozenset({"disabledServers", "disabledMcpjsonServers"})


def _home(home: Path | None) -> Path:
    return home if home is not None else Path(os.path.expanduser("~"))


def clean_name(value: Any, limit: int = TOKEN_MAX) -> str:
    """A tool, skill, server or plugin name as it may be shown: one short line."""
    if not isinstance(value, str):
        return ""
    return _NAME.sub("", value.strip())[:limit]


def clean_intent(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:INTENT_MAX]


# Installed ------------------------------------------------------------------------

def skill_patterns(kind: str, workspace: Path, home: Path | None = None) -> list[tuple[Path, str]]:
    """(root, glob) pairs in the agent's precedence order; the first skill of a name wins."""
    home = _home(home)
    if kind == "omp":
        # omp's providers (omp docs, skills.md): native user/project, its extension
        # packages, project roots of the Claude and Codex providers (their user roots are
        # opt-in in omp's own config), .agents, GitHub's project layout, managed skills.
        return [
            (workspace / ".omp" / "skills", "*/SKILL.md"),
            (home / ".omp" / "agent" / "skills", "*/SKILL.md"),
            (home / ".omp" / "plugins" / "node_modules", "*/skills/*/SKILL.md"),
            (home / ".omp" / "plugins" / "node_modules", "@*/*/skills/*/SKILL.md"),
            (workspace / ".claude" / "skills", "*/SKILL.md"),
            (workspace / ".agents" / "skills", "*/SKILL.md"),
            (workspace / ".agent" / "skills", "*/SKILL.md"),
            (home / ".agents" / "skills", "*/SKILL.md"),
            (workspace / ".codex" / "skills", "*/SKILL.md"),
            (workspace / ".github" / "skills", "*/SKILL.md"),
            (home / ".omp" / "agent" / "managed-skills", "*/SKILL.md"),
        ]
    if kind == "claude":
        return [
            (workspace / ".claude" / "skills", "*/SKILL.md"),
            (home / ".claude" / "skills", "*/SKILL.md"),
            (home / ".claude" / "skills" / "synced", "*/*/SKILL.md"),
            (home / ".claude" / "plugins" / "cache", "*/*/*/skills/*/SKILL.md"),
        ]
    if kind == "codex":
        return [
            (workspace / ".agents" / "skills", "*/SKILL.md"),
            (home / ".agents" / "skills", "*/SKILL.md"),
            (home / ".codex" / "skills", "*/SKILL.md"),
            (home / ".codex" / "skills" / ".system", "*/SKILL.md"),
            (home / ".codex" / "plugins" / "cache", "*/*/*/skills/*/SKILL.md"),
        ]
    raise ValueError("Unsupported coding agent")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def frontmatter(text: str) -> dict[str, str]:
    """``name`` and ``description`` from a SKILL.md's YAML frontmatter (scalars only)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, list[str]] = {}
    key = None
    for line in lines[1:]:
        if line.strip() in ("---", "..."):
            break
        match = re.match(r"([A-Za-z_][\w-]*):(?:\s+(.*))?$", line)
        if match and not line[:1].isspace():
            key = match[1]
            value = (match[2] or "").strip()
            fields[key] = [] if value in ("", ">", "|", ">-", "|-", ">+", "|+") else [_unquote(value)]
        elif key and line[:1].isspace() and line.strip():
            fields[key].append(line.strip())
    return {name: " ".join(fields[name]) for name in ("name", "description") if fields.get(name)}


def _read_head(path: Path, limit: int) -> str:
    with open(path, "rb") as handle:
        return handle.read(limit).decode("utf-8", "replace")


def installed_skills(kind: str, workspace: Path, home: Path | None = None) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for root, pattern in skill_patterns(kind, workspace, home):
        if not root.is_dir():
            continue
        for path in sorted(root.glob(pattern)):
            if len(found) >= SKILLS_MAX:
                break
            try:
                if not path.is_file():
                    continue
                fields = frontmatter(_read_head(path, FRONTMATTER_BYTES))
            except OSError:
                continue
            name = clean_name(fields.get("name") or path.parent.name)
            if name and name not in found:
                found[name] = {"name": name, "description": " ".join(fields.get("description", "").split())[:DESCRIPTION_MAX],
                               "path": str(path)}
    return sorted(found.values(), key=lambda item: item["name"].lower())


def _names_only(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json object hook: keeps keys, nested objects and flags; every other value is dropped."""
    kept: dict[str, Any] = {}
    for key, value in pairs:
        if isinstance(value, (dict, bool)):
            kept[key] = value
        elif key in _NAME_LISTS and isinstance(value, list):
            kept[key] = [item for item in value if isinstance(item, str)]
        else:
            kept[key] = None
    return kept


def _json_names(path: Path) -> dict[str, Any]:
    """A JSON config reduced to names and flags while it is parsed; {} when unreadable."""
    try:
        if not path.is_file() or path.stat().st_size > CONFIG_MAX:
            return {}
        with open(path, "rb") as handle:
            data = json.loads(handle.read(), object_pairs_hook=_names_only)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _toml_names(path: Path) -> dict[str, Any]:
    """A TOML config reduced to tables and flags as soon as it is parsed; {} when unreadable."""
    try:
        if not path.is_file() or path.stat().st_size > CONFIG_MAX:
            return {}
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}

    def reduce(value):
        if isinstance(value, dict):
            return {key: reduce(item) for key, item in value.items()}
        return value if isinstance(value, bool) else None

    return reduce(data)


def _servers(table: Any, disabled: Collection[str] = ()) -> list[dict[str, Any]]:
    if not isinstance(table, dict):
        return []
    found = []
    for name, config in table.items():
        label = clean_name(name)
        if not label:
            continue
        config = config if isinstance(config, dict) else {}
        enabled = config.get("enabled") is not False and config.get("disabled") is not True and name not in disabled
        found.append({"name": label, "enabled": enabled})
    return found


def installed_mcp(kind: str, workspace: Path, home: Path | None = None) -> list[dict[str, Any]]:
    """MCP server names and enabled flags; the first definition of a name wins."""
    home = _home(home)
    if kind == "omp":
        configs = [_json_names(path) for path in (
            workspace / ".omp" / "mcp.json", workspace / ".omp" / ".mcp.json", home / ".omp" / "agent" / "mcp.json",
            home / ".omp" / "agent" / ".mcp.json", workspace / "mcp.json", workspace / ".mcp.json")]
        # The user-level disabledServers list switches a server off whichever file defines it.
        disabled = {name for config in configs for name in config.get("disabledServers") or ()}
        groups = [_servers(config.get("mcpServers"), disabled) for config in configs]
    elif kind == "claude":
        user = _json_names(home / ".claude.json")
        projects = user.get("projects") if isinstance(user.get("projects"), dict) else {}
        project = projects.get(str(workspace)) if isinstance(projects.get(str(workspace)), dict) else {}
        local = _json_names(workspace / ".mcp.json")
        groups = [_servers(project.get("mcpServers")), _servers(local.get("mcpServers"), project.get("disabledMcpjsonServers") or ()),
                  _servers(user.get("mcpServers"))]
    elif kind == "codex":
        groups = [_servers(_toml_names(path).get("mcp_servers"))
                  for path in (workspace / ".codex" / "config.toml", home / ".codex" / "config.toml")]
    else:
        raise ValueError("Unsupported coding agent")
    return _first_of_each(groups)


def _first_of_each(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            seen.setdefault(item["name"], item)
    return sorted(seen.values(), key=lambda item: item["name"].lower())


def _folders(path: Path) -> list[str]:
    try:
        return sorted(entry.name for entry in os.scandir(path) if entry.is_dir() and not entry.name.startswith("."))
    except OSError:
        return []


def installed_plugins(kind: str, workspace: Path, home: Path | None = None) -> list[dict[str, Any]]:
    """Plugin names and enabled flags."""
    home = _home(home)
    found: list[dict[str, Any]] = []
    if kind == "omp":
        modules = home / ".omp" / "plugins" / "node_modules"
        for name in _folders(modules):
            names = [f"{name}/{child}" for child in _folders(modules / name)] if name.startswith("@") else [name]
            found += [{"name": clean_name(item), "enabled": True} for item in names]
    elif kind == "claude":
        flags: dict[str, Any] = {}
        for path in (home / ".claude" / "settings.json", workspace / ".claude" / "settings.json"):
            enabled = _json_names(path).get("enabledPlugins")
            if isinstance(enabled, dict):
                flags.update(enabled)
        listed = _json_names(home / ".claude" / "plugins" / "installed_plugins.json").get("plugins")
        keys = list(listed) if isinstance(listed, dict) else []
        keys += [key for key in flags if key not in keys]
        found = [{"name": clean_name(key.split("@")[0]), "enabled": flags.get(key, True) is not False} for key in keys]
    elif kind == "codex":
        plugins = _toml_names(home / ".codex" / "config.toml").get("plugins") or {}
        cache = home / ".codex" / "plugins" / "cache"
        for marketplace in _folders(cache):
            for name in _folders(cache / marketplace):
                flag = plugins.get(f"{name}@{marketplace}") if isinstance(plugins, dict) else None
                enabled = not (isinstance(flag, dict) and flag.get("enabled") is False)
                found.append({"name": clean_name(name), "enabled": enabled})
    else:
        raise ValueError("Unsupported coding agent")
    return _first_of_each([[item for item in found if item["name"]]])


def installed(kind: str, workspace: Path, home: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Skills, Tools (MCP) and Plugins one agent kind loads in `workspace`, names only."""
    if kind not in KINDS:
        raise ValueError("Unsupported coding agent")
    return {"skills": installed_skills(kind, workspace, home), "mcp": installed_mcp(kind, workspace, home),
            "plugins": installed_plugins(kind, workspace, home)}


# Now ------------------------------------------------------------------------------

def tail(path: Path, limit: int = TAIL_BYTES) -> str:
    """The last `limit` bytes of a log, starting at a whole line."""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        data = handle.read(limit)
    if size > limit:
        data = data.partition(b"\n")[2]
    return data.decode("utf-8", "replace")


def _skill_uri(value: Any) -> str:
    if isinstance(value, str) and value.startswith("skill://"):
        return clean_name(value[len("skill://"):].split("/")[0].split(":")[0])
    return ""


def _omp_line(entry: dict) -> tuple[str, str, str] | None:
    if entry.get("type") == "custom" and entry.get("customType") == "tool_execution_start":
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        return clean_name(data.get("toolName")), _skill_uri(args.get("path")), clean_intent(data.get("intent"))
    message = entry.get("message") if entry.get("type") == "message" else None
    if isinstance(message, dict) and message.get("role") == "assistant" and isinstance(message.get("content"), list):
        calls = [item for item in message["content"] if isinstance(item, dict) and item.get("type") == "toolCall"]
        if calls:
            call = calls[-1]
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            return clean_name(call.get("name")), _skill_uri(args.get("path")), clean_intent(call.get("intent"))
    return None


def _claude_line(entry: dict) -> tuple[str, str, str] | None:
    message = entry.get("message") if entry.get("type") == "assistant" else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return None
    calls = [item for item in message["content"] if isinstance(item, dict) and item.get("type") == "tool_use"]
    if not calls:
        return None
    call = calls[-1]
    tool = clean_name(call.get("name"))
    data = call.get("input") if isinstance(call.get("input"), dict) else {}
    return tool, clean_name(data.get("skill")) if tool == "Skill" else "", ""


def _codex_line(entry: dict) -> tuple[str, str, str] | None:
    payload = entry.get("payload") if entry.get("type") == "response_item" else None
    if not isinstance(payload, dict) or payload.get("type") not in ("function_call", "custom_tool_call", "local_shell_call"):
        return None
    tool = clean_name(payload.get("name")) or ("shell" if payload.get("type") == "local_shell_call" else "")
    text = payload.get("arguments") or payload.get("input") or payload.get("action") or ""
    match = _SKILL_FILE.search(text if isinstance(text, str) else json.dumps(text))
    return tool, clean_name(match[1]) if match else "", ""


_PARSERS = {"omp": _omp_line, "claude": _claude_line, "codex": _codex_line}


def parse_log(kind: str, text: str) -> dict[str, str]:
    """The last tool, the last skill and (omp) the intent of the last tool, from a log tail."""
    parser = _PARSERS[kind]
    found = {"tool": "", "skill": "", "intent": ""}
    for line in reversed(text.splitlines()):
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        values = parser(entry) if isinstance(entry, dict) else None
        if not values:
            continue
        tool, skill, intent = values
        if tool and not found["tool"]:
            found["tool"], found["intent"] = tool, intent
        if skill and not found["skill"]:
            found["skill"] = skill
        if found["tool"] and found["skill"]:
            break
    return {key: value for key, value in found.items() if value}


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _session_roots(kind: str, home: Path) -> Path:
    return {"omp": home / ".omp", "claude": home / ".claude" / "projects", "codex": home / ".codex" / "sessions"}[kind]


def _newest(paths) -> Path | None:
    best, stamp = None, -1.0
    for path in paths:
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if modified > stamp:
            best, stamp = path, modified
    return best


def _codex_days(home: Path, count: int = 14) -> list[Path]:
    root = home / ".codex" / "sessions"
    days = [root / year / month / day for year in _folders(root)[::-1] for month in _folders(root / year)[::-1]
            for day in _folders(root / year / month)[::-1]]
    return days[:count]


def session_file(kind: str, session: Any, cwd: Any, home: Path | None = None) -> Path | None:
    """The agent's session log: herdr's ``agent_session`` (a path for omp and Claude, an id
    for Codex), else the newest log for the pane's working directory. Only files under the
    agent's own session folder are ever named."""
    home = _home(home)
    root = _session_roots(kind, home)
    if isinstance(session, str):
        session = {"kind": "id" if _CODEX_ID.fullmatch(session) else "path", "value": session}
    value = session.get("value") if isinstance(session, dict) else None
    if isinstance(value, str) and value:
        if session.get("kind") == "path":
            path = Path(value)
            return path if path.is_absolute() and path.suffix == ".jsonl" and _within(path, root) and path.is_file() else None
        if kind == "codex" and _CODEX_ID.fullmatch(value):
            for day in _codex_days(home):
                match = _newest(day.glob(f"rollout-*{value}.jsonl"))
                if match:
                    return match
            return None
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        return None
    if kind == "omp":
        relative = os.path.relpath(cwd, home) if _within(Path(cwd), home) else cwd
        folder = "-" + ("" if relative == "." else relative.strip("/").replace("/", "-"))
        return _newest((home / ".omp" / "agent" / "sessions" / folder).glob("*.jsonl"))
    if kind == "claude":
        return _newest((home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)).glob("*.jsonl"))
    for day in _codex_days(home, 3):
        for path in sorted(day.glob("rollout-*.jsonl"), reverse=True)[:20]:
            try:
                first = json.loads(_read_head(path, 16384).partition("\n")[0])
            except (OSError, ValueError):
                continue
            if isinstance(first, dict) and isinstance(first.get("payload"), dict) and first["payload"].get("cwd") == cwd:
                return path
    return None


def _from_tokens(tokens: Any) -> dict[str, str]:
    if not isinstance(tokens, dict):
        return {}
    found = {"tool": clean_name(tokens.get(TOKEN_TOOL)), "skill": clean_name(tokens.get(TOKEN_SKILL))}
    return {key: value for key, value in found.items() if value}


def report_path(reports: Path, pane: str) -> Path:
    """Where the omp extension reports for one herdr pane."""
    return reports / (re.sub(r"[^\w.-]", "_", pane) + ".json")


def _from_report(reports: Path | None, pane: Any) -> dict[str, str]:
    if reports is None or not isinstance(pane, str) or not pane:
        return {}
    path = report_path(reports, pane)
    try:
        if time.time() - path.stat().st_mtime > REPORT_TTL:
            return {}
        data = json.loads(_read_head(path, 4096))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    found = {"tool": clean_name(data.get("tool")), "skill": clean_name(data.get("skill")), "intent": clean_intent(data.get("intent"))}
    return {key: value for key, value in found.items() if value}


def _from_log(agent: Mapping[str, Any], home: Path) -> dict[str, str]:
    kind = agent.get("kind")
    if kind not in KINDS:
        return {}
    path = session_file(kind, agent.get("session"), agent.get("cwd"), home)
    if path is None:
        return {}
    try:
        return parse_log(kind, tail(path))
    except OSError:
        return {}


def label(skill: str | None, tool: str | None) -> str:
    """The now chip's text without its "now:" prefix: "lavish · read", or "—"."""
    return " · ".join(part for part in (skill, tool) if part) or UNKNOWN


def now(agent: Mapping[str, Any], *, home: Path | None = None, reports: Path | None = None) -> dict[str, Any]:
    """What one running agent is using now.

    `agent` has ``kind`` and, as available, herdr's ``tokens``, its ``session``
    (``{kind: path|id, value}``), the pane's ``cwd`` and herdr ``pane`` id. Returns
    ``{skill, tool, intent, via, label}``; unknown fields are None and ``label`` is "—".
    """
    home = _home(home)
    found: dict[str, Any] = {"skill": None, "tool": None, "intent": None, "via": None}
    for via, read in (("hook", lambda: _from_tokens(agent.get("tokens"))),
                      ("extension", lambda: _from_report(reports, agent.get("pane"))),
                      ("log", lambda: _from_log(agent, home))):
        values = read()
        for key, value in values.items():
            if found[key] is None:
                found[key] = value
        if values and found["via"] is None:
            found["via"] = via
        if found["tool"] and found["skill"] and (found["intent"] or agent.get("kind") != "omp"):
            break
    found["label"] = label(found["skill"], found["tool"])
    return found


# Launch ---------------------------------------------------------------------------

def hook_command(script: Path, kind: str, python: str = "python3") -> str:
    """The shell command an agent runs per tool call; a missing Python stays silent."""
    return f"{python} {shlex.quote(str(script))} {kind} 2>/dev/null || true"


def _toml(value: Any) -> str:
    """A TOML inline value for Codex's ``-c key=value`` (strings use TOML basic-string escapes)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_toml(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(f"{key}={_toml(item)}" for key, item in value.items()) + "}"
    raise TypeError(type(value).__name__)


def launch_arguments(kind: str, *, skills: Sequence[str] = (), off: Collection[str] = (),
                     hook: Path | None = None, extension: Path | None = None) -> list[str]:
    """The agent's own flags for one launch: the activity hook (Claude, Codex) or extension
    (omp), and the skills switched off for it. The user's config files are never edited."""
    off = [name for name in dict.fromkeys(off) if _SKILL_NAME.fullmatch(name)]
    if kind == "omp":
        arguments = ["--extension", str(extension)] if extension else []
        if off:
            on = [name for name in skills if name not in off and _SKILL_NAME.fullmatch(name)]
            arguments += ["--skills", ",".join(on)] if on else ["--no-skills"]
        return arguments
    if kind == "claude":
        settings: dict[str, Any] = {}
        if hook:
            settings["hooks"] = {"PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": hook_command(hook, "claude"), "timeout": 5}]}]}
        if off:
            settings["skillOverrides"] = {name: "off" for name in off}
        return ["--settings", json.dumps(settings, separators=(",", ":"))] if settings else []
    if kind == "codex":
        arguments = []
        if hook:
            arguments += ["-c", "hooks.PreToolUse=" + _toml([{"hooks": [
                {"type": "command", "command": hook_command(hook, "codex"), "timeout": 5}]}])]
        if off:
            arguments += ["-c", "skills.config=" + _toml([{"name": name, "enabled": False} for name in off])]
        return arguments
    raise ValueError("Unsupported coding agent")

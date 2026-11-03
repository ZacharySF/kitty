"""Agent skills (docs/agent-skills-design.md), part of the Terminal module.

GET  /api/terminal/agent-skills  ->  {kinds: [{kind, label, enabled, skills, mcp, plugins}], can_open}
    Every agent kind's skills, MCP tools and plugins (kitty.core.agent_skills.installed),
    names only. A kind that is off answers empty groups and nothing is read for it.
POST /api/terminal/agent-skills/settings {kind, enabled}  ->  the same catalogue
    "Show agent skills and activity", per agent kind, off by default. The choice lives in
    the workspace's private ``.private/agents/settings.json``, never in the database.
POST /api/terminal/agent-skills/open {kind, path}  ->  {ok}
    Opens one listed SKILL.md with the desktop opener (the desktop shell's ``open-skill``
    command); a plain ``kitty-web`` site has no opener and answers 409.

The data is held in memory only and served only behind the page guard: no ingress route,
no record events (``terminal`` is a non-content prefix), no database rows, no peer sync.
While a kind is off, kitty! opens no file under ``~/.omp``, ``~/.claude``, ``~/.codex``
(or ``~/.claude.json``, ``~/.agents``) for it.

Per-launch hooks: ``launch(kind, off)`` copies kitty!'s activity hook and omp extension
(kitty/core/agent_hooks) into ``.private/agents/`` and returns the agent's flags for
that launch (kitty.core.agent_skills.launch_arguments).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from kitty.core import agent_skills
from kitty.core.agent_skills import KINDS, LABELS

PREFIX = "/api/terminal/agent-skills"
CACHE_SECONDS = 5.0
ASSETS = Path(__file__).parent / "core" / "agent_hooks"
HOOK = "kitty-activity-hook"
EXTENSION = "kitty-activity.ts"
CODEX_TRUST = ("Codex runs kitty!'s activity hook only after you trust it in /hooks. "
               "Until then rika! reads what Codex uses from its session log.")

#: The demo's synthetic catalogue: no file on the machine is read in the demo.
_DEMO_SKILLS = {
    "omp": [("deep-teaching-documents", "Explanatory documents that teach a mechanism and its rationale."),
            ("grill-me", "Adversarial plan interviews that pressure-test a design before you build it."),
            ("lavish", "Plans, comparisons, diagrams and reports as annotatable HTML artifacts."),
            ("no-mistakes", "Validate, gate, review, test and push code changes safely."),
            ("study-assessment-authoring", "Study guides, quizzes, exams, answer keys and rubrics.")],
    "claude": [("docx", "Create and edit Word documents."), ("grill-me", "Adversarial plan interviews."),
               ("pdf", "Read, fill and assemble PDF files."), ("skill-creator", "Write a new agent skill."),
               ("xlsx", "Spreadsheets with formulas and charts.")],
    "codex": [("imagegen", "Generate and edit images."), ("no-mistakes", "Validate and ship code changes safely."),
              ("openai-docs", "Answer from the OpenAI developer docs."), ("skill-creator", "Write a new agent skill.")],
}
_DEMO_ROOTS = {"omp": "/home/demo/.omp/agent/skills", "claude": "/home/demo/.claude/skills", "codex": "/home/demo/.codex/skills"}
_DEMO_EXTRA = {
    "omp": {"mcp": [], "plugins": []},
    "claude": {"mcp": [{"name": "claude-in-chrome", "enabled": True}], "plugins": [{"name": "code-review", "enabled": False}]},
    "codex": {"mcp": [], "plugins": [{"name": "sites", "enabled": True}, {"name": "work-pets", "enabled": True}]},
}
#: The demo roster's now (kitty.core.rika.agents): id -> (skill, tool, intent).
_DEMO_NOW = {
    "herdr:demo:w1:p2": ("no-mistakes", "bash", "Running the parser tests"),
    "herdr:demo:w1:p3": ("grill-me", "Bash", None),
    "herdr:demo:w1:p4": ("lavish", "write", "Writing the exam-dates summary"),
    "tern:demo:6": ("pdf", "Read", None),
}


def _demo_installed(kind: str) -> dict[str, list[dict[str, Any]]]:
    return {"skills": [{"name": name, "description": text, "path": f"{_DEMO_ROOTS[kind]}/{name}/SKILL.md"}
                       for name, text in _DEMO_SKILLS[kind]], **_DEMO_EXTRA[kind]}


class AgentSkills:
    """One per web application: the per-kind opt-in, a short-lived catalogue cache, the
    now state of running agents and the per-launch flags."""

    def __init__(self, settings, *, home: Path | None = None):
        self.demo = bool(settings.demo)
        self.workspace: Path = settings.root
        self.folder: Path = settings.root / ".private" / "agents"
        self._home = home
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._enabled = dict.fromkeys(KINDS, False)
        if not self.demo:
            self._enabled.update(self._load())

    @property
    def home(self) -> Path:
        return self._home if self._home is not None else Path(os.path.expanduser("~"))

    @property
    def reports(self) -> Path:
        return self.folder / "activity"

    # Opt-in -----------------------------------------------------------------------

    def _load(self) -> dict[str, bool]:
        try:
            with open(self.folder / "settings.json", encoding="utf-8") as handle:
                saved = json.load(handle).get("enabled", {})
        except (OSError, ValueError, AttributeError):
            return {}
        return {kind: saved.get(kind) is True for kind in KINDS} if isinstance(saved, dict) else {}

    def enabled(self, kind: str) -> bool:
        with self._lock:
            return self._enabled.get(kind, False)

    def set_enabled(self, kind: str, enabled: bool) -> None:
        from kitty.core.terminal import _write_private

        if kind not in KINDS:
            raise ValueError("Choose omp, claude or codex")
        with self._lock:
            self._enabled[kind] = enabled
            self._cache.pop(kind, None)
            if self.demo:
                return
            self._private_folder()
            _write_private(self.folder / "settings.json", json.dumps({"enabled": self._enabled}, sort_keys=True) + "\n")

    def _private_folder(self) -> None:
        self.folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.folder.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.folder, 0o700)

    # Catalogue --------------------------------------------------------------------

    def installed(self, kind: str) -> dict[str, list[dict[str, Any]]] | None:
        """The kind's groups, or None while it is off (then nothing is read)."""
        if not self.enabled(kind):
            return None
        if self.demo:
            return _demo_installed(kind)
        with self._lock:
            cached = self._cache.get(kind)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        found = agent_skills.installed(kind, self.workspace, self.home)
        with self._lock:
            self._cache[kind] = (time.monotonic(), found)
        return found

    def catalog(self) -> dict[str, Any]:
        kinds = []
        for kind in KINDS:
            groups = self.installed(kind)
            kinds.append({"kind": kind, "label": LABELS[kind], "enabled": groups is not None,
                          **(groups or {"skills": [], "mcp": [], "plugins": []})})
        return {"kinds": kinds}

    def skill_path(self, kind: str, path: str) -> Path:
        """`path` when it is one of the kind's listed skills; LookupError otherwise."""
        groups = self.installed(kind) if kind in KINDS else None
        if groups is None or not any(skill["path"] == path for skill in groups["skills"]):
            raise LookupError("That skill is not listed for this agent")
        return Path(path)

    # Now --------------------------------------------------------------------------

    def now(self, agent: dict[str, Any]) -> dict[str, Any] | None:
        """What a running agent uses now, or None while its kind is off."""
        kind = agent.get("kind")
        if kind not in KINDS or not self.enabled(kind):
            return None
        if self.demo:
            skill, tool, intent = _DEMO_NOW.get(str(agent.get("id")), (None, None, None))
            return {"skill": skill, "tool": tool, "intent": intent, "via": "hook" if tool else None,
                    "label": agent_skills.label(skill, tool)}
        return agent_skills.now(agent, home=self.home, reports=self.reports)

    # Launch -----------------------------------------------------------------------

    def launch(self, kind: str, off: list[str] | tuple[str, ...] = ()) -> tuple[list[str], str | None]:
        """The agent's flags for one launch kitty! or Rika starts, and a notice to show once.

        With the kind off, no hook is added and no skill can be switched off."""
        from kitty.core.terminal import _write_private

        if kind not in KINDS:
            raise ValueError("Choose omp, claude or codex")
        if not self.enabled(kind):
            if off:
                raise ValueError(f"Turn on agent skills for {LABELS[kind]} before choosing its skills")
            return [], None
        names = [skill["name"] for skill in (self.installed(kind) or {"skills": []})["skills"]]
        unknown = sorted(set(off) - set(names))
        if unknown:
            raise ValueError(f"Unknown {LABELS[kind]} skill: {', '.join(unknown)}")
        if self.demo:
            return agent_skills.launch_arguments(kind, skills=names, off=off), None
        with self._lock:
            self._private_folder()
            asset = EXTENSION if kind == "omp" else HOOK
            target = self.folder / asset
            _write_private(target, (ASSETS / asset).read_text(encoding="utf-8"))
        hooks = {"extension": target} if kind == "omp" else {"hook": target}
        return (agent_skills.launch_arguments(kind, skills=names, off=off, **hooks),
                CODEX_TRUST if kind == "codex" else None)


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    app.agent_skills = AgentSkills(app.settings)


def _catalog(app) -> dict[str, Any]:
    return {**app.agent_skills.catalog(), "can_open": app.desktop is not None and not app.settings.demo}


def get(app, conn, path, query):
    if path != PREFIX:
        return None
    app._no_query(query)
    return _catalog(app)


def post(app, conn, path, body):
    if path == PREFIX + "/settings":
        if set(body) != {"kind", "enabled"} or body["kind"] not in KINDS or type(body["enabled"]) is not bool:
            raise _error(400, "Expected {kind: omp|claude|codex, enabled: true|false}")
        app.agent_skills.set_enabled(body["kind"], body["enabled"])
        return _catalog(app)
    if path == PREFIX + "/open":
        if set(body) != {"kind", "path"} or not all(isinstance(body[key], str) for key in body):
            raise _error(400, "Expected {kind, path} of a listed skill")
        try:
            target = app.agent_skills.skill_path(body["kind"], body["path"])
        except LookupError as exc:
            raise _error(404, str(exc)) from None
        if app.desktop is None or app.settings.demo:
            raise _error(409, "Open file needs the rika! desktop app; use Copy path instead")
        app.desktop.open_file(str(target))
        return {"ok": True}
    return None

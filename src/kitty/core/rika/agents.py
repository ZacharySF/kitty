"""Worker agents Rika conducts (design Q11 and Q19): omp, Claude Code and Codex in
rika!'s herdr session, surfaced through Tern windows, plus coding agents a Tern pane runs.

An agent is ``{id, kind, name, status: working|blocked|idle|done, task, line, since,
where: {herdr_pane} | {tern_pane}, now}``; ``id`` is ``herdr:<pane>`` or ``tern:<block>``
and ``since`` is epoch ms its status began. ``now`` is what it is using now
(kitty.web_agent_skills: ``{skill, tool, intent, via, label}``), or None while
"Show agent skills and activity" is off for its kind.

Starting one (after the user approved it) runs herdr headless when no page has
attached it (the herdr dock is gone), starts the agent through
``HerdrRuntime.start_agent`` and hands it the task brief with ``herdr agent prompt``.
Opening one focuses its pane and shows it in a Tern window: a herdr agent through a
Tern tab attached to rika!'s herdr session. Without Tern the page opens the herdr view.

The demo has a scripted roster instead: no process ever starts there.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from kitty.core.terminal import CODING_AGENTS

STATUSES = ("working", "blocked", "idle", "done")
BRIEF_MAX = 8000
_AGENT_WORD = re.compile(r"\b(omp|claude|codex)\b", re.I)


class AgentError(RuntimeError):
    """An agent could not be started or opened; the message is safe to show."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def brief(task: str, cwd: Path) -> str:
    """The task brief Rika hands a worker agent."""
    return (f"Task from Rika, the rika! agent: {task.strip()}\n\n"
            f"Work in {cwd}. Ask if a decision has real stakes. When you finish, "
            "summarise what you changed and anything left undone.")[:BRIEF_MAX]


def _demo_roster() -> list[dict[str, Any]]:
    start = _now_ms()
    rows = [
        ("omp", "omp · parser", "working", "Refactor the PDF parser", "Running tests 14/32", {"herdr_pane": "demo:w1:p2"}, 4),
        ("claude", "claude · essay", "blocked", "Outline the history essay", "Keep the three-part structure? (y/n)",
         {"herdr_pane": "demo:w1:p3"}, 11),
        ("codex", "codex · flashcards", "idle", "Import the biology deck", "Waiting for input", {"tern_pane": "demo:5"}, 26),
        ("omp", "omp · calendar", "done", "Sync the exam dates", "Added 4 events; all tests pass", {"herdr_pane": "demo:w1:p4"}, 48),
        ("claude", "claude · notes", "working", "Summarise lecture 7", "Reading notes/lecture-7.md", {"tern_pane": "demo:6"}, 2),
    ]
    return [{"id": f"{'herdr' if 'herdr_pane' in where else 'tern'}:{next(iter(where.values()))}", "kind": kind,
             "name": name, "status": status, "task": task, "line": line, "since": start - minutes * 60_000,
             "where": where} for kind, name, status, task, line, where, minutes in rows]


def _tern_kind(block: dict) -> str | None:
    command = (block.get("command") or "").split()
    program = os.path.basename(command[0]) if command else os.path.basename(str(block.get("program") or ""))
    if program in CODING_AGENTS:
        return program
    found = _AGENT_WORD.search(str(block.get("title") or ""))
    return found[1].lower() if found else None


class Agents:
    """The live roster for one web application; the demo keeps a scripted one."""

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._demo = _demo_roster() if app.settings.demo else []
        #: herdr pane -> the brief Rika handed it, shown while herdr reports no title.
        self._briefs: dict[str, str] = {}
        #: tern block -> (status, since) for the cards' running timers.
        self._tern_states: dict[int, tuple[str, int]] = {}

    @property
    def _herdr(self):
        terminal = getattr(self.app, "terminal", None)
        return terminal.runtime if terminal is not None else None

    @property
    def _tern(self):
        return getattr(self.app, "tern", None)

    @property
    def _skills(self):
        return getattr(self.app, "agent_skills", None)

    # Listing ------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        if self.app.settings.demo:
            with self._lock:
                agents = [dict(agent) for agent in self._demo]
            return [{**agent, "now": self._now(agent, {})} for agent in agents]
        return [*self._herdr_agents(), *self._tern_agents()]

    def _now(self, agent: dict[str, Any], signals: dict[str, Any]) -> dict[str, Any] | None:
        skills = self._skills
        return skills.now({"id": agent["id"], "kind": agent["kind"], **signals}) if skills is not None else None

    def _herdr_agents(self) -> list[dict[str, Any]]:
        from kitty.core.terminal import TerminalUnavailable

        if self._herdr is None:
            return []
        try:
            activity = self._herdr.activity(signals=True)
        except TerminalUnavailable:
            return []
        found = []
        for space in activity["workspaces"]:
            for card in space["agents"]:
                agent = {"id": f"herdr:{card['pane']}", "kind": card.get("kind") or card["name"].split("-")[0],
                         "name": card["name"], "status": card["status"] if card["status"] in STATUSES else "idle",
                         "task": card["task"] or self._briefs.get(card["pane"], "").split("\n")[0][:200],
                         "line": card["line"], "since": card["since"], "where": {"herdr_pane": card["pane"]}}
                found.append({**agent, "now": self._now(agent, card.get("signals") or {})})
        return found

    def _tern_agents(self) -> list[dict[str, Any]]:
        from kitty.core.tern import TernUnavailable

        if self._tern is None:
            return []
        try:
            status = self._tern.status()
        except TernUnavailable:
            return []
        now, seen, found = _now_ms(), {}, []
        for session in status["sessions"]:
            for tab in session["tabs"]:
                for block in tab["blocks"]:
                    kind = _tern_kind(block)
                    if kind is None:
                        continue
                    state = "working" if block["working"] else "done" if block["exited"] is not None else "idle"
                    previous = self._tern_states.get(block["id"])
                    since = previous[1] if previous and previous[0] == state else now
                    seen[block["id"]] = (state, since)
                    agent = {"id": f"tern:{block['id']}", "kind": kind, "name": block["title"] or kind,
                             "status": state, "task": block["title"], "line": "", "since": since,
                             "where": {"tern_pane": block["id"]}}
                    found.append({**agent, "now": self._now(agent, {"cwd": block.get("cwd")})})
        self._tern_states = seen
        return found

    # Starting -----------------------------------------------------------------

    def start(self, kind: str, task: str, cwd: Path, skills_off: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
        """Start an approved worker agent and hand it the task brief. ``skills_off`` are
        the agent skills switched off for this launch only."""
        from kitty.core.terminal import TerminalUnavailable

        if kind not in CODING_AGENTS:
            raise AgentError("Choose omp, claude or codex")
        try:
            extra = self._skills.launch(kind, list(skills_off))[0] if self._skills is not None else []
        except ValueError as exc:
            raise AgentError(str(exc)) from None
        if self.app.settings.demo:
            with self._lock:
                pane = f"demo:w1:p{len(self._demo) + 2}"
                agent = {"id": f"herdr:{pane}", "kind": kind, "name": f"{kind} · {task[:24]}", "status": "working",
                         "task": task[:200], "line": "Reading the brief", "since": _now_ms(), "where": {"herdr_pane": pane}}
                self._demo.append(agent)
            return {**agent, "now": self._now(agent, {})}
        if self._herdr is None:
            raise AgentError("Worker agents need the Terminal module")
        try:
            self._herdr.serve(cwd)
            started = self._herdr.start_agent(kind, cwd, extra)
            self._herdr.prompt(started["pane"], brief(task, cwd))
        except TerminalUnavailable as exc:
            raise AgentError(str(exc)) from None
        self._briefs[started["pane"]] = task
        agent = {"id": f"herdr:{started['pane']}", "kind": kind, "name": started["name"], "status": "working",
                 "task": task[:200], "line": "", "since": _now_ms(), "where": {"herdr_pane": started["pane"]}}
        return {**agent, "now": self._now(agent, {"pane": started["pane"]})}

    # Opening ------------------------------------------------------------------

    def open(self, ident: str) -> dict[str, Any]:
        """Focus an agent's pane and show it; ``view`` asks the page to open a view instead."""
        from kitty.core.terminal import TerminalUnavailable
        from kitty.core.tern import TernMissing, TernUnavailable

        source, _, pane = ident.partition(":")
        if self.app.settings.demo:
            with self._lock:
                if not any(agent["id"] == ident for agent in self._demo):
                    raise LookupError("That agent is no longer running")
            return {"ok": True, "window": None, "view": None}
        try:
            if source == "herdr" and self._herdr is not None:
                self._herdr.focus_agent(pane)
                if self._tern is None:
                    return {"ok": True, "window": None, "view": "herdr"}
                try:
                    self._tern.show_program(self._herdr.client_command(), self.app.settings.root)
                except TernMissing:
                    return {"ok": True, "window": None, "view": "herdr"}
                return {"ok": True, "window": "tern", "view": None}
            if source == "tern" and self._tern is not None and pane.isdigit():
                if not any(agent["id"] == ident for agent in self._tern_agents()):
                    raise LookupError("That agent is no longer running")
                self._tern.show(int(pane))
                return {"ok": True, "window": "tern", "view": None}
        except ValueError:
            raise LookupError("That agent is no longer running") from None
        except (TerminalUnavailable, TernUnavailable) as exc:
            raise AgentError(str(exc)) from None
        raise LookupError("That agent is no longer running")

    def open_terminal(self) -> None:
        """A Tern window on rika!'s session (the bar's "open a terminal")."""
        from kitty.core.tern import TernUnavailable

        if self._tern is None:
            raise AgentError("Terminals need the Terminal module")
        try:
            self._tern.open_window()
        except TernUnavailable as exc:
            raise AgentError(str(exc)) from None

    def run_command(self, command: str, cwd: Path) -> int:
        """An approved shell command in a new Tern tab, kept open to read; returns its block."""
        from kitty.core.tern import TernUnavailable

        if self._tern is None:
            raise AgentError("Shell commands need the Terminal module")
        try:
            return self._tern.run_tab(["sh", "-c", command], cwd)
        except TernUnavailable as exc:
            raise AgentError(str(exc)) from None


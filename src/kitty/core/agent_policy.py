"""Agent policies over purpose labels, evaluated at the one mediator (paper §14.1).

Every record an agent token reaches carries a purpose label: what the data means, not
where it lives. A policy decides per record from its purpose, the operation (read or
write), the tool and the agent's kind; ``mode`` says whether a deny is enforced (the
record is withheld, the write refused) or only observed (journaled, then served).

A policy file (YAML)::

    mode: enforce              # or observe
    default: deny              # when no rule matches
    rules:                     # first match wins
      - decision: allow
        purposes: [notes, "notes.*"]   # glob patterns over PURPOSES; default: every purpose
        ops: [read, write]             # default: both
        tools: ["*"]                   # glob over the mediated tools; default: every tool
        agents: ["*"]                  # glob over agent kinds (omp, claude, codex, ...); default: every agent
    launch:                    # how kitty-agent-launch confines agent tabs (not replayed)
      tcp_ports: [443]         # besides rika!'s own port
      udp_ports: [53]
      projects: []             # more directories an agent may write (absolute paths)
      writable: []             # more home paths an agent may write (e.g. ~/.config/myagent)
      unconfined: run          # run (no token: attribution only) or refuse, when confinement fails

`simulate` replays the journal (``agent_access``) under a draft and lists every decision
that would flip. Because an agent tab's other routes to the data are kernel-denied
(kitty.core.confine), the journal is the complete record of what a Full-guarantee
session reached, so the replay covers everything it did, not only "supported paths".
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

import yaml

#: Purpose labels, with what they cover. Block purposes come from the root's type.
PURPOSES: dict[str, str] = {
    "notes": "Note pages and their blocks",
    "notes.documents": "Imported documents (Markdown chunks)",
    "notes.quick": "Quick notes",
    "notes.tags": "Tag names, keywords and counts",
    "notes.history": "Agent sessions and pinned-citation checks in History",
    "applyai.jobs": "Saved and discovered job postings",
    "applyai.applications": "Applications and their status history",
    "applyai.profile": "The applicant profile (config/profile.yaml)",
    "applyai.answers": "Saved screening answers (config/answers.yaml)",
    "resume": "Resumes, resume versions and their PDFs (resumes/)",
    "calendar": "Calendar events and tasks",
    "calendar.attendees": "People named on calendar events",
    "mail": "Gmail messages and drafts",
    "secrets": "Keys, tokens and credentials (.private/, .env, data/*token*)",
    "store": "The database file and its copies (data/)",
}
#: Root block type -> purpose of every block on it.
BLOCK_PURPOSES = {"page": "notes", "document": "notes.documents", "quick_note": "notes.quick"}
#: Tables -> purpose; no agent tool reaches these, the labels name what the kernel denies.
TABLE_PURPOSES = {
    "jobs": "applyai.jobs", "applications": "applyai.applications", "status_events": "applyai.applications",
    "resumes": "resume", "resume_versions": "resume", "calendar_events": "calendar", "calendar_tasks": "calendar",
    "calendar_google_events": "calendar.attendees", "gmail_messages": "mail", "clients": "secrets", "keys": "secrets",
    "tags": "notes.tags", "op_log": "notes.history",
}
OPS = ("read", "write")
MODES = ("enforce", "observe")
DECISIONS = ("allow", "deny")
UNCONFINED = ("run", "refuse")
MAX_RULES = 200
MAX_SOURCE = 64 * 1024

DEFAULT_SOURCE = """\
# rika!'s built-in agent policy (version 0): agents read and write notes, nothing else.
mode: enforce
default: deny
rules:
  - decision: allow
    purposes: [notes, "notes.*"]
launch:
  tcp_ports: [443]
  udp_ports: [53]
  projects: []
  writable: []
  unconfined: run
"""


class PolicyError(ValueError):
    """A policy file that cannot be used; ``problems`` lists why."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _globs(value: Any, where: str, known: tuple[str, ...] | None, problems: list[str]) -> list[str]:
    if value is None:
        return ["*"]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        problems.append(f"{where} must be a list of names or glob patterns")
        return ["*"]
    if known is not None:
        for item in value:
            if not fnmatch.filter(known, item):
                problems.append(f"{where}: {item!r} matches no known value ({', '.join(known)})")
    return [item.strip() for item in value]


def _ports(value: Any, where: str, problems: list[str]) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(type(item) is int and 1 <= item <= 65535 for item in value):
        problems.append(f"{where} must be a list of port numbers")
        return []
    return sorted(set(value))


def _paths(value: Any, where: str, problems: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and (item.startswith("/") or item.startswith("~/"))
                                              for item in value):
        problems.append(f"{where} must be a list of absolute (or ~/) paths")
        return []
    return list(dict.fromkeys(value))


def _tools() -> tuple[str, ...]:
    """The mediated tools (kitty.core.agent_access.TOOLS): the policy's tool vocabulary."""
    from kitty.core.agent_access import TOOLS

    return tuple(TOOLS)


def parse(source: str) -> dict:
    """The validated policy in `source` (YAML); raises PolicyError with every problem."""
    if not isinstance(source, str) or len(source) > MAX_SOURCE:
        raise PolicyError([f"A policy is YAML text of at most {MAX_SOURCE} characters"])
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise PolicyError([f"Not valid YAML: {exc}"]) from None
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise PolicyError(["A policy is a mapping with mode, default, rules and launch"])
    unknown = set(raw) - {"mode", "default", "rules", "launch"}
    if unknown:
        problems.append(f"Unknown keys: {', '.join(sorted(map(str, unknown)))}")
    mode = raw.get("mode", "enforce")
    if mode not in MODES:
        problems.append("mode is enforce or observe")
    default = raw.get("default", "deny")
    if default not in DECISIONS:
        problems.append("default is allow or deny")
    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list) or len(rules_raw) > MAX_RULES:
        problems.append(f"rules is a list of at most {MAX_RULES} rules")
        rules_raw = []
    rules = []
    for index, rule in enumerate(rules_raw, 1):
        where = f"rule {index}"
        if not isinstance(rule, dict):
            problems.append(f"{where} must be a mapping")
            continue
        extra = set(rule) - {"decision", "purposes", "ops", "tools", "agents"}
        if extra:
            problems.append(f"{where}: unknown keys {', '.join(sorted(map(str, extra)))}")
        if rule.get("decision") not in DECISIONS:
            problems.append(f"{where}: decision is allow or deny")
        rules.append({
            "decision": rule.get("decision"),
            "purposes": _globs(rule.get("purposes"), f"{where} purposes", tuple(PURPOSES), problems),
            "ops": _globs(rule.get("ops"), f"{where} ops", OPS, problems),
            "tools": _globs(rule.get("tools"), f"{where} tools", _tools() if "tools" in rule else None, problems),
            "agents": _globs(rule.get("agents"), f"{where} agents", None, problems),
        })
    launch_raw = raw.get("launch") or {}
    if not isinstance(launch_raw, dict):
        problems.append("launch is a mapping")
        launch_raw = {}
    extra = set(launch_raw) - {"tcp_ports", "udp_ports", "projects", "writable", "unconfined"}
    if extra:
        problems.append(f"launch: unknown keys {', '.join(sorted(map(str, extra)))}")
    unconfined = launch_raw.get("unconfined", "run")
    if unconfined not in UNCONFINED:
        problems.append("launch.unconfined is run or refuse")
    launch = {"tcp_ports": _ports(launch_raw.get("tcp_ports", [443]), "launch.tcp_ports", problems),
              "udp_ports": _ports(launch_raw.get("udp_ports", [53]), "launch.udp_ports", problems),
              "projects": _paths(launch_raw.get("projects"), "launch.projects", problems),
              "writable": _paths(launch_raw.get("writable"), "launch.writable", problems),
              "unconfined": unconfined}
    if problems:
        raise PolicyError(problems)
    return {"mode": mode, "default": default, "rules": rules, "launch": launch}


DEFAULT = parse(DEFAULT_SOURCE)


def decide(policy: dict, *, purpose: str, op: str, tool: str, agent: str) -> str:
    """``allow`` or ``deny`` for one record: the first matching rule's decision, else the default."""
    for rule in policy["rules"]:
        if (any(fnmatch.fnmatchcase(purpose, item) for item in rule["purposes"])
                and any(fnmatch.fnmatchcase(op, item) for item in rule["ops"])
                and any(fnmatch.fnmatchcase(tool, item) for item in rule["tools"])
                and any(fnmatch.fnmatchcase(agent, item) for item in rule["agents"])):
            return rule["decision"]
    return policy["default"]


# ----------------------------------------------------------------------------- storage

def active(conn: sqlite3.Connection) -> tuple[int, dict]:
    """``(version, policy)`` in force: the newest applied one, else the built-in version 0."""
    row = conn.execute("SELECT version, body FROM agent_policies ORDER BY version DESC LIMIT 1").fetchone()
    return (row["version"], json.loads(row["body"])) if row else (0, DEFAULT)


def source(conn: sqlite3.Connection, version: int | None = None) -> str:
    if version == 0:
        return DEFAULT_SOURCE
    query = "SELECT source FROM agent_policies " + ("WHERE version=?" if version is not None else "ORDER BY version DESC LIMIT 1")
    row = conn.execute(query, (version,) if version is not None else ()).fetchone()
    if row is None:
        if version is None:
            return DEFAULT_SOURCE
        raise PolicyError([f"No policy version {version}"])
    return row["source"]


def apply(conn: sqlite3.Connection, text: str) -> dict:
    """Validate `text` and put it in force as the next version; returns ``{version, policy}``."""
    policy = parse(text)
    version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM agent_policies").fetchone()[0]
    conn.execute("INSERT INTO agent_policies(version, body, source, created_at) VALUES (?,?,?,?)",
                 (version, json.dumps(policy, separators=(",", ":")), text, datetime.now(UTC).isoformat(timespec="seconds")))
    return {"version": version, "policy": policy}


def versions(conn: sqlite3.Connection) -> list[dict]:
    rows = [{"version": row["version"], "created_at": row["created_at"], "mode": json.loads(row["body"])["mode"]}
            for row in conn.execute("SELECT version, body, created_at FROM agent_policies ORDER BY version DESC")]
    return rows + [{"version": 0, "created_at": None, "mode": DEFAULT["mode"]}]


# ---------------------------------------------------------------------------- simulate

MAX_FLIPS = 500


def simulate(conn: sqlite3.Connection, draft_text: str, *, session: str | None = None, limit: int = MAX_FLIPS) -> dict:
    """Replay the journal under the draft policy in `draft_text`.

    Returns ``{draft, replayed, flips: [...], counts: {allow_to_deny, deny_to_allow},
    truncated, sessions, enforced}``; each flip is the journal row (id, session, agent,
    tool, op, purpose, block_id, seq, at, mode, policy_version) with ``recorded`` and
    ``draft`` decisions. ``enforced`` says whether the draft's mode would act on them.
    """
    draft = parse(draft_text)
    where, params = ("WHERE a.session=?", (session,)) if session else ("", ())
    rows = conn.execute(
        "SELECT a.id, a.session, a.tool, a.op, a.purpose, a.block_id, a.seq, a.decision, a.mode, a.policy_version, a.at, "
        f"s.kind AS agent, s.name AS name FROM agent_access a JOIN agent_sessions s ON s.id=a.session {where} ORDER BY a.id",
        params).fetchall()
    flips: list[dict] = []
    counts = {"allow_to_deny": 0, "deny_to_allow": 0}
    sessions: set[str] = set()
    for row in rows:
        decision = decide(draft, purpose=row["purpose"], op=row["op"], tool=row["tool"], agent=row["agent"])
        if decision == row["decision"]:
            continue
        counts["allow_to_deny" if decision == "deny" else "deny_to_allow"] += 1
        sessions.add(row["session"])
        if len(flips) < limit:
            flips.append({**{key: row[key] for key in ("id", "session", "agent", "name", "tool", "op", "purpose", "block_id",
                                                     "seq", "at", "mode", "policy_version")},
                          "recorded": row["decision"], "draft": decision})
    total = counts["allow_to_deny"] + counts["deny_to_allow"]
    return {"draft": draft, "replayed": len(rows), "flips": flips, "counts": counts, "total": total,
            "truncated": total > len(flips), "sessions": sorted(sessions), "enforced": draft["mode"] == "enforce"}

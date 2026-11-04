"""Synthetic agent mediation for `kitty-web --demo` (Settings → Agents): never a launch.

The demo runs no kitty-agent-launch, issues no token and confines nothing. It gets rows
instead: five past launches covering every guarantee and state (two confined Full tabs, an
unconfined Attribution-only Tern tab, an ended one and a refused one), their journals over
synthetic block IDs, a demo policy version 1 (codex reads notes but may not write them), a
user hold on the busiest session and one planted ``.envrc`` in that session's project.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kitty.core import agent_policy
from kitty.core.agent_sessions import planted_baseline
from kitty.core.blocks import retention
from kitty.db.connection import transaction

DEMO_POLICY = """\
# Demo policy (version 1): agents read and write notes; codex only reads them.
mode: enforce
default: deny
rules:
  - decision: deny
    agents: [codex]
    ops: [write]
  - decision: allow
    purposes: [notes, "notes.*"]
launch:
  tcp_ports: [443]
  udp_ports: [53]
  projects: []
  writable: []
  unconfined: run
"""

FULL = "Full: every route to rika!'s data from this tab goes through rika!'s API, and every access is journaled."
ATTRIBUTION = ("Attribution only: the launcher could not confine this tab (no Landlock). "
               "The agent runs without a token; its kitty notes writes carry this session.")
REFUSED = "rika! could not verify this launch, so it issued no token: the reporting process is not in the Agent tab"
ENDED = "The agent exited; its token was revoked"
#: (tool, op, purpose, has a block) in the order a coding agent tends to call them.
READ_PAGE = ("get_page", "read", "notes", True)
SEARCH = ("search_blocks", "read", "notes", True)
BACKLINKS = ("get_backlinks", "read", "notes", True)
TAGS = ("list_tags", "read", "notes.tags", False)
APPEND = ("append_blocks", "write", "notes", True)
UPDATE = ("update_block", "write", "notes", True)
QUICK = ("quick_note", "write", "notes.quick", True)


def _block(index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://kitty.invalid/demo/block/{index}"))


def _stamp(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def _journal(conn: sqlite3.Connection, session: str, start: datetime, calls: list[tuple[str, str, str, bool]], *,
             agent: str, policy: dict, version: int, seconds: int) -> None:
    for index, (tool, op, purpose, has_block) in enumerate(calls):
        decision = agent_policy.decide(policy, purpose=purpose, op=op, tool=tool, agent=agent)
        conn.execute("INSERT INTO agent_access(session, tool, op, purpose, block_id, seq, decision, mode, policy_version, at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (session, tool, op, purpose, _block(index % 23) if has_block else None, 1 + index // 3, decision,
                      policy["mode"], version, _stamp(start + timedelta(seconds=index * seconds))))


def seed_demo(conn: sqlite3.Connection, root: Path) -> None:
    """Fill an empty demo database with synthetic agent sessions, once."""
    if conn.execute("SELECT 1 FROM agent_sessions LIMIT 1").fetchone():
        return
    now = datetime.now(UTC)
    # The busiest agent's project: its launch baseline, then a .envrc it "planted" there.
    project = root / "projects" / "parser"
    project.mkdir(parents=True, exist_ok=True)
    baseline = planted_baseline([str(project)])
    (project / ".envrc").write_text("# Synthetic demo file: a planted .envrc\nexport PATH=\"$PWD/bin:$PATH\"\n")
    policy = agent_policy.parse(DEMO_POLICY)
    ids = {name: str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://kitty.invalid/demo/agent/{name}"))
           for name in ("omp", "claude", "codex-tern", "codex-old", "refused")}
    report = json.dumps({"abi": 10, "landlock": True, "seccomp": True, "no_new_privs": True,
                         "closed": ["files", "ptrace", "tcp", "abstract-unix-sockets", "signals", "unix-sockets", "tiocsti"],
                         "open": [], "problems": []})
    rows = [
        # id, kind, name, host, pane, cwd, state, guarantee, abi, message, report, baseline, created, confined, ended
        (ids["omp"], "omp", "omp-3f2a91c4", "herdr", "demo:w1:p2", str(project), "confined", "full", 10, FULL, report,
         json.dumps(baseline), now - timedelta(minutes=42), now - timedelta(minutes=42) + timedelta(seconds=2), None),
        (ids["claude"], "claude", "claude-5d07b1e2", "herdr", "demo:w1:p3", str(root), "confined", "full", 10, FULL, report,
         None, now - timedelta(minutes=15), now - timedelta(minutes=15) + timedelta(seconds=2), None),
        (ids["codex-tern"], "codex", "codex-7be0d215", "tern", "demo:5", str(root), "unconfined", "attribution", None,
         ATTRIBUTION, json.dumps({"abi": 0, "landlock": False, "error": "no Landlock"}), None,
         now - timedelta(minutes=27), None, None),
        (ids["codex-old"], "codex", "codex-a19c3f60", "herdr", "demo:w1:p7", str(root), "ended", "full", 10, ENDED, report,
         None, now - timedelta(hours=20), now - timedelta(hours=20) + timedelta(seconds=2), now - timedelta(hours=19)),
        (ids["refused"], "claude", "claude-c41e9a07", "herdr", "demo:w1:p8", str(root), "refused", "none", 10, REFUSED, None,
         None, now - timedelta(hours=26), None, now - timedelta(hours=26)),
    ]
    with transaction(conn):
        conn.execute("INSERT INTO agent_policies(version, body, source, created_at) VALUES (1,?,?,?)",
                     (json.dumps(policy, separators=(",", ":")), DEMO_POLICY, _stamp(now - timedelta(days=2))))
        for (sid, kind, name, host, pane, cwd, state, guarantee, abi, message, launch_report, base, created, confined,
             ended) in rows:
            conn.execute("INSERT INTO agent_sessions(id, kind, name, host, pane, cwd, state, guarantee, abi, report, baseline, "
                         "message, policy_version, created_at, confined_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                         (sid, kind, name, host, pane, cwd, state, guarantee, abi, launch_report, base, message,
                          _stamp(created), confined and _stamp(confined), ended and _stamp(ended)))
        # 230 calls: more than one page (200) of the busiest journal.
        busy = ([READ_PAGE, SEARCH, READ_PAGE, BACKLINKS, READ_PAGE, APPEND, READ_PAGE, UPDATE, SEARCH, TAGS] * 23)
        _journal(conn, ids["omp"], now - timedelta(minutes=41), busy, agent="omp", policy=policy, version=1, seconds=10)
        _journal(conn, ids["claude"], now - timedelta(minutes=14), [SEARCH, READ_PAGE, READ_PAGE, BACKLINKS, APPEND, QUICK,
                                                                    READ_PAGE, UPDATE, TAGS, READ_PAGE, APPEND, SEARCH, READ_PAGE, UPDATE],
                 agent="claude", policy=policy, version=1, seconds=40)
        # Policy version 1 denies codex's writes: two of its calls were refused.
        _journal(conn, ids["codex-old"], now - timedelta(hours=20), [SEARCH, READ_PAGE, READ_PAGE, APPEND, READ_PAGE, TAGS,
                                                                     BACKLINKS, UPDATE, READ_PAGE, SEARCH, READ_PAGE, READ_PAGE],
                 agent="codex", policy=policy, version=1, seconds=180)
    retention.hold(conn, ids["omp"], "Kept from the demo: review its .envrc")

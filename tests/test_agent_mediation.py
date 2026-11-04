"""Agent mediation without a kernel: policies, the same-transaction journal, policy replay,
retention by reason, the launch spec and its deny set, and the HTTP routes."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from terminal_support import make_root, site
from test_web_clients import call as ingress_call
from test_web_clients import pair

from kitty.core import agent_access, agent_policy, agent_sessions, block_api, clients, confine
from kitty.core.blocks import history, retention
from kitty.core.blocks.actor import Actor, acting
from kitty.db import connect

NOW = datetime.now(UTC)


def _session(conn, kind="claude", name="claude", scopes=agent_access.SCOPES) -> dict:
    sid = str(uuid.uuid4())
    conn.execute("INSERT INTO agent_sessions(id, kind, name, host, cwd, state, guarantee, created_at) "
                 "VALUES (?,?,?,'direct','/tmp','confined','full',?)", (sid, kind, name, NOW.isoformat()))
    return {"id": sid, "kind": kind, "name": name, "scopes": list(scopes)}


def _page(conn, title="Plan", blocks=("alpha", "beta")):
    with acting(Actor("user")):
        return block_api.create_page(conn, title, blocks=list(blocks))


def _journal(conn, session=None):
    sql = "SELECT tool, op, purpose, block_id, seq, decision, mode, policy_version FROM agent_access"
    return [dict(row) for row in conn.execute(sql + (" WHERE session=?" if session else "") + " ORDER BY id",
                                              (session,) if session else ())]


# ---------------------------------------------------------------------------- policies

def test_the_built_in_policy_lets_agents_read_and_write_notes_and_nothing_else():
    version, policy = 0, agent_policy.DEFAULT
    assert policy["mode"] == "enforce" and policy["default"] == "deny" and version == 0
    for purpose in ("notes", "notes.documents", "notes.quick", "notes.tags", "notes.history"):
        assert agent_policy.decide(policy, purpose=purpose, op="write", tool="append_blocks", agent="omp") == "allow"
    for purpose in ("applyai.answers", "applyai.profile", "resume", "calendar.attendees", "secrets"):
        assert agent_policy.decide(policy, purpose=purpose, op="read", tool="get_page", agent="omp") == "deny"


def test_a_policy_file_is_validated_strictly_and_every_problem_is_reported():
    with pytest.raises(agent_policy.PolicyError) as caught:
        agent_policy.parse("mode: loud\ndefault: maybe\nextra: 1\nrules:\n  - decision: allow\n    purposes: [nothing.here]\n"
                           "    tools: [rm_rf]\n    colour: red\nlaunch:\n  tcp_ports: [0]\n  unconfined: sometimes\n")
    problems = " | ".join(caught.value.problems)
    for expected in ("Unknown keys: extra", "mode is enforce or observe", "default is allow or deny",
                     "'nothing.here' matches no known value", "'rm_rf' matches no known value", "unknown keys colour",
                     "launch.tcp_ports must be a list of port numbers", "launch.unconfined is run or refuse"):
        assert expected in problems
    with pytest.raises(agent_policy.PolicyError):
        agent_policy.parse("- not a mapping")


def test_the_first_matching_rule_decides():
    policy = agent_policy.parse("default: allow\nrules:\n"
                                "  - {decision: deny, purposes: [notes.quick], ops: [write], agents: [codex]}\n"
                                "  - {decision: allow, purposes: [notes.quick]}\n"
                                "  - {decision: deny, tools: [delete_block]}\n")
    assert agent_policy.decide(policy, purpose="notes.quick", op="write", tool="quick_note", agent="codex") == "deny"
    assert agent_policy.decide(policy, purpose="notes.quick", op="write", tool="quick_note", agent="omp") == "allow"
    assert agent_policy.decide(policy, purpose="notes", op="write", tool="delete_block", agent="omp") == "deny"
    assert agent_policy.decide(policy, purpose="notes", op="read", tool="get_page", agent="omp") == "allow"


# ----------------------------------------------------------------- the journal and the mediator

def test_reads_are_journaled_per_record_in_their_own_transaction(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn)
    result = agent_access.call(conn, session, "get_page", {"page": made["page_id"]})
    assert [block["text"] for block in result["blocks"]] == ["alpha", "beta"]
    rows = _journal(conn, session["id"])
    assert {row["block_id"] for row in rows} == {made["page_id"], *(item["id"] for item in made["blocks"])}
    assert all(row["tool"] == "get_page" and row["op"] == "read" and row["purpose"] == "notes" and row["seq"] == result["seq"]
               and row["decision"] == "allow" and row["mode"] == "enforce" and row["policy_version"] == 0 for row in rows)
    assert not conn.in_transaction


def test_writes_carry_the_agent_session_and_land_with_their_journal_rows(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn, kind="omp", name="omp")
    receipt = agent_access.call(conn, session, "append_blocks", {"page": made["page_id"], "blocks": ["from the agent"]})
    actor = conn.execute("SELECT actor, actor_name, actor_session FROM op_log WHERE seq=?", (receipt["seq"],)).fetchone()
    assert tuple(actor) == ("agent", "omp", session["id"])
    written = [row for row in _journal(conn, session["id"]) if row["op"] == "write"]
    assert {row["block_id"] for row in written} == {made["page_id"], receipt["blocks"][0]["id"]}
    assert all(row["seq"] == receipt["seq"] for row in written)


def test_no_access_is_served_or_kept_when_its_journal_row_cannot_be_written(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn)
    before = conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0]
    conn.execute("CREATE TEMP TRIGGER refuse_journal BEFORE INSERT ON agent_access BEGIN SELECT RAISE(ABORT, 'disk full'); END")
    with pytest.raises(sqlite3.IntegrityError):
        agent_access.call(conn, session, "append_blocks", {"page": made["page_id"], "blocks": ["unjournaled"]})
    with pytest.raises(sqlite3.IntegrityError):
        agent_access.call(conn, session, "get_page", {"page": made["page_id"]})
    assert conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0] == before
    assert "unjournaled" not in json.dumps(block_api.get_page(conn, made["page_id"]))
    assert not conn.in_transaction


def test_scopes_arguments_and_unknown_tools_are_refused_before_any_access(workspace):
    conn = workspace.conn
    made = _page(conn)
    reader = _session(conn, scopes=("notes.read",))
    with pytest.raises(agent_access.AccessError) as caught:
        agent_access.call(conn, reader, "append_blocks", {"page": made["page_id"], "blocks": ["x"]})
    assert caught.value.status == 403 and "notes.write" in str(caught.value)
    with pytest.raises(agent_access.AccessError) as caught:
        agent_access.call(conn, reader, "search_blocks", {"query": "alpha"})
    assert caught.value.status == 403
    with pytest.raises(agent_access.AccessError) as caught:
        agent_access.call(conn, reader, "read_answers", {})
    assert caught.value.status == 404
    with pytest.raises(agent_access.AccessError):
        agent_access.call(conn, reader, "get_page", {"page": made["page_id"], "sql": "SELECT 1"})
    with pytest.raises(agent_access.AccessError):
        agent_access.call(conn, reader, "list_pages", {"limit": 10**6})
    assert _journal(conn) == []


def test_an_enforced_deny_withholds_records_from_listings_and_refuses_single_reads_and_writes(workspace):
    conn = workspace.conn
    made = _page(conn, blocks=["shared word"])
    with acting(Actor("user")):
        block_api.quick_note(conn, "shared word in a quick note")
    agent_policy.apply(conn, "default: allow\nrules:\n  - {decision: deny, purposes: [notes.quick]}\n"
                             "  - {decision: deny, purposes: [notes], ops: [write]}\n")
    session = _session(conn)
    hits = agent_access.call(conn, session, "search_blocks", {"query": "shared"})["hits"]
    assert [hit["root_type"] for hit in hits] == ["page"]
    decisions = {(row["purpose"], row["decision"]) for row in _journal(conn, session["id"])}
    assert decisions == {("notes", "allow"), ("notes.quick", "deny")}
    before = conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0]
    with pytest.raises(agent_access.AccessError) as caught:
        agent_access.call(conn, session, "append_blocks", {"page": made["page_id"], "blocks": ["refused"]})
    assert caught.value.status == 403 and "version 1" in str(caught.value)
    assert conn.execute("SELECT MAX(seq) FROM op_log").fetchone()[0] == before
    refused = [row for row in _journal(conn, session["id"]) if row["op"] == "write"]
    assert refused and all(row["decision"] == "deny" and row["policy_version"] == 1 for row in refused)


def test_an_observed_deny_is_journaled_and_served(workspace):
    conn = workspace.conn
    made = _page(conn)
    agent_policy.apply(conn, "mode: observe\ndefault: deny\nrules: []\n")
    session = _session(conn)
    receipt = agent_access.call(conn, session, "append_blocks", {"page": made["page_id"], "blocks": ["observed"]})
    assert receipt["seq"]
    assert {(row["decision"], row["mode"]) for row in _journal(conn, session["id"])} == {("deny", "observe")}


def test_an_agent_token_undoes_only_its_own_session(workspace):
    conn = workspace.conn
    made = _page(conn)
    mine, other = _session(conn), _session(conn, kind="codex", name="codex")
    agent_access.call(conn, other, "append_blocks", {"page": made["page_id"], "blocks": ["theirs"]})
    with pytest.raises(agent_access.AccessError) as caught:
        agent_access.call(conn, mine, "undo_agent_session", {"session": other["id"]})
    assert caught.value.status == 403
    agent_access.call(conn, mine, "append_blocks", {"page": made["page_id"], "blocks": ["mine"]})
    undone = agent_access.call(conn, mine, "undo_agent_session", {})
    assert undone["applied"] and [item["text"] for item in undone["reverted"]] == ["mine"]


# -------------------------------------------------------------------------- simulate

def test_simulate_replays_the_journal_under_a_draft_and_lists_every_flip(workspace):
    conn = workspace.conn
    made = _page(conn)
    with acting(Actor("user")):
        block_api.quick_note(conn, "a quick thought")
    claude, codex = _session(conn), _session(conn, kind="codex", name="codex")
    agent_access.call(conn, claude, "list_pages", {})
    agent_access.call(conn, codex, "append_blocks", {"page": made["page_id"], "blocks": ["by codex"]})
    draft = "default: allow\nrules:\n  - {decision: deny, purposes: [notes.quick]}\n  - {decision: deny, ops: [write], agents: [codex]}\n"
    result = agent_policy.simulate(conn, draft)
    assert result["replayed"] == len(_journal(conn))
    flips = {(flip["agent"], flip["tool"], flip["purpose"], flip["recorded"], flip["draft"]) for flip in result["flips"]}
    assert flips == {("claude", "list_pages", "notes.quick", "allow", "deny"), ("codex", "append_blocks", "notes", "allow", "deny")}
    assert result["counts"] == {"allow_to_deny": 3, "deny_to_allow": 0} and result["enforced"]
    assert set(result["sessions"]) == {claude["id"], codex["id"]}
    only = agent_policy.simulate(conn, draft, session=claude["id"])
    assert {flip["session"] for flip in only["flips"]} == {claude["id"]}
    # The draft that is now in force flips nothing when replayed again over new accesses.
    agent_policy.apply(conn, draft)
    with pytest.raises(agent_access.AccessError):
        agent_access.call(conn, codex, "append_blocks", {"page": made["page_id"], "blocks": ["refused now"]})
    again = agent_policy.simulate(conn, draft)
    assert {flip["policy_version"] for flip in again["flips"]} == {0}


# ------------------------------------------------------------------------- retention

def _future(days=31):
    return datetime.now(UTC) + timedelta(days=days)


def test_compaction_keeps_the_op_log_rows_journal_rows_in_the_window_cite(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn)
    read = agent_access.call(conn, session, "get_page", {"page": made["page_id"]}, now=_future(30))
    with acting(Actor("user")):
        block_api.update_block(conn, made["blocks"][0]["id"], text="alpha, later")
    result = history.compact(conn, now=_future())
    assert result["floor"] == read["seq"] and "replay_window" in result["retained"]
    assert result["horizon_seq"] < read["seq"]
    [cited] = history.citations(conn, [made["blocks"][0]["cite"].replace(f"@{made['seq']}", f"@{read['seq']}")])["citations"]
    assert cited["status"] == "changed" and cited["then"]["text"] == "alpha"
    assert len(_journal(conn, session["id"])) == 3


def test_without_a_reason_everything_old_folds_and_old_journal_rows_go(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn)
    agent_access.call(conn, session, "get_page", {"page": made["page_id"]})
    result = history.compact(conn, now=_future())
    assert result["floor"] is None and result["folded"] > 0 and result["journal_pruned"] == 3
    assert _journal(conn) == []


def test_a_pinned_citation_in_live_text_clamps_the_horizon_and_keeps_its_journal_row(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn)
    read = agent_access.call(conn, session, "get_page", {"page": made["page_id"]})
    cite = read["blocks"][0]["cite"]
    with acting(Actor("user")):
        block_api.append_blocks(conn, made["page_id"], [f"Source: {cite}"])
        block_api.update_block(conn, made["blocks"][0]["id"], text="alpha, changed")
    result = history.compact(conn, now=_future())
    assert result["retained"] == ["pinned_citation"] and result["floor"] == read["seq"]
    [checked] = history.citations(conn, [cite])["citations"]
    assert checked["status"] == "changed" and checked["then"]["text"] == "alpha"
    kept = _journal(conn, session["id"])
    assert [(row["block_id"], row["seq"]) for row in kept] == [(made["blocks"][0]["id"], read["seq"])]


def test_a_user_hold_keeps_a_session_past_the_window_until_released(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn)
    read = agent_access.call(conn, session, "get_page", {"page": made["page_id"]})
    retention.hold(conn, session["id"], "keep for the audit")
    result = history.compact(conn, now=_future())
    assert result["retained"] == ["user_hold"] and result["floor"] == read["seq"] and len(_journal(conn)) == 3
    assert retention.release(conn, session["id"])
    assert history.compact(conn, now=_future())["journal_pruned"] == 3


def test_the_size_cap_releases_the_oldest_reasons_and_says_so(workspace):
    conn = workspace.conn
    made = _page(conn)
    session = _session(conn)
    agent_access.call(conn, session, "get_page", {"page": made["page_id"]})
    retention.hold(conn, session["id"])
    result = history.compact(conn, now=_future(), cap=0)
    assert [item["reason"] for item in result["released"]] == ["user_hold"] and result["floor"] is None
    assert result["folded"] > 0
    row = conn.execute("SELECT released_at, released_by FROM retention_holds").fetchone()
    assert row["released_at"] and row["released_by"] == "size_cap"


# -------------------------------------------------------------------- launch spec, deny set

def test_the_launch_plan_never_reaches_the_deny_set(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    root = settings.root
    for path in ("data/web.db", "data/exports/notes.json", ".private/key", "config/answers.yaml", "config/profile.yaml",
                 "resumes/r.pdf", ".env", ".cache/x", "src/app.py", "config/tracks.yaml", ".git/hooks/pre-commit",
                 ".git/HEAD"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("synthetic")
    home = tmp_path / "home"
    for path in (".ssh/id_ed25519", ".mozilla/profile/sessionstore", ".claude/settings.json", "code/notes.txt"):
        (home / path).parent.mkdir(parents=True, exist_ok=True)
        (home / path).write_text("synthetic")
    deny = agent_sessions.deny_set(settings, herdr_dir=home / ".config/herdr/sessions", runtime_dir=tmp_path / "run",
                                   home=home)
    spec = {"deny": [list(item) for item in deny], "tcp": [443], "udp": [53],
            **agent_sessions.grants(settings, "claude", root, agent_policy.DEFAULT["launch"], home=home)}
    plan = agent_sessions.compile_plan(spec)
    for denied in ("data/web.db", "data/exports/notes.json", ".private/key", "config/answers.yaml", "config/profile.yaml",
                   "resumes/r.pdf", ".env", ".cache/x"):
        assert confine.reaches(plan, str(root / denied)) == [], denied
    for denied in (".ssh/id_ed25519", ".mozilla/profile/sessionstore"):
        assert confine.reaches(plan, str(home / denied)) == [], denied
    rules = dict(plan.rules)
    assert rules[str(root / "src")] & confine.FS_WRITE_FILE
    assert rules[str(root / "config" / "tracks.yaml")] == confine.READ & confine.FILE_RIGHTS
    assert rules[str(root / ".git" / "hooks")] == confine.READ, "planted git hooks stay read-only"
    assert rules[str(root / ".git" / "HEAD")] & confine.FS_WRITE_FILE
    assert rules[str(root)] == confine.FS_READ_DIR, "the root is listed, never granted"
    assert rules[str(home / ".claude")] & confine.FS_WRITE_FILE


def test_the_host_guarantee_follows_the_kernel_matrix():
    assert confine.guarantee(confine.closures(10) + ["tiocsti"])[0] == confine.FULL
    level, open_routes = confine.guarantee(confine.closures(6) + ["tiocsti"])
    assert level == confine.ATTRIBUTION and any("herdr" in route and "ABI 9" in route for route in open_routes)
    level, open_routes = confine.guarantee([])
    assert level == confine.ATTRIBUTION and len(open_routes) == len(confine.REQUIRED)


def test_the_seccomp_filter_refuses_tiocsti_and_tioclinux_on_every_supported_arch():
    for machine in ("x86_64", "aarch64"):
        program = confine.seccomp_program(machine)
        assert len(program) % 8 == 0 and len(program) // 8 < 64
    with pytest.raises(confine.ConfineError):
        confine.seccomp_program("sparc64")


def test_clients_take_the_agent_session_kind_but_never_list_or_pair_it(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running, closing(connect(settings.db_path)) as conn:
        agent, token = clients.create(conn, "omp", clients.AGENT_KIND, list(agent_access.SCOPES), None)
        assert clients.authenticate(conn, token)["kind"] == "agent-session"
        assert clients.listing(conn) == []
        status, body, _ = ingress_call(running, "POST", "/api/ingress/pair", {"name": "x", "kind": "agent-session",
                                                                               "scopes": ["read"]})
        assert status == 400
        # An agent token reaches only the agent routes; a paired client's token never reaches them.
        assert ingress_call(running, "GET", "/api/ingress/recent", token=token)[0] == 403
        _, paired = pair(running)
        assert ingress_call(running, "POST", "/api/ingress/agent/call", {"tool": "list_tags"}, token=paired)[0] == 401


# ------------------------------------------------------------------------------- HTTP

def _agent_token(running, conn, *, kind="omp"):
    """A confined session's token, as the exchange issues it (pid: this test process)."""
    session = _session(conn, kind=kind, name=kind)
    client, token = clients.create(conn, kind, clients.AGENT_KIND, list(agent_access.SCOPES), None)
    conn.execute("UPDATE agent_sessions SET client_id=?, pid=?, pid_start=? WHERE id=?",
                 (client["id"], os.getpid(), agent_sessions.start_time(os.getpid()), session["id"]))
    return session, token


def test_agent_routes_take_only_programs_and_agent_tokens(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        with closing(connect(settings.db_path)) as conn:
            made = _page(conn)
            session, token = _agent_token(running, conn)
        path = "/api/ingress/agent/call"
        assert ingress_call(running, "POST", path, {"tool": "list_tags"})[0] == 401
        assert ingress_call(running, "POST", path, {"tool": "list_tags"}, token="kitty_" + "x" * 43)[0] == 401
        assert ingress_call(running, "POST", path, {"tool": "list_tags"}, token=token,
                            origin="chrome-extension://" + "b" * 32)[0] == 403
        status, page, _ = ingress_call(running, "POST", path, {"tool": "get_page", "arguments": {"page": made["page_id"]}},
                                       token=token)
        assert status == 200 and page["page"]["title"] == "Plan"
        status, info, _ = ingress_call(running, "GET", "/api/ingress/agent/session", token=token)
        assert status == 200 and info["session"] == session["id"] and "append_blocks" in info["tools"]
        # The page API refuses the token outright (it is not the UI secret).
        assert running.request("/api/blocks/pages", headers={"Authorization": f"Bearer {token}"})[0] == 403
        status, listed = running.request("/api/agents")
        assert status == 200 and listed["sessions"][0]["id"] == session["id"] and listed["sessions"][0]["accesses"] == 3
        status, detail = running.request(f"/api/agents/session?id={session['id']}")
        assert status == 200 and {row["tool"] for row in detail["journal"]} == {"get_page"}
        assert running.request("/api/agents", headers={"X-Kitty-Secret": None})[0] == 401


def test_an_exited_agents_token_ends_with_it(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        with closing(connect(settings.db_path)) as conn:
            session, token = _agent_token(running, conn)
            conn.execute("UPDATE agent_sessions SET pid_start=pid_start+1 WHERE id=?", (session["id"],))
        assert ingress_call(running, "POST", "/api/ingress/agent/call", {"tool": "list_tags"}, token=token)[0] == 401
        with closing(connect(settings.db_path)) as conn:
            row = conn.execute("SELECT state, ended_at FROM agent_sessions WHERE id=?", (session["id"],)).fetchone()
            assert row["state"] == "ended" and row["ended_at"]
            assert conn.execute("SELECT revoked_at FROM clients").fetchone()[0]


def test_settings_simulates_applies_holds_and_ends(tmp_path):
    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        with closing(connect(settings.db_path)) as conn:
            made = _page(conn)
            session, token = _agent_token(running, conn)
        ingress_call(running, "POST", "/api/ingress/agent/call",
                     {"tool": "append_blocks", "arguments": {"page": made["page_id"], "blocks": ["agent text"]}}, token=token)
        draft = "default: allow\nrules:\n  - {decision: deny, ops: [write]}\n"
        status, simulated = running.request("/api/agents/policy/simulate", {"source": draft})
        assert status == 200 and simulated["counts"]["allow_to_deny"] == 2
        status, bad = running.request("/api/agents/policy/simulate", {"source": "mode: loud"})
        assert status == 400 and "mode is enforce or observe" in bad["problems"]
        status, applied = running.request("/api/agents/policy/apply", {"source": draft})
        assert status == 200 and applied["version"] == 1 and applied["policy"]["mode"] == "enforce"
        status, refused, _ = ingress_call(running, "POST", "/api/ingress/agent/call",
                                          {"tool": "append_blocks", "arguments": {"page": made["page_id"], "blocks": ["no"]}},
                                          token=token)
        assert status == 403 and "policy (version 1)" in refused["error"]
        status, held = running.request("/api/agents/sessions/hold", {"id": session["id"], "hold": True})
        assert status == 200 and held["held"]
        status, ended = running.request("/api/agents/sessions/end", {"id": session["id"]})
        assert status == 200 and ended["session"]["state"] == "ended"
        assert ingress_call(running, "POST", "/api/ingress/agent/call", {"tool": "list_tags"}, token=token)[0] == 401


def test_the_demo_seed_covers_every_guarantee_with_a_paged_journal_a_hold_and_a_planted_file(tmp_path):
    from kitty.core import agent_demo

    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        with closing(connect(settings.db_path)) as conn:
            agent_demo.seed_demo(conn, settings.root)
            agent_demo.seed_demo(conn, settings.root)  # once
        status, overview = running.request("/api/agents")
        assert status == 200 and overview["policy"]["version"] == 1 and "codex only reads" in overview["policy"]["source"]
        sessions = {item["name"]: item for item in overview["sessions"]}
        assert len(sessions) == 5
        assert {item["guarantee"] for item in sessions.values()} == {"full", "attribution", "none"}
        assert {item["state"] for item in sessions.values()} == {"confined", "unconfined", "ended", "refused"}
        busy = sessions["omp-3f2a91c4"]
        assert busy["accesses"] == 230 and busy["held"] and not busy["token"]
        status, detail = running.request(f"/api/agents/session?id={busy['id']}")
        assert status == 200 and len(detail["journal"]) == 200 and detail["next"] and detail["held"]
        assert detail["planted"] == [{"path": str(settings.root / "projects" / "parser" / ".envrc"), "change": "created"}]
        older = running.request(f"/api/agents/session?id={busy['id']}&before={detail['next']}")[1]
        assert len(older["journal"]) == 30 and older["next"] is None
        # Version 1 refused the old codex session's writes; the built-in policy would allow them.
        old = running.request(f"/api/agents/session?id={sessions['codex-a19c3f60']['id']}")[1]
        assert [row["tool"] for row in old["journal"] if row["decision"] == "deny"] == ["update_block", "append_blocks"]
        simulated = running.request("/api/agents/policy/simulate", {"source": agent_policy.DEFAULT_SOURCE})[1]
        assert simulated["counts"] == {"allow_to_deny": 0, "deny_to_allow": 2} and simulated["replayed"] == 256

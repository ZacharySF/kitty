"""Settings → Agents and the Terminal module's local routes answered by rika-server
(server/src/agents, server/src/terminal.rs) against the Python core they came from
(`kitty.core.agent_sessions`, `agent_policy`, `agent_access`, `blocks.retention`): one scenario
over `GET /api/agents`, `GET /api/agents/session` and `POST /api/agents/sessions/{hold,end}`,
with the refusals between, run through the front and through the Python core on two copies of
the same workspace at the same clock reading. Every answer (sessions, journal pages, planted
files, the retention floor and its size cap, the host guarantee), the rows left behind
(sessions, revoked tokens, holds) and the content events recorded must be the same. Then
`GET /api/terminal/agents` and the socket without an upgrade, with their module and pack guard."""
from __future__ import annotations

import json
import shutil
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from terminal_support import front_site, make_root
from test_web_terminal import herdr  # noqa: F401 (the isolated fake herdr fixture)

from kitty import packs
from kitty.core import (
    agent_access,
    agent_demo,
    agent_policy,
    agent_sessions,
    clients,
    record_events,
)
from kitty.core.blocks import retention
from kitty.db import connect
from kitty.modules import ModuleDisabledError
from kitty.web_server import APIError, _integer

#: A reading with microseconds: stamps keep whole seconds, the History cutoff keeps them all.
BASE = datetime(2026, 11, 16, 12, 30, 15, 123456, tzinfo=UTC)
PREFIX = "/api/agents"


class Clock:
    """`datetime` for the reference modules: the reading the front is given."""

    @staticmethod
    def now(zone=None):
        return BASE


class Refused(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Reference:
    """The page routes as `kitty.web_agents` answered them before the port, on the core."""

    def __init__(self, db_path):
        self.conn = connect(db_path)
        self.events = []

    def overview(self):
        conn = self.conn
        cutoff = (BASE - timedelta(days=conn.execute("SELECT history_retention_days FROM build_state WHERE id=1").fetchone()[0])).isoformat()
        version, policy = agent_policy.active(conn)
        return {"host": agent_sessions.host_guarantee(), "sessions": agent_sessions.listing(conn),
                "policy": {"version": version, "mode": policy["mode"], "source": agent_policy.source(conn)},
                "versions": agent_policy.versions(conn), "retention": retention.status(conn, cutoff),
                "purposes": agent_policy.PURPOSES,
                "tools": {name: {"op": tool.op, "scope": tool.scope} for name, tool in agent_access.TOOLS.items()}}

    def get(self, path, query):
        if path == PREFIX:
            if query:
                raise Refused(400, "This route does not accept query parameters")
            return self.overview()
        if set(query) - {"id", "before"} or "id" not in query:
            raise Refused(400, "Expected ?id= (and optionally before=)")
        before = _integer(query["before"], "before", 1, 2**62) if "before" in query else None
        return agent_sessions.detail(self.conn, query["id"], before=before)

    def post(self, path, body):
        conn = self.conn
        session = body.get("id")
        if not isinstance(session, str):
            raise Refused(400, "Expected {\"id\"}")
        if path.endswith("/hold"):
            if set(body) != {"id", "hold"} or type(body["hold"]) is not bool:
                raise Refused(400, "Expected {\"id\", \"hold\": true|false}")
            retention.hold(conn, session) if body["hold"] else retention.release(conn, session)
        else:
            if set(body) != {"id"}:
                raise Refused(400, "Expected {\"id\"}")
            agent_sessions.end(conn, session, "Ended in Settings → Agents; its token was revoked")
        return agent_sessions.detail(conn, session)

    def __call__(self, path, body, query):
        try:
            if body is None:
                return 200, self.get(path, query)
            mark = record_events.op_log_mark(self.conn)
            result = self.post(path, body)
            record_events.capture(self.conn, path, body, result, op_log_before=mark)
            self.events = [list(row) for row in self.conn.execute(
                "SELECT section,entity,entity_id,action,route FROM record_events ORDER BY seq")]
            return 200, result
        except Refused as exc:
            return exc.status, {"error": exc.message}
        except APIError as exc:
            return exc.status, {"error": str(exc)}
        except LookupError as exc:
            return 404, {"error": str(exc)}


class Front:
    def __init__(self, settings):
        self.context = front_site(settings, {"KITTY_TEST_NOW": BASE.isoformat()})
        self.site = self.context.__enter__()

    def __call__(self, path, body, query):
        return self.site.request(path + ("?" + urlencode(query) if query else ""), body)

    def close(self):
        self.context.__exit__(None, None, None)


def rows(db_path):
    with closing(connect(db_path)) as conn:
        found = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                 for table in ("agent_sessions", "clients", "retention_holds", "agent_access", "agent_policies")}
        found["events"] = [list(row) for row in conn.execute("SELECT section,entity,entity_id,action,route FROM record_events ORDER BY seq")]
    return found


def _session(conn, sid, *, state, created, client=None, report=None, baseline=None, name=None, kind="omp"):
    conn.execute("INSERT INTO agent_sessions(id, client_id, kind, name, host, pane, cwd, state, guarantee, abi, pid, report, baseline, "
                 "message, policy_version, created_at) VALUES (?,?,?,?,'herdr','w1:p9','/work',?,'full',10,4242,?,?,'synthetic',2,?)",
                 (sid, client, kind, name or sid, state, report, baseline, created))


def _journal(conn, session, at, seq, tool="get_page", op="read", decision="allow"):
    conn.execute("INSERT INTO agent_access(session, tool, op, purpose, block_id, seq, decision, mode, policy_version, at) "
                 "VALUES (?,?,?,'notes',NULL,?,?,'enforce',1,?)", (session, tool, op, seq, decision, at))


def _op_log(conn, count, at):
    conn.executemany("INSERT INTO op_log(device_id, txn_id, page_id, at, ops) VALUES ('dev','txn','page',?,x'00')",
                     [(at,)] * count)


def seed(settings, monkeypatch):
    """The demo's five sessions plus every case the routes distinguish."""
    root = settings.root
    monkeypatch.setattr(agent_demo, "datetime", Clock)
    ids = {name: str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://kitty.invalid/demo/agent/{name}"))
           for name in ("omp", "claude", "codex-tern", "codex-old", "refused")}
    old = (BASE - timedelta(days=40)).isoformat(timespec="seconds")
    recent = (BASE - timedelta(hours=1)).isoformat(timespec="seconds")
    with closing(connect(settings.db_path)) as conn:
        agent_demo.seed_demo(conn, root)
        conn.execute("INSERT INTO agent_policies(version, body, source, created_at) VALUES (2,?,?,?)",
                     (json.dumps({**agent_policy.DEFAULT, "mode": "observe"}, separators=(",", ":")), "mode: observe\n",
                      (BASE - timedelta(days=1)).isoformat(timespec="seconds")))
        # Planted files: a project named with `.` and doubled slashes (pathlib's spelling), a
        # hooks folder, a changed and a removed file, and a project that is gone.
        project = root / "projects" / "planted"
        (project / ".git" / "hooks").mkdir(parents=True)
        (project / ".git" / "config").write_text("[core]\n")
        (project / ".vscode").mkdir()
        (project / ".vscode" / "tasks.json").write_text("{}")
        (project / ".git" / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\n")
        spelled = str(project).replace("/projects/", "/projects//./") + "/"
        baseline = agent_sessions.planted_baseline([spelled, str(root / "projects" / "gone")])
        (project / ".envrc").write_text("export X=1\n")
        (project / ".git" / "config").write_text("[core]\n\tbare = false\n")
        (project / ".vscode" / "tasks.json").unlink()
        (project / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\ncurl example.invalid\n")
        (project / ".git" / "hooks" / "unreadable").write_text("x")
        (project / ".git" / "hooks" / "unreadable").chmod(0)
        live, _token = clients.create(conn, "omp", clients.AGENT_KIND, list(agent_access.SCOPES), None)
        _session(conn, "live", state="confined", created=recent, client=live["id"], baseline=json.dumps(baseline),
                 report=json.dumps({"abi": 10, "landlock": True, "nested": {"z": 1, "a": [1.5, None, "é"]}}))
        _session(conn, "pending", state="pending", created=recent, baseline="{}", report="")
        _session(conn, "old", state="unconfined", created=old, kind="claude")
        _session(conn, "same-time-b", state="refused", created=recent)
        for index in range(30):
            _journal(conn, "old", old, 1000 + index, tool="append_blocks", op="write", decision="deny" if index % 4 else "allow")
        for index in range(5):
            _journal(conn, "live", recent, 7 + index)
        _op_log(conn, 40, old)
        _op_log(conn, 10, recent)
        # Pinned citations in live block text (one cited twice, one in a deleted block).
        cited = ["11111111-2222-3333-4444-555555555555", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
        texts = [f"see (({cited[0]}@3)) and (({cited[1]}@12)) and (({cited[0]}@3))", f"again (({cited[1]}@12))",
                 f"(({cited[0]}@999999999999999999))", "((not-a-citation@1))"]
        for index, text in enumerate(texts):
            conn.execute("INSERT INTO blocks(id, type, page_id, order_key, text, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                         (f"pin-{index}", "page", f"pin-{index}", "a", text, recent, recent))
        conn.execute("INSERT INTO blocks(id, type, page_id, order_key, text, created_at, updated_at, deleted_at) "
                     "VALUES ('pin-gone','page','pin-gone','a',?,?,?,?)", (f"(({cited[0]}@1))", recent, recent, recent))
    return ids


def scenario(call, ids):
    answers = []

    def do(path, body=None, **query):
        status, answer = call(path, body, query)
        answers.append((path, status, answer))
        return answer

    busy = ids["omp"]
    do(PREFIX)
    do(PREFIX, x="1")
    first = do(PREFIX + "/session", id=busy)
    do(PREFIX + "/session", id=busy, before=str(first.get("next")))
    for before in ("1", "0", "abc", "", "-1", "12345678901", "9999999999", "\u0661"):
        do(PREFIX + "/session", id=busy, before=before)
    do(PREFIX + "/session")
    do(PREFIX + "/session", id=busy, limit="5")
    do(PREFIX + "/session", id="nope")
    for sid in (*ids.values(), "live", "pending", "old", "same-time-b"):
        do(PREFIX + "/session", id=sid)
    for body in ({"id": busy, "hold": True}, {"id": ids["claude"], "hold": True}, {"id": "pending", "hold": True},
                 {"id": busy, "hold": False}, {"id": busy, "hold": False}, {"id": "nope", "hold": True},
                 {"id": "nope", "hold": False}, {"id": busy, "hold": "yes"}, {"id": busy, "hold": 1}, {"id": busy},
                 {"hold": True}, {"id": 5, "hold": True}, {"id": busy, "hold": True, "x": 1}, {"id": "old", "hold": True}):
        do(PREFIX + "/sessions/hold", body)
    for body in ({"id": "live"}, {"id": "live"}, {"id": "pending"}, {"id": ids["refused"]}, {"id": ids["codex-tern"]},
                 {"id": "nope"}, {"id": "live", "x": 1}, {}, {"id": None}, {"id": ["live"]}):
        do(PREFIX + "/sessions/end", body)
    do(PREFIX)
    return answers


def compare(settings, tmp_path, monkeypatch, run):
    copy = tmp_path / "reference.db"
    with closing(connect(settings.db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(settings.db_path, copy)
    monkeypatch.setattr(agent_sessions, "now", lambda: BASE.isoformat(timespec="seconds"))
    monkeypatch.setattr(retention, "datetime", Clock)
    reference = Reference(copy)
    try:
        expected = run(reference)
    finally:
        reference.conn.close()
    front = Front(settings)
    try:
        answered = run(front)
    finally:
        front.close()
    assert len(answered) == len(expected)
    for (path, status, answer), (_, wanted_status, wanted) in zip(answered, expected, strict=True):
        assert (status, answer) == (wanted_status, wanted), path
    assert rows(settings.db_path) == rows(copy)
    return expected


def test_the_front_answers_settings_agents_as_the_python_core_does(tmp_path, monkeypatch):
    settings = make_root(tmp_path, ("workspace",))
    with front_site(settings):  # the front migrates the new workspace
        pass
    ids = seed(settings, monkeypatch)
    expected = compare(settings, tmp_path, monkeypatch, lambda call: scenario(call, ids))
    answers = {(path, index): (status, answer) for index, (path, status, answer) in enumerate(expected)}
    statuses = {status for status, _ in answers.values()}
    assert statuses == {200, 400, 404}
    overview = expected[0][2]
    assert overview["policy"] == {"version": 2, "mode": "observe", "source": "mode: observe\n"}
    assert [item["version"] for item in overview["versions"]] == [2, 1, 0]
    assert overview["retention"]["reasons"] == {"replay_window": 1, "pinned_citation": 3, "user_hold": 1}
    assert overview["retention"]["floor"] == 1 and overview["retention"]["over_cap"] == []
    live = next(answer for path, status, answer in expected if path.endswith("/session") and status == 200
                and answer["session"]["id"] == "live")
    assert {item["change"] for item in live["planted"]} == {"created", "changed", "removed"}
    assert any(item["path"].endswith("/.git/hooks/post-checkout") for item in live["planted"])
    assert live["session"]["report"]["nested"] == {"z": 1, "a": [1.5, None, "é"]}
    final = expected[-1][2]
    states = {item["id"]: (item["state"], item["held"], item["token"]) for item in final["sessions"]}
    assert states["live"][0] == states["pending"][0] == "ended" and states[ids["refused"]][0] == "refused"
    assert states[ids["claude"]][1] and not states[ids["omp"]][1]


def test_the_size_cap_releases_the_oldest_reasons_alike(tmp_path, monkeypatch):
    settings = make_root(tmp_path, ("workspace",))
    with front_site(settings):
        pass
    seed(settings, monkeypatch)
    with closing(connect(settings.db_path)) as conn:
        _op_log(conn, retention.MAX_RETAINED + 5, (BASE - timedelta(days=90)).isoformat())
    expected = compare(settings, tmp_path, monkeypatch, lambda call: [(PREFIX, *call(PREFIX, None, {}))])
    over = expected[0][2]["retention"]
    assert over["over_cap"] and over["floor"] is not None


def test_a_fresh_workspace_shows_the_built_in_policy(tmp_path, monkeypatch):
    settings = make_root(tmp_path, ())
    with front_site(settings):
        pass
    expected = compare(settings, tmp_path, monkeypatch, lambda call: [(PREFIX, *call(PREFIX, None, {}))])
    overview = expected[0][2]
    assert overview["policy"] == {"version": 0, "mode": "enforce", "source": agent_policy.DEFAULT_SOURCE}
    assert overview["sessions"] == [] and overview["versions"] == [{"version": 0, "created_at": None, "mode": "enforce"}]


def test_the_terminal_routes_answer_the_workers_herdr(tmp_path, monkeypatch, herdr):  # noqa: F811
    settings = make_root(tmp_path, ("terminal",))
    # The worker reads the session name from the workspace's .env, as Settings.load does.
    monkeypatch.delenv("KITTY_HERDR_SESSION")
    (settings.root / ".env").write_text("KITTY_HERDR_SESSION=from-dotenv\n")
    with front_site(settings) as running:
        assert running.request("/api/terminal/agents?any=1") == (200, {
            "binary": str(herdr.binary), "session": "from-dotenv", "cwd": str(settings.root), "default": "omp",
            "kinds": ["omp", "claude", "codex"]})
        assert running.request("/api/terminal/socket") == (426, {"error": "Open the terminal with a WebSocket upgrade"})
        assert running.request("/api/terminal/agents", method="HEAD")[0] == 200
    assert herdr.invocations() == []


@pytest.mark.parametrize("modules", [(), ("terminal",)])
def test_the_terminal_routes_keep_the_module_and_pack_guard(tmp_path, monkeypatch, modules):
    monkeypatch.delenv("KITTY_HERDR", raising=False)
    settings = make_root(tmp_path, modules)
    message = (str(ModuleDisabledError("terminal")), 403) if not modules else (str(packs.PackMissing("herdr")), 409)
    with front_site(settings) as running:
        for path in ("/api/terminal/agents", "/api/terminal/socket"):
            assert running.request(path) == (message[1], {"error": message[0]})

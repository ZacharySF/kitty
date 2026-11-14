"""Agent usage (kitty.core.agent_usage, kitty.web_usage) against synthetic session logs
in a fake HOME: each agent's log format, reading on from a cursor, numbers only in the
database, days and models with Rika's and the in-app AI's rows, and the routes' opt-in."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kitty.core import agent_usage as usage
from kitty.db import connect
from kitty.db.migrations import migrate
from terminal_support import make_root, site

FIXTURES = Path(__file__).parent / "fixtures" / "usage"
#: The day the fixtures' lines carry.
DAY = "2026-11-10"
CLAUDE = ".claude/projects/-work-essay/s-claude.jsonl"
SUBAGENT = ".claude/projects/-work-essay/s-claude/subagents/agent-1.jsonl"
OMP = ".omp/agent/sessions/-work-parser/2026-11-10T09-00-00_o-1.jsonl"
CODEX = ".codex/sessions/2026/11/10/rollout-2026-11-10T08-00-00-c-1.jsonl"
LOGS = {CLAUDE: "claude.jsonl", SUBAGENT: "claude-subagent.jsonl", OMP: "omp.jsonl", CODEX: "codex.jsonl"}


def lines(name: str, day: str = DAY) -> list[bytes]:
    return (FIXTURES / name).read_text().replace(DAY, day).encode().splitlines(keepends=True)


def install(home: Path, day: str = DAY, only: tuple[str, ...] = tuple(LOGS)) -> None:
    for target in only:
        (home / target).parent.mkdir(parents=True, exist_ok=True)
        (home / target).write_bytes(b"".join(lines(LOGS[target], day)))


def totals(reading: usage.Reading, role: str | None = None) -> tuple[int, int, int, int, int]:
    chosen = [request for request in reading.requests if role is None or request.role == role]
    return (sum(r.count for r in chosen), sum(r.input for r in chosen), sum(r.cache_read for r in chosen),
            sum(r.cache_write for r in chosen), sum(r.output for r in chosen))


def dollars(reading: usage.Reading) -> float:
    return round(sum(request.cost for request in reading.requests), 9)


@pytest.fixture
def conn(tmp_path):
    with closing(connect(tmp_path / "usage.db")) as opened:
        migrate(opened)
        yield opened


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return folder


# Formats --------------------------------------------------------------------------

def test_claude_counts_a_response_once_and_its_output_as_the_last_line_says():
    reading = usage.read_lines("claude", lines("claude.jsonl"), {})
    # msg_1 is two lines (output 5, then 120); msg_3 is a sidechain; <synthetic> is no request.
    assert totals(reading, "main") == (3, 170, 36100, 2300, 240)
    assert totals(reading, "subagent") == (1, 10, 0, 0, 20)
    # Sonnet 5.5 at list price, the 1 h cache write apart from the 5 min one; Haiku 5.5 for the sidechain.
    assert dollars(reading) == round((9650 + 2110 + 2590 + 11) / 1e6, 9)
    assert (reading.tool_calls, len(reading.changed)) == (4, 2)
    assert usage.path_hash("/work/essay/a.md") in reading.changed and usage.path_hash("/work/essay/z.md") not in reading.changed
    assert (reading.session, reading.cwd, reading.model) == ("s-claude", "/work/essay", "claude-sonnet-5-5")
    assert (reading.context_tokens, reading.context_window, reading.skipped) == (14320, 1_000_000, 1)


def test_claude_remembers_a_response_across_two_readings():
    state: dict = {}
    first = usage.read_lines("claude", lines("claude.jsonl")[:2], state)
    # The cursor survives as JSON between syncs.
    rest = usage.read_lines("claude", lines("claude.jsonl")[2:], json.loads(json.dumps(state)))
    assert totals(first) == (1, 100, 10000, 2000, 5)
    assert tuple(a + b for a, b in zip(totals(first), totals(rest))) == (4, 180, 36100, 2300, 260)
    assert first.tool_calls + rest.tool_calls == 4


def test_omp_reports_its_own_cost_and_counts_auxiliary_calls():
    reading = usage.read_lines("omp", lines("omp.jsonl"), {})
    assert totals(reading, "main") == (2, 40, 102000, 1000, 500)
    assert totals(reading, "aux") == (1, 200, 0, 0, 20)
    assert dollars(reading) == 0.03203
    # edit names its files in its patch headers, write in its path; both name src/a.py.
    assert (reading.tool_calls, reading.changed) == (3, {usage.path_hash("src/a.py"), usage.path_hash("src/b.py")})
    assert (reading.session, reading.cwd) == ("o-1", "/work/parser")
    assert (reading.context_tokens, reading.context_window) == (52010, 1_000_000)


def test_codex_counts_the_running_total_not_repeated_events():
    reading = usage.read_lines("codex", lines("codex.jsonl"), {})
    # Cached tokens are inside OpenAI's input: 1000 + (2500 - 2000) new, 2000 read.
    assert totals(reading) == (2, 1500, 2000, 0, 250)
    assert dollars(reading) == round((1500 * 2 + 2000 * 0.1 + 250 * 10) / 1e6, 9)
    assert (reading.tool_calls, len(reading.changed)) == (2, 2)
    assert (reading.session, reading.cwd, reading.model) == ("c-1", "/work/deck", "gpt-6.1-sol")
    assert (reading.context_tokens, reading.context_window) == (2650, 400_000)
    # A total that went down is a new count, not a negative one.
    state = {"total": {"input_tokens": 9000, "cached_input_tokens": 0, "output_tokens": 900}, "model": "gpt-6.1-sol"}
    assert totals(usage.read_lines("codex", lines("codex.jsonl")[3:4], state)) == (1, 1000, 0, 0, 100)


def test_prices_match_a_model_by_its_id_before_a_date_or_variant():
    assert usage.price("claude-haiku-4-5-20251001")["output"] == 5
    assert usage.price("anthropic/claude-opus-5-5")["input"] == 4
    assert usage.price("claude-opus-4-20250514")["input"] == 15
    assert usage.price("gpt-5.1-codex-max")["input"] == 1.25
    # A newer model is not priced as its older namesake.
    assert usage.price("claude-opus-4-9") is None and usage.price("gpt-5.7") is None and usage.price(None) is None
    assert usage.cost("some-new-model", input=1000) is None
    # Haiku 5.5 costs more for a prompt over 100 000 tokens.
    assert usage.cost("claude-haiku-5-5", input=50_000, output=1000) == pytest.approx(0.0055)
    assert usage.cost("claude-haiku-5-5", input=10_000, cache_read=100_000, output=1000) == pytest.approx(0.0125)


# Reading logs ---------------------------------------------------------------------

def test_sync_reads_on_from_its_cursor_and_keeps_numbers_only(conn, home, tmp_path):
    whole = lines("claude.jsonl")
    install(home, only=(SUBAGENT, OMP, CODEX))
    log = home / CLAUDE
    # The second line of msg_1 is still being written.
    log.write_bytes(b"".join(whole[:2]) + whole[2][:40])
    assert usage.sync(conn, home, usage.KINDS, environ={}) is False
    key = usage.file_key(log)
    assert usage.summary(conn, key)["output"] == 5 + 200
    assert conn.execute("SELECT offset FROM usage_files WHERE key=?", (key,)).fetchone()[0] == len(b"".join(whole[:2]))
    with open(log, "ab") as handle:
        handle.write(whole[2][40:] + b"".join(whole[3:]))
    assert usage.sync(conn, home, usage.KINDS, environ={}) is False
    # Nothing changed: a third sync opens no file and changes nothing.
    before = conn.execute("SELECT total_changes()").fetchone()[0]
    usage.sync(conn, home, usage.KINDS, environ={})
    assert conn.execute("SELECT total_changes()").fetchone()[0] == before

    claude, omp, codex = (usage.summary(conn, usage.file_key(home / path)) for path in (CLAUDE, OMP, CODEX))
    # The session counts its subagent's log with its own.
    assert {name: claude[name] for name in ("requests", "input", "cache_read", "cache_write", "output", "new_tokens")} == {
        "requests": 5, "input": 1180, "cache_read": 36100, "cache_write": 2300, "output": 460, "new_tokens": 3940}
    assert (claude["tool_calls"], claude["files_changed"], claude["context_pct"], claude["cwd_name"]) == (5, 3, 1, "essay")
    assert (claude["cost_usd"], claude["cost_source"]) == (0.014561, "priced")
    assert (claude["first_at"], claude["last_at"]) == (f"{DAY}T10:00:01Z", f"{DAY}T10:12:30Z")
    assert (omp["cost_usd"], omp["cost_source"], omp["context_pct"], omp["files_changed"]) == (0.03203, "reported", 5, 2)
    assert (codex["cost_usd"], codex["cost_source"], codex["new_tokens"], codex["cache_read"]) == (0.0057, "priced", 1750, 2000)
    assert [item["key"] for item in usage.sessions(conn)] == [claude["key"], omp["key"], codex["key"]]
    assert usage.skipped(conn) == 1

    detail = usage.session(conn, claude["key"])
    assert [(step["t"], step["input"], step["output"], step["cache"]) for step in detail["series"]] == [
        (f"{DAY}T10:00:00Z", 2100, 120, 10000), (f"{DAY}T10:05:00Z", 1060, 300, 12100), (f"{DAY}T10:10:00Z", 320, 40, 14000)]
    assert [(item["model"], item["role"], item["output"]) for item in detail["models"]] == [
        ("claude-sonnet-5-5", "main", 240), ("claude-haiku-5-5", "subagent", 220)]
    assert usage.session(conn)["key"] == claude["key"] and usage.session(conn, "0" * 16) is None

    # No message, title, tool argument or file name reaches the database.
    dump = "\n".join(conn.iterdump())
    assert "SECRET" not in dump and "a.md" not in dump and "src/a.py" not in dump

    # A log that was replaced by a shorter one is read again from its start, not added twice.
    log.write_bytes(b"".join(whole[:2]))
    usage.sync(conn, home, usage.KINDS, environ={})
    assert usage.summary(conn, key)["output"] == 5 + 200 and usage.summary(conn, key)["tool_calls"] == 1
    usage.forget(conn, "claude")
    assert [item["agent"] for item in usage.sessions(conn)] == ["omp", "codex"]
    assert conn.execute("SELECT count(*) FROM agent_usage u JOIN usage_files f ON f.key=u.file_key WHERE f.agent='claude'").fetchone()[0] == 0


def test_sync_stops_at_its_budget_and_finishes_a_long_log_in_steps(conn, home, monkeypatch):
    install(home, only=(CLAUDE, OMP))
    monkeypatch.setattr(usage, "CHUNK", 600)
    rounds = 0
    while usage.sync(conn, home, ("claude", "omp"), environ={}):
        rounds += 1
        assert rounds < 50
    assert rounds > 2
    assert usage.summary(conn, usage.file_key(home / CLAUDE))["output"] == 260
    assert usage.summary(conn, usage.file_key(home / OMP))["output"] == 520
    # With no time at all, nothing is read and the caller is told to come back.
    usage.forget(conn, "omp")
    assert usage.sync(conn, home, ("omp",), environ={}, budget=-1) is True and usage.sessions(conn)[0]["agent"] == "claude"


def test_only_the_kinds_asked_for_are_opened_and_folders_can_move(conn, home, tmp_path):
    install(home)
    usage.sync(conn, home, ("omp",), environ={})
    assert [item["agent"] for item in usage.sessions(conn)] == ["omp"]
    moved = tmp_path / "elsewhere"
    (moved / "projects/-work-essay").mkdir(parents=True)
    (moved / "projects/-work-essay/s-moved.jsonl").write_bytes(b"".join(lines("claude.jsonl")))
    usage.sync(conn, home, ("claude",), environ={"CLAUDE_CONFIG_DIR": str(moved)})
    assert [(item["agent"], item["output"]) for item in usage.sessions(conn)] == [("claude", 260), ("omp", 520)]


# Days -----------------------------------------------------------------------------

def test_report_sums_days_per_source_in_the_calendar_zone(conn, home):
    install(home)
    usage.sync(conn, home, usage.KINDS, environ={})
    rika = "INSERT INTO rika_messages(id,conversation_id,role,text,model,cost_usd,tokens_in,tokens_cached,tokens_out,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)"
    conn.execute(rika, ("r1", "c", "rika", "x", "openai/gpt-6.1-terra", "0.002", 1000, 400, 50, "2026-11-11T03:00:00+00:00"))
    conn.execute(rika, ("r2", "c", "rika", "x", "deterministic", None, None, None, None, "2026-11-11T03:01:00+00:00"))
    conn.execute(rika, ("r3", "c", "user", "x", None, None, None, None, None, "2026-11-11T03:02:00+00:00"))
    ledger = "INSERT INTO ai_reviews(id,purpose,plan_json,fingerprint,expires_at,created_at,state,reserved_usd,actual_usd) VALUES(?,?,?,'f','e',?,?,?,?)"
    conn.execute(ledger, ("a1", "summarize", '{"model":"m/one","text":"SECRET"}', "2026-11-12T08:00:00+00:00", "completed", "0.05", "0.01"))
    conn.execute(ledger, ("a2", "summarize", '{"model":"m/one"}', "2026-11-12T09:00:00+00:00", "failed", "0.05", None))
    # Rika's ledger row repeats her reply's cost.
    conn.execute(ledger, ("a3", "rika", '{"model":"openai/gpt-6.1-terra"}', "2026-11-11T03:00:00+00:00", "completed", "0.01", "0.002"))

    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    found = usage.report(conn, 7, now=now, zone=timezone.utc)
    assert [item["day"] for item in found["days"]] == [f"2026-11-{day:02}" for day in range(6, 13)]
    days = {item["day"]: item["sources"] for item in found["days"]}
    assert days[DAY]["claude"] == {"input": 1180, "cache_read": 36100, "cache_write": 2300, "output": 460, "cost_usd": 0.014561}
    assert days[DAY]["omp"]["cost_usd"] == 0.03203 and days[DAY]["codex"]["input"] == 1500
    assert days["2026-11-11"]["rika"] == {"input": 600, "cache_read": 400, "cache_write": 0, "output": 50, "cost_usd": 0.002}
    assert days["2026-11-12"]["ai"] == {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost_usd": 0.06}
    assert found["total"]["cost_usd"] == pytest.approx(0.014561 + 0.03203 + 0.0057 + 0.002 + 0.06)
    assert found["total"]["output"] == 460 + 520 + 250 + 50
    assert [(item["source"], item["model"]) for item in found["models"]] == [
        ("omp", "claude-opus-5-5"), ("claude", "claude-sonnet-5-5"), ("codex", "gpt-6.1-sol"), ("claude", "claude-haiku-5-5"),
        ("rika", "openai/gpt-6.1-terra"), ("omp", "claude-haiku-5-5"), ("ai", "m/one")]
    # 03:00 UTC is the evening before in Chicago; a window of two days no longer holds the 10th.
    chicago = {item["day"]: item["sources"] for item in usage.report(conn, 7, now=now, zone=ZoneInfo("America/Chicago"))["days"]}
    assert chicago[DAY]["rika"]["output"] == 50 and chicago["2026-11-11"]["rika"]["output"] == 0
    assert usage.report(conn, 2, now=now, zone=timezone.utc)["total"]["output"] == 50


# Routes ---------------------------------------------------------------------------

def recent() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()


def test_logs_are_read_only_for_a_kind_that_is_on(tmp_path, home):
    install(home, recent())
    settings = make_root(tmp_path, ())
    with site(settings) as running:
        status, body = running.request("/api/usage")
        assert status == 200 and body["sessions"] == [] and body["scanning"] is False and len(body["days"]) == 30
        assert [(kind["kind"], kind["enabled"]) for kind in body["kinds"]] == [("omp", False), ("claude", False), ("codex", False)]
        assert running.request("/api/usage/session") == (200, {"scanning": False, "session": None})
        with sqlite3.connect(settings.db_path) as conn:
            assert conn.execute("SELECT count(*) FROM usage_files").fetchone()[0] == 0

        status, body = running.request("/api/usage/settings", {"kind": "claude", "enabled": True})
        assert status == 200 and [item["agent"] for item in body["sessions"]] == ["claude"]
        session = body["sessions"][0]
        assert (session["cwd_name"], session["tool_calls"], session["files_changed"], session["new_tokens"]) == ("essay", 5, 3, 3940)
        assert str(home) not in json.dumps(body) and "/work/essay" not in json.dumps(body)
        day = next(item for item in body["days"] if item["day"] == recent())
        assert day["sources"]["claude"]["output"] == 460 and day["sources"]["omp"]["output"] == 0
        assert running.request("/api/usage?days=1")[1]["total"]["output"] == 0

        status, detail = running.request(f"/api/usage/session?key={session['key']}")
        assert status == 200 and detail["session"]["key"] == session["key"] and len(detail["session"]["series"]) == 3
        assert running.request("/api/usage/session")[1]["session"]["key"] == session["key"]
        assert running.request("/api/usage/session?key=" + "0" * 16)[0] == 404
        for path in ("/api/usage?days=0", "/api/usage?days=91", "/api/usage?days=x", "/api/usage?limit=3",
                     "/api/usage/session?key=../etc", "/api/usage/session?key=ABC", "/api/usage/session?path=/etc/passwd"):
            assert running.request(path)[0] == 400, path
        for bad in ({"kind": "sh", "enabled": True}, {"kind": "omp", "enabled": "yes"}, {"kind": "omp"}):
            assert running.request("/api/usage/settings", bad)[0] == 400
        assert json.loads((settings.root / ".private/agents/usage.json").read_text()) == {
            "enabled": {"claude": True, "codex": False, "omp": False}}

        # Switching the kind off drops what was read from its logs.
        status, body = running.request("/api/usage/settings", {"kind": "claude", "enabled": False})
        assert status == 200 and body["sessions"] == []
        with sqlite3.connect(settings.db_path) as conn:
            assert conn.execute("SELECT count(*) FROM usage_files").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM agent_usage").fetchone()[0] == 0
    # The switch is remembered by the next launch.
    with site(settings) as running:
        running.request("/api/usage/settings", {"kind": "omp", "enabled": True})
    with site(settings) as running:
        assert [item["agent"] for item in running.request("/api/usage")[1]["sessions"]] == ["omp"]


def test_a_changed_log_is_read_again_only_after_the_refresh_time(tmp_path, home, monkeypatch):
    from kitty import web_usage

    install(home, recent(), only=(OMP,))
    with site(make_root(tmp_path, ())) as running:
        running.request("/api/usage/settings", {"kind": "omp", "enabled": True})
        assert running.request("/api/usage/session")[1]["session"]["output"] == 520
        more = json.loads(lines("omp.jsonl", recent())[-1])
        more["id"] = "m4"
        with open(home / OMP, "ab") as handle:
            handle.write(json.dumps(more).encode() + b"\n")
        assert running.request("/api/usage/session")[1]["session"]["output"] == 520
        monkeypatch.setattr(web_usage, "REFRESH_SECONDS", 0.0)
        assert running.request("/api/usage/session")[1]["session"]["output"] == 620


def test_a_running_agent_card_carries_its_sessions_usage(tmp_path, home):
    from kitty.web_usage import Usage

    install(home, recent(), only=(CLAUDE, SUBAGENT))
    settings = make_root(tmp_path, ())
    with site(settings) as running:
        cards: Usage = running.app.usage
        agent = {"id": "tern:4", "kind": "claude", "cwd": "/work/essay"}
        assert cards.card(agent) is None
        running.request("/api/usage/settings", {"kind": "claude", "enabled": True})
        found = cards.card(agent)
        assert (found["context_pct"], found["new_tokens"], found["cache_read"], found["tool_calls"], found["files_changed"]) == (1, 3940, 36100, 5, 3)
        assert cards.card({"id": "tern:5", "kind": "claude", "cwd": "/work/other"}) is None
        assert cards.card({"id": "tern:6", "kind": "sh", "cwd": "/work/essay"}) is None

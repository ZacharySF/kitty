"""Agent usage: tokens, cost, tool calls and files changed per coding-agent session.

Claude Code, Codex and omp each keep a JSON-lines log per session. ``sync`` reads the
logs of the kinds the user turned on, from where it stopped last time, into two tables
(migration 058): ``usage_files`` holds one row per log with its cursor and the session's
running totals, ``agent_usage`` its requests summed per five minutes, model and role.
Only numbers, model ids, the session id and the working directory are kept; a path an
edit tool named is kept as a hash, so files changed can be counted. No prompt, reply,
title or tool argument is stored, and the page sees a log only as an opaque key.

The formats are not stable interfaces: a line that does not parse is skipped and
counted, and a log that shrank or was replaced is read again from its start.

Cost is the agent's own figure when its log carries one (omp) and otherwise an estimate
at list price from ``agent_prices.json``; a model the table does not know has no cost.

Rika's replies and the in-app AI are not copied: ``report`` reads them where they are
(``rika_messages``, ``ai_reviews``, ``llm_cache``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kitty.core.agent_skills import KINDS, LABELS
from kitty.db.connection import transaction

#: Seconds one row of ``agent_usage`` spans.
BUCKET = 300
#: Bytes read from one log per sync; a longer log is finished by the next ones.
CHUNK = 4 * 1024 * 1024
#: A log untouched for longer is not picked up (one already read keeps its rows).
MAX_AGE_DAYS = 90
#: Request and tool-call ids a Claude cursor remembers, to count a repeated line once.
RECENT = 64
#: The sources of a day, in the order the page stacks them.
SOURCES = ("rika", "ai", "claude", "codex", "omp")
#: Bytes a line must hold to be parsed at all: most lines are message text.
_MARKS = {"claude": (b'"usage"',), "omp": (b'"usage"', b'"session"'),
          "codex": (b"token_count", b"session_meta", b"turn_context", b'_call"')}
_CLAUDE_EDITS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_OMP_EDIT_HEADER = re.compile(r"^\[([^\]\n]+?)#[0-9A-Fa-f]{4}\]\s*$", re.M)
_CODEX_PATCH_FILE = re.compile(r"\*\*\* (?:Add|Update|Delete) File: ([^\n\\\"]+)")
_CODEX_CALLS = frozenset({"function_call", "custom_tool_call", "local_shell_call"})
#: What may follow a price-table key in a model id: a date, a variant word or nothing.
_SUFFIX = re.compile(r"(-\d{8}.*|-[a-z].*|[@\[:].*)?", re.I)
_FIELDS = ("input", "cache_read", "cache_write", "output")


# Prices ---------------------------------------------------------------------------

@cache
def _prices() -> dict[str, dict[str, Any]]:
    with open(Path(__file__).with_name("agent_prices.json"), encoding="utf-8") as handle:
        return json.load(handle)["models"]


def price(model: Any) -> dict[str, Any] | None:
    """The price-table entry for a model id (the longest key it starts with), or None."""
    if not isinstance(model, str):
        return None
    name = model.rsplit("/", 1)[-1].lower()
    best = None
    for key in _prices():
        if name.startswith(key) and _SUFFIX.fullmatch(name[len(key):]) and (best is None or len(key) > len(best)):
            best = key
    return _prices()[best] if best else None


def context_window(model: Any) -> int | None:
    entry = price(model)
    return entry.get("context") if entry else None


def cost(model: Any, *, input: int = 0, cache_read: int = 0, write: int = 0, write_long: int = 0, output: int = 0,
         prompt: int | None = None) -> float | None:
    """Dollars at list price, or None for a model the table does not know. ``prompt`` is
    the request's whole prompt, for a model priced by prompt length."""
    entry = price(model)
    if entry is None:
        return None
    over = entry.get("over")
    size = input + cache_read + write + write_long if prompt is None else prompt
    rates = {**entry, **over} if over and size > over["tokens"] else entry
    short = rates.get("write", rates["input"])
    return (input * rates["input"] + cache_read * rates.get("cache_read", rates["input"]) + write * short
            + write_long * rates.get("write_long", short) + output * rates["output"]) / 1_000_000


# Parsing --------------------------------------------------------------------------

@dataclass
class Request:
    """One model request, or the rest of one a repeated line completed (``count`` 0)."""
    t: int
    model: str
    role: str
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    cost: float | None = None
    count: int = 1


@dataclass
class Reading:
    """What a run of log lines added to a session."""
    requests: list[Request] = field(default_factory=list)
    tool_calls: int = 0
    #: Hashes of the paths edit tools named.
    changed: set[str] = field(default_factory=set)
    context_tokens: int | None = None
    context_window: int | None = None
    model: str | None = None
    session: str | None = None
    cwd: str | None = None
    skipped: int = 0


def _count(value: Any) -> int:
    return value if type(value) is int and value > 0 else 0


def _seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp())


def path_hash(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8", "replace")).hexdigest()[:12]


def _remember(seen: dict, key: str, value: Any) -> None:
    seen[key] = value
    while len(seen) > RECENT:
        del seen[next(iter(seen))]


def _claude(entry: dict, state: dict, reading: Reading, subagent: bool) -> None:
    message = entry.get("message")
    if entry.get("type") != "assistant" or not isinstance(message, dict):
        return
    usage, model, at = message.get("usage"), message.get("model"), _seconds(entry.get("timestamp"))
    if not isinstance(usage, dict) or not isinstance(model, str) or model.startswith("<") or at is None:
        return
    if isinstance(entry.get("sessionId"), str):
        reading.session = entry["sessionId"]
    if isinstance(entry.get("cwd"), str):
        reading.cwd = entry["cwd"]
    tools = state.setdefault("tools", {})
    content = message.get("content")
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        ident = block.get("id")
        if isinstance(ident, str):
            if ident in tools:
                continue
            _remember(tools, ident, 1)
        reading.tool_calls += 1
        arguments = block.get("input")
        if block.get("name") in _CLAUDE_EDITS and isinstance(arguments, dict):
            target = arguments.get("file_path") or arguments.get("notebook_path")
            if isinstance(target, str) and target:
                reading.changed.add(path_hash(target))
    role = "subagent" if subagent or entry.get("isSidechain") is True else "main"
    output = _count(usage.get("output_tokens"))
    # One response is written as a line per content block, each with the response's usage;
    # the output count can grow from line to line. Count the request once and its output
    # as far as the last line says.
    ident = f"{message.get('id')}:{entry.get('requestId')}" if message.get("id") or entry.get("requestId") else None
    seen = state.setdefault("recent", {})
    if ident is not None and ident in seen:
        more = output - seen[ident]
        if more > 0:
            seen[ident] = output
            reading.requests.append(Request(at, model, role, output=more, cost=cost(model, output=more), count=0))
        return
    if ident is not None:
        _remember(seen, ident, output)
    fresh, read, written = (_count(usage.get(key)) for key in
                            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    split = usage.get("cache_creation")
    long = min(written, _count(split.get("ephemeral_1h_input_tokens"))) if isinstance(split, dict) else 0
    prompt = fresh + read + written
    reading.requests.append(Request(at, model, role, fresh, read, written, output,
                                    cost(model, input=fresh, cache_read=read, write=written - long, write_long=long, output=output)))
    if role == "main":
        window = context_window(model)
        reading.model, reading.context_tokens = model, prompt
        # Claude Code can run a model on its long window without the log saying so.
        reading.context_window = 1_000_000 if window and prompt > window else window


def _omp(entry: dict, state: dict, reading: Reading, subagent: bool) -> None:
    kind = entry.get("type")
    if kind == "session":
        if isinstance(entry.get("id"), str):
            reading.session = entry["id"]
        if isinstance(entry.get("cwd"), str):
            reading.cwd = entry["cwd"]
        return
    message = entry.get("message") if kind == "message" else entry if kind == "model_usage" else None
    if not isinstance(message, dict) or (kind == "message" and message.get("role") != "assistant"):
        return
    usage, model, at = message.get("usage"), message.get("model"), _seconds(entry.get("timestamp"))
    if not isinstance(usage, dict) or not isinstance(model, str) or at is None:
        return
    fresh, read, written, output = (_count(usage.get(key)) for key in ("input", "cacheRead", "cacheWrite", "output"))
    reported = usage.get("cost")
    total = reported.get("total") if isinstance(reported, dict) else None
    dollars = float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) and total >= 0 else None
    role = "aux" if kind == "model_usage" else "subagent" if subagent else "main"
    reading.requests.append(Request(at, model, role, fresh, read, written, output, dollars))
    if kind != "message":
        return
    reading.model, reading.context_tokens, reading.context_window = model, fresh + read + written, context_window(model)
    content = message.get("content")
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get("type") != "toolCall":
            continue
        reading.tool_calls += 1
        arguments = block.get("arguments")
        if block.get("name") not in ("edit", "write") or not isinstance(arguments, dict):
            continue
        if isinstance(arguments.get("path"), str) and arguments["path"]:
            reading.changed.add(path_hash(arguments["path"]))
        if isinstance(arguments.get("input"), str):
            reading.changed.update(path_hash(found) for found in _OMP_EDIT_HEADER.findall(arguments["input"]))


def _codex(entry: dict, state: dict, reading: Reading, subagent: bool) -> None:
    payload, kind = entry.get("payload"), entry.get("type")
    if not isinstance(payload, dict):
        return
    if kind in ("session_meta", "turn_context"):
        if kind == "session_meta" and isinstance(payload.get("id"), str):
            reading.session = payload["id"]
        if isinstance(payload.get("cwd"), str):
            reading.cwd = payload["cwd"]
        if isinstance(payload.get("model"), str):
            state["model"] = payload["model"]
        return
    if kind == "response_item" and payload.get("type") in _CODEX_CALLS:
        reading.tool_calls += 1
        if payload.get("name") == "apply_patch":
            patch = payload.get("input") if isinstance(payload.get("input"), str) else payload.get("arguments")
            if isinstance(patch, str):
                reading.changed.update(path_hash(found.strip()) for found in _CODEX_PATCH_FILE.findall(patch))
        return
    info = payload.get("info")
    at = _seconds(entry.get("timestamp"))
    if kind != "event_msg" or payload.get("type") != "token_count" or not isinstance(info, dict) or at is None:
        return
    total, last = info.get("total_token_usage"), info.get("last_token_usage")
    if not isinstance(total, dict):
        return
    # Codex repeats the last request's usage on updates that carry only rate limits, so the
    # running total is read and the difference counted.
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    now = {key: _count(total.get(key)) for key in keys}
    before = state.get("total") if isinstance(state.get("total"), dict) else {}
    if any(now[key] < _count(before.get(key)) for key in keys):
        before = {}
    prompt, cached, output = (now[key] - _count(before.get(key)) for key in keys)
    state["total"] = now
    model = state.get("model") if isinstance(state.get("model"), str) else "codex"
    if isinstance(last, dict):
        reading.model = model
        reading.context_tokens = _count(last.get("total_tokens")) or _count(last.get("input_tokens")) + _count(last.get("output_tokens"))
        reading.context_window = _count(info.get("model_context_window")) or context_window(model)
    if not (prompt or cached or output):
        return
    # OpenAI counts cached tokens inside the input.
    fresh = max(0, prompt - cached)
    size = _count(last.get("input_tokens")) if isinstance(last, dict) else None
    reading.requests.append(Request(at, model, "subagent" if subagent else "main", fresh, cached, 0, output,
                                    cost(model, input=fresh, cache_read=cached, output=output, prompt=size or None)))


_PARSERS = {"claude": _claude, "omp": _omp, "codex": _codex}


def read_lines(kind: str, lines: Iterable[bytes], state: dict, *, subagent: bool = False) -> Reading:
    """What whole log lines of one agent kind add. ``state`` is the log's cursor, kept
    between calls (JSON-able): the ids already counted and Codex's running total."""
    parse, marks, reading = _PARSERS[kind], _MARKS[kind], Reading()
    for line in lines:
        if not any(mark in line for mark in marks):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            reading.skipped += 1
            continue
        if isinstance(entry, dict):
            try:
                parse(entry, state, reading, subagent)
            except (TypeError, ValueError, AttributeError, KeyError):
                reading.skipped += 1
    return reading


# Reading the logs -----------------------------------------------------------------

def file_key(path: Path | str) -> str:
    """The opaque name the page knows a log by."""
    return hashlib.sha256(str(path).encode("utf-8", "replace")).hexdigest()[:16]


def log_files(kind: str, home: Path, environ: Mapping[str, str] | None = None) -> list[Path]:
    """Every session log of one agent kind."""
    environ = os.environ if environ is None else environ
    if kind == "claude":
        root = Path(environ.get("CLAUDE_CONFIG_DIR") or home / ".claude") / "projects"
        return [*root.glob("*/*.jsonl"), *root.glob("*/*/subagents/*.jsonl")]
    if kind == "codex":
        return list((Path(environ.get("CODEX_HOME") or home / ".codex") / "sessions").glob("*/*/*/rollout-*.jsonl"))
    return list((home / ".omp" / "agent" / "sessions").glob("*/*.jsonl"))


def _whole_lines(handle, limit: int):
    """Complete lines from the handle's place up to about `limit` bytes; it is left at the
    end of the last one, so a line still being written is read next time."""
    start = at = handle.tell()
    while at - start < limit:
        line = handle.readline()
        if not line.endswith(b"\n"):
            break
        at += len(line)
        yield line
    handle.seek(at)


def _read_file(conn: sqlite3.Connection, kind: str, path: Path, known: sqlite3.Row | None) -> bool:
    """Read one log on from its cursor; True when more of it is left."""
    key = file_key(path)
    with open(path, "rb") as handle:
        status = os.fstat(handle.fileno())
        fresh = known is None or known["inode"] != status.st_ino or status.st_size < known["offset"]
        state = {} if fresh else json.loads(known["state"])
        handle.seek(0 if fresh else known["offset"])
        subagent = kind == "claude" and path.parent.name == "subagents"
        reading = read_lines(kind, _whole_lines(handle, CHUNK), state, subagent=subagent)
        offset = handle.tell()
        more = offset < status.st_size and handle.readline().endswith(b"\n")
    # A log read only in part keeps a size that is not the file's, so the next sync opens it again.
    size = offset if more else status.st_size
    buckets: dict[tuple[int, str, str], list[float]] = {}
    for request in reading.requests:
        sums = buckets.setdefault((request.t - request.t % BUCKET, request.model, request.role), [0, 0, 0, 0, 0, 0, 0.0])
        sums[0] += request.count
        sums[1] += request.count if request.cost is not None else 0
        for index, name in enumerate(_FIELDS):
            sums[2 + index] += getattr(request, name)
        sums[6] += request.cost or 0.0
    times = [request.t for request in reading.requests]
    with transaction(conn):
        if fresh and known is not None:
            conn.execute("DELETE FROM usage_files WHERE key=?", (known["key"],))
        if fresh:
            parent = file_key(str(path.parent.parent) + ".jsonl") if subagent else None
            conn.execute("INSERT INTO usage_files(key,path,agent,parent,inode,size,mtime_ns) VALUES(?,?,?,?,?,0,0)",
                         (key, str(path), kind, parent, status.st_ino))
            changed = reading.changed
        else:
            changed = reading.changed | set(json.loads(known["changed"]))
        conn.execute(
            "UPDATE usage_files SET inode=:inode,size=:size,mtime_ns=:mtime,offset=:offset,state=:state,changed=:changed,"
            " tool_calls=tool_calls+:tools,skipped=skipped+:skipped,session=coalesce(:session,session),cwd=coalesce(:cwd,cwd),"
            " model=coalesce(:model,model),context_tokens=coalesce(:context,context_tokens),"
            " context_window=CASE WHEN :context IS NULL THEN context_window ELSE :window END,"
            " first_t=CASE WHEN first_t IS NULL OR :first<first_t THEN coalesce(:first,first_t) ELSE first_t END,"
            " last_t=CASE WHEN last_t IS NULL OR :last>last_t THEN coalesce(:last,last_t) ELSE last_t END WHERE key=:key",
            {"inode": status.st_ino, "size": size, "mtime": status.st_mtime_ns, "offset": offset,
             "state": json.dumps(state, separators=(",", ":")), "changed": json.dumps(sorted(changed)),
             "tools": reading.tool_calls, "skipped": reading.skipped, "session": reading.session, "cwd": reading.cwd,
             "model": reading.model, "context": reading.context_tokens, "window": reading.context_window,
             "first": min(times) if times else None, "last": max(times) if times else None, "key": key})
        conn.executemany(
            "INSERT INTO agent_usage(file_key,t,model,role,requests,priced,input,cache_read,cache_write,output,cost_usd)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(file_key,t,model,role) DO UPDATE SET"
            " requests=requests+excluded.requests,priced=priced+excluded.priced,input=input+excluded.input,"
            " cache_read=cache_read+excluded.cache_read,cache_write=cache_write+excluded.cache_write,"
            " output=output+excluded.output,cost_usd=cost_usd+excluded.cost_usd",
            [(key, *bucket, *sums) for bucket, sums in buckets.items()])
    return more


def sync(conn: sqlite3.Connection, home: Path, kinds: Iterable[str], *, environ: Mapping[str, str] | None = None,
         budget: float = 0.25) -> bool:
    """Read on in every log of `kinds` that changed, for about `budget` seconds. True
    while logs are left unread: the caller asks again."""
    deadline = time.monotonic() + budget
    oldest = time.time() - MAX_AGE_DAYS * 86400
    known = {row["path"]: row for row in conn.execute("SELECT * FROM usage_files")}
    for kind in kinds:
        for path in log_files(kind, home, environ):
            row = known.get(str(path))
            try:
                status = path.stat()
                if row is None and status.st_mtime < oldest:
                    continue
                if row is not None and (row["inode"], row["size"], row["mtime_ns"]) == (status.st_ino, status.st_size, status.st_mtime_ns):
                    continue
                if time.monotonic() > deadline:
                    return True
                if _read_file(conn, kind, path, row):
                    return True
            except (OSError, ValueError):
                continue  # Unreadable now, or a cursor that no longer parses: the next sync retries.
    return False


def forget(conn: sqlite3.Connection, kind: str) -> None:
    """Drop everything read from one agent kind's logs (its switch went off)."""
    with transaction(conn):
        conn.execute("DELETE FROM usage_files WHERE agent=?", (kind,))


# Answers --------------------------------------------------------------------------

def _iso(seconds: int | None) -> str | None:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z") if seconds is not None else None


_SUMS = ("coalesce(sum(requests),0) AS requests, coalesce(sum(priced),0) AS priced, coalesce(sum(input),0) AS input,"
         " coalesce(sum(cache_read),0) AS cache_read, coalesce(sum(cache_write),0) AS cache_write,"
         " coalesce(sum(output),0) AS output, coalesce(sum(cost_usd),0) AS cost_usd")
_OF_SESSION = "file_key IN (SELECT key FROM usage_files WHERE key=:key OR parent=:key)"


def _money(agent: str, priced: int, dollars: float) -> dict[str, Any]:
    if not priced:
        return {"cost_usd": None, "cost_source": "none"}
    return {"cost_usd": round(dollars, 6), "cost_source": "reported" if agent == "omp" else "priced"}


def _summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """A session's totals: its own log and its subagents' logs."""
    sums = conn.execute(f"SELECT {_SUMS} FROM agent_usage WHERE {_OF_SESSION}", {"key": row["key"]}).fetchone()
    family = conn.execute("SELECT tool_calls, changed FROM usage_files WHERE key=:key OR parent=:key", {"key": row["key"]}).fetchall()
    window, used = row["context_window"], row["context_tokens"]
    return {
        "key": row["key"], "agent": row["agent"], "label": LABELS[row["agent"]],
        "cwd_name": Path(row["cwd"]).name if row["cwd"] else None,
        "first_at": _iso(row["first_t"]), "last_at": _iso(row["last_t"]), "model": row["model"],
        "context_tokens": used, "context_window": window,
        "context_pct": min(100, round(100 * used / window)) if used is not None and window else None,
        **{name: sums[name] for name in ("requests", *_FIELDS)},
        "new_tokens": sums["input"] + sums["cache_write"] + sums["output"],
        "tool_calls": sum(item["tool_calls"] for item in family),
        "files_changed": len({name for item in family for name in json.loads(item["changed"])}),
        **_money(row["agent"], sums["priced"], sums["cost_usd"]),
    }


def sessions(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """The sessions used last, newest first."""
    rows = conn.execute("SELECT * FROM usage_files WHERE parent IS NULL AND last_t IS NOT NULL ORDER BY last_t DESC, key LIMIT ?", (limit,))
    return [_summary(conn, row) for row in rows.fetchall()]


def summary(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM usage_files WHERE key=? AND last_t IS NOT NULL", (key,)).fetchone()
    return _summary(conn, row) if row else None


def session(conn: sqlite3.Connection, key: str | None = None, series: int = 288) -> dict[str, Any] | None:
    """One session (the one used last without a key): its totals, its models and its
    last `series` five-minute steps for the sparkline."""
    if key is None:
        row = conn.execute("SELECT * FROM usage_files WHERE parent IS NULL AND last_t IS NOT NULL ORDER BY last_t DESC, key LIMIT 1").fetchone()
    else:
        row = conn.execute("SELECT * FROM usage_files WHERE key=? AND last_t IS NOT NULL", (key,)).fetchone()
    if row is None:
        return None
    models = conn.execute(f"SELECT model, role, {_SUMS} FROM agent_usage WHERE {_OF_SESSION} GROUP BY model, role"
                          " ORDER BY sum(input+cache_read+cache_write+output) DESC, model, role", {"key": row["key"]}).fetchall()
    steps = conn.execute(f"SELECT t, sum(input+cache_write) AS input, sum(output) AS output, sum(cache_read) AS cache FROM agent_usage"
                         f" WHERE {_OF_SESSION} GROUP BY t ORDER BY t DESC LIMIT :limit", {"key": row["key"], "limit": series}).fetchall()
    return {
        **_summary(conn, row),
        "models": [{"model": item["model"], "role": item["role"], **{name: item[name] for name in _FIELDS},
                    **_money(row["agent"], item["priced"], item["cost_usd"])} for item in models],
        "series": [{"t": _iso(item["t"]), "input": item["input"], "output": item["output"], "cache": item["cache"]}
                   for item in reversed(steps)],
    }


def calendar_zone(conn: sqlite3.Connection) -> tzinfo | None:
    """The calendar's time zone, as Rika reads it; None is the machine's."""
    try:
        row = conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()
        return ZoneInfo(row["timezone"]) if row and row["timezone"] else None
    except (sqlite3.Error, ValueError, OSError, KeyError):
        return None


def _stamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _dollars(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _own(conn: sqlite3.Connection, since: str):
    """Rika's replies and the in-app AI since an ISO time: (source, at, model, input,
    cache_read, output, dollars). Rika's ledger rows repeat her replies' cost and are left out."""
    for row in conn.execute("SELECT created_at, model, tokens_in, tokens_cached, tokens_out, cost_usd FROM rika_messages"
                            " WHERE role='rika' AND created_at>=? AND (tokens_in IS NOT NULL OR cost_usd IS NOT NULL)", (since,)):
        cached = _count(row["tokens_cached"])
        yield ("rika", row["created_at"], row["model"], max(0, _count(row["tokens_in"]) - cached), cached,
               _count(row["tokens_out"]), _dollars(row["cost_usd"]))
    for row in conn.execute("SELECT created_at, json_extract(plan_json,'$.model') AS model, coalesce(actual_usd,reserved_usd) AS spent"
                            " FROM ai_reviews WHERE purpose<>'rika' AND created_at>=? AND state<>'pending'", (since,)):
        yield ("ai", row["created_at"], row["model"], 0, 0, 0, _dollars(row["spent"]))
    for row in conn.execute("SELECT created_at, model, input_tokens, output_tokens, cost_usd FROM llm_cache WHERE created_at>=?", (since,)):
        yield ("ai", row["created_at"], row["model"], _count(row["input_tokens"]), 0, _count(row["output_tokens"]), _dollars(row["cost_usd"]))


def _blank() -> dict[str, Any]:
    return {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost_usd": 0.0}


def _add(into: dict[str, Any], input: int, cache_read: int, cache_write: int, output: int, dollars: float | None) -> None:
    into["input"] += input
    into["cache_read"] += cache_read
    into["cache_write"] += cache_write
    into["output"] += output
    into["cost_usd"] += dollars or 0.0


def report(conn: sqlite3.Connection, days: int = 30, *, now: datetime | None = None, zone: tzinfo | None = None) -> dict[str, Any]:
    """The last `days` days, oldest first, each with what every source spent; the models
    of that time, largest first; and the period's total. Days are the calendar zone's."""
    zone = zone or calendar_zone(conn)
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    first = today - timedelta(days=days - 1)
    # A day early in UTC, so the zone's first day is whole.
    since = datetime(first.year, first.month, first.day, tzinfo=timezone.utc) - timedelta(days=1)
    by_day = {(first + timedelta(days=index)).isoformat(): {source: _blank() for source in SOURCES} for index in range(days)}
    models: dict[tuple[str, str], dict[str, Any]] = {}

    def count(source: str, at: datetime | None, model: Any, *numbers) -> None:
        day = by_day.get(at.astimezone(zone).date().isoformat()) if at else None
        if day is None:
            return
        _add(day[source], *numbers)
        _add(models.setdefault((source, str(model or "unknown")), _blank()), *numbers)

    for source, at, model, fresh, cached, output, dollars in _own(conn, since.isoformat()):
        count(source, _stamp(at), model, fresh, cached, 0, output, dollars)
    rows = conn.execute("SELECT f.agent, u.t, u.model, sum(u.input) AS input, sum(u.cache_read) AS cache_read,"
                        " sum(u.cache_write) AS cache_write, sum(u.output) AS output, sum(u.cost_usd) AS cost_usd"
                        " FROM agent_usage u JOIN usage_files f ON f.key=u.file_key WHERE u.t>=? GROUP BY f.agent, u.t, u.model",
                        (int(since.timestamp()),))
    for row in rows:
        count(row["agent"], datetime.fromtimestamp(row["t"], timezone.utc), row["model"],
              *(row[name] for name in _FIELDS), row["cost_usd"])
    total = _blank()
    for sources in by_day.values():
        for sums in sources.values():
            _add(total, *(sums[name] for name in _FIELDS), sums["cost_usd"])
            sums["cost_usd"] = round(sums["cost_usd"], 6)
    total["cost_usd"] = round(total["cost_usd"], 6)
    ranked = sorted(models.items(), key=lambda item: (-sum(item[1][name] for name in _FIELDS), -item[1]["cost_usd"], item[0]))
    return {
        "days": [{"day": day, "sources": sources} for day, sources in by_day.items()],
        "total": total,
        "models": [{"source": source, "model": model, **{**sums, "cost_usd": round(sums["cost_usd"], 6)}}
                   for (source, model), sums in ranked],
    }


def skipped(conn: sqlite3.Connection) -> int:
    """Log lines that did not parse, over every log read."""
    return conn.execute("SELECT coalesce(sum(skipped),0) FROM usage_files").fetchone()[0]

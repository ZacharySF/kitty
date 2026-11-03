"""Timed mental-arithmetic sprints with Zetamac's defaults.

Addition is (2–100) + (2–100), subtraction is addition reversed, multiplication is
(2–12) × (2–100) and division is multiplication reversed. ``zetamac`` takes a typed
answer the moment it is right; ``mc`` offers four choices and scores right − wrong.
Problems come from a stored seed, so the browser checks answers with no round trip and
the server can regenerate any run.
"""
from __future__ import annotations

import json
import random
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kitty.core.math.practice import Conflict, NotFound
from kitty.db.connection import transaction

MODES = ("zetamac", "mc")
OPS = ("add", "sub", "mul", "div")
DEFAULT_DURATION = 120
#: Problems issued per second of sprint: well beyond the fastest Zetamac pace.
RATE = 2
SLACK_MS = 5000


def now() -> datetime:
    from kitty.core.math.mastery import now as clock
    return clock()


def problems(mode: str, seed: int, ops, count: int) -> list[dict]:
    rng = random.Random(f"sprint:{mode}:{seed}")
    out, previous = [], None
    while len(out) < count:
        op = rng.choice(ops)
        if op in ("add", "sub"):
            a, b = rng.randint(2, 100), rng.randint(2, 100)
            q, answer = (f"{a} + {b}", a + b) if op == "add" else (f"{a + b} − {a}", b)
            near = [answer + 10, answer - 10]
        else:
            a, b = rng.randint(2, 12), rng.randint(2, 100)
            q, answer = (f"{a} × {b}", a * b) if op == "mul" else (f"{a * b} ÷ {a}", b)
            near = [(a + 1) * b, (a - 1) * b, a * (b + 1)] if op == "mul" else [b + 10, b - 10]
        if q == previous:
            continue
        previous = q
        item = {"q": q, "a": answer}
        if mode == "mc":
            item["choices"] = _choices(rng, answer, near)
        out.append(item)
    return out


def _choices(rng: random.Random, answer: int, near: list[int]) -> list[int]:
    swapped = int(str(answer)[:-2] + str(answer)[-1] + str(answer)[-2]) if answer >= 10 else answer + 3
    pool = sorted({answer + 1, answer - 1, answer + 2, answer - 2, swapped, *near} - {answer})
    pool = [value for value in pool if value > 0]
    picks = rng.sample(pool, 3)
    choices = [answer, *picks]
    rng.shuffle(choices)
    return choices


def _ops(value) -> list[str]:
    if value is None:
        return list(OPS)
    if not isinstance(value, list) or not value or any(op not in OPS for op in value) or len(set(value)) != len(value):
        raise ValueError("ops must be a non-empty list of add, sub, mul, div")
    return [op for op in OPS if op in value]


def start(conn: sqlite3.Connection, body: dict, moment: datetime | None = None) -> dict:
    if not isinstance(body, dict) or set(body) - {"mode", "duration_s", "ops"}:
        raise ValueError("Unknown sprint field")
    mode = body.get("mode", "zetamac")
    duration = body.get("duration_s", DEFAULT_DURATION)
    if mode not in MODES:
        raise ValueError("mode must be zetamac or mc")
    if type(duration) is not int or not 10 <= duration <= 900:
        raise ValueError("duration_s must be an integer from 10 to 900")
    ops = _ops(body.get("ops"))
    seed, count = secrets.randbelow(2 ** 31), duration * RATE
    ident = secrets.token_hex(16)
    with transaction(conn):
        conn.execute("INSERT INTO math_sprints(id, mode, duration_s, seed, ops, problem_count, started_at) VALUES (?,?,?,?,?,?,?)",
                     (ident, mode, duration, seed, json.dumps(ops), count, (moment or now()).isoformat()))
    return {"sprint_id": ident, "mode": mode, "duration_s": duration, "ops": ops, "problems": problems(mode, seed, ops, count)}


def finish(conn: sqlite3.Connection, sprint_id: str, body: dict, moment: datetime | None = None, tz: str | None = None) -> dict:
    if not isinstance(body, dict) or set(body) - {"score", "wrong", "elapsed_ms"}:
        raise ValueError("Unknown sprint result field")
    score, wrong, elapsed = body.get("score"), body.get("wrong", 0), body.get("elapsed_ms")
    if any(type(value) is not int for value in (score, wrong, elapsed)) or wrong < 0 or elapsed < 0:
        raise ValueError("score, wrong and elapsed_ms must be integers; wrong and elapsed_ms non-negative")
    moment = moment or now()
    with transaction(conn, immediate=True):
        row = conn.execute("SELECT * FROM math_sprints WHERE id=?", (sprint_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown sprint")
        if row["finished_at"]:
            raise Conflict("This sprint's result is already saved")
        right = score + wrong if row["mode"] == "mc" else score
        if right < 0 or right + wrong > row["problem_count"] or (row["mode"] == "zetamac" and score < 0):
            raise ValueError("That score isn't possible for this sprint")
        if elapsed > row["duration_s"] * 1000 + SLACK_MS:
            raise ValueError("elapsed_ms is longer than the sprint")
        conn.execute("UPDATE math_sprints SET score=?, wrong=?, elapsed_ms=?, finished_at=? WHERE id=?",
                     (score, wrong, elapsed, moment.isoformat(), sprint_id))
    result = history(conn, {"duration_s": str(row["duration_s"]), **({"timezone": tz} if tz else {})}, moment)
    saved = next(item for item in reversed(result["sprints"]) if item["id"] == sprint_id)
    return {"sprint": saved, "best": result["best"], "streak_days": result["streak_days"]}


def _zone(name):
    if name is None:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown timezone") from None


def history(conn: sqlite3.Connection, query: dict, moment: datetime | None = None) -> dict:
    """Finished sprints oldest first (the last 200), best score per mode, daily streak."""
    if set(query) - {"duration_s", "timezone"}:
        raise ValueError("Unknown sprint history parameter")
    zone = _zone(query.get("timezone"))
    duration = query.get("duration_s")
    where, params = "finished_at IS NOT NULL", []
    if duration is not None:
        if not str(duration).isdigit():
            raise ValueError("duration_s must be an integer")
        where += " AND duration_s=?"
        params.append(int(duration))
    rows = conn.execute(f"SELECT id, mode, duration_s, score, wrong, elapsed_ms, finished_at FROM math_sprints WHERE {where} "
                        "ORDER BY finished_at DESC, rowid DESC LIMIT 200", params).fetchall()
    best = {mode: None for mode in MODES}
    for mode, top in conn.execute(f"SELECT mode, MAX(score) FROM math_sprints WHERE {where} GROUP BY mode", params):
        best[mode] = top
    days = {datetime.fromisoformat(day).astimezone(zone).date()
            for (day,) in conn.execute("SELECT finished_at FROM math_sprints WHERE finished_at IS NOT NULL")}
    return {"sprints": [dict(row) for row in reversed(rows)], "best": best,
            "streak_days": _streak(days, (moment or now()).astimezone(zone).date())}


def _streak(days: set[date], today: date) -> int:
    """Consecutive sprint days ending today, or yesterday while today is still open."""
    current = today if today in days else today - timedelta(days=1)
    streak = 0
    while current in days:
        streak += 1
        current -= timedelta(days=1)
    return streak

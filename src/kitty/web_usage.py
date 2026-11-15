"""Agent usage over HTTP (kitty.core.agent_usage): Settings → AI → Usage, the usage line
on live agent cards and home's session widget.

GET  /api/usage?days=1-90 (default 30)
     ->  {scanning, skipped, kinds: [{kind, label, enabled}],
          days: [{day, sources: {rika|ai|claude|codex|omp: {input, cache_read, cache_write, output, cost_usd}}}],
          total: {input, cache_read, cache_write, output, cost_usd},
          models: [{source, model, input, cache_read, cache_write, output, cost_usd}],
          sessions: [session summary, the 20 used last]}
GET  /api/usage/session[?key=<16 hex>]
     ->  {scanning, session: summary + {models, series: [{t, input, output, cache}]} | null}
     Without a key it is the session used last; an unknown key answers 404.
POST /api/usage/settings {kind: omp|claude|codex, enabled: bool}  ->  as GET /api/usage

A session summary is ``{key, agent, label, cwd_name, first_at, last_at, model,
context_tokens, context_window, context_pct, requests, input, cache_read, cache_write,
output, new_tokens, tool_calls, files_changed, cost_usd, cost_source: reported|priced|none}``.
``key`` is opaque: no path leaves the server, and the working directory only as its
folder name.

Rika's and the in-app AI's usage is always shown. A coding agent's local logs are read
only while its kind is switched on here (off by default, kept in
``.private/agents/usage.json``); switching a kind off drops what was read from its
logs. Nothing runs in the background: a GET reads on in the logs first, at most every
REFRESH_SECONDS once it has caught up, and answers ``scanning`` while logs are left.
The demo answers invented numbers and opens no log.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kitty.core import agent_skills, agent_usage
from kitty.core.agent_skills import KINDS, LABELS
from kitty.db import connect

PREFIX = "/api/usage"
REFRESH_SECONDS = 5.0
_KEY = re.compile(r"[0-9a-f]{16}")


class Usage:
    """One per web application: the per-kind opt-in and the paced reading of the logs."""

    def __init__(self, settings, *, home: Path | None = None):
        self.demo = bool(settings.demo)
        self.db_path: Path = settings.db_path
        self.folder: Path = settings.root / ".private" / "agents"
        self._home = home
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._enabled = dict.fromkeys(KINDS, self.demo)
        self._read_at = float("-inf")
        self._scanning = False
        if not self.demo:
            self._enabled.update(self._load())

    @property
    def home(self) -> Path:
        return self._home if self._home is not None else Path(os.path.expanduser("~"))

    # Opt-in -----------------------------------------------------------------------

    def _load(self) -> dict[str, bool]:
        try:
            with open(self.folder / "usage.json", encoding="utf-8") as handle:
                saved = json.load(handle).get("enabled", {})
        except (OSError, ValueError, AttributeError):
            return {}
        return {kind: saved.get(kind) is True for kind in KINDS} if isinstance(saved, dict) else {}

    def enabled(self, kind: str) -> bool:
        with self._lock:
            return self._enabled.get(kind, False)

    def kinds(self) -> list[dict[str, Any]]:
        return [{"kind": kind, "label": LABELS[kind], "enabled": self.enabled(kind)} for kind in KINDS]

    def set_enabled(self, kind: str, enabled: bool) -> None:
        from kitty.core.terminal import _write_private

        if kind not in KINDS:
            raise ValueError("Choose omp, claude or codex")
        with self._sync_lock, self._lock:
            self._enabled[kind] = enabled
            self._read_at = float("-inf")
            if self.demo:
                return
            self.folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.folder.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self.folder, 0o700)
            _write_private(self.folder / "usage.json", json.dumps({"enabled": self._enabled}, sort_keys=True) + "\n")
            if not enabled:
                with closing(connect(self.db_path)) as conn:
                    agent_usage.forget(conn, kind)

    # Reading ----------------------------------------------------------------------

    def refresh(self) -> bool:
        """Read on in the logs of the kinds that are on; True while logs are left."""
        kinds = [kind for kind in KINDS if self.enabled(kind)]
        if self.demo or not kinds:
            return False
        with self._sync_lock:
            if not self._scanning and time.monotonic() - self._read_at < REFRESH_SECONDS:
                return False
            with closing(connect(self.db_path)) as conn:
                self._scanning = agent_usage.sync(conn, self.home, kinds)
            self._read_at = time.monotonic()
            return self._scanning

    # Answers ----------------------------------------------------------------------

    def overview(self, conn, days: int) -> dict[str, Any]:
        scanning = self.refresh()
        found = agent_usage.report(conn, days)
        if self.demo:
            _demo_days(found)
            listed, skipped = [_demo_summary(index) for index, row in enumerate(_DEMO) if self.enabled(row[1])], 0
        else:
            listed, skipped = agent_usage.sessions(conn), agent_usage.skipped(conn)
        return {"scanning": scanning, "skipped": skipped, "kinds": self.kinds(), **found, "sessions": listed}

    def session(self, conn, key: str | None) -> dict[str, Any]:
        """LookupError for a key that names no session."""
        scanning = self.refresh()
        if self.demo:
            index = next((index for index in range(len(_DEMO)) if _demo_key(index) == key), 0 if key is None else None)
            found = _demo_session(index) if index is not None and self.enabled(_DEMO[index][1]) else None
        else:
            found = agent_usage.session(conn, key)
        if found is None and key is not None:
            raise LookupError("That session is not in the usage rika! has read")
        return {"scanning": scanning, "session": found}

    def card(self, agent: dict[str, Any]) -> dict[str, Any] | None:
        """The usage of a running agent's session: `agent` has ``id`` and ``kind`` and, as
        herdr or Tern knows them, ``session`` and ``cwd``. None while its kind is off or
        its log has not been read."""
        kind = agent.get("kind")
        if kind not in KINDS or not self.enabled(kind):
            return None
        if self.demo:
            index = next((index for index, row in enumerate(_DEMO) if row[0] == agent.get("id")), None)
            return _demo_summary(index) if index is not None else None
        path = agent_skills.session_file(kind, agent.get("session"), agent.get("cwd"), self.home)
        if path is None:
            return None
        self.refresh()
        with closing(connect(self.db_path)) as conn:
            return agent_usage.summary(conn, agent_usage.file_key(path))


# The demo: invented numbers for the scripted roster (kitty.core.rika.agents) ----------

#: (roster id, kind, folder, model, context %, input, cache read, cache write, output, tool calls, files, dollars, minutes)
_DEMO = (
    ("herdr:demo:w1:p2", "omp", "pdf-parser", "claude-opus-5-5", 42, 48_200, 3_920_000, 212_000, 61_400, 118, 9, 3.41, 96),
    ("herdr:demo:w1:p3", "claude", "history-essay", "claude-sonnet-5-5", 18, 12_600, 640_000, 58_000, 14_900, 23, 2, 0.39, 34),
    ("tern:demo:5", "codex", "biology-deck", "gpt-6.1-sol", 27, 96_400, 1_180_000, 0, 22_300, 41, 5, 0.53, 58),
    ("herdr:demo:w1:p4", "omp", "exam-dates", "claude-opus-5-5", 9, 8_100, 410_000, 39_000, 6_200, 17, 3, 0.43, 21),
    ("tern:demo:6", "claude", "lecture-notes", "claude-haiku-5-5", 6, 22_000, 96_000, 11_000, 4_300, 8, 1, 0.01, 12),
)


def _demo_key(index: int) -> str:
    return agent_usage.file_key("demo:" + _DEMO[index][0])


def _demo_summary(index: int) -> dict[str, Any]:
    _, kind, folder, model, percent, fresh, read, written, output, tools, files, dollars, minutes = _DEMO[index]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = lambda moment: moment.isoformat().replace("+00:00", "Z")  # noqa: E731
    window = agent_usage.context_window(model) or 400_000
    return {"key": _demo_key(index), "agent": kind, "label": LABELS[kind], "cwd_name": folder,
            "first_at": stamp(now - timedelta(minutes=minutes)), "last_at": stamp(now - timedelta(minutes=index)),
            "model": model, "context_tokens": window * percent // 100, "context_window": window, "context_pct": percent,
            "requests": tools + 6, "input": fresh, "cache_read": read, "cache_write": written, "output": output,
            "new_tokens": fresh + written + output, "tool_calls": tools, "files_changed": files,
            "cost_usd": dollars, "cost_source": "reported" if kind == "omp" else "priced"}


def _demo_session(index: int) -> dict[str, Any]:
    found = _demo_summary(index)
    steps = max(4, _DEMO[index][12] // 5)
    rng = random.Random(index + 1)
    weights = [rng.uniform(0.3, 1.0) * (0.35 if step % 7 == 5 else 1.0) for step in range(steps)]
    share = [weight / sum(weights) for weight in weights]
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=5 * steps)
    return {
        **found,
        "models": [{"model": found["model"], "role": "main", **{name: found[name] for name in ("input", "cache_read", "cache_write", "output")},
                    "cost_usd": found["cost_usd"], "cost_source": found["cost_source"]}],
        "series": [{"t": (start + timedelta(minutes=5 * step)).isoformat().replace("+00:00", "Z"),
                    "input": round((found["input"] + found["cache_write"]) * part), "output": round(found["output"] * part),
                    "cache": round(found["cache_read"] * part)} for step, part in enumerate(share)],
    }


def _demo_days(found: dict[str, Any]) -> None:
    """Invented days on top of what the demo workspace's own tables hold."""
    rng = random.Random(23)
    scale = {"rika": (40_000, 0.02), "ai": (15_000, 0.01), "claude": (900_000, 0.9), "codex": (500_000, 0.35), "omp": (2_400_000, 2.6)}
    models = {"rika": "openai/gpt-6.1-terra", "ai": "openai/gpt-6-luna", "claude": "claude-sonnet-5-5", "codex": "gpt-6.1-sol",
              "omp": "claude-opus-5-5"}
    by_model = {source: agent_usage._blank() for source in scale}
    for index, day in enumerate(found["days"]):
        quiet = 0.25 if index % 7 in (2, 3) else 1.0
        for source, (tokens, dollars) in scale.items():
            amount = rng.uniform(0.2, 1.0) * quiet * (0 if source == "codex" and index % 3 == 0 else 1)
            numbers = (round(tokens * amount * 0.04), round(tokens * amount * 0.86), round(tokens * amount * 0.06),
                       round(tokens * amount * 0.04), round(dollars * amount, 4))
            for target in (day["sources"][source], by_model[source], found["total"]):
                agent_usage._add(target, *numbers)
    for sums in [found["total"], *by_model.values(), *(day["sources"][source] for day in found["days"] for source in scale)]:
        sums["cost_usd"] = round(sums["cost_usd"], 4)
    found["models"] = sorted([{"source": source, "model": models[source], **sums} for source, sums in by_model.items()],
                             key=lambda item: -(item["input"] + item["cache_read"] + item["cache_write"] + item["output"]))


# Routes ---------------------------------------------------------------------------

def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def start(app) -> None:
    app.usage = Usage(app.settings)


def get(app, conn, path, query):
    if path == PREFIX:
        days = query.get("days", "30")
        if set(query) - {"days"} or not re.fullmatch(r"[1-9][0-9]?", days) or int(days) > 90:
            raise _error(400, "Usage takes days from 1 to 90")
        return app.usage.overview(conn, int(days))
    if path == PREFIX + "/session":
        key = query.get("key")
        if set(query) - {"key"} or (key is not None and not _KEY.fullmatch(key)):
            raise _error(400, "Usage of a session takes its key")
        try:
            return app.usage.session(conn, key)
        except LookupError as exc:
            raise _error(404, str(exc)) from None
    return None


def post(app, conn, path, body):
    if path != PREFIX + "/settings":
        return None
    if set(body) != {"kind", "enabled"} or body["kind"] not in KINDS or type(body["enabled"]) is not bool:
        raise _error(400, "Expected {kind: omp|claude|codex, enabled: true|false}")
    app.usage.set_enabled(body["kind"], body["enabled"])
    return app.usage.overview(conn, 30)

"""The build's settings in SQLite: a projection of the active build file.

The settings rows the rest of rika! reads (``ai_settings``, ``note_preferences``,
``study_settings``, ``opportunity_settings``, ``tag_settings`` and ``build_state``) are
written only here, from the build. Their readers (the AI budget check, attachment
cleanup, the study timer, the monitoring scheduler, the tagger's hot path, history
compaction, pairing) keep reading SQLite inside their own transactions, and every
process (CLI, TUI, MCP, the server) sees the same values without parsing JSON.

``sync`` brings the projection up to date with the file and is cheap when nothing
changed (one read and hash). The first time a workspace's database is projected, the
settings the file does not mention are taken from the existing rows and written into
the file: that is the one-time move of SQLite settings into the build.

``editing`` is how a Settings screen changes a setting: under the build lock and one
SQLite transaction it yields the build document, then writes the file atomically and
projects it before committing, so the file and the rows never disagree.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from kitty.core.builds import document, store
from kitty.db import connect
from kitty.db.connection import transaction


@dataclass
class Synced:
    """The build a process should use now."""

    #: The build file's text and its revision, as on disk (may be invalid).
    text: str
    revision: str
    #: The complete active build: the file's when valid, else the last good one.
    build: dict[str, Any]
    #: The revision of the text `build` came from.
    active_revision: str
    #: Problems in the file on disk (errors mean `build` is the last good one).
    problems: list[document.Problem] = field(default_factory=list)

    @property
    def invalid(self) -> bool:
        return any(problem.severity == "error" for problem in self.problems)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _budget(value: Any) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def settings_from_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """The build's ``settings`` as the SQLite rows hold them now."""
    ai = conn.execute("SELECT model, routing, max_output_tokens, daily_budget_usd FROM ai_settings WHERE id=1").fetchone()
    notes = conn.execute("SELECT allow_ai, retention_days FROM note_preferences WHERE id=1").fetchone()
    study = conn.execute("SELECT focus_seconds, short_break_seconds, sound, notifications FROM study_settings WHERE id=1").fetchone()
    monitoring = conn.execute("SELECT enabled, interval_hours FROM opportunity_settings WHERE id=1").fetchone()
    tags = conn.execute("SELECT mode FROM tag_settings WHERE id=1").fetchone()
    state = conn.execute("SELECT history_retention_days, tern_plugin FROM build_state WHERE id=1").fetchone()
    return {
        "ai": {"model": ai["model"], "routing": ai["routing"], "max_output_tokens": ai["max_output_tokens"],
               "daily_budget_usd": float(Decimal(ai["daily_budget_usd"]))},
        "notes": {"allow_ai": bool(notes["allow_ai"]), "attachment_retention_days": notes["retention_days"]},
        "history": {"retention_days": state["history_retention_days"]},
        "tags": {"mode": tags["mode"]},
        "study": {"focus_seconds": study["focus_seconds"], "short_break_seconds": study["short_break_seconds"],
                  "sound": bool(study["sound"]), "notifications": bool(study["notifications"])},
        "opportunities": {"monitoring": bool(monitoring["enabled"]), "interval_hours": monitoring["interval_hours"]},
        "tern": {"plugin": bool(state["tern_plugin"])},
    }


def apply(conn: sqlite3.Connection, build: dict[str, Any]) -> None:
    """Write a complete build's settings into the rows (inside the caller's transaction).

    A row's ``revision`` moves only when one of its values changes, so an open Settings
    screen gets a 409 exactly when what it shows went stale.
    """
    from kitty.core import study

    settings = build["settings"]
    ai = settings["ai"]
    conn.execute(
        "UPDATE ai_settings SET model=?, routing=?, max_output_tokens=?, daily_budget_usd=?, revision=revision+1 "
        "WHERE id=1 AND (model IS NOT ? OR routing IS NOT ? OR max_output_tokens IS NOT ? OR daily_budget_usd IS NOT ?)",
        (ai["model"], ai["routing"], ai["max_output_tokens"], _budget(ai["daily_budget_usd"])) * 2)
    notes = settings["notes"]
    conn.execute(
        "UPDATE note_preferences SET allow_ai=?, retention_days=?, revision=revision+1 "
        "WHERE id=1 AND (allow_ai IS NOT ? OR retention_days IS NOT ?)",
        (int(notes["allow_ai"]), notes["attachment_retention_days"]) * 2)
    timer = settings["study"]
    values = (timer["focus_seconds"], timer["short_break_seconds"], int(timer["sound"]), int(timer["notifications"]))
    if conn.execute(
            "UPDATE study_settings SET focus_seconds=?, short_break_seconds=?, sound=?, notifications=?, revision=revision+1 "
            "WHERE id=1 AND (focus_seconds IS NOT ? OR short_break_seconds IS NOT ? OR sound IS NOT ? OR notifications IS NOT ?)",
            values * 2).rowcount:
        study.settle_idle_timer(conn)
    monitoring = settings["opportunities"]
    current = conn.execute("SELECT enabled, interval_hours FROM opportunity_settings WHERE id=1").fetchone()
    if (bool(current["enabled"]), current["interval_hours"]) != (monitoring["monitoring"], monitoring["interval_hours"]):
        conn.execute("UPDATE opportunity_settings SET enabled=?, interval_hours=?, next_run=?, revision=revision+1 WHERE id=1",
                     (int(monitoring["monitoring"]), monitoring["interval_hours"],
                      _now() if monitoring["monitoring"] else None))
    conn.execute("UPDATE tag_settings SET mode=?, revision=revision+1 WHERE id=1 AND mode IS NOT ?",
                 (settings["tags"]["mode"],) * 2)
    conn.execute("UPDATE build_state SET history_retention_days=?, tern_plugin=? WHERE id=1",
                 (settings["history"]["retention_days"], int(settings["tern"]["plugin"])))


def _record(conn: sqlite3.Connection, text: str) -> None:
    conn.execute("UPDATE build_state SET applied_hash=?, applied_text=?, applied_at=? WHERE id=1",
                 (store.revision(text), text, _now()))


def _state(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT applied_hash, applied_text FROM build_state WHERE id=1").fetchone()


def _move_rows_into(text: str, rows: dict[str, dict[str, Any]]) -> str:
    """The one-time move: settings the file does not mention are taken from the rows."""
    def fill(build: dict[str, Any]) -> None:
        for name, values in rows.items():
            if name in ("history", "tern"):
                continue  # no older table held these; their defaults apply
            target = store.section(build, "settings", name)
            for key, value in values.items():
                target.setdefault(key, value)
    return store.changed(text, fill)


def _last_good(conn: sqlite3.Connection) -> tuple[dict[str, Any], str]:
    row = _state(conn)
    if row["applied_text"]:
        checked = document.check(row["applied_text"])
        if checked.ok:
            return checked.build, row["applied_hash"]  # type: ignore[return-value]
    return document.normalize({"schema": document.SCHEMA_VERSION}), ""


def sync(conn: sqlite3.Connection, root: Path) -> Synced:
    """Project the build file into SQLite when it changed; return what is active.

    A file with an error changes nothing: the last good build (the one projected)
    stays active and the problems are returned for the editor and the windows.
    """
    text = store.ensure(root)
    checked = document.check(text)
    if checked.ok and _state(conn)["applied_hash"] is None:
        with store.locked(root):
            text = store.read_text(root) or text
            checked = document.check(text)
            if checked.ok:
                moved = _move_rows_into(text, settings_from_rows(conn))
                if moved != text:
                    store.write_text(root, moved)
                    text, checked = moved, document.check(moved)
    if not checked.ok:
        build, active = _last_good(conn)
        return Synced(text, store.revision(text), build, active, checked.problems)
    revision = store.revision(text)
    if _state(conn)["applied_hash"] != revision:
        with transaction(conn, immediate=True):
            if _state(conn)["applied_hash"] != revision:
                apply(conn, checked.build)  # type: ignore[arg-type]
                _record(conn, text)
    return Synced(text, revision, checked.build, revision, checked.problems)  # type: ignore[arg-type]


@contextmanager
def editing(conn: sqlite3.Connection, root: Path) -> Iterator[dict[str, Any]]:
    """Change the build from a Settings screen; yields the document as written.

    The caller mutates the yielded document (and may read or write other rows: it runs
    inside the same ``BEGIN IMMEDIATE`` transaction). On a clean exit the new document
    is validated, written atomically and projected before the commit; any exception
    rolls the rows back and leaves the file untouched. A build file with an error is
    refused (409) rather than overwritten.
    """
    store.ensure(root)
    with store.locked(root):
        text = store.read_text(root) or store.initial_text()
        checked = document.check(text)
        if not checked.ok:
            raise store.BuildStoreError(
                f"config/{store.BUILD_FILE} has an error ({checked.errors[0]}); fix it in the build editor first",
                409, checked.problems)
        with transaction(conn, immediate=True):
            data = json.loads(text)
            yield data
            new = store.changed(text, lambda build: (build.clear(), build.update(data)))
            built = document.require(new)
            apply(conn, built)
            _record(conn, new)
            if new != text:
                store.write_text(root, new)


def replace(conn: sqlite3.Connection, root: Path, text: str, *, history: tuple[str, str] | None = None) -> str:
    """Make `text` the active build in one step (install, rollback, the build editor).

    With `history` (reason, source) the build it replaces is kept for rollback first.
    The rows are projected inside one transaction that commits only after the file was
    renamed into place; a failure leaves both as they were. Returns the new revision.
    """
    built = document.require(text)
    store.ensure(root)
    with store.locked(root):
        previous = store.read_text(root)
        with transaction(conn, immediate=True):
            apply(conn, built)
            _record(conn, text)
            if history is not None and previous is not None and previous != text:
                store.push_history(root, previous, *history)
            store.write_text(root, text)
    return store.revision(text)


def claim_local_preferences(conn: sqlite3.Connection) -> bool:
    """Record (in the caller's transaction) the one-time move of a browser's old local
    preferences into the build; False when a browser already moved its own."""
    return conn.execute("UPDATE build_state SET local_preferences_moved=1 "
                        "WHERE id=1 AND local_preferences_moved=0").rowcount == 1


def local_preferences_moved(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT local_preferences_moved FROM build_state WHERE id=1").fetchone()[0])


def history_retention_days(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT history_retention_days FROM build_state WHERE id=1").fetchone()[0]


def tern_plugin_enabled(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT tern_plugin FROM build_state WHERE id=1").fetchone()[0])


def startup_modules(settings) -> tuple[str, ...]:
    """The modules a server starting now runs: the build file's, or, when the file has an
    error, those of the last good build projected into the database (Q9: an invalid save
    keeps the last good build, across a restart too). Raises the file's ConfigError when
    there is no last good build."""
    from kitty.config import ConfigError
    from kitty.modules import load_modules

    try:
        return load_modules(settings.config_dir)
    except ConfigError:
        if not settings.db_path.exists():
            raise
        with closing(connect(settings.db_path, create_parents=False)) as conn:
            try:
                row = conn.execute("SELECT applied_text FROM build_state WHERE id=1").fetchone()
            except sqlite3.OperationalError:
                row = None
        checked = document.check(row[0]) if row and row[0] else None
        if checked is None or not checked.ok:
            raise
        return tuple(checked.build["modules"])  # type: ignore[index]

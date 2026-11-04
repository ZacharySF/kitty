"""Retention by reason: what History compaction may not fold yet (paper §13, "retention by reason").

Compaction (kitty.core.blocks.history.compact) folds op_log rows older than the History
window into daily snapshots. Without this module, a journal row or a pinned citation
that names ``block@seq`` inside a folded day would silently resolve to that day's
snapshot. So compaction keeps every op_log row from the lowest seq a live reason still
needs (the "floor", like a PostgreSQL replication slot), and deletes a journal row only
when no reason covers it.

The reasons:

- ``replay_window``: journal rows (``agent_access``) inside the History window cite the
  op_log rows from their seq on (policy replay, citation checks);
- ``pinned_citation``: a ``((id@seq))`` citation in a live block's text needs the rows
  from that seq on (``history.citations`` and ``preview``); its journal rows for
  exactly that block and seq stay too;
- ``user_hold``: the user kept an agent session (Settings → Agents): its journal rows
  stay past the window and so do the op_log rows from its oldest journaled seq.

Size cap: reasons may keep at most `MAX_RETAINED` op_log rows that the window alone
would have folded. Past it, the reasons holding the oldest rows are released (a hold is
marked ``released_by = 'size_cap'``; derived reasons are reported in the compaction
result), oldest first, until the rest fit.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

#: op_log rows older than the History window that retention reasons may keep.
MAX_RETAINED = 200_000
_PINNED = re.compile(r"\(\(([0-9a-f-]{36})@([0-9]{1,18})\)\)")


def pinned(conn: sqlite3.Connection) -> dict[tuple[str, int], str]:
    """``(block id, seq) -> citing block id`` for every pinned citation in live block text."""
    found: dict[tuple[str, int], str] = {}
    for row in conn.execute("SELECT id, text FROM blocks WHERE deleted_at IS NULL AND text LIKE '%((%@%))%'"):
        for bid, seq in _PINNED.findall(row["text"]):
            found.setdefault((bid, int(seq)), row["id"])
    return found


def reasons(conn: sqlite3.Connection, cutoff: str) -> list[dict]:
    """Every live reason with the lowest op_log seq it needs, lowest first."""
    out: list[dict] = []
    row = conn.execute("SELECT MIN(seq), COUNT(*) FROM agent_access WHERE at>=?", (cutoff,)).fetchone()
    if row[1]:
        out.append({"reason": "replay_window", "seq": row[0], "rows": row[1]})
    for (bid, seq), citing in pinned(conn).items():
        out.append({"reason": "pinned_citation", "seq": seq, "block_id": bid, "cited_by": citing})
    for hold in conn.execute("SELECT id, session, seq FROM retention_holds WHERE released_at IS NULL"):
        out.append({"reason": "user_hold", "seq": hold["seq"], "session": hold["session"], "hold": hold["id"]})
    return sorted(out, key=lambda item: (item["seq"], item["reason"]))


def floor(conn: sqlite3.Connection, cutoff: str, *, cap: int = MAX_RETAINED, release: bool = False,
          now: datetime | None = None) -> dict:
    """``{floor, reasons, released}``: compaction folds only op_log rows below ``floor`` (None: no limit).

    With `release`, reasons the size cap drops are released (holds marked in the
    database); otherwise they are only reported.
    """
    live = reasons(conn, cutoff)
    released: list[dict] = []
    if live:
        lowest = live[0]["seq"]
        kept = conn.execute("SELECT COUNT(*) FROM op_log WHERE seq>=? AND at<?", (lowest, cutoff)).fetchone()[0]
        if kept > cap:
            row = conn.execute("SELECT seq FROM op_log WHERE at<? ORDER BY seq DESC LIMIT 1 OFFSET ?",
                               (cutoff, max(cap - 1, 0))).fetchone()
            limit = (row[0] + (1 if cap == 0 else 0)) if row else lowest
            released = [item for item in live if item["seq"] < limit]
            live = [item for item in live if item["seq"] >= limit]
            if release:
                stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
                conn.executemany("UPDATE retention_holds SET released_at=?, released_by='size_cap' WHERE id=?",
                                 [(stamp, item["hold"]) for item in released if item["reason"] == "user_hold"])
    return {"floor": live[0]["seq"] if live else None, "reasons": live, "released": released}


def prune_journal(conn: sqlite3.Connection, cutoff: str) -> int:
    """Delete journal rows older than the window that no hold or pinned citation covers."""
    held = {row[0] for row in conn.execute("SELECT session FROM retention_holds WHERE released_at IS NULL")}
    cited = pinned(conn)
    doomed = [row["id"] for row in conn.execute("SELECT id, session, block_id, seq FROM agent_access WHERE at<?", (cutoff,))
              if row["session"] not in held and (row["block_id"], row["seq"]) not in cited]
    for start in range(0, len(doomed), 500):
        chunk = doomed[start:start + 500]
        conn.execute(f"DELETE FROM agent_access WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    return len(doomed)


def hold(conn: sqlite3.Connection, session: str, note: str | None = None, *, now: datetime | None = None) -> dict:
    """Keep one agent session's journal (and the op_log rows it cites) past the window."""
    if not conn.execute("SELECT 1 FROM agent_sessions WHERE id=?", (session,)).fetchone():
        raise LookupError("No such agent session")
    existing = conn.execute("SELECT * FROM retention_holds WHERE session=? AND released_at IS NULL", (session,)).fetchone()
    if existing:
        return dict(existing)
    seq = conn.execute("SELECT MIN(seq) FROM agent_access WHERE session=?", (session,)).fetchone()[0]
    if seq is None:
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM op_log").fetchone()[0]
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    hold_id = conn.execute("INSERT INTO retention_holds(session, seq, note, created_at) VALUES (?,?,?,?)",
                           (session, seq, note, stamp)).lastrowid
    return dict(conn.execute("SELECT * FROM retention_holds WHERE id=?", (hold_id,)).fetchone())


def release(conn: sqlite3.Connection, session: str, *, now: datetime | None = None) -> bool:
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    return conn.execute("UPDATE retention_holds SET released_at=?, released_by='user' WHERE session=? AND released_at IS NULL",
                        (stamp, session)).rowcount > 0


def status(conn: sqlite3.Connection, cutoff: str) -> dict[str, Any]:
    """For Settings → Agents: the current floor, its reasons, and the horizon."""
    result = floor(conn, cutoff)
    horizon = conn.execute("SELECT horizon_seq FROM block_history WHERE id=1").fetchone()[0]
    counts: dict[str, int] = {}
    for item in result["reasons"]:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    return {"floor": result["floor"], "horizon_seq": horizon, "reasons": counts, "cap": MAX_RETAINED,
            "over_cap": [{key: item[key] for key in ("reason", "seq")} for item in result["released"]],
            "journal_rows": conn.execute("SELECT COUNT(*) FROM agent_access").fetchone()[0]}

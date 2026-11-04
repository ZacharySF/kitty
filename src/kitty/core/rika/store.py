"""Rika's conversation, actions, approvals and brain-dump plans (migrations 037, 045, 049).

Public shapes, shared by ``GET /api/rika/history`` and the stream's events:

* message ``{kind:'message', id, conversation_id, role:'user'|'rika', text, reply_to,
  tier, model, status:'pending'|'done'|'failed', source:'typed'|'voice'|'dump',
  cost_usd, usage, created_at}``, where ``usage`` is ``{tokens_in, tokens_cached,
  tokens_out, path, signature, remembered, shortcut}`` for a finished Rika reply
  (migration 049) and null otherwise;
* action ``{kind:'action', id, message_id, tool, summary, reversible,
  status:'done'|'undone'|'failed', created_at}``; its undo payload stays private;
* approval ``{kind:'approval', id, message_id, tool, summary, detail, cost_usd,
  status:'pending'|'approved'|'rejected'|'expired', created_at}``;
* plan ``{kind:'plan', id, message_id, request_id, items, duration,
  note:{page_id, title}|null, status:'pending'|'running'|'applied'|'dismissed'|'failed',
  created_at, decided_at}``; items are :func:`kitty.core.rika.dumps.public_items`.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from kitty.db.connection import transaction

#: A request continues the latest conversation when it came within this long of it.
CONVERSATION_GAP = timedelta(hours=6)
#: Earlier turns sent with each model request.
TRANSCRIPT_TURNS = 20
HISTORY_PAGE = 50


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id() -> str:
    return secrets.token_hex(16)


def _money(value: str | None) -> float | None:
    return float(value) if value is not None else None


def _message(row: sqlite3.Row) -> dict[str, Any]:
    return {"kind": "message", "id": row["id"], "conversation_id": row["conversation_id"], "role": row["role"],
            "text": row["text"], "reply_to": row["reply_to"], "tier": row["tier"], "model": row["model"],
            "status": row["status"], "source": row["source"], "cost_usd": _money(row["cost_usd"]),
            "usage": _usage(row), "created_at": row["created_at"]}


def _usage(row: sqlite3.Row) -> dict[str, Any] | None:
    """A Rika reply's tokens and route (migration 049); None for user messages and unfinished replies."""
    counts = (row["tokens_in"], row["tokens_cached"], row["tokens_out"])
    if row["role"] != "rika" or (row["route_json"] is None and counts == (None, None, None)):
        return None
    route = json.loads(row["route_json"]) if row["route_json"] else {}
    return {"tokens_in": counts[0], "tokens_cached": counts[1], "tokens_out": counts[2],
            "path": route.get("path", []), "signature": route.get("signature", ""),
            "remembered": bool(route.get("remembered")), "shortcut": bool(route.get("shortcut"))}


def _action(row: sqlite3.Row) -> dict[str, Any]:
    return {"kind": "action", "id": row["id"], "message_id": row["message_id"], "tool": row["tool"],
            "summary": row["summary"], "reversible": bool(row["reversible"]), "status": row["status"],
            "created_at": row["created_at"]}


def _approval(row: sqlite3.Row) -> dict[str, Any]:
    return {"kind": "approval", "id": row["id"], "message_id": row["message_id"], "tool": row["tool"],
            "summary": row["summary"], "detail": row["detail"], "cost_usd": _money(row["cost_usd"]),
            "status": row["status"], "created_at": row["created_at"]}


# Messages ---------------------------------------------------------------------

def conversation_for(conn: sqlite3.Connection, requested: str | None = None) -> str:
    """`requested` when it exists, else the latest conversation if recent, else a new one."""
    if requested is not None:
        if not conn.execute("SELECT 1 FROM rika_messages WHERE conversation_id=? LIMIT 1", (requested,)).fetchone():
            raise LookupError("Conversation not found")
        return requested
    row = conn.execute("SELECT conversation_id,created_at FROM rika_messages ORDER BY created_at DESC LIMIT 1").fetchone()
    if row and datetime.fromisoformat(row["created_at"]) > datetime.now(timezone.utc) - CONVERSATION_GAP:
        return row["conversation_id"]
    return new_id()


def add_message(conn: sqlite3.Connection, *, conversation_id: str, role: str, text: str,
                reply_to: str | None = None, tier: str | None = None, model: str | None = None,
                status: str = "done", source: str = "typed") -> dict[str, Any]:
    ident = new_id()
    with transaction(conn):
        conn.execute("INSERT INTO rika_messages(id,conversation_id,role,text,reply_to,tier,model,status,source,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (ident, conversation_id, role, text, reply_to, tier, model, status, source, now()))
    return message(conn, ident)


def update_message(conn: sqlite3.Connection, ident: str, **fields: Any) -> dict[str, Any]:
    allowed = {"text", "tier", "model", "status", "cost_usd", "tokens_in", "tokens_cached", "tokens_out", "route_json"}
    if set(fields) - allowed:
        raise ValueError("Unknown message field")
    if "cost_usd" in fields and fields["cost_usd"] is not None:
        fields["cost_usd"] = str(fields["cost_usd"])
    if isinstance(fields.get("route_json"), dict):
        fields["route_json"] = json.dumps(fields["route_json"], ensure_ascii=False)
    with transaction(conn):
        conn.execute("UPDATE rika_messages SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
                     (*fields.values(), ident))
    return message(conn, ident)


def message(conn: sqlite3.Connection, ident: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM rika_messages WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise LookupError("Message not found")
    return _message(row)


def transcript(conn: sqlite3.Connection, conversation_id: str, before: str) -> list[dict[str, str]]:
    """Earlier finished turns of a conversation, oldest first, as model chat messages."""
    rows = conn.execute(
        "SELECT role,text FROM rika_messages WHERE conversation_id=? AND created_at<? AND status='done' AND text<>''"
        " ORDER BY created_at DESC LIMIT ?", (conversation_id, before, TRANSCRIPT_TURNS * 2)).fetchall()
    return [{"role": "user" if row["role"] == "user" else "assistant", "content": row["text"]} for row in reversed(rows)]


def failed_before(conn: sqlite3.Connection, conversation_id: str, before: str) -> bool:
    """Whether the conversation's previous Rika reply failed (the next try goes deep)."""
    row = conn.execute("SELECT status FROM rika_messages WHERE conversation_id=? AND role='rika' AND created_at<?"
                       " ORDER BY created_at DESC LIMIT 1", (conversation_id, before)).fetchone()
    return row is not None and row["status"] == "failed"


# Actions ----------------------------------------------------------------------

def add_action(conn: sqlite3.Connection, *, message_id: str, tool: str, args: dict, summary: str,
               reversible: bool, undo: dict | None, status: str = "done") -> dict[str, Any]:
    ident = new_id()
    with transaction(conn):
        conn.execute("INSERT INTO rika_actions(id,message_id,tool,args_json,summary,reversible,undo_json,status,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?)",
                     (ident, message_id, tool, json.dumps(args), summary, int(reversible),
                      json.dumps(undo) if undo is not None else None, status, now()))
    return action(conn, ident)


def action(conn: sqlite3.Connection, ident: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM rika_actions WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise LookupError("Action not found")
    return _action(row)


def undo_payload(conn: sqlite3.Connection, ident: str) -> tuple[dict[str, Any], dict | None]:
    """An action and its private undo payload."""
    row = conn.execute("SELECT * FROM rika_actions WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise LookupError("Action not found")
    return _action(row), json.loads(row["undo_json"]) if row["undo_json"] else None


def mark_undone(conn: sqlite3.Connection, ident: str) -> dict[str, Any]:
    with transaction(conn):
        conn.execute("UPDATE rika_actions SET status='undone',undone_at=? WHERE id=? AND status='done'", (now(), ident))
    return action(conn, ident)


# Approvals --------------------------------------------------------------------

def add_approval(conn: sqlite3.Connection, *, message_id: str, tool: str, args: dict, summary: str,
                 detail: str, cost_usd: float | None = None) -> dict[str, Any]:
    ident = new_id()
    with transaction(conn):
        conn.execute("INSERT INTO rika_approvals(id,message_id,tool,args_json,summary,detail,cost_usd,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?)",
                     (ident, message_id, tool, json.dumps(args), summary, detail,
                      str(cost_usd) if cost_usd is not None else None, now()))
    return approval(conn, ident)


def approval(conn: sqlite3.Connection, ident: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM rika_approvals WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise LookupError("Approval not found")
    return _approval(row)


def decide(conn: sqlite3.Connection, ident: str, status: str) -> dict[str, Any]:
    """Settle a pending approval once; a second decision raises."""
    if status not in {"approved", "rejected", "expired"}:
        raise ValueError("Approve or reject")
    with transaction(conn, immediate=True):
        current = approval(conn, ident)
        if current["status"] != "pending":
            raise PermissionError(f"This approval is already {current['status']}")
        conn.execute("UPDATE rika_approvals SET status=?,decided_at=? WHERE id=?", (status, now(), ident))
    return approval(conn, ident)


# Plans ------------------------------------------------------------------------

def _plan(row: sqlite3.Row, conn: sqlite3.Connection) -> dict[str, Any]:
    from kitty.core.rika.dumps import public_items

    note = None
    if row["note_page_id"] is not None:
        page = conn.execute("SELECT id,title FROM note_page_index WHERE id=?", (row["note_page_id"],)).fetchone()
        note = {"page_id": page["id"], "title": page["title"]} if page else None
    return {"kind": "plan", "id": row["id"], "message_id": row["message_id"], "request_id": row["request_id"],
            "items": public_items(json.loads(row["items_json"])), "duration": row["duration_s"], "note": note,
            "status": row["status"], "created_at": row["created_at"], "decided_at": row["decided_at"]}


def add_plan(conn: sqlite3.Connection, *, message_id: str, request_id: str, items: list[dict], segments: list[dict],
             duration: float, note_page_id: int | None, note_block_id: str | None) -> dict[str, Any]:
    ident = new_id()
    with transaction(conn):
        conn.execute("INSERT INTO rika_plans(id,message_id,request_id,items_json,segments_json,duration_s,note_page_id,"
                     "note_block_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (ident, message_id, request_id, json.dumps(items), json.dumps(segments), duration, note_page_id,
                      note_block_id, now()))
    return plan(conn, ident)


def plan(conn: sqlite3.Connection, ident: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM rika_plans WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise LookupError("Plan not found")
    return _plan(row, conn)


def plan_record(conn: sqlite3.Connection, ident: str) -> dict[str, Any]:
    """A plan's private record: its items with their tool calls, and where its dump note is."""
    row = conn.execute("SELECT * FROM rika_plans WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise LookupError("Plan not found")
    return {"id": row["id"], "message_id": row["message_id"], "request_id": row["request_id"],
            "items": json.loads(row["items_json"]), "status": row["status"], "note_page_id": row["note_page_id"],
            "note_block_id": row["note_block_id"]}


def start_plan(conn: sqlite3.Connection, ident: str, status: str, items: list[dict]) -> dict[str, Any]:
    """Settle a pending plan once (running or dismissed); a second decision raises."""
    with transaction(conn, immediate=True):
        current = plan_record(conn, ident)
        if current["status"] != "pending":
            raise PermissionError(f"This plan is already {current['status']}")
        conn.execute("UPDATE rika_plans SET status=?,items_json=?,decided_at=? WHERE id=?",
                     (status, json.dumps(items), now(), ident))
    return plan(conn, ident)


def finish_plan(conn: sqlite3.Connection, ident: str, status: str, items: list[dict]) -> dict[str, Any]:
    with transaction(conn):
        conn.execute("UPDATE rika_plans SET status=?,items_json=? WHERE id=?", (status, json.dumps(items), ident))
    return plan(conn, ident)


def recover(conn: sqlite3.Connection) -> None:
    """After a restart nothing waits any more: pending approvals expire, unfinished replies
    and plans that were running fail (a plan still waiting for the user keeps waiting)."""
    with transaction(conn):
        conn.execute("UPDATE rika_approvals SET status='expired',decided_at=? WHERE status='pending'", (now(),))
        conn.execute("UPDATE rika_messages SET status='failed' WHERE status='pending'")
        conn.execute("UPDATE rika_plans SET status='failed' WHERE status='running'")


# History ----------------------------------------------------------------------

def history(conn: sqlite3.Connection, before: str | None = None, limit: int = HISTORY_PAGE) -> dict[str, Any]:
    """The newest `limit` items older than `before`, returned oldest first."""
    from kitty.core.rika import mail

    bound = before or "~"  # sorts after every ISO timestamp
    items: list[tuple[str, dict]] = []
    for table, shape in (("rika_messages", _message), ("rika_actions", _action), ("rika_approvals", _approval),
                         ("rika_plans", lambda row: _plan(row, conn)), (mail.HISTORY_TABLE, mail.draft_shape)):
        for row in conn.execute(f"SELECT * FROM {table} WHERE created_at<? ORDER BY created_at DESC LIMIT ?",
                                (bound, limit + 1)).fetchall():
            items.append((row["created_at"], shape(row)))
    items.sort(key=lambda item: item[0], reverse=True)
    page, more = items[:limit], len(items) > limit
    page.reverse()
    return {"items": [item for _, item in page], "has_more": more,
            "next_before": page[0][0] if more and page else None}

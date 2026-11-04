"""The agent mediator: every block-API call an agent token makes, journaled in its own transaction.

Paper §13 step 4 ("a semantic journal, written synchronously") and §14.1. An agent
tab's token reaches rika!'s notes only through `call`: one tool of `TOOLS`, run inside
one immediate SQLite transaction together with the ``agent_access`` rows that record
what it read or wrote (session, tool, op, purpose, block_id, seq, decision, policy
version and mode). If the journal cannot be written, the read is not returned and the
write is not kept: there is no unjournaled access.

The policy in force (kitty.core.agent_policy) decides per record. In ``enforce`` mode a
denied record is withheld from a listing (search hits, page lists, backlinks) or, where
a call is about one thing (a page, a citation check, any write), the whole call is
refused with 403 after its denials are journaled and its writes rolled back. In
``observe`` mode denials are journaled and the call is served.

`TOOLS` is the one list of what agents may call: the MCP notes tools (kitty.mcp_server),
``kitty notes`` and the HTTP route ``POST /api/ingress/agent/call`` are the same tools.
Each needs one token scope: ``notes.read``, ``search`` or ``notes.write``.

Agents propose effects here (kitty.core.effects: ``propose_delete_block``, a write of
that block for the policy) and read back only their own session's (``get_effect``).
No tool approves, executes or denies one: only the owner does, in rika!'s own window
behind its UI secret, which an agent token never is.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kitty.core import agent_policy, block_api, effect_kinds, effects
from kitty.core.blocks import BlockError, history, sessions
from kitty.core.blocks.actor import Actor, acting, current

SCOPES = ("notes.read", "notes.write", "search")
MAX_ARGUMENTS = 16


class AccessError(BlockError):
    """A call the mediator refuses (unknown tool, missing scope, policy denial)."""


# ------------------------------------------------------------------------ arguments

def _int(low: int, high: int):
    def check(name: str, value: Any) -> int:
        if type(value) is not int or not low <= value <= high:
            raise AccessError(f"{name} must be a whole number from {low} to {high}")
        return value
    return check


def _bool(name: str, value: Any) -> bool:
    if type(value) is not bool:
        raise AccessError(f"{name} must be true or false")
    return value


def _any(name: str, value: Any) -> Any:
    """Checked by the block API itself (references, texts, specs)."""
    return value


_REQUIRED = object()


@dataclass(frozen=True)
class Tool:
    name: str
    scope: str
    op: str
    #: argument -> (default or _REQUIRED, checker)
    params: dict[str, tuple[Any, Callable[[str, Any], Any]]]
    run: Callable[[sqlite3.Connection, dict, str], dict]
    #: ``[(block_id or None, purpose)]`` the result reached.
    records: Callable[[sqlite3.Connection, dict], list[tuple[str | None, str]]]
    #: For listings: the result without the denied block IDs (None: refuse the call).
    withhold: Callable[[dict, set[str]], dict | None] | None = None

    def arguments(self, given: Any) -> dict:
        if not isinstance(given, dict) or len(given) > MAX_ARGUMENTS:
            raise AccessError("arguments is an object of the tool's parameters")
        unknown = set(given) - set(self.params)
        if unknown:
            raise AccessError(f"{self.name} takes no {', '.join(sorted(map(str, unknown)))}")
        out = {}
        for name, (default, check) in self.params.items():
            if name in given and given[name] is not None:
                out[name] = check(name, given[name])
            elif default is _REQUIRED:
                raise AccessError(f"{self.name} needs {name}")
            else:
                out[name] = default
        return out


# --------------------------------------------------------------------------- records

def purposes_of(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    """Block ID -> purpose (from its root's type; tags are ``notes.tags``)."""
    found: dict[str, str] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for row in conn.execute(f"SELECT b.id, r.type FROM blocks b JOIN blocks r ON r.id=b.page_id "
                                f"WHERE b.id IN ({','.join('?' * len(chunk))})", chunk):
            found[row["id"]] = "notes.tags" if row["type"] == "tag" else agent_policy.BLOCK_PURPOSES.get(row["type"], "notes")
    return found


def _labelled(conn: sqlite3.Connection, ids: list[str | None]) -> list[tuple[str | None, str]]:
    unique = list(dict.fromkeys(bid for bid in ids if bid))
    labels = purposes_of(conn, unique)
    return [(bid, labels.get(bid, "notes")) for bid in unique]


def _tree(blocks: list[dict]) -> list[str]:
    out: list[str] = []
    for block in blocks:
        out.append(block["id"])
        out += _tree(block["children"])
    return out


def _page_records(conn, result):
    return _labelled(conn, [result["page"]["id"], *_tree(result["blocks"])])


def _receipt_records(conn, result):
    return _labelled(conn, [result.get("page_id"), *result.get("pages", {}), *(item["id"] for item in result.get("blocks", []))])


_BACKLINK_LISTS = ("references", "links", "mentions", "tagged")


def _backlink_records(conn, result):
    return _labelled(conn, [result["target"]["id"], *(item["block_id"] for key in _BACKLINK_LISTS for item in result[key]["blocks"])])


def _without_backlinks(result, denied):
    if result["target"]["id"] in denied:
        return None
    out = dict(result)
    for key in _BACKLINK_LISTS:
        kept = [item for item in result[key]["blocks"] if item["block_id"] not in denied]
        out[key] = {**result[key], "blocks": kept, "count": result[key]["count"] - (len(result[key]["blocks"]) - len(kept))}
    return out


def _without_pages(result, denied):
    kept = [page for page in result["pages"] if page["id"] not in denied]
    return {**result, "pages": kept, "total": result["total"] - (len(result["pages"]) - len(kept))}


def _without_hits(result, denied):
    kept = [hit for hit in result["hits"] if hit["block_id"] not in denied]
    return {**result, "hits": kept, "total": result["total"] - (len(result["hits"]) - len(kept))}


def _undo(conn, args, session):
    target = args["session"] or session
    if target not in {session, f"{session}:undo"}:
        raise AccessError("An agent token undoes only its own session", 403)
    return sessions.undo_session(conn, target, dry_run=args["dry_run"])


def _sessions(conn, args, session):
    return {**sessions.list_sessions(conn, kind="agent", limit=args["limit"]), "current": session}


# --------------------------------------------------------------------------- effects

#: The effect kinds an agent proposes and reads back here.
EFFECT_KINDS = frozenset({effect_kinds.DELETE_BLOCK.name})
PROPOSED = "Waiting for the owner's approval in rika!'s pending approvals. You cannot approve it."


def effect_state(conn: sqlite3.Connection, effect: effects.Effect) -> dict:
    """A stored effect as its proposer sees it: state, preview and every transition."""
    return {**effect.as_dict(), "events": effects.events(conn, effect.id)}


def propose_delete_block(conn: sqlite3.Connection, block: Any, reason: Any) -> dict:
    """Propose, as the current actor, moving `block` and its children to Trash."""
    try:
        effect = effects.propose(conn, effect_kinds.DELETE_BLOCK.name, {"block": block, "reason": reason},
                                 actor=current(), ctx=effect_kinds.Context(conn))
    except effects.EffectError as exc:
        raise AccessError(str(exc), exc.status) from None
    return {**effect_state(conn, effect), "notice": PROPOSED}


def get_effect(conn: sqlite3.Connection, effect_id: Any, session: str) -> dict:
    """One effect agent `session` proposed; any other is not found."""
    try:
        effect = effects.get(conn, effect_id)
    except effects.EffectError as exc:
        raise AccessError(str(exc), exc.status) from None
    if effect.kind not in EFFECT_KINDS or effect.actor.session != session:
        raise AccessError("Effect not found", 404)
    return effect_state(conn, effect)


def _effect_records(conn, result):
    return _labelled(conn, [result["payload"]["block"]])


TOOLS: dict[str, Tool] = {tool.name: tool for tool in (
    Tool("list_pages", "notes.read", "read",
         {"query": ("", _any), "kind": (None, _any), "limit": (50, _int(1, block_api.MAX_LIST))},
         lambda conn, a, s: block_api.list_pages(conn, a["query"], kind=a["kind"], limit=a["limit"]),
         lambda conn, r: _labelled(conn, [page["id"] for page in r["pages"]]), _without_pages),
    Tool("get_page", "notes.read", "read", {"page": (_REQUIRED, _any)},
         lambda conn, a, s: block_api.get_page(conn, a["page"]), _page_records),
    Tool("search_blocks", "search", "read", {"query": (_REQUIRED, _any), "limit": (20, _int(1, 200))},
         lambda conn, a, s: block_api.search_blocks(conn, a["query"], limit=a["limit"]),
         lambda conn, r: _labelled(conn, [hit["block_id"] for hit in r["hits"]]), _without_hits),
    Tool("get_backlinks", "notes.read", "read", {"target": (_REQUIRED, _any)},
         lambda conn, a, s: block_api.backlinks(conn, a["target"]), _backlink_records, _without_backlinks),
    Tool("list_tags", "notes.read", "read", {}, lambda conn, a, s: block_api.list_tags(conn),
         lambda conn, r: [(None, "notes.tags")]),
    Tool("check_citations", "notes.read", "read", {"cites": (_REQUIRED, _any)},
         lambda conn, a, s: history.citations(conn, a["cites"]),
         lambda conn, r: _labelled(conn, [item["id"] for item in r["citations"]])),
    Tool("list_agent_sessions", "notes.read", "read", {"limit": (20, _int(1, 200))}, _sessions,
         lambda conn, r: [(None, "notes.history")]),
    Tool("create_page", "notes.write", "write", {"title": (_REQUIRED, _any), "notebook": (None, _any), "blocks": (None, _any)},
         lambda conn, a, s: block_api.create_page(conn, a["title"], notebook=a["notebook"], blocks=a["blocks"]), _receipt_records),
    Tool("append_blocks", "notes.write", "write",
         {"page": (_REQUIRED, _any), "blocks": (_REQUIRED, _any), "parent": (None, _any), "revision": (None, _any)},
         lambda conn, a, s: block_api.append_blocks(conn, a["page"], a["blocks"], parent=a["parent"], revision=a["revision"]),
         _receipt_records),
    Tool("insert_blocks", "notes.write", "write",
         {"blocks": (_REQUIRED, _any), "after": (None, _any), "before": (None, _any), "parent": (None, _any),
          "revision": (None, _any)},
         lambda conn, a, s: block_api.insert_blocks(conn, a["blocks"], after=a["after"], before=a["before"],
                                                    parent=a["parent"], revision=a["revision"]), _receipt_records),
    Tool("update_block", "notes.write", "write",
         {"block": (_REQUIRED, _any), "text": (None, _any), "type": (None, _any), "props": (None, _any), "revision": (None, _any)},
         lambda conn, a, s: block_api.update_block(conn, a["block"], text=a["text"], kind=a["type"], props=a["props"],
                                                   revision=a["revision"]), _receipt_records),
    Tool("move_block", "notes.write", "write",
         {"block": (_REQUIRED, _any), "after": (None, _any), "before": (None, _any), "parent": (None, _any),
          "page": (None, _any), "revision": (None, _any)},
         lambda conn, a, s: block_api.move_block(conn, a["block"], after=a["after"], before=a["before"], parent=a["parent"],
                                                 page=a["page"], revision=a["revision"]), _receipt_records),
    Tool("delete_block", "notes.write", "write", {"block": (_REQUIRED, _any), "revision": (None, _any)},
         lambda conn, a, s: block_api.delete_block(conn, a["block"], revision=a["revision"]), _receipt_records),
    Tool("add_to_inbox", "notes.write", "write", {"blocks": (_REQUIRED, _any)},
         lambda conn, a, s: block_api.add_to_inbox(conn, a["blocks"]), _receipt_records),
    Tool("quick_note", "notes.write", "write", {"text": (_REQUIRED, _any)},
         lambda conn, a, s: block_api.quick_note(conn, a["text"]), _receipt_records),
    Tool("tag_block", "notes.write", "write", {"block": (_REQUIRED, _any), "tag": (_REQUIRED, _any), "create": (False, _bool)},
         lambda conn, a, s: block_api.tag_block(conn, a["block"], a["tag"], create=a["create"]), _receipt_records),
    Tool("undo_agent_session", "notes.write", "write", {"session": (None, _any), "dry_run": (False, _bool)}, _undo,
         lambda conn, r: _labelled(conn, [item["id"] for item in r["reverted"]])),
    Tool("propose_delete_block", "notes.write", "write", {"block": (_REQUIRED, _any), "reason": (None, _any)},
         lambda conn, a, s: propose_delete_block(conn, a["block"], a["reason"]), _effect_records),
    Tool("get_effect", "notes.read", "read", {"effect_id": (_REQUIRED, _any)},
         lambda conn, a, s: get_effect(conn, a["effect_id"], s), _effect_records),
)}


def run_local(conn: sqlite3.Connection, name: str, arguments: dict, session: str) -> dict:
    """One tool on a direct connection (``kitty mcp``/``kitty notes`` outside an agent tab): not mediated."""
    tool = TOOLS[name]
    return tool.run(conn, tool.arguments(arguments), session)


# ------------------------------------------------------------------------------ call

def _seq(conn: sqlite3.Connection, result: dict) -> int:
    value = result.get("seq") if isinstance(result, dict) else None
    if type(value) is int:
        return value
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM op_log").fetchone()[0]


def call(conn: sqlite3.Connection, session: dict, name: str, arguments: Any, *, now: datetime | None = None) -> dict:
    """Run tool `name` for agent `session` (``{id, kind, name, scopes}``) and journal it.

    Raises AccessError (403 scope or policy, 404 unknown tool, 400 arguments) or the
    block API's BlockError; a policy refusal is raised after its journal rows commit.
    """
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        raise AccessError("Unknown tool", 404)
    if tool.scope not in session["scopes"]:
        raise AccessError(f"This agent token was not granted the {tool.scope} scope", 403)
    args = tool.arguments(arguments)
    stamp = (now or datetime.now(UTC)).isoformat(timespec="milliseconds")
    refused: list[str] = []
    with acting(Actor("agent", session["name"], session["id"])):
        conn.execute("BEGIN IMMEDIATE")
        try:
            version, policy = agent_policy.active(conn)
            conn.execute("SAVEPOINT agent_tool")
            result = tool.run(conn, args, session["id"])
            records = tool.records(conn, result)
            decisions = [agent_policy.decide(policy, purpose=purpose, op=tool.op, tool=tool.name, agent=session["kind"])
                         for _, purpose in records]
            denied = {bid for (bid, _), decision in zip(records, decisions) if decision == "deny" and bid}
            if policy["mode"] == "enforce" and "deny" in decisions:
                kept = tool.withhold(result, denied) if tool.withhold and None not in {
                    bid for (bid, _), decision in zip(records, decisions) if decision == "deny"} else None
                if kept is None:
                    conn.execute("ROLLBACK TO agent_tool")
                    refused = sorted({purpose for (_, purpose), decision in zip(records, decisions) if decision == "deny"})
                else:
                    result = kept
            conn.execute("RELEASE agent_tool")
            seq = _seq(conn, result)
            conn.executemany(
                "INSERT INTO agent_access(session, tool, op, purpose, block_id, seq, decision, mode, policy_version, at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(session["id"], tool.name, tool.op, purpose, bid, seq, decision, policy["mode"], version, stamp)
                 for (bid, purpose), decision in zip(records, decisions)])
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    if refused:
        raise AccessError(f"rika!'s agent policy (version {version}) does not allow {tool.op} of {', '.join(refused)}", 403)
    return result


def journal(conn: sqlite3.Connection, session: str, *, limit: int = 200, before: int | None = None) -> dict:
    """One session's journal, newest first: ``{rows, next}`` (``next`` pages further back)."""
    rows = [dict(row) for row in conn.execute(
        "SELECT id, tool, op, purpose, block_id, seq, decision, mode, policy_version, at FROM agent_access "
        "WHERE session=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?", (session, before, before, limit + 1))]
    more = len(rows) > limit
    rows = rows[:limit]
    return {"rows": rows, "next": rows[-1]["id"] if more else None}

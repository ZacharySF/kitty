"""Rika's routing memory (design Q13, migration 049): what worked, per kind of request.

A finished turn is recorded under the *signature* of the tool path that served it
(consecutive repeats collapsed, joined with ``>``; ``""`` is a reply with no tool):

* ``tier``: the cheapest tier that succeeded: ``direct`` for deterministic and shortcut
  turns; the tier a turn finished on when it had to escalate; otherwise the lower of
  the stored tier and this turn's;
* ``path_json``: the shortest successful tool path; uses, escalations, tokens and cost
  are totals.

The request's *shape* (:func:`shape`) links it to the route, so the next request of the
same kind starts on the remembered tier instead of the router's heuristic.

Learned shortcuts: when a model turn made exactly one tool call, a ``change`` (never an
outward tool), and every string argument occurs verbatim in the request, the request
becomes a template with those arguments as slots (``quiz me on {front}, answer {back}``).
Seen twice, the template runs like a deterministic phrase with no model; undoing an
action it made deletes it.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from kitty.core.rika import commands, store, tools as toolset
from kitty.db.connection import transaction

ORDER = ("direct", "fast", "standard", "deep")
#: The nouns of Rika's tool names: the thing a request acts on.
OBJECTS = frozenset({"card", "node", "task", "event", "page", "block", "topic", "goal", "mail", "reply", "draft",
                     "measure", "subject", "map", "job", "note", "canvas", "view", "worker", "preference", "deck"})
SYNONYMS = {"flashcard": "card", "todo": "task", "sticky": "node", "email": "mail", "notebook": "note",
            "meeting": "event", "appointment": "event", "agent": "worker", "mindmap": "map"}
STOP = frozenset("""
a an the to of for in on at by with about from into onto and or but please could would can will you me my mine i im
we our us your it its this that these those is are was were be been being do does did have has had some any just
also so very really there here then than hey hi rika thanks thank ok okay let lets want need should shall may might
must am up out off over again kindly quickly maybe all one
""".split())
SHORTCUT_ACTIVE = 2
EXAMPLE_MAX = 200
TREND_DAYS = 14
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
_SLOT = re.compile(r"\{(\w+)\}")
_LITERAL_WORD = re.compile(r"[^\W\d_]{3,}")


@dataclass(frozen=True)
class Recall:
    tier: str
    signature: str
    path: list[str]


@dataclass
class ShortcutPlan(commands.Plan):
    """A plan from a learned shortcut; `template` identifies it for uses and undo."""
    template: str = ""


# Shapes -----------------------------------------------------------------------

def _normal(word: str) -> str:
    word = word.removesuffix("'s").replace("'", "")
    word = SYNONYMS.get(word, word)
    if word not in OBJECTS and word.endswith("s") and (word[:-1] in OBJECTS or SYNONYMS.get(word[:-1]) in OBJECTS):
        word = SYNONYMS.get(word[:-1], word[:-1])
    return word


def words(text: str) -> list[str]:
    """The request's content words, lowercased, without stop words or numbers."""
    found = (_normal(word) for word in _WORD.findall(text.lower()))
    return [word for word in found if word and word not in STOP and len(word) > 1]


def shape(text: str) -> str:
    """A stable shape for a request: its first content word and the object it acts on
    (the first object word after it, else its second content word)."""
    content = words(text)
    if not content:
        return ""
    rest = content[1:]
    target = next((word for word in rest if word in OBJECTS), rest[0] if rest else None)
    return " ".join(part for part in (content[0], target) if part)


def signature(path: list[str]) -> str:
    collapsed = [name for index, name in enumerate(path) if index == 0 or path[index - 1] != name]
    return ">".join(collapsed)


def label(signature_: str) -> str:
    if not signature_:
        return "Talk"
    return " → ".join(name.replace("_", " ").capitalize() for name in signature_.split(">"))


def recall(conn: sqlite3.Connection, text: str) -> Recall | None:
    key = shape(text)
    if not key:
        return None
    row = conn.execute("SELECT r.signature,r.tier,r.path_json FROM rika_route_shapes s JOIN rika_routes r"
                       " ON r.signature=s.signature WHERE s.shape=?", (key,)).fetchone()
    return Recall(row["tier"], row["signature"], json.loads(row["path_json"])) if row else None


# Recording --------------------------------------------------------------------

def record(conn: sqlite3.Connection, *, text: str, path: list[str], start_tier: str | None, final_tier: str | None,
           escalated: int, tokens: int, cost: Decimal | None, deterministic: bool,
           call: tuple[str, dict] | None = None, shortcut: str | None = None) -> None:
    """Remember one successful turn; `call` is its only tool call (to learn a shortcut),
    `shortcut` the template that served it."""
    key, stamp = signature(path), store.now()
    tier = "direct" if deterministic else (final_tier or start_tier or "fast")
    with transaction(conn, immediate=True):
        row = conn.execute("SELECT * FROM rika_routes WHERE signature=?", (key,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO rika_routes(signature,label,tier,path_json,uses,escalations,tokens,cost_usd,example,"
                         "created_at,updated_at) VALUES(?,?,?,?,1,?,?,?,?,?,?)",
                         (key, label(key), tier, json.dumps(path), escalated, tokens, str(cost or 0),
                          text[:EXAMPLE_MAX], stamp, stamp))
        else:
            if not escalated:
                tier = min(row["tier"], tier, key=ORDER.index)
            stored = json.loads(row["path_json"])
            conn.execute("UPDATE rika_routes SET tier=?,path_json=?,uses=uses+1,escalations=escalations+?,tokens=tokens+?,"
                         "cost_usd=?,example=?,updated_at=? WHERE signature=?",
                         (tier, json.dumps(path if len(path) < len(stored) else stored), escalated, tokens,
                          str(Decimal(row["cost_usd"]) + (cost or 0)), text[:EXAMPLE_MAX], stamp, key))
        shaped = shape(text)
        if shaped:
            conn.execute("INSERT INTO rika_route_shapes(shape,signature,updated_at) VALUES(?,?,?) ON CONFLICT(shape)"
                         " DO UPDATE SET signature=excluded.signature,updated_at=excluded.updated_at", (shaped, key, stamp))
        if shortcut is not None:
            conn.execute("UPDATE rika_shortcuts SET uses=uses+1,updated_at=? WHERE template=?", (stamp, shortcut))
        elif call is not None and not deterministic and path == [call[0]]:
            _learn(conn, text, call, key, stamp)


def _clean(text: str) -> str:
    return " ".join(text.strip().rstrip(".!?").split())


def template(text: str, tool: str, args: dict) -> tuple[str, dict] | None:
    """The request as a template whose slots are the call's string arguments, and the
    call's other arguments as constants; None when it cannot be one."""
    phrase = _clean(text)
    if "{" in phrase or "}" in phrase:
        return None
    strings = {name: value for name, value in args.items() if isinstance(value, str)}
    if not strings:
        return None
    spans: list[tuple[int, int, str]] = []
    for name, value in sorted(strings.items(), key=lambda item: -len(item[1])):
        value = value.strip()
        if not value:
            return None
        found = next((match.span() for match in re.finditer(re.escape(value), phrase, re.I)
                      if not any(start < match.end() and match.start() < end for start, end, _ in spans)), None)
        if found is None:
            return None
        spans.append((*found, name))
    spans.sort()
    parts, literal, position = [], [], 0
    for start, end, name in spans:
        literal.append(phrase[position:start])
        parts += [phrase[position:start].lower(), "{" + name + "}"]
        position = end
    literal.append(phrase[position:])
    parts.append(phrase[position:].lower())
    if not _LITERAL_WORD.search(" ".join(literal)):
        return None
    return "".join(parts), {name: value for name, value in args.items() if name not in strings}


def _learn(conn: sqlite3.Connection, text: str, call: tuple[str, dict], key: str, stamp: str) -> None:
    tool = toolset.registry().get(call[0])
    if tool is None or tool.effect != "change":
        return
    made = template(text, *call)
    if made is None:
        return
    pattern, constants = made
    encoded = json.dumps(constants, sort_keys=True)
    row = conn.execute("SELECT tool,constants_json FROM rika_shortcuts WHERE template=?", (pattern,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO rika_shortcuts(template,signature,tool,constants_json,seen,uses,created_at,updated_at)"
                     " VALUES(?,?,?,?,1,0,?,?)", (pattern, key, tool.name, encoded, stamp, stamp))
    elif (row["tool"], row["constants_json"]) == (tool.name, encoded):
        conn.execute("UPDATE rika_shortcuts SET seen=seen+1,updated_at=? WHERE template=?", (stamp, pattern))
    else:  # the same words now meant something else: start counting again
        conn.execute("UPDATE rika_shortcuts SET signature=?,tool=?,constants_json=?,seen=1,uses=0,updated_at=?"
                     " WHERE template=?", (key, tool.name, encoded, stamp, pattern))


def _pattern(template_: str) -> re.Pattern:
    pieces, position = [], 0
    for match in _SLOT.finditer(template_):
        pieces += [re.escape(template_[position:match.start()]), f"(?P<{match[1]}>.+?)"]
        position = match.end()
    pieces.append(re.escape(template_[position:]))
    return re.compile("".join(pieces), re.I | re.S)


def shortcut(conn: sqlite3.Connection, text: str, available: dict) -> ShortcutPlan | None:
    """The plan of the most specific active shortcut matching `text` whose tool is available."""
    phrase = _clean(text)
    rows = conn.execute("SELECT template,tool,constants_json FROM rika_shortcuts WHERE seen>=?"
                        " ORDER BY length(template) DESC,seen DESC", (SHORTCUT_ACTIVE,)).fetchall()
    for row in rows:
        tool = available.get(row["tool"])
        match = _pattern(row["template"]).fullmatch(phrase) if tool is not None else None
        if match is None:
            continue
        slots = {name: value.strip() for name, value in match.groupdict().items()}
        if not all(slots.values()):
            continue
        args = {**json.loads(row["constants_json"]), **slots}
        try:
            toolset.validate(tool.parameters, args)
        except toolset.ToolError:
            continue
        return ShortcutPlan([(tool.name, args)], template=row["template"])
    return None


def forget_shortcut(conn: sqlite3.Connection, template_: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM rika_shortcuts WHERE template=?", (template_,))


def undone(conn: sqlite3.Connection, message_id: str) -> None:
    """An action of `message_id` was undone: a shortcut that made it is forgotten."""
    row = conn.execute("SELECT route_json FROM rika_messages WHERE id=?", (message_id,)).fetchone()
    route = json.loads(row["route_json"]) if row and row["route_json"] else {}
    if route.get("template"):
        forget_shortcut(conn, route["template"])


def forget(conn: sqlite3.Connection, signature_: str) -> None:
    """Forget a route with its shapes and shortcuts."""
    with transaction(conn):
        if conn.execute("DELETE FROM rika_routes WHERE signature=?", (signature_,)).rowcount == 0:
            raise LookupError("Route not found")


# Readouts ---------------------------------------------------------------------

def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.00000001")))


def listing(conn: sqlite3.Connection) -> list[dict]:
    """Routes, most used first."""
    shortcuts: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM rika_shortcuts ORDER BY seen>=? DESC,uses DESC,seen DESC,updated_at DESC",
                            (SHORTCUT_ACTIVE,)):
        shortcuts.setdefault(row["signature"], {"template": row["template"], "uses": row["uses"],
                                                "active": row["seen"] >= SHORTCUT_ACTIVE})
    found = []
    for row in conn.execute("SELECT * FROM rika_routes ORDER BY uses DESC,updated_at DESC"):
        uses = max(row["uses"], 1)
        found.append({"signature": row["signature"], "label": row["label"], "tier": row["tier"],
                      "path": json.loads(row["path_json"]), "uses": row["uses"], "escalations": row["escalations"],
                      "avg_tokens": row["tokens"] // uses, "avg_cost_usd": _money(Decimal(row["cost_usd"]) / uses),
                      "shortcut": shortcuts.get(row["signature"]), "example": row["example"],
                      "updated_at": row["updated_at"]})
    return found


def summary(conn: sqlite3.Connection) -> dict:
    """Today's requests, tokens and cost, the cached share of input tokens and the
    average cost per request over the last 14 local days (oldest first)."""
    zone = ZoneInfo(conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"])
    today = datetime.now(zone).date()
    first = today - timedelta(days=TREND_DAYS - 1)
    start = datetime.combine(first, time(), zone).astimezone(timezone.utc).isoformat(timespec="microseconds")
    days = {first + timedelta(days=offset): [0, Decimal(0)] for offset in range(TREND_DAYS)}
    tokens = prompt = cached = 0
    for row in conn.execute("SELECT created_at,cost_usd,tokens_in,tokens_cached,tokens_out FROM rika_messages"
                            " WHERE role='rika' AND reply_to IS NOT NULL AND status<>'pending' AND created_at>=?", (start,)):
        day = datetime.fromisoformat(row["created_at"]).astimezone(zone).date()
        if day not in days:
            continue
        days[day][0] += 1
        days[day][1] += Decimal(row["cost_usd"] or 0)
        prompt += row["tokens_in"] or 0
        cached += row["tokens_cached"] or 0
        if day == today:
            tokens += (row["tokens_in"] or 0) + (row["tokens_out"] or 0)
    requests, cost = days[today]
    return {"today_requests": requests, "today_tokens": tokens, "today_cost_usd": _money(cost),
            "cached_share": min(1.0, cached / prompt) if prompt else 0,
            "trend": [{"day": day.isoformat(), "requests": count, "avg_cost_usd": _money(total / count) if count else 0}
                      for day, (count, total) in days.items()]}


# Demo -------------------------------------------------------------------------

_DEMO = (
    # signature, tier, path, uses, escalations, tokens per use, cost per use, example, shape
    ("add_canvas_node", "direct", ["add_canvas_node"], 23, 0, 0, "0", "add a node Photosynthesis", "add node"),
    ("open_view", "direct", ["open_view"], 31, 0, 0, "0", "open calendar", "open calendar"),
    ("add_card", "fast", ["add_card"], 12, 1, 1180, "0.0002", "make a flashcard about the Krebs cycle", "make card"),
    ("search>add_card", "fast", ["search", "add_card"], 5, 0, 2400, "0.0004",
     "find my biology deck and add a card on osmosis", "find deck"),
    ("create_event", "standard", ["create_event"], 4, 1, 6240, "0.0041",
     "plan a study session before Friday's exam", "plan study"),
)
DEMO_SHORTCUT = "flashcard {front} means {back}"


def seed_demo(conn: sqlite3.Connection) -> None:
    """Synthetic routes, one active shortcut and two preferences for `kitty-web --demo`."""
    from kitty.core.rika import memory

    if conn.execute("SELECT 1 FROM rika_routes LIMIT 1").fetchone():
        return
    stamp = store.now()
    with transaction(conn):
        for key, tier, path, uses, escalations, tokens, cost, example, shaped in _DEMO:
            conn.execute("INSERT INTO rika_routes(signature,label,tier,path_json,uses,escalations,tokens,cost_usd,example,"
                         "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (key, label(key), tier, json.dumps(path), uses, escalations, tokens * uses,
                          str(Decimal(cost) * uses), example, stamp, stamp))
            conn.execute("INSERT INTO rika_route_shapes(shape,signature,updated_at) VALUES(?,?,?)", (shaped, key, stamp))
        conn.execute("INSERT INTO rika_shortcuts(template,signature,tool,constants_json,seen,uses,created_at,updated_at)"
                     " VALUES(?,'add_card','add_card','{}',3,2,?,?)", (DEMO_SHORTCUT, stamp, stamp))
    memory.add_preference(conn, "Keep replies short", source="added")
    memory.add_preference(conn, "Put new flashcards in the Biology deck", source="stated")

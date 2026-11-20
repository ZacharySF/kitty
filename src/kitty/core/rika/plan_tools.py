"""Rika's planning reads: the calendar's days ahead, opportunities and the Brain's records.

All three only look. The calendar reads its own zone and hides what the Calendar view
hides (hidden calendars; ApplyAI's events while that module is off). The Brain's nodes
are titles and names only (kitty.web_brain), so they are safe to give the model.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kitty import web_brain
from kitty.core import calendar, opportunities
from kitty.core.rika.tools import Context, Result, Tool, ToolError, boolean, obj, string
from kitty.core.rika.workspace_tools import _calendar_zone

LIST_LIMIT = 40
OPPORTUNITY_LIMIT = 30
BRAIN_LIMIT = 20
#: Longest calendar range one read covers.
MAX_DAYS = 62
_OPPORTUNITY_KEYS = ("id", "title", "category", "status", "deadline", "start", "end", "location", "remote", "url",
                     "favorite", "availability")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# Calendar ---------------------------------------------------------------------

def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError("Use a date such as 2026-11-20") from None


def _task_day(due: str, zone: ZoneInfo) -> date:
    return date.fromisoformat(due) if len(due) == 10 else datetime.fromisoformat(due).astimezone(zone).date()


def _list_events(ctx: Context, args: dict) -> Result:
    conn = ctx.conn
    zone = ZoneInfo(_calendar_zone(conn))
    today = datetime.now(zone).date()
    start = _day(args["start"]) if "start" in args else today
    end = _day(args["end"]) if "end" in args else start + timedelta(days=6)
    if end < start:
        raise ToolError("The end date comes before the start date")
    if (end - start).days >= MAX_DAYS:
        raise ToolError(f"Choose at most {MAX_DAYS} days")
    lower, upper = datetime.combine(start, time.min, zone), datetime.combine(end + timedelta(days=1), time.min, zone)
    found = calendar.occurrences(conn, lower.isoformat(), upper.isoformat(), search=args.get("query", ""),
                                 applyai="applyai" in ctx.app.modules)
    events = [{"id": item["id"], "title": item["title"], "start": item["start"], "end": item["end"],
               "all_day": item["all_day"], **({"location": item["location"]} if item["location"] else {}),
               **({"occurrence": item["occurrence"]} if item["rrule"] else {})} for item in found]
    # Open tasks due in the range; while it includes today, overdue ones too.
    query = args.get("query", "")
    tasks = []
    for item in calendar.tasks(conn, query):
        if item["status"] not in ("todo", "doing") or not item.get("due"):
            continue
        due = _task_day(item["due"], zone)
        overdue = due < today
        if due <= end and (due >= start or (overdue and start <= today <= end)):
            tasks.append({"id": item["id"], "title": item["title"], "due": item["due"], "priority": item["priority"],
                          "status": item["status"], "overdue": overdue})
    tasks.sort(key=lambda task: task["due"])
    span = f"{start:%a %d %b}" + ("" if end == start else f" to {end:%a %d %b}")
    return Result(f"{_plural(len(events), 'event')} and {_plural(len(tasks), 'open task')}, {span}", {
        "start": start.isoformat(), "end": end.isoformat(), "timezone": str(zone),
        "events": events[:LIST_LIMIT], "more_events": max(0, len(events) - LIST_LIMIT),
        "tasks": tasks[:LIST_LIMIT], "more_tasks": max(0, len(tasks) - LIST_LIMIT)})


# Opportunities ----------------------------------------------------------------

def _list_opportunities(ctx: Context, args: dict) -> Result:
    query = {key: value for key, value in (("search", args.get("query")), ("status", args.get("status")),
                                           ("availability", args.get("availability"))) if value}
    if args.get("favorites"):
        query["favorites"] = "true"
    rows = opportunities.list_items(ctx.conn, query)
    if "status" not in args:
        rows = [row for row in rows if row["status"] != "skipped"]
    found = [{key: row.get(key) for key in _OPPORTUNITY_KEYS} for row in rows[:OPPORTUNITY_LIMIT]]
    noun = "opportunity" if len(rows) == 1 else "opportunities"
    return Result(f"Found {len(rows)} {noun}", {"opportunities": found, "more": max(0, len(rows) - OPPORTUNITY_LIMIT)})


# Brain ------------------------------------------------------------------------

def _node(node: dict) -> dict:
    # A Brain section id is the id of the view that shows it.
    return {"id": node["id"], "section": node["section"], "type": node["type"], "label": node["label"],
            "view": node["section"]}


def _find_brain_nodes(ctx: Context, args: dict) -> Result:
    if "query" not in args and "node_id" not in args:
        raise ToolError("Give words to look for or a node id")
    records = [node for node in web_brain.brain(ctx.conn, web_brain.MAX_LIMIT, ctx.app.modules)["nodes"]
               if node["kind"] == "record"]
    if "node_id" in args:
        node = next((node for node in records if node["id"] == args["node_id"]), None)
        if node is None:
            raise ToolError(f"No Brain node has the id {args['node_id']!r}")
        parent = next((item for item in records if item["id"] == node["parent"]), None)
        # Outline blocks have generic labels ("Paragraph block"); they are counted, not listed.
        children = [item for item in records if item["parent"] == node["id"]]
        named = [_node(item) for item in children if item["type"] != "block"]
        return Result(f"Read {node['label']}", {
            "node": _node(node), "parent": _node(parent) if parent else None, "children": named[:LIST_LIMIT],
            "more_children": max(0, len(named) - LIST_LIMIT), "blocks": len(children) - len(named)})
    query = args["query"].casefold()
    matches = [_node(node) for node in records if node["type"] != "block" and query in node["label"].casefold()
               and args.get("section") in (None, node["section"])]
    return Result(f"Found {_plural(len(matches), 'Brain node')}",
                  {"nodes": matches[:BRAIN_LIMIT], "more": max(0, len(matches) - BRAIN_LIMIT)})


# Declarations -----------------------------------------------------------------

TOOLS = (
    Tool("list_events", "List calendar events and open tasks for a range of days in the calendar's time zone; "
         "defaults to the 7 days from today, with overdue tasks. Use it for \"what's on this week\" or before planning.",
         obj(start=string("First day, YYYY-MM-DD; defaults to today", maximum=10),
             end=string("Last day, YYYY-MM-DD; defaults to 6 days after start", maximum=10),
             query=string("Only items whose text matches", minimum=0, maximum=200)),
         "read", _list_events, module="workspace"),
    Tool("list_opportunities", "List saved opportunities (fellowships, competitions, programmes, events) best match "
         "first, with deadlines and whether they are open. Skipped ones are left out unless status asks for them.",
         obj(query=string("Words to match", minimum=0, maximum=200),
             status=string("Status", enum=opportunities.STATUSES),
             availability=string("Open now (active), later (future), closed or unknown",
                                 enum=("active", "future", "closed", "unknown")),
             favorites=boolean("Only favourites")),
         "read", _list_opportunities, module="workspace"),
    Tool("find_brain_nodes", "Find records in the Brain (every page, notebook, deck, subject, exam, event, document, "
         "job and more, by title) or read one node with its parent and children by node_id. Each node's view "
         "can be opened with open_view.",
         obj(query=string("Title words", maximum=200), section=string("Only this section", enum=tuple(web_brain.SECTIONS)),
             node_id=string("Brain node id, e.g. notes.page:12", maximum=200)),
         "read", _find_brain_nodes, module="brain"),
)

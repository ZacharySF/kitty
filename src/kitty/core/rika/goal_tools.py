"""Rika's goal tools (design Q14): list_goals, create_goal, link_to_goal, update_measure,
and the read-only morning_brief (Q15)."""
from __future__ import annotations

from kitty.core import goals
from kitty.core.goals import GoalError
from kitty.core.rika import brief
from kitty.core.rika.tools import (
    Context,
    Result,
    Tool,
    ToolError,
    integer,
    number,
    obj,
    string,
)

_DATE = "YYYY-MM-DD"


def _goal_id(ctx: Context, args: dict) -> int:
    """The goal by id, else by exact title (case-insensitive), newest first."""
    if "goal_id" in args:
        return args["goal_id"]
    if "goal" not in args:
        raise ToolError("Say which goal: goal_id or its title")
    row = ctx.conn.execute("SELECT id FROM goals WHERE title=? COLLATE NOCASE ORDER BY status<>'active',id DESC LIMIT 1",
                           (args["goal"].strip(),)).fetchone()
    if row is None:
        raise ToolError(f"No goal is called {args['goal']!r}")
    return row["id"]


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except GoalError as exc:
        raise ToolError(str(exc)) from None


def _brief_goal(goal: dict) -> dict:
    return {"id": goal["id"], "title": goal["title"], "status": goal["status"], "target_date": goal["target_date"],
            "days_left": goal["days_left"], "progress": goal["progress"],
            "measures": [{key: item[key] for key in ("id", "label", "current", "target", "unit", "auto")}
                         for item in goal["measures"]]}


def _list_goals(ctx: Context, args: dict) -> Result:
    found = [_brief_goal(goal) for goal in goals.listing(ctx.conn)]
    active = [goal for goal in found if goal["status"] == "active"]
    if not found:
        return Result("You have no goals yet", {"goals": []})
    names = ", ".join(f"{goal['title']} ({round(goal['progress'] * 100)}%)" for goal in active[:4])
    return Result(f"You have {len(active)} active goal{'s' if len(active) != 1 else ''}" + (f": {names}" if names else ""),
                  {"goals": found})


def _create_goal(ctx: Context, args: dict) -> Result:
    goal = _call(goals.create, ctx.conn, args)
    return Result(f"Added the goal {goal['title']}", {"goal_id": goal["id"]},
                  undo={"goal_id": goal["id"], "revision": goal["revision"]})


def _undo_create_goal(ctx: Context, undo: dict) -> None:
    if not ctx.conn.execute("SELECT 1 FROM goals WHERE id=?", (undo["goal_id"],)).fetchone():
        return
    try:
        goals.delete(ctx.conn, undo["goal_id"], undo["revision"])
    except GoalError:
        raise ToolError("The goal changed since Rika created it, so it was kept") from None


def _link_to_goal(ctx: Context, args: dict) -> Result:
    goal_id = _goal_id(ctx, args)
    kind, ref = args["kind"], args["ref"]
    before = goals.linked(ctx.conn, goal_id, kind, ref)
    goal = _call(goals.link, ctx.conn, goal_id, kind, ref, modules=ctx.app.modules)
    item = next(link for link in goal["links"] if (link["kind"], link["ref"]) == (kind, ref))
    summary = f"Linked {item['title']} to {goal['title']}"
    return Result(summary if not before else f"{item['title']} was already linked to {goal['title']}",
                  {"goal_id": goal_id, "links_count": goal["links_count"]},
                  undo=None if before else {"goal_id": goal_id, "kind": kind, "ref": ref})


def _undo_link_to_goal(ctx: Context, undo: dict) -> None:
    if ctx.conn.execute("SELECT 1 FROM goals WHERE id=?", (undo["goal_id"],)).fetchone():
        goals.unlink(ctx.conn, undo["goal_id"], undo["kind"], undo["ref"])


def _measure(goal: dict, reference: str) -> dict:
    for item in goal["measures"]:
        if item["id"] == reference or item["label"].casefold() == reference.strip().casefold():
            return item
    raise ToolError(f"{goal['title']} has no measure {reference!r}")


def _update_measure(ctx: Context, args: dict) -> Result:
    goal = _call(goals.get, ctx.conn, _goal_id(ctx, args))
    item = _measure(goal, args["measure"])
    updated = _call(goals.set_measure, ctx.conn, goal["id"], item["id"], args["current"])
    after = _measure(updated, item["id"])
    unit = after["unit"] if after["unit"] in ("", "%") else f" {after['unit']}"
    return Result(f"Set {after['label']} to {after['current']:g}{unit} of {after['target']:g} for {goal['title']}",
                  {"goal_id": goal["id"], "progress": updated["progress"]},
                  undo={"goal_id": goal["id"], "measure_id": item["id"], "before": item["current"], "after": after["current"]})


def _undo_update_measure(ctx: Context, undo: dict) -> None:
    try:
        goals.set_measure(ctx.conn, undo["goal_id"], undo["measure_id"], undo["before"], expected=undo["after"])
    except GoalError:
        raise ToolError("The measure changed since, so Rika's change was kept") from None


def _morning_brief(ctx: Context, args: dict) -> Result:
    today = brief.get(ctx.conn)
    if today is None:
        return Result("There's no morning brief yet today; it comes after 5:00 while rika! is open", {"brief": None})
    text = today["message_id"] and ctx.conn.execute("SELECT text FROM rika_messages WHERE id=?",
                                                    (today["message_id"],)).fetchone()
    items = [{key: item[key] for key in ("goal_title", "kind", "title", "detail", "urgent", "due_at", "status")}
             for item in today["items"]]
    return Result(text["text"] if text else f"Today's brief has {len(items)} item{'s' if len(items) != 1 else ''}",
                  {"day": today["day"], "items": items, "mail": today["mail"], "notes": today["notes"]})


_GOAL = {"goal_id": integer("Goal id"), "goal": string("Goal title, when the id is unknown", maximum=200)}
_MEASURE = obj(("label", "target"), label=string("What is measured, e.g. Applications sent", maximum=120),
               target=number("Target value, above 0", minimum=0.000001, maximum=1e9),
               unit=string("Unit, e.g. min or %", minimum=0, maximum=16),
               current=number("Current value", minimum=0, maximum=1e9),
               auto=string("Compute current from linked data instead", enum=goals.AUTO))

TOOLS = (
    Tool("list_goals", "List the user's goals with their measures, progress and days left.", obj(), "read", _list_goals),
    Tool("create_goal", "Create a goal: a title, why it matters, an optional target date and measures.",
         obj(("title",), title=string("Goal title", maximum=200), why=string("Why it matters", minimum=0, maximum=2000),
             target_date=string(f"Target date {_DATE}", maximum=10),
             measures={"type": "array", "items": _MEASURE, "maxItems": goals.MAX_MEASURES}),
         "change", _create_goal, _undo_create_goal),
    Tool("link_to_goal", "Link a note page, block, calendar task or event, job, study session, math topic, "
         "opportunity or email to a goal, so it counts toward it.",
         obj(("kind", "ref"), kind=string("What is linked", enum=tuple(goals.LINK_KINDS)),
             ref=string("The item's id", maximum=200), **_GOAL),
         "change", _link_to_goal, _undo_link_to_goal),
    Tool("update_measure", "Set the current value of one of a goal's manual measures.",
         obj(("measure", "current"), measure=string("Measure id or label", maximum=120),
             current=number("New current value", minimum=0, maximum=1e9), **_GOAL),
         "change", _update_measure, _undo_update_measure),
    Tool("morning_brief", "Read today's morning brief: what Rika found for the user's goals, and email it handled.",
         obj(), "read", _morning_brief),
)

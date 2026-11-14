"""Confirm before act: is the date of an item Rika is about to add clear enough to write?

``create_event``, ``create_task`` and ``create_study_task`` carry their date as an ISO
value and, in ``said``, the user's own words for it. :func:`check` reads those words
(:mod:`~kitty.core.rika.when`) and decides. The call runs at once (with Undo, design
Q9 A) when the words have one reading, it lies ahead and the ISO value agrees with it.
Otherwise Rika asks one question whose choices are the readings:

* the words can mean more than one day or time;
* an event names a day but no time (Morning, Afternoon, All day);
* the date has passed;
* the ISO value is none of the readings of the words.

The question is an item of a ``confirm`` plan (:func:`items`): each choice holds the
whole tool call for one reading, so approving runs it through the ordinary plan runner
and nothing waits on a thread. :func:`override` is "Another date": a day (and time) the
user picked instead, written into the same argument and validated by the tool's schema.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from kitty.core.rika import tools as toolset, when

#: The argument that holds each dated tool's date.
DATED = {"create_event": "start", "create_task": "due", "create_study_task": "due_date", "reschedule_task": "due"}
#: The tools :func:`check` asks about, and the plan area of each.
ASKED = {"create_event": "calendar", "create_task": "tasks", "create_study_task": "tasks"}
NEEDS_USER = {"status": "needs_user", "summary": "Waiting for the user to pick the date; do not retry"}


def _value(raw: Any, zone: ZoneInfo) -> tuple[date, time | None] | None:
    """A tool's ISO date or time as a local day and time; None when it is not one."""
    if not isinstance(raw, str):
        return None
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw), None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(zone)
    return parsed.date(), parsed.time().replace(second=0, microsecond=0, tzinfo=None)


def with_date(tool: str, args: dict, day: date, at: time | None) -> dict:
    """`args` of a dated tool with its date set to `day` (at `at`, or the whole day)."""
    slot = DATED[tool]
    changed = {key: value for key, value in args.items() if key != "said"}
    study = tool == "create_study_task" or args.get("kind") == "study_task"
    stamp = day.isoformat() if at is None or study else f"{day.isoformat()}T{at:%H:%M}"
    if tool == "create_event":
        length = None
        if at is not None and not args.get("all_day") and "end" in args:
            try:
                length = datetime.fromisoformat(args["end"].replace("Z", "+00:00")) \
                    - datetime.fromisoformat(args["start"].replace("Z", "+00:00"))
            except (ValueError, TypeError):  # an end the tool would refuse, or one zone given and one not
                length = None
        changed.pop("end", None)
        changed.pop("all_day", None)
        if at is None:
            changed["all_day"] = True
        elif length is not None and timedelta(0) < length <= timedelta(days=1):
            changed["end"] = (datetime.combine(day, at) + length).isoformat(timespec="minutes")
    changed[slot] = stamp
    return changed


def _past(day: date, at: time | None, local: datetime) -> bool:
    return day < local.date() if at is None else datetime.combine(day, at) < local


def _agrees(reading: when.Reading, value: tuple[date, time | None] | None, event: bool) -> bool:
    """The ISO value is this reading: the same day, and the same time where both (or an event) have one."""
    if value is None or reading.day != value[0]:
        return False
    if reading.time is None or (value[1] is None and not event):
        return True
    return reading.time == value[1]


def _listing(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]


def check(tool: str, args: dict, *, now: datetime, zone: ZoneInfo) -> dict | None:
    """None when the call may run now; else what to ask: ``{tool, title, said, text, choices, choice}``,
    each choice a ``{label, args}`` of the whole call for one reading."""
    if tool not in ASKED:
        return None
    slot, said = DATED[tool], args.get("said") or ""
    event = tool == "create_event"
    raw = args.get(slot)
    value = _value(raw, zone)
    if raw is not None and value is None:
        return None  # not a date: the tool says so itself
    local = now.astimezone(zone).replace(tzinfo=None)
    today = local.date()
    found = when.readings(said, now=now, zone=zone, need_time=event and (value is None or value[1] is None))
    options = list(found.readings)
    if tool == "create_study_task":  # a study task is due on a day, never at a time
        options = [when.Reading(day, None, when.label(day, None, today))
                   for day in dict.fromkeys(option.day for option in options)]
    title = args.get("title", "")
    if not options:
        # No words to read: the ISO value stands unless it has passed.
        if value is None or not _past(*value, local):
            return None
        options = [when.Reading(*value, when.label(*value, today))]
    agreeing = [index for index, option in enumerate(options) if _agrees(option, value, event)]
    gone = len(options) == 1 and _past(options[0].day, options[0].time, local)
    if len(options) == 1 and agreeing and not gone:
        return None
    if gone:
        at = options[0].time
        later = [today, today + timedelta(days=1)]
        options += [when.Reading(day, at, when.label(day, at, today)) for day in later
                    if not _past(day, at, local)][:when.MAX_READINGS - 1]
        text = f"{options[0].label} has passed. When should “{title}” go?"
    elif found.missing_time and len({option.day for option in options}) == 1:
        text = f"“{title}” on {when.label(options[0].day, None, today)}: what time?"
    else:
        if value is not None and not agreeing and len(options) < when.MAX_READINGS:
            # What the model worked out is none of the readings: offer it beside them.
            options.append(when.Reading(*value, when.label(*value, today)))
        labels = [option.label for option in options]
        text = f"So you want “{title}” added on {labels[0]}?" if len(labels) == 1 else f"Is “{title}” on {_listing(labels)}?"
    return {"tool": tool, "title": title, "said": said, "text": text, "choice": agreeing[0] if agreeing and not gone else 0,
            "choices": [{"label": option.label, "args": with_date(tool, args, option.day, option.time)} for option in options]}


def items(asked: list[dict], tools: dict) -> list[dict]:
    """The items of a confirm plan, one question each; raises ToolError when a choice's call is not valid."""
    made = []
    for index, ask in enumerate(asked, 1):
        for choice in ask["choices"]:
            toolset.validate(tools[ask["tool"]].parameters, choice["args"])
        made.append({"id": f"i{index}", "area": ASKED[ask["tool"]], "title": ask["title"][:200], "detail": "",
                     "source": ask["said"][:600], "calls": [],
                     "question": {"text": ask["text"], "choices": [
                         {"label": choice["label"], "calls": [{"tool": ask["tool"], "args": choice["args"]}]}
                         for choice in ask["choices"]]},
                     "choice": ask["choice"], "result": None,
                     "slot": {"tool": ask["tool"], "base": 0, "time": ask["tool"] != "create_study_task"}})
    return made


def override(entry: dict, picked: Any, tools: dict, today: date) -> dict:
    """`entry` answered with another date, ``{"date": "YYYY-MM-DD", "time": "HH:MM" | None}``: a further
    choice built from the slot's base choice. Raises ValueError (ToolError from the schema) when it is not one."""
    slot = entry.get("slot")
    if slot is None or entry["question"] is None:
        raise ValueError("This item takes no other date")
    if not isinstance(picked, dict) or set(picked) - {"date", "time"} or not isinstance(picked.get("date"), str) \
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", picked["date"]):
        raise ValueError("Pick a date as YYYY-MM-DD")
    day = date.fromisoformat(picked["date"])
    at = picked.get("time")
    if at is not None:
        if not slot["time"] or not isinstance(at, str) or not re.fullmatch(r"\d{2}:\d{2}", at):
            raise ValueError("Pick a time as HH:MM" if slot["time"] else "This item takes a date only")
        at = time.fromisoformat(at)
    choices = entry["question"]["choices"]
    base = choices[slot["base"]]["calls"]
    calls = [{"tool": call["tool"], "args": with_date(call["tool"], call["args"], day, at)} if call["tool"] == slot["tool"]
             else call for call in base]
    for call in calls:
        toolset.validate(tools[call["tool"]].parameters, call["args"])
    question = {**entry["question"], "choices": [*choices, {"label": when.label(day, at, today), "calls": calls}]}
    return {**entry, "question": question, "choice": len(choices)}

"""Deterministic phrases that run tools directly, with no model (design Q13: deterministic
actions skip the model). In the demo they are the whole scripted responder, so the bar
works offline; anything else gets a scripted reply there and goes to the model live.

A phrase must match completely, so a longer request never half-runs as a command.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from kitty.core.rika.workspace_tools import view_for

DEMO_REPLY = ("I'm Rika. In the demo I work offline and follow simple phrases only. Try “add a node Photosynthesis”, "
              "“new card Mitosis / Cell division”, “add a task Read chapter 3 tomorrow”, “add an event Study group "
              "tomorrow at 4pm”, “add a topic Cells”, “find biology”, “open calendar” or “start omp to tidy my notes”.")


@dataclass
class Plan:
    """Tool calls to run in order, or a direct reply when the phrase cannot run."""
    calls: list[tuple[str, dict]] = field(default_factory=list)
    reply: str | None = None


def _(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


PATTERNS = (
    ("terminal", _(r"open (?:a |the |new |a new )?(?:terminal|tern(?: window)?)")),
    ("view", _(r"(?:open|go to|show(?: me)?) (?:the |my )?(?P<view>[\w &-]{2,40}?)(?: view| page)?")),
    ("task", _(r"add (?:a |an )?(?:todo|task)(?: to)? (?P<title>.+?)(?: (?P<day>today|tomorrow))?")),
    ("event", _(r"add (?:an? )?event (?P<title>.+?)(?: (?P<day>today|tomorrow))?"
                r"(?: at (?P<hour>\d{1,2})(?::(?P<minute>\d{2}))? ?(?P<half>am|pm)?)?")),
    ("node", _(r"add (?:a |an )?(?:canvas )?(?:node|sticky(?: note)?) (?P<text>.+)")),
    ("connect_nodes", _(r"connect (?:the )?nodes? (?P<a>.+?) (?:to|and|with) (?P<b>.+)")),
    ("topic", _(r"add (?:a |an )?topic (?P<label>.+?)(?: under (?P<parent>.+))?")),
    ("connect_topics", _(r"connect (?:the )?topics? (?P<a>.+?) (?:to|and|with) (?P<b>.+)")),
    ("rename_topic", _(r"rename (?:the )?topic (?P<topic>.+?) to (?P<label>.+)")),
    ("card", _(r"(?:new|add(?: a)?|make(?: a)?) (?:flash)?card (?P<front>.+?) ?/ ?(?P<back>.+)")),
    ("page", _(r"(?:new|create(?: a)?|make(?: a)?|add(?: a)?) (?:note )?page (?P<title>.+)")),
    ("append", _(r"append (?P<text>.+) to (?:the page )?(?P<page>.+)")),
    ("subject", _(r"(?:new|add(?: a)?) (?:study )?subject (?P<name>.+)")),
    ("study_task", _(r"(?:new |add (?:a )?)?study task (?P<title>.+?)(?: for (?P<subject>.+))?")),
    ("search", _(r"(?:find|search(?: for)?|look up) (?P<query>.+)")),
    ("worker", _(r"start (?:an? |the )?(?P<kind>omp|claude|codex|agent|worker)(?: agent| worker)?"
                 r"(?: (?:to|on|for) (?P<task>.+))?")),
    ("delete_page", _(r"delete (?:the )?page (?P<title>.+)")),
    ("command", _(r"run `(?P<command>[^`]+)`(?: in (?P<cwd>/\S+))?")),
)


def _clean(text: str) -> str:
    return " ".join(text.strip().rstrip(".!").split())


def _day(conn: sqlite3.Connection, word: str | None) -> datetime:
    tz = ZoneInfo(conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"])
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return today + timedelta(days=1) if word == "tomorrow" else today


def parse(text: str, conn: sqlite3.Connection, tools: dict) -> Plan | None:
    """The plan for a phrase whose tools this process has, else None."""
    phrase = _clean(text)
    for name, pattern in PATTERNS:
        match = pattern.fullmatch(phrase)
        if match is None:
            continue
        plan = _plan(name, match, conn)
        if plan is None:
            continue
        if all(tool in tools for tool, _args in plan.calls):
            return plan
    return None


def _plan(name: str, match: re.Match, conn: sqlite3.Connection) -> Plan | None:
    group = match.groupdict()
    if name == "terminal":
        return Plan([("open_terminal", {})])
    if name == "view":
        view = view_for(group["view"])
        return Plan([("open_view", {"view": view})]) if view else None
    if name == "task":
        args = {"title": group["title"]}
        if group["day"]:
            args["due"] = _day(conn, group["day"]).date().isoformat()
        return Plan([("create_task", args)])
    if name == "event":
        day = _day(conn, group["day"])
        if group["hour"] is None:
            return Plan([("create_event", {"title": group["title"], "start": day.date().isoformat(), "all_day": True})])
        hour = int(group["hour"])
        if group["half"]:
            hour = hour % 12 + (12 if group["half"].lower() == "pm" else 0)
        if not 0 <= hour <= 23 or int(group["minute"] or 0) > 59:
            return Plan(reply="That time does not exist; say it like 4pm or 16:30")
        start = day.replace(hour=hour, minute=int(group["minute"] or 0))
        return Plan([("create_event", {"title": group["title"], "start": start.isoformat(timespec="minutes")})])
    if name == "node":
        return Plan([("add_canvas_node", {"text": group["text"]})])
    if name == "connect_nodes":
        return Plan([("connect_canvas_nodes", {"from": group["a"], "to": group["b"]})])
    if name == "topic":
        args = {"label": group["label"]}
        if group["parent"]:
            args["parent"] = group["parent"]
        return Plan([("add_map_topic", args)])
    if name == "connect_topics":
        return Plan([("connect_map_topics", {"from": group["a"], "to": group["b"]})])
    if name == "rename_topic":
        return Plan([("rename_map_topic", {"topic": group["topic"], "label": group["label"]})])
    if name == "card":
        return Plan([("add_card", {"front": group["front"].strip(), "back": group["back"].strip()})])
    if name == "page":
        return Plan([("create_page", {"title": group["title"]})])
    if name == "append":
        return Plan([("append_block", {"text": group["text"], "page": group["page"]})])
    if name == "subject":
        return Plan([("create_subject", {"name": group["name"]})])
    if name == "study_task":
        args = {"title": group["title"]}
        if group["subject"]:
            args["subject"] = group["subject"]
        return Plan([("create_study_task", args)])
    if name == "search":
        return Plan([("search", {"query": group["query"]})])
    if name == "worker":
        kind = group["kind"].lower()
        return Plan([("start_worker", {"kind": "omp" if kind in {"agent", "worker"} else kind,
                                       "task": group["task"] or "Introduce yourself and wait for instructions"})])
    if name == "delete_page":
        row = conn.execute("SELECT id FROM note_page_index WHERE title=? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1",
                           (group["title"],)).fetchone()
        if row is None:
            return Plan(reply=f"No page is called {group['title']}")
        return Plan([("delete_page", {"page_id": row["id"]})])
    if name == "command":
        args = {"command": group["command"]}
        if group["cwd"]:
            args["cwd"] = group["cwd"]
        return Plan([("run_command", args)])
    return None

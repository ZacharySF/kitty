"""Mixed review (design Q2 C): one session of due work across the whole graph.

Due topic reviews, due textbook cards and due own-book problems are taken most overdue
first (at most :data:`SESSION`), topped up with the soonest early topic reviews when fewer
than :data:`TOP_UP` are due, then ordered so no two consecutive problems share a
strategy (``Topic.strategy``; book items use their topic's). Interleaving keeps the
overdue order wherever the rule allows. It becomes Practice's default once
:data:`MIN_MASTERED` topics are mastered.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime

from kitty.core.math import library, mastery
from kitty.core.math import topics as graph

MIN_MASTERED = 5
SESSION = 10
TOP_UP = 6


def interleave(items: list[dict], key=lambda item: item["strategy"]) -> tuple[list[dict], list[dict]]:
    """``items`` (in priority order) reordered so neighbours never share a strategy, and
    the items left out because no such order fits them all.

    Each step takes the first item whose strategy differs from the last one placed,
    unless a strategy holds more than half of what remains: then it must go now or it
    can't be spaced out. That keeps every feasible set whole, and when one strategy
    dominates it places as many as possible (it alternates with the rest).
    """
    remaining, placed, previous = list(items), [], None
    while remaining:
        counts = Counter(key(item) for item in remaining)
        forced = next((name for name, count in counts.items() if name != previous and 2 * count > len(remaining)), None)
        choice = next((item for item in remaining if key(item) != previous and forced in (None, key(item))), None)
        if choice is None:
            break
        placed.append(choice)
        remaining.remove(choice)
        previous = key(choice)
    return placed, remaining


def _topic_item(state: dict, moment: datetime) -> dict:
    return {"kind": "topic", "topic_id": state["id"], "title": state["title"], "strategy": graph.BY_ID[state["id"]].strategy,
            "due_at": state["due_at"], "early": datetime.fromisoformat(state["due_at"]) > moment, "why": state["why"]}


def _textbook_item(exercise, row) -> dict:
    primary = exercise.topics[0]
    return {"kind": "textbook", "exercise_id": exercise.id, "label": exercise.label, "topic_id": primary,
            "title": exercise.section_title, "strategy": graph.BY_ID[primary].strategy, "due_at": row["due_at"],
            "graded": exercise.graded}


def _book_item(row) -> dict:
    topic = graph.BY_ID.get(row["topic_id"]) if row["topic_id"] else None
    # Without a topic the method is unknown, so the problem never blocks another.
    return {"kind": "book", "entry_id": row["id"], "label": library.book_label(row), "topic_id": topic.id if topic else None,
            "strategy": topic.strategy if topic else f"book:{row['id']}", "due_at": row["due_at"]}


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def queue(conn: sqlite3.Connection, moment: datetime | None = None) -> dict:
    moment = moment or mastery.now()
    mastered = [state for state in mastery.states(conn, moment) if state["state"] == "mastered"]
    topics = [_topic_item(state, moment) for state in mastered]
    due = [item for item in topics if not item["early"]]
    due += [_textbook_item(exercise, row) for exercise, row in library.due_textbook(conn, moment)]
    due += [_book_item(row) for row in library.due_books(conn, moment)]
    due.sort(key=lambda item: item["due_at"])
    pool = due[:SESSION]
    if len(pool) < TOP_UP:
        pool += sorted((item for item in topics if item["early"]), key=lambda item: item["due_at"])[:TOP_UP - len(pool)]
    items, left = interleave(pool)
    held_back = sum(1 for item in left if not item.get("early"))
    if len(mastered) < MIN_MASTERED:
        why = f"Mixed review opens once {MIN_MASTERED} topics are mastered ({len(mastered)} so far)"
    else:
        kinds = Counter(item["kind"] for item in items if not item.get("early"))
        parts = [_count(kinds[kind], noun) for kind, noun in (("topic", "due topic"), ("textbook", "textbook exercise"),
                                                             ("book", "book problem")) if kinds[kind]]
        early = sum(1 for item in items if item.get("early"))
        if early:
            parts.append(_count(early, "early review"))
        why = ("Mixed review: " + ", ".join(parts) + ", no two in a row with the same strategy") if parts else "Nothing to review yet"
    return {"default": len(mastered) >= MIN_MASTERED, "mastered": len(mastered), "needed": MIN_MASTERED, "why": why,
            "held_back": held_back, "items": items}

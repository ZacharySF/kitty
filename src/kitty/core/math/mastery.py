"""Graph mastery: locked → ready → learning → mastered, FSRS review per topic, and
FIRe-lite implicit credit to encompassed prerequisites.

- **Locked** until every prerequisite is mastered; **ready** after that; **learning**
  once a graded answer exists; **mastered** after :data:`MASTERY_RUN` correct first
  tries in a row. Mastery never reverts: a missed review is an FSRS lapse instead.
- **Review.** Mastery starts the topic's FSRS card with a Good review. Each later graded
  answer on a mastered topic is a review rated by correctness and time against the
  generator's ``target_s`` (see :func:`rating`).
- **FIRe-lite.** A correct graded answer passes ``credit`` (``math_prereqs``) to each
  mastered prerequisite, halved when that prerequisite isn't due yet. Each whole unit
  of credit becomes one implicit Good review.
- Every state carries a ``why`` string, and :func:`next_task` explains its choice.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from kitty.core.math import topics as graph

MASTERY_RUN = 3
EARLY_DISCOUNT = 0.5
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4


def now() -> datetime:
    return datetime.now(timezone.utc)


def _scheduler():
    from fsrs import Scheduler

    # A topic review is a fresh problem, not a memorised card: no same-day learning steps.
    return Scheduler(desired_retention=0.9, learning_steps=(), relearning_steps=(),
                     maximum_interval=36500, enable_fuzzing=False)


def rating(correct: bool, elapsed_ms: int | None, target_s: int) -> int:
    """Again when wrong; Hard when slower than 2× target; Easy under ½ target; else Good."""
    if not correct:
        return AGAIN
    if elapsed_ms is None:
        return GOOD
    seconds = elapsed_ms / 1000
    if seconds > 2 * target_s:
        return HARD
    if seconds < target_s / 2:
        return EASY
    return GOOD


def _rows(conn) -> dict[str, dict]:
    return {row["topic_id"]: dict(row) for row in conn.execute("SELECT * FROM math_mastery")}


def _review(card_json: str | None, grade: int, moment: datetime):
    from fsrs import Card, Rating

    card = Card.from_json(card_json) if card_json else Card(due=moment)
    updated, _ = _scheduler().review_card(card, Rating(grade), review_datetime=moment)
    return updated


def _days(delta_seconds: float) -> str:
    days = round(delta_seconds / 86400)
    if days <= 0:
        return "today"
    return "1 day" if days == 1 else f"{days} days"


def states(conn: sqlite3.Connection, moment: datetime | None = None) -> list[dict]:
    """Every topic in curriculum order with its state, schedule and why."""
    from fsrs import Card

    moment = moment or now()
    rows = _rows(conn)
    titles = {topic.id: topic.title for topic in graph.TOPICS}
    out = []
    for topic in graph.TOPICS:
        row = rows.get(topic.id)
        missing = [p for p in topic.prereqs if not (rows.get(p) or {}).get("mastered_at")]
        item = {"id": topic.id, "strand": topic.strand, "title": topic.title, "prereqs": list(topic.prereqs)}
        if row and row["mastered_at"]:
            card = Card.from_json(row["fsrs"])
            due = card.due
            item.update(state="mastered", due_at=due.isoformat(), stability_days=round(card.stability or 0, 2), progress=1.0)
            gap = (due - moment).total_seconds()
            item["why"] = ("Review due: practised " + _days((moment - card.last_review).total_seconds()) + " ago"
                           if gap <= 0 else f"Mastered; next review in {_days(gap)}")
        elif missing:
            item.update(state="locked", progress=0.0, why="Needs " + _join([titles[p] for p in missing]))
        elif row and row["updated_at"]:
            item.update(state="learning", progress=round(row["run"] / MASTERY_RUN, 3),
                        why=f"{row['run']} of {MASTERY_RUN} correct in a row toward mastery")
        else:
            item.update(state="ready", progress=0.0, why=_unlock_reason(topic, rows, titles))
        out.append(item)
    return out


def _unlock_reason(topic, rows, titles) -> str:
    if not topic.prereqs:
        return "A starting point: no prerequisites"
    latest = max(topic.prereqs, key=lambda p: rows[p]["mastered_at"])
    return f"Unlocked because you mastered {titles[latest]}"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def state_of(conn, topic_id: str, moment: datetime | None = None) -> dict:
    return next(item for item in states(conn, moment) if item["id"] == topic_id)


def record(conn: sqlite3.Connection, topic_id: str, correct: bool, elapsed_ms: int | None, target_s: int,
           moment: datetime | None = None) -> int | None:
    """Apply one graded answer inside the caller's transaction; returns the FSRS rating
    when it was a review of a mastered topic."""
    moment = moment or now()
    stamp = moment.isoformat()
    conn.execute("INSERT OR IGNORE INTO math_mastery(topic_id, updated_at) VALUES (?, ?)", (topic_id, stamp))
    row = conn.execute("SELECT * FROM math_mastery WHERE topic_id=?", (topic_id,)).fetchone()
    applied = None
    if row["mastered_at"]:
        applied = rating(correct, elapsed_ms, target_s)
        card = _review(row["fsrs"], applied, moment)
        conn.execute("UPDATE math_mastery SET fsrs=?, due_at=?, reviews=reviews+1, lapses=lapses+?, updated_at=? WHERE topic_id=?",
                     (card.to_json(), card.due.isoformat(), int(applied == AGAIN), stamp, topic_id))
    else:
        run = row["run"] + 1 if correct else 0
        if run >= MASTERY_RUN:
            card = _review(None, GOOD, moment)
            conn.execute("UPDATE math_mastery SET run=?, mastered_at=?, fsrs=?, due_at=?, updated_at=? WHERE topic_id=?",
                         (run, stamp, card.to_json(), card.due.isoformat(), stamp, topic_id))
        else:
            conn.execute("UPDATE math_mastery SET run=?, updated_at=? WHERE topic_id=?", (run, stamp, topic_id))
    if correct:
        _implicit_credit(conn, topic_id, moment)
    return applied


def _implicit_credit(conn, topic_id: str, moment: datetime) -> None:
    stamp = moment.isoformat()
    edges = conn.execute(
        "SELECT p.prereq_id, p.credit, m.due_at, m.fsrs, m.implicit_credit FROM math_prereqs p "
        "JOIN math_mastery m ON m.topic_id = p.prereq_id WHERE p.topic_id=? AND p.credit > 0 AND m.mastered_at IS NOT NULL",
        (topic_id,)).fetchall()
    for edge in edges:
        due = datetime.fromisoformat(edge["due_at"]) <= moment
        credit = edge["implicit_credit"] + edge["credit"] * (1 if due else EARLY_DISCOUNT)
        card_json, due_at, reviews = edge["fsrs"], edge["due_at"], 0
        while credit >= 1 - 1e-9:
            card = _review(card_json, GOOD, moment)
            card_json, due_at = card.to_json(), card.due.isoformat()
            credit, reviews = max(0.0, credit - 1), reviews + 1
        conn.execute("UPDATE math_mastery SET implicit_credit=?, fsrs=?, due_at=?, implicit_reviews=implicit_reviews+?, updated_at=? WHERE topic_id=?",
                     (round(credit, 6), card_json, due_at, reviews, stamp, edge["prereq_id"]))


def next_task(conn: sqlite3.Connection, moment: datetime | None = None) -> dict:
    """The one thing to do next: a due review, the topic being learned, or a new topic."""
    moment = moment or now()
    items = states(conn, moment)
    due = sorted((item for item in items if item["state"] == "mastered" and datetime.fromisoformat(item["due_at"]) <= moment),
                 key=lambda item: item["due_at"])
    if due:
        return _task("review", due[0])
    last = conn.execute("SELECT topic_id FROM math_attempts WHERE graded_at IS NOT NULL AND purpose != 'guided' "
                        "ORDER BY graded_at DESC LIMIT 1").fetchone()
    learning = [item for item in items if item["state"] == "learning"]
    if learning:
        recent = next((item for item in learning if last and item["id"] == last["topic_id"]), learning[0])
        return _task("lesson", recent, "Continue: " + recent["why"][0].lower() + recent["why"][1:])
    ready = [item for item in items if item["state"] == "ready"]
    if ready:
        last_strand = graph.BY_ID[last["topic_id"]].strand if last and last["topic_id"] in graph.BY_ID else None
        # Interleave: prefer a strand other than the one just practised.
        choice = next((item for item in ready if item["strand"] != last_strand), ready[0])
        return _task("lesson", choice)
    upcoming = min((item for item in items if item["state"] == "mastered"), key=lambda item: item["due_at"])
    return _task("review", upcoming, f"Nothing due; the next review is {upcoming['title']}, an early review")


def _task(kind: str, item: dict, why: str | None = None) -> dict:
    return {"kind": kind, "topic_id": item["id"], "title": item["title"], "why": why or item["why"]}

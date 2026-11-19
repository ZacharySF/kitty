"""The authored math topic graph and its sync into ``math_topics`` / ``math_prereqs``.

A topic unlocks once every prerequisite is mastered. ``credit`` is the FIRe-lite weight
a correct answer on the topic passes to each prerequisite it directly encompasses
(a heuristic, tuned after real use). Topic ids are stable: attempts and mastery
reference them. The graph must be acyclic; :func:`validate` and the database trigger
on ``math_prereqs`` both enforce it.

The graph is ``topics.json`` beside this file (strands, the default credit and the topics in
curriculum order, each with its prerequisites, credit overrides and strategy); rika-server
compiles the same file in (server/src/math/topics.rs).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from kitty.db.connection import transaction

_GRAPH = json.loads(Path(__file__).with_name("topics.json").read_text(encoding="utf-8"))
STRANDS: dict[str, str] = dict(_GRAPH["strands"])
#: Default implicit credit a topic passes to each direct prerequisite.
CREDIT: float = _GRAPH["credit"]


@dataclass(frozen=True)
class Topic:
    id: str
    strand: str
    title: str
    prereqs: tuple[str, ...] = ()
    #: Per-prerequisite FIRe-lite weight overrides (0 = prerequisite, not encompassed).
    credit: dict[str, float] = field(default_factory=dict)
    #: The solution method; mixed review never puts two problems of one strategy in a row.
    strategy: str = ""

    def weight(self, prereq: str) -> float:
        return self.credit.get(prereq, CREDIT)


TOPICS: tuple[Topic, ...] = tuple(
    Topic(item["id"], item["strand"], item["title"], tuple(item["prereqs"]), dict(item.get("credit", {})), item["strategy"])
    for item in _GRAPH["topics"])
BY_ID = {topic.id: topic for topic in TOPICS}


class GraphError(ValueError):
    """The topic graph has a cycle, a dangling prerequisite or a bad weight."""


def validate(topics=TOPICS) -> list[str]:
    """Topic ids in prerequisite order; raises GraphError for a cycle or unknown id."""
    by_id = {}
    for topic in topics:
        if topic.id in by_id:
            raise GraphError(f"Duplicate topic {topic.id}")
        if topic.strand not in STRANDS:
            raise GraphError(f"Topic {topic.id} has unknown strand {topic.strand}")
        by_id[topic.id] = topic
    for topic in topics:
        for prereq in topic.prereqs:
            if prereq not in by_id:
                raise GraphError(f"Topic {topic.id} needs unknown topic {prereq}")
        if set(topic.credit) - set(topic.prereqs) or any(not 0 <= w <= 1 for w in topic.credit.values()):
            raise GraphError(f"Topic {topic.id} credits a non-prerequisite or a weight outside 0–1")
    order, state = [], {}

    def visit(ident, path):
        if state.get(ident) == "done":
            return
        if state.get(ident) == "open":
            cycle = path[path.index(ident):] + [ident]
            raise GraphError("Prerequisite cycle: " + " → ".join(cycle))
        state[ident] = "open"
        for prereq in by_id[ident].prereqs:
            visit(prereq, path + [ident])
        state[ident] = "done"
        order.append(ident)

    for topic in topics:
        visit(topic.id, [])
    return order


def sync(conn: sqlite3.Connection, topics=TOPICS) -> None:
    """Make the tables match the authored graph; a no-op when they already do."""
    validate(topics)
    want_topics = [(topic.id, topic.strand, topic.title, index) for index, topic in enumerate(topics)]
    want_edges = sorted((topic.id, prereq, topic.weight(prereq)) for topic in topics for prereq in topic.prereqs)
    have_topics = [tuple(row) for row in conn.execute("SELECT id, strand, title, position FROM math_topics ORDER BY position")]
    have_edges = sorted(tuple(row) for row in conn.execute("SELECT topic_id, prereq_id, credit FROM math_prereqs"))
    if have_topics == want_topics and have_edges == want_edges:
        return
    with transaction(conn, immediate=True):
        conn.execute("DELETE FROM math_prereqs")
        keep = [row[0] for row in want_topics]
        conn.execute(f"DELETE FROM math_topics WHERE id NOT IN ({','.join('?' * len(keep))})", keep)
        conn.executemany(
            "INSERT INTO math_topics(id, strand, title, position) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET strand=excluded.strand, title=excluded.title, position=excluded.position",
            want_topics)
        conn.executemany("INSERT INTO math_prereqs(topic_id, prereq_id, credit) VALUES (?,?,?)", want_edges)

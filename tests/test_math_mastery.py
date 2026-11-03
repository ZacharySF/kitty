"""Topic graph acyclicity, prerequisite gating, mastery transitions, FSRS reviews,
FIRe-lite credit and the explained next task."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core.math import mastery, practice
from kitty.core.math import topics as graph

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(workspace):
    graph.sync(workspace.conn)
    return workspace.conn


def master(conn, topic_id, moment=T0):
    for _ in range(mastery.MASTERY_RUN):
        mastery.record(conn, topic_id, True, 5000, 30, moment)
    conn.commit()


def state(conn, topic_id, moment=T0):
    return mastery.state_of(conn, topic_id, moment)


def test_authored_graph_is_acyclic_and_validation_names_the_cycle():
    order = graph.validate()
    assert len(order) == len(graph.TOPICS)
    for topic in graph.TOPICS:
        assert all(order.index(p) < order.index(topic.id) for p in topic.prereqs)
    looped = (graph.Topic("a", "counting", "A", ("c",)), graph.Topic("b", "counting", "B", ("a",)), graph.Topic("c", "counting", "C", ("b",)))
    with pytest.raises(graph.GraphError, match="a → c → b → a"):
        graph.validate(looped)
    with pytest.raises(graph.GraphError, match="unknown topic"):
        graph.validate((graph.Topic("a", "counting", "A", ("missing",)),))
    with pytest.raises(graph.GraphError):
        graph.sync(sqlite3.connect(":memory:"), looped)


def test_database_trigger_rejects_cycles_whatever_writes_the_graph(conn):
    with pytest.raises(sqlite3.IntegrityError, match="math prerequisite cycle"):
        conn.execute("INSERT INTO math_prereqs(topic_id, prereq_id) VALUES ('arith.add-sub', 'arith.squares')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO math_prereqs(topic_id, prereq_id) VALUES ('arith.add-sub', 'arith.add-sub')")
    with pytest.raises(sqlite3.IntegrityError, match="math prerequisite cycle"):
        conn.execute("UPDATE math_prereqs SET prereq_id='arith.mul-2x2' WHERE topic_id='arith.mul-2x1' AND prereq_id='arith.add-sub'")
    conn.execute("INSERT INTO math_prereqs(topic_id, prereq_id) VALUES ('arith.squares', 'arith.add-sub')")  # acyclic is fine


def test_sync_is_idempotent_and_follows_the_authored_graph(conn):
    before = conn.total_changes
    graph.sync(conn)
    assert conn.total_changes == before
    assert conn.execute("SELECT count(*) FROM math_topics").fetchone()[0] == len(graph.TOPICS)
    edges = conn.execute("SELECT count(*) FROM math_prereqs").fetchone()[0]
    assert edges == sum(len(topic.prereqs) for topic in graph.TOPICS)
    assert conn.execute("SELECT credit FROM math_prereqs WHERE topic_id='prob.bayes' AND prereq_id='prob.total-probability'").fetchone()[0] == 0.5
    smaller = (graph.Topic("arith.add-sub", "arithmetic", "Adding up"),)
    graph.sync(conn, smaller)
    assert [tuple(row) for row in conn.execute("SELECT id, title FROM math_topics")] == [("arith.add-sub", "Adding up")]


def test_new_learner_sees_ready_roots_and_locked_topics_with_reasons(conn):
    items = {item["id"]: item for item in mastery.states(conn, T0)}
    assert items["arith.add-sub"]["state"] == "ready"
    assert items["arith.add-sub"]["why"] == "A starting point: no prerequisites"
    assert items["arith.mul-2x1"]["state"] == "locked"
    assert items["arith.mul-2x1"]["why"] == "Needs Times tables to 20 × 20 and Addition and subtraction"
    assert {item["state"] for item in items.values()} == {"ready", "locked"}


def test_locked_topics_refuse_problems_and_lessons(conn):
    with pytest.raises(practice.Conflict, match="Master Times tables to 20 × 20, Addition and subtraction first"):
        practice.issue(conn, "arith.mul-2x1", moment=T0)
    with pytest.raises(practice.Conflict):
        practice.lesson(conn, "prob.bayes", moment=T0)
    with pytest.raises(practice.NotFound):
        practice.issue(conn, "no.such-topic", moment=T0)


def test_learning_to_mastery_unlocks_dependents_and_says_why(conn):
    mastery.record(conn, "arith.add-sub", False, 4000, 8, T0)
    assert state(conn, "arith.add-sub")["state"] == "learning"
    mastery.record(conn, "arith.add-sub", True, 4000, 8, T0)
    mastery.record(conn, "arith.add-sub", True, 4000, 8, T0)
    learning = state(conn, "arith.add-sub")
    assert learning["state"] == "learning" and learning["progress"] == pytest.approx(2 / 3, abs=1e-3)
    assert learning["why"] == "2 of 3 correct in a row toward mastery"
    mastery.record(conn, "arith.add-sub", False, 4000, 8, T0)  # a miss resets the run
    assert state(conn, "arith.add-sub")["progress"] == 0
    master(conn, "arith.add-sub", T0)
    assert state(conn, "arith.mul-2x1")["state"] == "locked"
    master(conn, "arith.times-tables", T0 + timedelta(minutes=5))
    done = state(conn, "arith.add-sub", T0 + timedelta(minutes=5))
    assert done["state"] == "mastered" and done["stability_days"] > 0
    assert datetime.fromisoformat(done["due_at"]) > T0 + timedelta(days=1)
    unlocked = state(conn, "arith.mul-2x1", T0 + timedelta(minutes=5))
    assert unlocked["state"] == "ready"
    assert unlocked["why"] == "Unlocked because you mastered Times tables to 20 × 20"
    assert practice.issue(conn, "arith.mul-2x1", seed=4, moment=T0)["purpose"] == "lesson"
    assert practice.issue(conn, "arith.add-sub", seed=4, moment=T0)["purpose"] == "review"


def test_review_ratings_follow_correctness_and_time():
    assert mastery.rating(False, 1000, 30) == mastery.AGAIN
    assert mastery.rating(True, 61_000, 30) == mastery.HARD
    assert mastery.rating(True, 20_000, 30) == mastery.GOOD
    assert mastery.rating(True, 14_000, 30) == mastery.EASY
    assert mastery.rating(True, None, 30) == mastery.GOOD


def test_reviews_reschedule_with_fsrs_and_count_lapses(conn):
    master(conn, "arith.add-sub", T0)
    first_due = datetime.fromisoformat(state(conn, "arith.add-sub")["due_at"])
    later = first_due + timedelta(hours=1)
    assert state(conn, "arith.add-sub", later)["why"].startswith("Review due: practised")
    assert mastery.record(conn, "arith.add-sub", True, 6000, 8, later) == mastery.GOOD
    good = json.loads(conn.execute("SELECT fsrs FROM math_mastery WHERE topic_id='arith.add-sub'").fetchone()[0])
    assert datetime.fromisoformat(good["due"]) - later > first_due - T0
    again_at = datetime.fromisoformat(good["due"]) + timedelta(hours=1)
    assert mastery.record(conn, "arith.add-sub", False, 6000, 8, again_at) == mastery.AGAIN
    row = conn.execute("SELECT reviews, lapses, due_at FROM math_mastery WHERE topic_id='arith.add-sub'").fetchone()
    assert (row["reviews"], row["lapses"]) == (2, 1)
    assert datetime.fromisoformat(row["due_at"]) - again_at < datetime.fromisoformat(good["due"]) - later
    assert state(conn, "arith.add-sub", again_at)["state"] == "mastered"  # a lapse never relocks


def test_fire_lite_credit_trickles_to_encompassed_prerequisites(conn):
    master(conn, "arith.add-sub", T0)
    master(conn, "arith.times-tables", T0)
    due = datetime.fromisoformat(state(conn, "arith.add-sub")["due_at"]) + timedelta(hours=1)
    before = conn.execute("SELECT due_at FROM math_mastery WHERE topic_id='arith.add-sub'").fetchone()[0]
    for index in range(3):  # learning mul-2x1: three correct answers, 0.3 credit each while due
        mastery.record(conn, "arith.mul-2x1", True, 5000, 8, due + timedelta(minutes=index))
    row = conn.execute("SELECT implicit_credit, implicit_reviews, due_at FROM math_mastery WHERE topic_id='arith.add-sub'").fetchone()
    assert row["implicit_reviews"] == 0 and row["implicit_credit"] == pytest.approx(0.9)
    mastery.record(conn, "arith.mul-2x1", True, 5000, 8, due + timedelta(minutes=4))
    row = conn.execute("SELECT implicit_credit, implicit_reviews, due_at FROM math_mastery WHERE topic_id='arith.add-sub'").fetchone()
    assert row["implicit_reviews"] == 1 and row["implicit_credit"] == pytest.approx(0.2)
    assert row["due_at"] > before
    # Not yet due: the credit is discounted; a wrong answer passes none.
    mastery.record(conn, "arith.mul-2x1", False, 5000, 8, due + timedelta(minutes=5))
    mastery.record(conn, "arith.mul-2x1", True, 5000, 8, due + timedelta(minutes=6))
    credit = conn.execute("SELECT implicit_credit FROM math_mastery WHERE topic_id='arith.add-sub'").fetchone()[0]
    assert credit == pytest.approx(0.2 + 0.3 * mastery.EARLY_DISCOUNT)


def test_next_task_prefers_due_reviews_then_learning_then_new_topics_with_reasons(conn):
    first = mastery.next_task(conn, T0)
    assert first == {"kind": "lesson", "topic_id": "arith.add-sub", "title": "Addition and subtraction",
                     "why": "A starting point: no prerequisites"}
    practice_problem = practice.issue(conn, "arith.add-sub", seed=1, moment=T0)
    practice.answer(conn, practice_problem["problem_id"], "-5", 1000, moment=T0)
    learning = mastery.next_task(conn, T0)
    assert learning["kind"] == "lesson" and learning["topic_id"] == "arith.add-sub"
    assert learning["why"] == "Continue: 0 of 3 correct in a row toward mastery"
    master(conn, "arith.add-sub", T0)
    master(conn, "arith.times-tables", T0)
    # The last practice was arithmetic, so the next new topic interleaves another strand.
    interleaved = mastery.next_task(conn, T0)
    assert interleaved == {"kind": "lesson", "topic_id": "count.product-rule", "title": "Product rule",
                           "why": "Unlocked because you mastered Times tables to 20 × 20"}
    due_at = datetime.fromisoformat(state(conn, "arith.add-sub")["due_at"])
    review = mastery.next_task(conn, due_at + timedelta(days=1))
    assert review["kind"] == "review" and review["topic_id"] == "arith.add-sub"
    assert review["why"].startswith("Review due: practised")

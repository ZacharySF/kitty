"""Mixed review (design Q2 C): due topics, textbook cards and own-book problems
interleaved so no two consecutive problems share a strategy."""
from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core.math import library, mastery, mixed
from kitty.core.math import topics as graph

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=400)


@pytest.fixture
def conn(workspace):
    graph.sync(workspace.conn)
    return workspace.conn


def master(conn, *topic_ids, moment=T0):
    for topic_id in topic_ids:
        for _ in range(mastery.MASTERY_RUN):
            mastery.record(conn, topic_id, True, 5000, 30, moment)
    conn.commit()


def strategies(items):
    return [item["strategy"] for item in items]


def no_repeats(items):
    return all(a != b for a, b in zip(strategies(items), strategies(items)[1:]))


@pytest.mark.parametrize("seed", range(240))
def test_interleave_never_repeats_a_strategy_and_keeps_what_it_can(seed):
    rng = random.Random(seed)
    items = [{"id": index, "strategy": rng.choice("ABCDE"[:rng.randint(1, 5)])} for index in range(rng.randint(0, 14))]
    ordered, left = mixed.interleave(items)
    assert no_repeats(ordered)
    assert sorted(item["id"] for item in ordered + left) == list(range(len(items)))
    counts = Counter(strategies(items))
    # Any multiset whose largest strategy fits in every other slot can be spaced out completely.
    if not items or max(counts.values()) <= (len(items) + 1) // 2:
        assert left == []
    else:
        assert len(ordered) == 2 * (len(items) - max(counts.values())) + 1
    if no_repeats(items):
        assert ordered == items  # priority order is kept whenever the rule allows it


def test_mixed_review_waits_for_enough_mastery_data(conn):
    master(conn, "arith.add-sub", "arith.times-tables")
    result = mixed.queue(conn, LATER)
    assert result["default"] is False and result["mastered"] == 2 and result["needed"] == mixed.MIN_MASTERED == 5
    assert result["why"] == "Mixed review opens once 5 topics are mastered (2 so far)"
    assert {item["topic_id"] for item in result["items"]} == {"arith.add-sub", "arith.times-tables"}


def test_due_topics_are_interleaved_most_overdue_first(conn):
    master(conn, "arith.mul-2x1", moment=T0)
    master(conn, "arith.mul-2x2", "count.complement", moment=T0 + timedelta(days=1))
    master(conn, "prob.complement", "arith.division", "count.product-rule", moment=T0 + timedelta(days=2))
    result = mixed.queue(conn, LATER)
    items = result["items"]
    assert result["default"] is True and result["mastered"] == 6 and result["held_back"] == 0
    assert len(items) == 6 and no_repeats(items) and not any(item["early"] for item in items)
    assert items[0]["topic_id"] == "arith.mul-2x1" and items[0]["strategy"] == "split-multiply"
    assert items[1]["topic_id"] != "arith.mul-2x2"  # same strategy as the first: spaced apart
    assert set(items[0]) == {"kind", "topic_id", "title", "strategy", "due_at", "early", "why"}
    assert "6 due" in result["why"]


def test_nothing_due_tops_up_with_the_soonest_early_reviews(conn):
    ids = [topic.id for topic in graph.TOPICS[:8]]
    master(conn, *ids)
    result = mixed.queue(conn, T0 + timedelta(minutes=5))
    assert result["default"] is True and result["held_back"] == 0
    assert 1 <= len(result["items"]) <= mixed.TOP_UP and all(item["early"] for item in result["items"])
    assert no_repeats(result["items"]) and "early" in result["why"]


def test_same_strategy_due_items_are_held_back_rather_than_repeated(conn):
    master(conn, "arith.mul-2x1", "arith.mul-2x2")
    result = mixed.queue(conn, LATER)
    assert [item["strategy"] for item in result["items"]] == ["split-multiply"] and result["held_back"] == 1


def test_a_session_is_capped(conn):
    master(conn, *[topic.id for topic in graph.TOPICS[:30]])
    result = mixed.queue(conn, LATER)
    assert len(result["items"]) <= mixed.SESSION and no_repeats(result["items"])


def test_due_book_problems_and_textbook_cards_join_the_mix(conn):
    from kitty.core.math import textbook

    master(conn, "prob.bayes", "arith.add-sub")
    logged = library.add_book_problem(conn, {"book": "Zhou", "section": "2.3", "problem": "14", "confidence": 1,
                                             "topic_id": "prob.bayes"}, T0)
    loose = library.add_book_problem(conn, {"book": "Zhou", "problem": "15", "confidence": 1}, T0)
    other = library.add_book_problem(conn, {"book": "Zhou", "problem": "16", "confidence": 1}, T0)
    exercise = next(item for item in textbook.exercises() if item.answer is None)
    library.textbook_rate(conn, {"exercise_id": exercise.id, "rating": 1}, T0)
    items = mixed.queue(conn, LATER)["items"]
    assert no_repeats(items)
    book = next(item for item in items if item.get("entry_id") == logged["id"])
    assert book == {"kind": "book", "entry_id": logged["id"], "label": "Zhou §2.3 #14", "topic_id": "prob.bayes",
                    "strategy": "bayes", "due_at": logged["card"]["due_at"]}
    # Problems without a topic have no known strategy, so they never block each other.
    loose_items = [item for item in items if item.get("entry_id") in (loose["id"], other["id"])]
    assert len(loose_items) == 2 and loose_items[0]["strategy"] != loose_items[1]["strategy"]
    card = next(item for item in items if item["kind"] == "textbook")
    assert card == {"kind": "textbook", "exercise_id": exercise.id, "label": exercise.label, "topic_id": exercise.topics[0],
                    "title": exercise.section_title, "strategy": graph.BY_ID[exercise.topics[0]].strategy,
                    "due_at": card["due_at"], "graded": "self"}
    assert all(item["kind"] != "topic" or not item["early"] for item in items)

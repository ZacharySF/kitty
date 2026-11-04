"""Own-book problems logged by reference and Grinstead & Snell exercises, each on its own
FSRS schedule: validation, confidence as the first rating, reviews, edits and deletes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kitty.core.math import library, mastery, practice
from kitty.core.math import topics as graph

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(workspace):
    graph.sync(workspace.conn)
    return workspace.conn


def log(conn, moment=T0, **fields):
    body = {"book": "Zhou", "section": "2.3", "problem": "14", "answer": "1/3", "confidence": 3, "topic_id": "prob.bayes"}
    body.update(fields)
    return library.add_book_problem(conn, {key: value for key, value in body.items() if value is not None}, moment)


def test_logging_a_book_problem_schedules_it_from_its_confidence(conn):
    entry = log(conn)
    assert {key: entry[key] for key in ("book", "section", "problem", "answer", "confidence", "topic_id", "topic_title")} == {
        "book": "Zhou", "section": "2.3", "problem": "14", "answer": "1/3", "confidence": 3,
        "topic_id": "prob.bayes", "topic_title": "Bayes' rule"}
    assert len(entry["id"]) == 32 and entry["card"]["reviews"] == 1 and entry["card"]["last_rating"] == 3
    assert entry["card"]["due"] is False and datetime.fromisoformat(entry["card"]["due_at"]) > T0 + timedelta(days=1)
    unsure = log(conn, problem="15", confidence=1)
    certain = log(conn, problem="16", confidence=4)
    # Lower confidence comes back sooner; "couldn't do it" comes back within a day.
    assert unsure["card"]["due_at"] < entry["card"]["due_at"] < certain["card"]["due_at"]
    assert datetime.fromisoformat(unsure["card"]["due_at"]) <= T0 + timedelta(days=1)
    assert unsure["card"]["lapses"] == 0
    bare = log(conn, problem="17", section=None, answer=None, topic_id=None)
    assert bare["section"] == "" and bare["answer"] == "" and bare["topic_id"] is None and bare["topic_title"] is None


@pytest.mark.parametrize("fields", [
    {"book": ""}, {"book": "x" * 121}, {"problem": ""}, {"problem": "x" * 41}, {"section": "x" * 61},
    {"answer": "x" * 201}, {"confidence": 0}, {"confidence": 5}, {"confidence": True}, {"confidence": "3"},
    {"topic_id": "missing"}, {"book": "Zhou\x00"}, {"answer": "a\nb"}, {"book": 3},
])
def test_book_problems_are_short_references_and_validated(conn, fields):
    with pytest.raises((ValueError, practice.NotFound)):
        log(conn, **fields)


def test_book_problems_reject_unknown_fields_and_duplicates(conn):
    with pytest.raises(ValueError):
        library.add_book_problem(conn, {"book": "Zhou", "problem": "1", "confidence": 3, "text": "the problem's wording"}, T0)
    with pytest.raises(ValueError):
        library.add_book_problem(conn, {"book": "Zhou", "problem": "1"}, T0)
    log(conn)
    with pytest.raises(practice.Conflict):
        log(conn, answer="2/3")
    assert log(conn, book="  Zhou  ", section="2.4")["book"] == "Zhou"


def test_reviews_edits_and_deletes(conn):
    entry = log(conn)
    later = datetime.fromisoformat(entry["card"]["due_at"]) + timedelta(hours=1)
    listed = library.books(conn, later)
    assert listed["due"] == 1 and listed["problems"][0]["card"]["due"] is True and listed["books"] == ["Zhou"]
    again = library.rate_book_problem(conn, entry["id"], {"rating": 1, "answer": "2/5"}, later)
    assert again["answer"] == "2/5" and again["card"]["lapses"] == 1 and again["card"]["reviews"] == 2
    assert datetime.fromisoformat(again["card"]["due_at"]) <= later + timedelta(days=1)
    good = library.rate_book_problem(conn, entry["id"], {"rating": 3}, later + timedelta(days=2))
    assert good["answer"] == "2/5" and good["card"]["last_rating"] == 3 and good["card"]["lapses"] == 1
    for body in ({"rating": 0}, {"rating": 3, "extra": 1}, {}, {"rating": 2.5}):
        with pytest.raises(ValueError):
            library.rate_book_problem(conn, entry["id"], body, later)
    edited = library.edit_book_problem(conn, entry["id"], {"section": "2.5", "topic_id": None}, later)
    assert edited["section"] == "2.5" and edited["topic_id"] is None and edited["card"] == good["card"]
    other = log(conn, problem="99")
    with pytest.raises(practice.Conflict):
        library.edit_book_problem(conn, other["id"], {"section": "2.5", "problem": "14"}, later)
    with pytest.raises(ValueError):
        library.edit_book_problem(conn, entry["id"], {"confidence": 4}, later)  # confidence is the first rating only
    library.delete_book_problem(conn, entry["id"])
    with pytest.raises(practice.NotFound):
        library.delete_book_problem(conn, entry["id"])
    with pytest.raises(practice.NotFound):
        library.rate_book_problem(conn, "0" * 32, {"rating": 3}, later)
    assert [item["id"] for item in library.books(conn, later)["problems"]] == [other["id"]]


def test_book_list_puts_due_first_and_most_used_books_first(conn):
    soon = log(conn, confidence=1)
    log(conn, problem="2", confidence=4)
    log(conn, book="Crack", problem="7", confidence=3)
    log(conn, book="Crack", problem="8", confidence=3)
    log(conn, book="Crack", problem="9", confidence=3)
    listed = library.books(conn, T0 + timedelta(days=1, hours=1))
    assert listed["problems"][0]["id"] == soon["id"] and listed["due"] == 1
    assert [item["card"]["due_at"] for item in listed["problems"]] == sorted(item["card"]["due_at"] for item in listed["problems"])
    assert listed["books"] == ["Crack", "Zhou"]


def test_book_rows_never_hold_more_than_the_reference(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(math_book_problems)")}
    assert columns == {"id", "book", "section", "problem", "answer", "confidence", "topic_id", "fsrs", "due_at",
                       "reviews", "lapses", "last_rating", "created_at", "updated_at"}


def test_mastery_now_is_the_default_moment(conn):
    entry = library.add_book_problem(conn, {"book": "Stat 110", "problem": "3", "confidence": 2})
    assert abs(datetime.fromisoformat(entry["created_at"]) - mastery.now()) < timedelta(minutes=1)

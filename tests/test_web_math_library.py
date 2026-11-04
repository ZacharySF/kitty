"""rika! math phase 2 over real loopback HTTP: Grinstead & Snell exercises, own-book
problems and mixed review."""
from __future__ import annotations

import pytest

from kitty.core.math import mastery, textbook
from kitty.core.math import topics as graph

from test_web_math import master
from test_web_server import website  # noqa: F401  the shared loopback HTTP fixture


def self_graded():
    return next(item for item in textbook.exercises() if item.answer is None)


def numeric():
    return next(item for item in textbook.exercises() if item.answer is not None)


def exact(value):
    return f"{value.numerator}/{value.denominator}"


def test_textbook_lists_by_topic_and_shows_one_exercise(website):
    status, data, _ = website.request("/api/math/textbook")
    assert status == 200 and len(data["exercises"]) == len(textbook.exercises())
    assert data["source"]["short"] == "Grinstead & Snell" and data["source"]["licence"].startswith("GNU Free Documentation License")
    assert "text_md" not in data["exercises"][0]
    topic = self_graded().topics[0]
    _, linked, _ = website.request(f"/api/math/textbook?topic_id={topic}")
    assert linked["exercises"] and all(topic in item["topics"] for item in linked["exercises"])
    first = linked["exercises"][0]
    assert set(first) == {"id", "label", "section", "section_title", "topics", "graded", "program", "figure", "starred", "card"}
    status, one, _ = website.request(f"/api/math/textbook/{first['id']}")
    assert status == 200 and one["text_md"] == textbook.get(first["id"]).text_md and one["source"] == data["source"]
    assert website.request("/api/math/textbook?topic_id=nope")[0] == 404
    assert website.request("/api/math/textbook?page=2")[0] == 400
    assert website.request("/api/math/textbook/gs.99.9.999")[0] == 404


def test_self_graded_exercises_take_a_rating_and_join_the_schedule(website):
    exercise = self_graded()
    status, rated, _ = website.request("/api/math/textbook/ratings", {"exercise_id": exercise.id, "rating": 2})
    assert status == 200 and rated["card"]["reviews"] == 1 and rated["card"]["last_rating"] == 2
    _, listed, _ = website.request(f"/api/math/textbook?topic_id={exercise.topics[0]}")
    assert next(item for item in listed["exercises"] if item["id"] == exercise.id)["card"]["reviews"] == 1
    assert website.request("/api/math/textbook/ratings", {"exercise_id": exercise.id, "rating": 7})[0] == 400
    assert website.request("/api/math/textbook/ratings", {"exercise_id": numeric().id, "rating": 3})[0] == 409
    assert website.request("/api/math/textbook/problems", {"exercise_id": exercise.id})[0] == 409


def test_numeric_exercises_are_checked_and_graded_into_their_card(website, workspace):
    exercise = numeric()
    status, problem, _ = website.request("/api/math/textbook/problems", {"exercise_id": exercise.id})
    assert status == 200 and problem["exercise_id"] == exercise.id and problem["prompt_md"].startswith(exercise.text_md)
    assert exact(exercise.answer.value) not in str({k: v for k, v in problem.items() if k != "prompt_md"})
    body = {"problem_id": problem["problem_id"], "answer": "banana", "elapsed_ms": 4000}
    status, unread, _ = website.request("/api/math/textbook/answers", body)
    assert status == 200 and unread["correct"] is None and unread["card"] is None and unread["tries_left"] == 3
    status, right, _ = website.request("/api/math/textbook/answers", {**body, "answer": exact(exercise.answer.value)})
    assert status == 200 and right["correct"] is True and right["revealed"] is True and right["canonical"]
    assert right["card"]["reviews"] == 1 and right["card"]["last_rating"] == mastery.EASY
    assert website.request("/api/math/textbook/answers", body)[0] == 409
    assert website.request("/api/math/textbook/answers", {**body, "problem_id": "0" * 32})[0] == 404


def test_textbook_problems_never_reach_explain_my_mistake(website, workspace):
    exercise = numeric()
    _, problem, _ = website.request("/api/math/textbook/problems", {"exercise_id": exercise.id})
    website.request("/api/math/textbook/answers", {"problem_id": problem["problem_id"], "answer": "123456789", "elapsed_ms": 4000})
    status, _, _ = website.request("/api/math/explain", {"problem_id": problem["problem_id"], "working": "my steps"})
    assert status == 404
    assert workspace.conn.execute("SELECT count(*) FROM effects").fetchone()[0] == 0
    assert workspace.conn.execute("SELECT count(*) FROM ai_reviews").fetchone()[0] == 0


def test_own_book_problems_over_http(website):
    body = {"book": "Zhou", "section": "2.3", "problem": "14", "answer": "1/3", "confidence": 2, "topic_id": "prob.bayes"}
    status, entry, _ = website.request("/api/math/books", body)
    assert status == 200 and entry["topic_title"] == "Bayes' rule" and entry["card"]["last_rating"] == 2
    assert website.request("/api/math/books", body)[0] == 409
    assert website.request("/api/math/books", {**body, "problem": "15", "text": "quoted problem"})[0] == 400
    assert website.request("/api/math/books", {**body, "problem": "15", "topic_id": "nope"})[0] == 404
    _, listed, _ = website.request("/api/math/books")
    assert [item["id"] for item in listed["problems"]] == [entry["id"]] and listed["books"] == ["Zhou"] and listed["due"] == 0
    status, edited, _ = website.request(f"/api/math/books/{entry['id']}", {"section": "2.4"})
    assert status == 200 and edited["section"] == "2.4"
    status, reviewed, _ = website.request(f"/api/math/books/{entry['id']}/ratings", {"rating": 4, "answer": "2/7"})
    assert status == 200 and reviewed["answer"] == "2/7" and reviewed["card"]["reviews"] == 2
    assert website.request(f"/api/math/books/{entry['id']}/ratings", {"rating": 0})[0] == 400
    assert website.request(f"/api/math/books/{entry['id']}/delete", {})[1] == {"ok": True}
    assert website.request(f"/api/math/books/{entry['id']}/delete", {})[0] == 404
    assert website.request("/api/math/books?x=1")[0] == 400


def test_mixed_review_over_http(website, workspace):
    status, data, _ = website.request("/api/math/mixed")
    assert status == 200 and data == {"default": False, "mastered": 0, "needed": 5, "held_back": 0, "items": [],
                                      "why": "Mixed review opens once 5 topics are mastered (0 so far)"}
    master(workspace, *[topic.id for topic in graph.TOPICS[:6]])
    _, data, _ = website.request("/api/math/mixed")
    assert data["default"] is True and data["items"] and all(item["kind"] == "topic" for item in data["items"])
    item = data["items"][0]
    status, problem, _ = website.request("/api/math/problems", {"topic_id": item["topic_id"]})
    assert status == 200 and problem["purpose"] == "review"
    assert website.request("/api/math/mixed?x=1")[0] == 400


@pytest.mark.parametrize("path", ["/api/math/textbook/problems", "/api/math/textbook/ratings", "/api/math/textbook/answers"])
def test_textbook_posts_reject_unknown_fields(website, path):
    assert website.request(path, {"exercise_id": self_graded().id, "problem_id": "0" * 32, "answer": "1",
                                  "elapsed_ms": 1, "rating": 3, "text": "x"})[0] == 400

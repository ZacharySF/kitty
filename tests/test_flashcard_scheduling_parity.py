"""FSRS scheduling in rika-server (server/src/flashcards/scheduler.rs, the `fsrs` crate) against
py-fsrs 6, the scheduler the Python core used, as the reference.

Thousands of single reviews from random reachable states, settings and ratings must give the
same state, learning step and due time; stability and difficulty may differ only by the crate's
f32 arithmetic (a relative 1e-5). Whole review histories must keep agreeing on every due date.
"""
from __future__ import annotations

import json
import random
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from terminal_support import RIKA_SERVER

fsrs = pytest.importorskip("fsrs")

START = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _built():
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")


def scheduler(settings: dict) -> "fsrs.Scheduler":
    return fsrs.Scheduler(desired_retention=settings["retention"],
                          learning_steps=tuple(timedelta(seconds=s) for s in settings["learning_steps"]),
                          relearning_steps=tuple(timedelta(seconds=s) for s in settings["relearning_steps"]),
                          maximum_interval=settings["maximum_interval"], enable_fuzzing=False)


def random_settings(rng: random.Random) -> dict:
    steps = lambda most: sorted(rng.randint(1, 86400) for _ in range(rng.randint(0, most)))
    return {"retention": round(rng.uniform(0.7, 0.99), 2), "learning_steps": steps(3) if rng.random() < 0.8 else [60, 600],
            "relearning_steps": steps(2) if rng.random() < 0.8 else [600], "maximum_interval": rng.choice([36500, 365, 30, 1])}


def rust(cases: list[dict]) -> list:
    result = subprocess.run([str(RIKA_SERVER), "fsrs-review"], input=json.dumps(cases), capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)


def next_time(rng: random.Random, card, now: datetime) -> datetime:
    """A plausible next review: on time, early the same day, or late."""
    choice = rng.random()
    if choice < 0.5:
        return max(card.due, now)
    if choice < 0.75:
        return now + timedelta(seconds=rng.randint(1, 20000))
    return max(card.due, now) + timedelta(days=rng.randint(0, 400), seconds=rng.randint(0, 86399))


def assert_same(python: str, ours: str, context) -> None:
    expected, got = json.loads(python), json.loads(ours)
    # The same f64 operations in the same order: the stored numbers are py-fsrs's, to the last bit.
    for key in ("card_id", "state", "step", "due", "last_review", "stability", "difficulty"):
        assert got[key] == expected[key], (key, context, expected, got)


def test_single_reviews_match_py_fsrs():
    rng = random.Random(20261113)
    cases, expected = [], []
    for history in range(400):
        settings = random_settings(rng)
        reference = scheduler(settings)
        card = fsrs.Card(card_id=history + 1, due=START)
        now = START
        for _ in range(rng.randint(1, 12)):
            rating = rng.randint(1, 4)
            stored = None if card.stability is None else card.to_json()
            reviewed, _ = reference.review_card(card, fsrs.Rating(rating), review_datetime=now)
            cases.append({"card": stored, "card_id": card.card_id, "due": card.due.isoformat(), "rating": rating,
                          "now": now.isoformat(), "settings": settings})
            expected.append(reviewed.to_json())
            card = reviewed
            now = next_time(rng, card, now)
    results = rust(cases)
    assert len(results) == len(cases) > 2000
    for case, python, ours in zip(cases, expected, results):
        assert isinstance(ours, str), (case, ours)
        assert_same(python, ours, case)


def test_whole_histories_keep_the_same_due_dates():
    """Each implementation feeds its own output to its next review."""
    rng = random.Random(7)
    for history in range(60):
        settings = random_settings(rng)
        reference = scheduler(settings)
        card = fsrs.Card(card_id=history + 1, due=START)
        ours = None
        now = START
        for step in range(15):
            rating = rng.randint(1, 4)
            [ours] = rust([{"card": ours, "card_id": card.card_id, "due": START.isoformat(), "rating": rating,
                            "now": now.isoformat(), "settings": settings}])
            card, _ = reference.review_card(card, fsrs.Rating(rating), review_datetime=now)
            got = json.loads(ours)
            assert (got["state"], got["step"], got["due"]) == (card.state.value, card.step, card.due.isoformat()), (history, step)
            assert (got["stability"], got["difficulty"]) == (card.stability, card.difficulty), (history, step)
            now = next_time(rng, card, now)


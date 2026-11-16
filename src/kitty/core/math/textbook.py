"""Grinstead & Snell's *Introduction to Probability* exercises, linked to rika!'s topics.

The exercise text is GNU FDL data outside the MIT tree: ``third_party/grinstead-snell/
exercises.json`` (made by ``scripts/extract_grinstead_snell.py``), bundled as
``grinstead-snell`` in the desktop build. Everything else here is rika!'s own work, in
``textbook.json`` beside this file (rika-server compiles the same file in,
server/src/math/textbook.rs): the source's citation, the topic links (each section's
default topics, refined by keywords in the text, and the exercises with a more specific
primary topic) and the numeric answers in :data:`ANSWERS`, computed by us and checked in
the tests against independent enumerations. The free edition has no answer key, so every
other exercise is self-graded.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from pathlib import Path

from kitty.core.math import problems
from kitty.core.math.topics import BY_ID

_LINKS = json.loads(Path(__file__).with_name("textbook.json").read_text(encoding="utf-8"))
SOURCE: dict[str, str] = dict(_LINKS["source"])
#: Seconds a fluent learner needs for a book exercise.
TARGET_S: int = _LINKS["target_s"]
#: Topics linked to one exercise, at most.
MAX_TOPICS: int = _LINKS["max_topics"]


@dataclass(frozen=True)
class Answer:
    """A numeric answer authored by rika! (our own computation), never copied from the book."""
    value: Fraction
    #: "b" when the number answers part (b) only.
    part: str | None = None
    sigfigs: int | None = None
    tolerance: Fraction | None = None


@dataclass(frozen=True)
class Exercise:
    id: str
    label: str
    chapter: int
    section: str
    section_title: str
    number: int
    starred: bool
    #: Markdown with ``$…$`` / ``$$…$$`` TeX, KaTeX-safe.
    text_md: str
    #: At least one topic id; the first is the primary topic.
    topics: tuple[str, ...]
    #: Asks for a computer program.
    program: bool
    #: Refers to a figure that isn't bundled (the text stands without it).
    figure: bool
    answer: Answer | None

    @property
    def graded(self) -> str:
        return "number" if self.answer else "self"


#: Each section's topics, primary first.
SECTIONS: dict[str, tuple[str, ...]] = {section: tuple(topics) for section, topics in _LINKS["sections"].items()}
#: Keywords in an exercise's text that add topics after its section's, in this order.
KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = tuple((pattern, tuple(topics)) for pattern, topics in _LINKS["keywords"])
_KEYWORDS = tuple((re.compile(pattern, re.IGNORECASE), topics) for pattern, topics in KEYWORDS)
#: Exercises whose method calls for a more specific primary topic than their section's.
PRIMARY: dict[str, str] = dict(_LINKS["primary"])
#: Numeric answers, rika!'s own computation; tests/test_math_textbook.py checks each one
#: against an independent enumeration or exact oracle.
ANSWERS: dict[str, Answer] = {
    exercise_id: Answer(Fraction(item["value"]), item.get("part"), item.get("sigfigs"),
                        Fraction(item["tolerance"]) if "tolerance" in item else None)
    for exercise_id, item in _LINKS["answers"].items()}


def directory() -> Path:
    """``third_party/grinstead-snell`` in a checkout, ``grinstead-snell`` in the frozen app."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "grinstead-snell"
    return Path(__file__).resolve().parents[4] / "third_party/grinstead-snell"


def topics_for(exercise_id: str, section: str, text: str) -> tuple[str, ...]:
    """Primary first: an exercise's override, its section's topics, then keyword topics."""
    found = [topic for pattern, topics in _KEYWORDS if pattern.search(text) for topic in topics]
    ordered = ([PRIMARY[exercise_id]] if exercise_id in PRIMARY else []) + list(SECTIONS[section]) + found
    return tuple(dict.fromkeys(ordered))[:MAX_TOPICS]


@cache
def exercises() -> tuple[Exercise, ...]:
    """Every bundled exercise, in book order."""
    data = json.loads((directory() / "exercises.json").read_text(encoding="utf-8"))
    out = []
    for item in data["exercises"]:
        topics = topics_for(item["id"], item["section"], item["text_md"])
        unknown = [topic for topic in topics if topic not in BY_ID]
        if unknown:
            raise ValueError(f"{item['id']} links unknown topics {unknown}")
        out.append(Exercise(item["id"], item["label"], item["chapter"], item["section"], item["section_title"],
                            item["number"], item["starred"], item["text_md"], topics, item["program"],
                            item["figure"], ANSWERS.get(item["id"])))
    missing = ANSWERS.keys() - {exercise.id for exercise in out}
    if missing:
        raise ValueError(f"Answers for unknown exercises {sorted(missing)}")
    return tuple(out)


@cache
def _by_id() -> dict[str, Exercise]:
    return {exercise.id: exercise for exercise in exercises()}


def get(exercise_id: str) -> Exercise:
    return _by_id()[exercise_id]


def for_topic(topic_id: str) -> list[Exercise]:
    return [exercise for exercise in exercises() if topic_id in exercise.topics]


def problem(exercise: Exercise) -> problems.Problem:
    """The exercise as a checkable problem; a self-graded exercise has none."""
    answer = exercise.answer
    if answer is None:
        raise ValueError(f"{exercise.id} is self-graded")
    prompt = exercise.text_md + (f"\n\nAnswer part ({answer.part})." if answer.part else "")
    whole = answer.value.denominator == 1 and answer.sigfigs is None and answer.tolerance is None
    return problems.Problem(f"textbook:{exercise.id}", exercise.topics[0], 0, {}, prompt, answer.value,
                            "number" if whole else "fraction", (), tolerance=answer.tolerance,
                            sigfigs=answer.sigfigs, target_s=TARGET_S)


def clear_cache() -> None:
    """Forget the loaded exercises (tests point :func:`directory` elsewhere)."""
    exercises.cache_clear()
    _by_id.cache_clear()

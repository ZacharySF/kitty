"""Grinstead & Snell's *Introduction to Probability* exercises, linked to rika!'s topics.

The exercise text is GNU FDL data outside the MIT tree: ``third_party/grinstead-snell/
exercises.json`` (made by ``scripts/extract_grinstead_snell.py``), bundled as
``grinstead-snell`` in the desktop build. Everything else here is rika!'s own work:
the topic links (each section's default topics, refined by keywords in the text) and
the numeric answers in :data:`ANSWERS`, computed by us and checked in the tests against
independent enumerations. The free edition has no answer key, so every other exercise
is self-graded.
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

SOURCE = {
    "title": "Grinstead and Snell's Introduction to Probability",
    "short": "Grinstead & Snell",
    "version": "Version dated 22 July 2006",
    "licence": "GNU Free Documentation License 1.3",
    "url": "https://math.dartmouth.edu/~doyle/docs/prob/prob.tar.gz",
}
#: Seconds a fluent learner needs for a book exercise.
TARGET_S = 300
#: Topics linked to one exercise, at most.
MAX_TOPICS = 4


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
SECTIONS: dict[str, tuple[str, ...]] = {
    "1.1": ("prob.basic",),
    "1.2": ("prob.basic", "prob.complement"),
    "2.1": ("prob.basic", "calc.definite"),
    "2.2": ("calc.definite", "prob.basic"),
    "3.1": ("count.permutations", "count.product-rule"),
    "3.2": ("count.combinations", "prob.binomial"),
    "3.3": ("count.permutations",),
    "4.1": ("prob.conditional", "prob.independence"),
    "4.2": ("prob.conditional", "calc.definite"),
    "4.3": ("prob.conditional", "ev.expected-value"),
    "5.1": ("prob.binomial",),
    "5.2": ("calc.definite", "calc.integrals-exp-trig"),
    "6.1": ("ev.expected-value", "ev.linearity"),
    "6.2": ("ev.variance", "ev.variance-sum"),
    "6.3": ("ev.expected-value", "calc.definite"),
    "7.1": ("prob.independence", "ev.linearity"),
    "7.2": ("calc.definite", "prob.independence"),
    "8.1": ("ev.variance-sum", "stats.standard-error"),
    "8.2": ("ev.variance-sum", "calc.definite"),
    "9.1": ("stats.normal-rule", "prob.binomial", "stats.z-score"),
    "9.2": ("stats.standard-error", "stats.normal-rule"),
    "9.3": ("stats.standard-error", "stats.confidence"),
    "10.1": ("calc.taylor", "ev.expected-value"),
    "10.2": ("calc.taylor", "prob.gamblers-ruin"),
    "10.3": ("calc.taylor", "calc.definite"),
    "11.1": ("linalg.matmul", "prob.total-probability"),
    "11.2": ("prob.gamblers-ruin", "linalg.matmul", "linalg.inverse3"),
    "11.3": ("linalg.eigen2", "linalg.matmul"),
    "11.4": ("linalg.matmul", "linalg.eigen2"),
    "11.5": ("ev.waiting-time", "linalg.inverse3"),
    "12.1": ("disc.lattice-paths", "prob.gamblers-ruin"),
    "12.2": ("prob.gamblers-ruin", "disc.recurrence"),
    "12.3": ("disc.lattice-paths", "count.combinations"),
}
#: Keywords in an exercise's text that add topics after its section's, in this order.
KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\bBayes|posterior", ("prob.bayes",)),
    (r"generating function", ("calc.taylor",)),
    (r"Markov chain|transition matrix", ("linalg.matmul", "prob.total-probability")),
    (r"fundamental matrix", ("linalg.inverse3",)),
    (r"fixed (probability )?(row |column )?vector", ("linalg.eigen2",)),
    (r"random walk", ("prob.gamblers-ruin", "disc.lattice-paths")),
    (r"gambler|\bruin\b", ("prob.gamblers-ruin",)),
    (r"\bvariance|standard deviation", ("ev.variance",)),
    (r"expected (value|number|winning|time|cost|fortune|score|amount)", ("ev.expected-value",)),
    (r"waiting time|expected (time|number of (tosses|rolls|steps|trials))", ("ev.waiting-time",)),
    (r"\bdensit(y|ies)\b", ("calc.definite",)),
    (r"hypergeometric|(poker|bridge|gin) hand (is|consists|has)|bridge partner", ("prob.hypergeometric",)),
    (r"binomial distribution|Bernoulli trials", ("prob.binomial",)),
    (r"geometric distribution|first (success|head|six|tail)", ("prob.geometric",)),
    (r"normal (density|distribution|approximation|curve)|Central Limit", ("stats.normal-rule",)),
    (r"confidence interval", ("stats.confidence",)),
    (r"indistinguishable", ("count.stars-bars",)),
    (r"\\choose|binomial coefficient", ("count.combinations",)),
    (r"permutation", ("count.permutations",)),
    (r"independen", ("prob.independence",)),
    (r"\bgiven (that|the)\b|conditional probability", ("prob.conditional",)),
    (r"at least one\b", ("prob.complement",)),
    (r"\bodds\b", ("arith.odds",)),
    (r"\b(max|min)(imum)?\b", ("prob.order-stats",)),
    (r"fair (game|bet|price)|favorable (game|bet)", ("ev.fair-price",)),
    (r"recursion|recurrence|difference equation", ("disc.recurrence",)),
    (r"\\int\b|integral", ("calc.definite",)),
)
_KEYWORDS = tuple((re.compile(pattern, re.IGNORECASE), topics) for pattern, topics in KEYWORDS)
#: Exercises whose method calls for a more specific primary topic than their section's.
PRIMARY = {
    "gs.1.2.10": "count.inclusion-exclusion",
    "gs.1.2.21": "prob.geometric",
    "gs.1.2.26": "prob.symmetry",
    "gs.3.1.10": "prob.symmetry",
    "gs.3.2.1": "prob.binomial",
    "gs.3.2.12": "prob.hypergeometric",
    "gs.3.2.19": "prob.hypergeometric",
    "gs.3.2.20": "prob.hypergeometric",
    "gs.3.2.21": "count.product-rule",
    "gs.3.2.22": "count.stars-bars",
    "gs.3.2.24": "count.stars-bars",
    "gs.3.2.25": "count.stars-bars",
    "gs.3.2.31": "prob.binomial",
    "gs.4.1.18": "prob.bayes",
    "gs.4.1.19": "prob.bayes",
    "gs.4.1.22": "prob.bayes",
    "gs.4.1.43": "prob.binomial",
    "gs.5.1.7": "prob.geometric",
    "gs.5.1.8": "prob.geometric",
    "gs.5.1.38": "prob.hypergeometric",
    "gs.5.1.40": "prob.hypergeometric",
}

F = Fraction
#: Numeric answers, rika!'s own computation; tests/test_math_textbook.py checks each one
#: against an independent enumeration or exact oracle.
ANSWERS: dict[str, Answer] = {
    "gs.1.2.6": Answer(F(4, 7)),
    "gs.1.2.7": Answer(F(11, 12)),
    "gs.1.2.10": Answer(F(1, 2)),
    "gs.1.2.21": Answer(F(7, 4096)),
    "gs.1.2.26": Answer(F(8, 17)),
    "gs.3.1.1": Answer(F(24)),
    "gs.3.1.2": Answer(F(12)),
    "gs.3.1.3": Answer(F(2 ** 32)),
    "gs.3.1.7": Answer(F(24, 625)),
    "gs.3.1.10": Answer(F(1, 13)),
    "gs.3.1.13": Answer(F(351_520_000), part="b"),
    "gs.3.2.1": Answer(F(583_443, 12_500_000), part="h", sigfigs=3),
    "gs.3.2.2": Answer(F(252)),
    "gs.3.2.3": Answer(F(36)),
    "gs.3.2.12": Answer(F(6, 4165), part="d"),
    "gs.3.2.19": Answer(F(2, 27_657_385), part="a"),
    "gs.3.2.20": Answer(F(12, 2_544_815), part="b"),
    "gs.3.2.21": Answer(F(93)),
    "gs.3.2.22": Answer(F(28)),
    "gs.3.2.24": Answer(F(646, 70_035), sigfigs=3),
    "gs.3.2.25": Answer(F(6, 143), part="b"),
    "gs.3.2.31": Answer(F(99_999_603, 100_000_000), sigfigs=6),
    "gs.4.1.3": Answer(F(2, 3), part="b"),
    "gs.4.1.4": Answer(F(4, 13), part="b"),
    "gs.4.1.6": Answer(F(3, 10)),
    "gs.4.1.9": Answer(F(1, 3), part="a"),
    "gs.4.1.13": Answer(F(1, 2)),
    "gs.4.1.15": Answer(F(2223, 7249), part="a", sigfigs=3),
    "gs.4.1.19": Answer(F(3, 11), sigfigs=3),
    "gs.4.1.22": Answer(F(1, 2)),
    "gs.4.1.29": Answer(F(2, 3)),
    "gs.4.1.43": Answer(F(11_097, 15_625), sigfigs=3),
    "gs.5.1.7": Answer(F(125, 216), part="b"),
    "gs.5.1.8": Answer(F(1, 8)),
    "gs.5.1.38": Answer(F(2275, 5168), part="b", sigfigs=3),
    "gs.5.1.40": Answer(F(342_132_219, 1_587_533_899), part="a", sigfigs=3),
    "gs.6.1.1": Answer(F(-1, 9)),
    "gs.6.1.2": Answer(F(-1, 2)),
    "gs.6.1.4": Answer(F(-1, 19)),
    "gs.6.1.5": Answer(F(-1, 19)),
    "gs.6.1.13": Answer(F(45)),
    "gs.6.2.4": Answer(F(10_015), part="a"),
}
del F


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

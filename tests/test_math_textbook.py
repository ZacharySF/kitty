"""Grinstead & Snell exercises: the bundled JSON regenerates from the pinned source, its
numbering matches the official build, every exercise has KaTeX-safe text and valid topics,
and every authored answer equals an independent enumeration and grades through
:func:`checking.check`.

The source checks need the pinned tarball, and the numbering check the official build's
``ch1.aux`` … ``ch12.aux`` (https://math.dartmouth.edu/~prob/prob/), in one directory:

    KITTY_GS_SOURCE=/path/to/dir uv run --locked pytest -q tests/test_math_textbook.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
from decimal import Decimal
from fractions import Fraction as F
from functools import cache
from itertools import combinations, combinations_with_replacement, permutations, product
from math import comb, prod
from pathlib import Path

import pytest

from kitty.core.math import checking, textbook
from kitty.core.math.topics import BY_ID

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "third_party/grinstead-snell"
SOURCE_DIR = Path(os.environ["KITTY_GS_SOURCE"]) if os.environ.get("KITTY_GS_SOURCE") else None


@cache
def extractor():
    """scripts/extract_grinstead_snell.py, loaded once (it isn't a package module)."""
    spec = importlib.util.spec_from_file_location("extract_grinstead_snell", ROOT / "scripts/extract_grinstead_snell.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tarball() -> Path:
    if SOURCE_DIR is None or not (SOURCE_DIR / "prob.tar.gz").is_file():
        pytest.skip("set KITTY_GS_SOURCE to a directory with the pinned prob.tar.gz")
    return SOURCE_DIR / "prob.tar.gz"


@pytest.fixture(scope="module")
def bundle() -> dict:
    return json.loads((BUNDLE / "exercises.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the bundle and its source


def test_bundle_regenerates_byte_identically_from_the_pinned_tarball(tarball):
    script = extractor()
    assert script.dump(script.extract(script.source(tarball))) == (BUNDLE / "exercises.json").read_text(encoding="utf-8")


def test_numbering_and_exercise_counts_match_the_official_build(tarball):
    aux = tarball.parent
    if not all((aux / f"ch{chapter}.aux").is_file() for chapter in range(1, 13)):
        pytest.skip("put the official ch1.aux … ch12.aux next to prob.tar.gz")
    script = extractor()
    result = script.extract(script.source(tarball), aux)  # every \label prints as in the .aux files
    counts: dict[str, int] = {}
    for label in [item["label"] for item in result["exercises"]] + [item["label"] for item in result["skipped"]]:
        section = label.rsplit(".", 1)[0]
        counts[section] = counts.get(section, 0) + 1
    for chapter in range(1, 13):
        text = (aux / f"ch{chapter}.aux").read_text()
        highest: dict[str, int] = {}
        section = None
        for line in text.splitlines():
            if match := re.search(r"contentsline \{section\}\{\\numberline \{([\d.]+)\}", line):
                section = match.group(1)
            elif section and (match := re.match(r"\\newlabel\{[^}]*\}\{\{(\d+)\}", line)):
                highest[section] = max(highest.get(section, 0), int(match.group(1)))
        # The highest exercise label in each section is its last exercise, and the
        # chapter's final enumerate counter is the last section's exercise count.
        assert {key: counts[key] for key in highest} == highest
        last = max(highest, key=lambda key: int(key.split(".")[1]))
        assert int(re.search(r"\\setcounter\{enumi\}\{(\d+)\}", text).group(1)) == counts[last]


def test_source_note_records_the_pin_and_every_skipped_exercise(bundle):
    note = (BUNDLE / "SOURCE.txt").read_text(encoding="utf-8")
    assert bundle["source"]["url"] in note and bundle["source"]["sha256"] in note
    assert "GNU Free Documentation License" in (BUNDLE / "LICENSE").read_text(encoding="utf-8")
    assert bundle["skipped"]
    for item in bundle["skipped"]:
        assert f"{item['label']}: {item['reason']}" in " ".join(note.split())
    labels = {exercise.label for exercise in textbook.exercises()}
    assert not labels & {item["label"] for item in bundle["skipped"]}


def test_source_matches_the_bundle(bundle):
    assert textbook.SOURCE["url"] == bundle["source"]["url"]
    assert textbook.SOURCE["version"] == bundle["source"]["version"]
    assert set(textbook.SOURCE) == {"title", "short", "version", "licence", "url"}


# ---------------------------------------------------------------- exercises


def test_every_exercise_has_text_a_valid_primary_topic_and_a_unique_id():
    exercises = textbook.exercises()
    assert len(exercises) > 700
    assert len({exercise.id for exercise in exercises}) == len(exercises)
    order = [(exercise.chapter, int(exercise.section.split(".")[1]), exercise.number) for exercise in exercises]
    assert order == sorted(order)
    for exercise in exercises:
        assert exercise.id == f"gs.{exercise.label}" == f"gs.{exercise.section}.{exercise.number}"
        assert exercise.section.split(".")[0] == str(exercise.chapter)
        assert exercise.text_md.strip() and exercise.section_title
        assert exercise.topics and all(topic in BY_ID for topic in exercise.topics)
        assert len(set(exercise.topics)) == len(exercise.topics) <= textbook.MAX_TOPICS
        assert exercise.topics[0] == textbook.PRIMARY.get(exercise.id, textbook.SECTIONS[exercise.section][0])


def convert(latex: str, refs: dict | None = None) -> str:
    converter = extractor().Converter("t.1.1", refs or {})
    return converter.markdown(converter.text(latex))


def test_converter_writes_katex_markdown():
    assert convert("Find \\emx {at random} $x$~and ``this'' --- {\\bf Bold} \\$5.") == \
        "Find *at random* $x$\u00a0and “this” — **Bold** \\$5."
    assert convert("See Exercise~\\ref{exer 1.1.2}\\index{x}.", {"exer 1.1.2": "2"}) == "See Exercise\u00a02."
    assert convert("Let\n\\begin{enumerate}\n\\item one\n\\item two\n\\end{enumerate}") == "Let\n\n(a) one\n\n(b) two"
    assert convert("$$\\pmatrix{1 & 2 \\cr 3 & 4 \\cr}$$") == "$$\\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}$$"
    assert convert("\\begin{eqnarray*} a & = & b \\\\ & = & c \\end{eqnarray*}") == \
        "$$\\begin{aligned} a &= b \\\\ &= c \\end{aligned}$$"
    assert convert("$\\mbox{if $x > 0$}$") == "$\\text{if }x > 0$"


@pytest.mark.parametrize("latex", ["\\unknownmacro", "\\begin{verbatim}x\\end{verbatim}", "a_b", "\\ref{nowhere}",
                                   "$x$2", "$$\\eqno(1)$$"])
def test_converter_fails_loudly_instead_of_guessing(latex):
    script = extractor()
    with pytest.raises(script.Unsupported):
        convert(latex)


def test_keywords_refine_section_topics():
    assert textbook.topics_for("gs.x", "4.1", "Use Bayes' formula.") == ("prob.conditional", "prob.independence", "prob.bayes")
    chains = [exercise for exercise in textbook.exercises() if "Markov chain" in exercise.text_md]
    assert chains and all("linalg.matmul" in exercise.topics for exercise in chains)
    walks = [exercise for exercise in textbook.exercises() if "random walk" in exercise.text_md.lower()]
    assert walks and all("prob.gamblers-ruin" in exercise.topics for exercise in walks)
    assert textbook.topics_for("gs.x", "6.1", "Find the variance.") == ("ev.expected-value", "ev.linearity", "ev.variance")


#: LaTeX the converter must never pass through, in prose or math.
LEFTOVERS = [r"\\index\b", r"\\label\b", r"\\ref\b", r"\\i\b", r"\\istar\b", r"\\item\b", r"LJSItem", r"\\emx\b",
             r"\\mat\b", r"\\NA\b", r"\\putfig", r"\\footnote", r"\\choice", r"\\cr\b", r"\\pmatrix", r"\\bordermatrix",
             r"eqnarray", r"tabular", r"\\multicolumn", r"\\cline", r"\\begin\{(enumerate|itemize|table|quote)\}",
             r"\\(noindent|par|medskip|smallskip|bigskip|medbreak|hfill|vskip)\b", r"\\protect", r"\\mbox"]
#: …and what must not be left in the prose between math segments.
PROSE_LEFTOVERS = [r"\\[A-Za-z]", r"[{}]\\", r"``", r"''", r"~", r"(?<!\\)\$"]


def prose(text: str) -> str:
    return re.sub(r"(?<!\\)\$(?!\$)(?:\\.|[^$\\])*\$", " ", re.sub(r"\$\$.*?\$\$", " ", text, flags=re.DOTALL))


@pytest.mark.parametrize("pattern", LEFTOVERS)
def test_no_latex_leftovers(pattern):
    found = [exercise.label for exercise in textbook.exercises() if re.search(pattern, exercise.text_md)]
    assert found == []


@pytest.mark.parametrize("pattern", PROSE_LEFTOVERS)
def test_no_tex_in_prose(pattern):
    found = [exercise.label for exercise in textbook.exercises() if re.search(pattern, prose(exercise.text_md))]
    assert found == []


def test_flags_parts_and_lookups():
    exercises = textbook.exercises()
    assert any(exercise.program for exercise in exercises) and any(exercise.starred for exercise in exercises)
    assert {exercise.label for exercise in exercises if exercise.figure} >= {"1.1.17", "6.1.26"}
    assert "(a) " in textbook.get("gs.2.2.8").text_md and "(i) " in textbook.get("gs.2.2.8").text_md
    # prob.tex's \i inside an enumerate is a part, as printed: exercise 10.2.6 has (a)–(c).
    assert "(c) If we start the branching process" in textbook.get("gs.10.2.6").text_md
    assert textbook.get("gs.4.1.39").text_md.count("**Table 4.6.**") == 1
    assert textbook.for_topic("prob.bayes") == [e for e in exercises if "prob.bayes" in e.topics]
    with pytest.raises(KeyError):
        textbook.get("gs.99.1.1")


def test_loader_reads_the_frozen_bundle(monkeypatch, tmp_path):
    shutil.copytree(BUNDLE, tmp_path / "grinstead-snell")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    textbook.clear_cache()
    try:
        assert textbook.directory() == tmp_path / "grinstead-snell"
        assert len(textbook.exercises()) > 700
        monkeypatch.delattr(sys, "_MEIPASS")
        assert textbook.directory() == BUNDLE
    finally:
        textbook.clear_cache()


# ---------------------------------------------------------------- authored answers and oracles

DECK = [(rank, suit) for rank in range(2, 15) for suit in "SHDC"]
RED = "HD"


def uniform(outcomes, event, given=lambda _: True):
    """P(event | given) with equally likely outcomes, by counting."""
    kept = [outcome for outcome in outcomes if given(outcome)]
    return F(sum(1 for outcome in kept if event(outcome)), len(kept))


def weighted(outcomes, event, given=lambda _: True):
    """P(event | given) for ``(outcome, probability)`` pairs."""
    kept = [(outcome, weight) for outcome, weight in outcomes if given(outcome)]
    return sum(weight for outcome, weight in kept if event(outcome)) / sum(weight for _, weight in kept)


def bernoulli(n, p):
    return [(trial, prod(p if hit else 1 - p for hit in trial)) for trial in product((1, 0), repeat=n)]


def hands_by_rank(size, wanted):
    """Probability that a ``size``-card hand's rank multiset satisfies ``wanted``, summing the
    suit choices of every rank multiset (the total is C(52, size) without the formula)."""
    good = total = 0
    for ranks in combinations_with_replacement(range(13), size):
        counts = [ranks.count(rank) for rank in set(ranks)]
        if max(counts) > 4:
            continue
        ways = prod(len(list(combinations(range(4), count))) for count in counts)
        total += ways
        good += ways if wanted(ranks) else 0
    return F(good, total)


def hands_by_suit(size, wanted):
    """Probability that a hand's suit counts satisfy ``wanted``, summing over compositions."""
    good = total = 0
    for counts in product(range(14), repeat=4):
        if sum(counts) == size:
            ways = prod(comb(13, count) for count in counts)
            total += ways
            good += ways if wanted(sorted(counts)) else 0
    return F(good, total)


def aces_dealt(cards):
    """Distribution of aces among ``cards`` dealt one at a time from 52, step by step."""
    state = {0: F(1)}
    for dealt in range(cards):
        nxt: dict[int, F] = {}
        for aces, weight in state.items():
            left = 52 - dealt
            nxt[aces + 1] = nxt.get(aces + 1, 0) + weight * F(4 - aces, left)
            nxt[aces] = nxt.get(aces, 0) + weight * F(48 - (dealt - aces), left)
        state = nxt
    return state


def compositions(total, boxes, least):
    """Ways to put ``total`` identical objects in ``boxes`` boxes, each getting ≥ ``least``."""
    ways = {0: 1}
    for _ in range(boxes):
        ways = {n: sum(w for m, w in ways.items() if n - m >= least) for n in range(total + 1)}
    return ways[total]


def fortune():
    """6.1.13: bet half on white at each draw; every order of WWBB is equally likely."""
    finals = []
    for order in set(permutations("WWBB")):
        money = F(80)
        for ball in order:
            money += money / 2 if ball == "W" else -money / 2
        finals.append(money)
    return sum(finals) / len(finals)


def three_point():
    """6.2.4: a distribution with E(X) = 100 and V(X) = 15, then E(X^2) by enumeration."""
    dist = {95: F(3, 10), 100: F(4, 10), 105: F(3, 10)}
    mean = sum(x * p for x, p in dist.items())
    assert mean == 100 and sum((x - mean) ** 2 * p for x, p in dist.items()) == 15
    return sum(x * x * p for x, p in dist.items())


def twelve_points():
    """1.2.7: a 12-point uniform space with P(A∩B)=1/4, P(not A)=1/3, P(B)=1/2."""
    a, b = set(range(8)), {0, 1, 2, 8, 9, 10}
    assert (F(len(a & b), 12), F(12 - len(a), 12), F(len(b), 12)) == (F(1, 4), F(1, 3), F(1, 2))
    return F(len(a | b), 12)


def bills():
    """1.2.10: ten bills, 6 pass the House, 8 the Senate, 9 at least one."""
    house, senate = set(range(6)), {0, 1, 2, 3, 4, 6, 7, 8}
    assert (len(house), len(senate), len(house | senate)) == (6, 8, 9)
    return F(len(house & senate), 10)


def admissions():
    """4.1.29: ten equally likely futures: both 2, Dartmouth only 3, Harvard only 1, neither 4."""
    worlds = ["DH"] * 2 + ["D"] * 3 + ["H"] * 1 + [""] * 4
    assert (uniform(worlds, lambda w: "D" in w), uniform(worlds, lambda w: "H" in w)) == (F(1, 2), F(3, 10))
    return uniform(worlds, lambda w: "D" in w, given=lambda w: "H" in w)


def last_card_ace():
    """3.1.10: condition on the aces among the first 12 cards dealt."""
    return sum(weight * F(4 - aces, 40) for aces, weight in aces_dealt(12).items())


def partner_two_aces():
    dist = aces_dealt(13)
    return dist[2] / (1 - dist[0])


ORACLES = {
    "gs.1.2.6": lambda: weighted([(face, F(face, 21)) for face in range(1, 7)], lambda face: face % 2 == 0),
    "gs.1.2.7": twelve_points,
    "gs.1.2.10": bills,
    "gs.1.2.21": lambda: uniform(list(product("HT", repeat=12)), lambda s: "H" in s and s.index("H") + 1 in (10, 11, 12)),
    "gs.1.2.26": lambda: uniform(list(permutations(DECK, 2)), lambda pair: pair[1][0] > pair[0][0]),
    "gs.3.1.1": lambda: F(len(list(permutations(range(4))))),
    "gs.3.1.2": lambda: F(len(list(product(range(4), range(3))))),
    "gs.3.1.3": lambda: F(sum(1 for _ in product((0, 1), repeat=16)) ** 2),
    "gs.3.1.7": lambda: uniform(list(product(range(5), repeat=5)), lambda floors: len(set(floors)) == 5),
    "gs.3.1.10": last_card_ace,
    "gs.3.1.13": lambda: F(len(list(combinations(range(6), 3))) * len(list(product(range(10), repeat=3)))
                           * len(list(product(range(26), repeat=3)))),
    "gs.3.2.1": lambda: weighted(bernoulli(8, F(3, 10)), lambda trial: sum(trial) == 5),
    "gs.3.2.2": lambda: F(len(list(combinations(range(10), 5)))),
    "gs.3.2.3": lambda: F(len(list(combinations(range(9), 7)))),
    "gs.3.2.12": lambda: hands_by_rank(5, lambda ranks: sorted(ranks.count(r) for r in set(ranks)) == [2, 3]),
    "gs.3.2.19": lambda: hands_by_suit(10, lambda counts: counts[-1] == 10),
    "gs.3.2.20": lambda: hands_by_rank(6, lambda ranks: (ranks.count(12), ranks.count(11), ranks.count(10)) == (3, 2, 1)),
    "gs.3.2.21": lambda: F(sum(1 for nails in product("RYB", repeat=5) if len(set(nails)) <= 2)),
    "gs.3.2.22": lambda: F(sum(1 for boxes in product(range(7), repeat=3) if sum(boxes) == 6)),
    "gs.3.2.24": lambda: F(compositions(20, 10, 1), compositions(20, 10, 0)),
    "gs.3.2.25": lambda: uniform(list(combinations_with_replacement(range(10), 6)), lambda floors: len(set(floors)) == 6),
    "gs.3.2.31": lambda: weighted(bernoulli(4, F(99, 100)), lambda engines: sum(engines) >= 2),
    "gs.4.1.3": lambda: uniform(list(product(range(1, 7), repeat=2)), lambda d: sum(d) > 7, given=lambda d: d[0] > 3),
    "gs.4.1.4": lambda: uniform(DECK, lambda card: card[0] > 10, given=lambda card: card[1] == "H"),
    "gs.4.1.6": lambda: uniform(list(product((2, 4, 6, 8, 10), repeat=3)), lambda d: d.count(2) == 2,
                                given=lambda d: sum(d) == 12),
    "gs.4.1.9": lambda: uniform(list(product("BG", repeat=2)), lambda kids: kids == ("B", "B"), given=lambda kids: "B" in kids),
    "gs.4.1.13": lambda: uniform(list(permutations(DECK, 2)), lambda pair: pair[1][1] in RED),
    "gs.4.1.15": partner_two_aces,
    "gs.4.1.19": lambda: weighted([((better, raises), F(4 if better else 96, 100) * F(r, 10))
                                   for better in (True, False)
                                   for raises, r in ((True, 9 if better else 1), (False, 1 if better else 9))],
                                  lambda o: o[0], given=lambda o: o[1]),
    "gs.4.1.22": lambda: weighted([((coin, tosses), F(1, 65) * prod(F(1) if coin == 0 else F(1, 2) for _ in tosses))
                                   for coin in range(65) for tosses in product("HT", repeat=6)
                                   if coin != 0 or tosses == ("H",) * 6],
                                  lambda o: o[0] == 0, given=lambda o: o[1] == ("H",) * 6),
    "gs.4.1.29": admissions,
    "gs.4.1.43": lambda: weighted(bernoulli(7, F(6, 10)), lambda games: sum(games) >= 4),
    "gs.5.1.7": lambda: uniform(list(product(range(1, 7), repeat=3)), lambda rolls: 6 not in rolls),
    "gs.5.1.8": lambda: uniform(list(product("HT", repeat=5)), lambda s: "H" not in s, given=lambda s: s[:2] == ("T", "T")),
    "gs.5.1.38": lambda: uniform(list(combinations(range(20), 5)), lambda sample: sum(item < 5 for item in sample) == 1),
    "gs.5.1.40": lambda: hands_by_suit(13, lambda counts: counts == [2, 3, 4, 4]),
    "gs.6.1.1": lambda: F(sum(1 if card % 2 else -1 for card in range(2, 11)), 9),
    "gs.6.1.2": lambda: F(sum(1 if suit in RED else -2 for _, suit in DECK), 52),
    "gs.6.1.4": lambda: F(sum(35 if slot == 17 else -1 for slot in range(38)), 38),
    "gs.6.1.5": lambda: F(sum(1 if slot < 18 else -1 for slot in range(38)), 38),
    "gs.6.1.13": fortune,
    "gs.6.2.4": three_point,
}


def test_at_least_thirty_answers_each_with_an_oracle():
    assert len(textbook.ANSWERS) >= 30
    assert set(ORACLES) == set(textbook.ANSWERS)


@pytest.mark.parametrize("exercise_id", sorted(textbook.ANSWERS))
def test_authored_answer_equals_its_oracle_and_grades(exercise_id):
    exercise = textbook.get(exercise_id)
    answer = exercise.answer
    assert exercise.graded == "number"
    assert answer.value == ORACLES[exercise_id]()
    if answer.part:
        assert f"({answer.part})" in exercise.text_md
    problem = textbook.problem(exercise)
    assert (problem.template_id, problem.topic_id, problem.seed, problem.steps) == (f"textbook:{exercise_id}", exercise.topics[0], 0, ())
    assert problem.target_s == textbook.TARGET_S
    assert problem.prompt_md.startswith(exercise.text_md)
    assert problem.prompt_md.endswith(f"Answer part ({answer.part}).") if answer.part else problem.prompt_md == exercise.text_md
    whole = answer.value.denominator == 1 and answer.sigfigs is None
    assert problem.answer_kind == ("number" if whole else "fraction")
    value = answer.value
    exact = str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    assert checking.check(problem, exact).correct is True
    assert checking.check(problem, str(value + 1) if whole else f"{value.numerator + 1}/{value.denominator}").correct is False
    if answer.sigfigs:
        rounded = f"{Decimal(value.numerator) / Decimal(value.denominator):#.{answer.sigfigs}g}"
        assert checking.check(problem, rounded).correct is True


def test_self_graded_exercises_have_no_problem():
    exercise = textbook.get("gs.1.2.2")
    assert exercise.answer is None and exercise.graded == "self"
    with pytest.raises(ValueError):
        textbook.problem(exercise)

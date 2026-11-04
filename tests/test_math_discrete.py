"""Discrete generators cross-checked over 240 seeds against brute force computed from each
problem's parameters alone: repeated multiplication, searching for inverses, two gcd
algorithms, iterating recurrences, running the loops, enumerating graphs and walking grids."""
from __future__ import annotations

import math
from collections import Counter
from fractions import Fraction as F
from functools import lru_cache
from itertools import combinations

import pytest
import sympy

from kitty.core.math import checking, problems
from kitty.core.math import topics as graph

SEEDS = range(240)
problems._load()


def reduce_by_search(value, n):
    return next(x for x in range(n) if (value - x) // n * n == value - x)


def power_by_multiplying(a, k, n):
    x = 1
    for _ in range(k):
        x *= a
    return reduce_by_search(x, n)


def gcd_by_subtraction(a, b):
    while a != b:
        a, b = (a - b, b) if a > b else (a, b - a)
    return a


def checked_gcd(a, b):
    assert math.gcd(a, b) == gcd_by_subtraction(a, b)
    return gcd_by_subtraction(a, b)


def iterate(r, s, a0, a1, count):
    seq = [a0, a1]
    while len(seq) < count:
        seq.append(r * seq[-1] + s * seq[-2])
    return seq


def nested_loops(shape, n):
    """The loop nests, written out literally, counting passes."""
    count = 0
    if shape == "square":
        for i in range(n):
            for j in range(n):
                count += 1
    elif shape == "below":
        for i in range(n):
            for j in range(i):
                count += 1
    elif shape == "after":
        for i in range(n):
            for j in range(i + 1, n):
                count += 1
    elif shape == "from":
        for i in range(n):
            for j in range(i, n):
                count += 1
    elif shape == "inclusive":
        for i in range(1, n + 1):
            for j in range(1, i + 1):
                count += 1
    elif shape == "triple":
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    count += 1
    elif shape == "triple-upto":
        for i in range(n):
            for j in range(i + 1):
                for k in range(j + 1):
                    count += 1
    else:
        raise KeyError(shape)
    return count


def big_o_passes(shape, n):
    count = 0
    if shape in ("single", "sequential"):
        for _ in range(2 if shape == "sequential" else 1):
            for i in range(n):
                count += 1
    elif shape in ("nested", "triangular", "triple"):
        count = nested_loops({"nested": "square", "triangular": "below", "triple": "triple"}[shape], n)
    elif shape == "halving":
        i = n
        while i > 1:
            i //= 2
            count += 1
    elif shape in ("doubling-inner", "nested-doubling"):
        for _ in range(n * (n if shape == "nested-doubling" else 1)):
            j = 1
            while j < n:
                j *= 2
                count += 1
    elif shape in ("doubling-outer", "geometric"):
        i = 1
        while i < n:
            count += n if shape == "doubling-outer" else i
            i *= 2
    else:
        raise KeyError(shape)
    return count


def run_prompt_code(problem, n):
    code = problem.prompt_md.split("```python\n", 1)[1].split("\n```", 1)[0]
    scope = {"n": n, "count": 0}
    exec(code, scope)  # noqa: S102  the template's own fixed code
    return scope["count"]


#: Θ-class TeX → (power of n, power of log n), as the learner sees it.
CLASSES = {"$\\Theta(1)$": (0, 0), "$\\Theta(\\log n)$": (0, 1), "$\\Theta(n)$": (1, 0), "$\\Theta(n\\log n)$": (1, 1),
           "$\\Theta(n^2)$": (2, 0), "$\\Theta(n^2\\log n)$": (2, 1), "$\\Theta(n^3)$": (3, 0), "$\\Theta(n^3\\log n)$": (3, 1),
           "$\\Theta(n^4)$": (4, 0)}


def closest_class(cost, e1, e2):
    """The class whose growth from n = 2^e1 to 2^e2 best matches cost's (logs base 2)."""
    observed = math.log(cost(e2)) - math.log(cost(e1))

    def gap(cls):
        k, l = cls
        return abs(observed - (k * (e2 - e1) * math.log(2) + l * math.log(e2 / e1)))
    ranked = sorted(CLASSES.values(), key=gap)
    assert gap(ranked[1]) - gap(ranked[0]) > 0.1, "growth is ambiguous"
    return ranked[0]


def realise(degrees):
    """Havel–Hakimi: an actual simple graph with these degrees, or None if none exists."""
    left, edges = [[d, v] for v, d in enumerate(degrees)], set()
    while left:
        left.sort(reverse=True)
        d, v = left.pop(0)
        if d > len(left):
            return None
        for item in left[:d]:
            item[0] -= 1
            if item[0] < 0:
                return None
            edges.add(frozenset((v, item[1])))
    assert Counter(v for edge in edges for v in edge) == Counter({v: d for v, d in enumerate(degrees) if d})
    return edges


def handshake(p):
    if p["variant"] == "complete":
        return len(list(combinations(range(p["n"]), 2)))
    if p["variant"] == "degrees":
        return len(realise(p["degrees"]))
    fits = [d for d in range(p["n"]) if (edges := realise(p["degrees"] + (d,))) is not None and len(edges) == p["edges"]]
    assert len(fits) == 1, fits
    return fits[0]


def is_tree(n, edges):
    parent = list(range(n))

    def root(v):
        while parent[v] != v:
            v = parent[v]
        return v
    for a, b in edges:
        if root(a) == root(b):
            return False  # a cycle
        parent[root(a)] = root(b)
    return len({root(v) for v in range(n)}) == 1


@lru_cache(maxsize=None)
def labelled_graphs(variant, n, m=None):
    pairs = list(combinations(range(n), 2))
    subsets = (subset for size in range(len(pairs) + 1) for subset in combinations(pairs, size))
    if variant == "graphs":
        return sum(1 for _ in subsets)
    if variant == "edges":
        return sum(1 for subset in subsets if len(subset) == m)
    return sum(1 for subset in subsets if is_tree(n, subset))


def grid_paths(p):
    """Walk the grid cell by cell, remembering whether the checkpoint was visited."""
    m, n, through = p["m"], p["n"], p["through"]
    count = Counter({(0, 0, through in (None, (0, 0))): 1})
    for x in range(m + 1):
        for y in range(n + 1):
            for seen in (False, True):
                for nx, ny in ((x + 1, y), (x, y + 1)):
                    if nx <= m and ny <= n:
                        count[(nx, ny, seen or (nx, ny) == through)] += count[(x, y, seen)]
    return count[(m, n, True)]


ORACLES = {
    "disc.mod-arith/expression": lambda p: reduce_by_search(p["a"] * p["b"] + (-p["c"] if p["minus"] else p["c"]), p["n"]),
    "disc.mod-power/repeated-squaring": lambda p: power_by_multiplying(p["a"], p["k"], p["n"]),
    "disc.gcd/euclid": lambda p: checked_gcd(p["a"], p["b"]),
    "disc.mod-inverse/extended-euclid": lambda p: next(x for x in range(p["n"]) if reduce_by_search(p["a"] * x, p["n"]) == 1),
    "disc.recurrence/iterate": lambda p: iterate(p["r"], p["s"], p["a0"], p["a1"], p["n"] + 1)[p["n"]],
    "disc.loop-count/nested": lambda p: nested_loops(p["shape"], p["n"]),
    "disc.graph-edges/handshake": handshake,
    "disc.graph-counting/labelled": lambda p: labelled_graphs(p["variant"], p["n"], p.get("m")),
    "disc.lattice-paths/grid": grid_paths,
}

#: Choice templates: the Θ-class that matches the cost's growth between two large sizes.
CHOICE_ORACLES = {
    "disc.big-o/cost": lambda p: closest_class(lambda e: sum(c * (1 << e) ** k * e ** l for c, k, l in p["terms"]), 1 << 16, 1 << 17),
    "disc.big-o/loops": lambda p: closest_class(lambda e: big_o_passes(p["shape"], 1 << e), 5, 7),
}

CLOSED_FORM = "disc.recurrence-closed/characteristic"


def test_every_discrete_topic_has_a_template_and_every_template_an_oracle():
    topics = {topic.id for topic in graph.TOPICS if topic.strand == "discrete"}
    assert len(topics) == 11 and all(problems.for_topic(topic) for topic in topics)
    ids = {item.id for item in problems.TEMPLATES.values() if item.topic_id in topics}
    assert ids == set(ORACLES) | set(CHOICE_ORACLES) | {CLOSED_FORM}


@pytest.mark.parametrize("template_id", sorted(ORACLES))
def test_exact_answer_matches_brute_force(template_id):
    oracle, cache = ORACLES[template_id], {}
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        key = repr(sorted(problem.params.items()))
        if key not in cache:
            cache[key] = F(oracle(problem.params))
        assert problem.answer == cache[key], (seed, problem.params, problem.answer, cache[key])


@pytest.mark.parametrize("template_id", ["disc.loop-count/nested", "disc.big-o/loops"])
def test_prompt_code_is_the_loop_nest_that_was_counted(template_id):
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        shape = problem.params["shape"]
        for n in ([problem.params["n"]] if "n" in problem.params else [32, 33]):
            counted = nested_loops(shape, n) if template_id == "disc.loop-count/nested" else big_o_passes(shape, n)
            assert run_prompt_code(problem, n) == counted, (seed, shape, n)


@pytest.mark.parametrize("template_id", sorted(CHOICE_ORACLES))
def test_growth_class_matches_the_cost_at_large_n(template_id):
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        assert problem.answer in problem.choices and len(set(problem.choices)) == 4
        assert all(choice in CLASSES for choice in problem.choices)
        assert CLASSES[problem.answer] == CHOICE_ORACLES[template_id](problem.params), (seed, problem.params, problem.answer)


def closed_form(problem):
    return sympy.sympify(problem.answer, locals=problems.symbols(problem.variables))


def test_closed_form_matches_the_iterated_recurrence():
    n = sympy.Symbol("n")
    for seed in SEEDS:
        problem = problems.generate(CLOSED_FORM, seed)
        p = problem.params
        assert problem.answer_kind == "expression" and problem.variables == ("n",)
        seq = iterate(p["r"], p["s"], p["a0"], p["a1"], 13)
        expr = closed_form(problem)
        assert [expr.subs(n, k) for k in range(13)] == seq, (seed, p, problem.answer)
        for wrong, _ in problem.mistakes:
            wrong_expr = sympy.sympify(wrong, locals={"n": n})
            assert [wrong_expr.subs(n, k) for k in range(13)] != seq, (seed, wrong)


def test_closed_form_checker_accepts_the_key_and_names_the_mistakes():
    for seed in range(0, 240, 24):
        problem = problems.generate(CLOSED_FORM, seed)
        assert checking.check(problem, sympy.latex(closed_form(problem)), timeout=30).correct is True, problem.answer
        for wrong, message in problem.mistakes:
            verdict = checking.check(problem, sympy.latex(sympy.sympify(wrong, locals={"n": sympy.Symbol("n")})), timeout=30)
            assert (verdict.kind, verdict.feedback) == ("mistake", message), (problem.answer, wrong, verdict)


def test_every_discrete_template_has_fadeable_steps():
    for item in problems.TEMPLATES.values():
        if item.topic_id.startswith("disc."):
            for seed in range(0, 240, 10):
                assert any(step.value is not None for step in problems.generate(item.id, seed).steps), (item.id, seed)

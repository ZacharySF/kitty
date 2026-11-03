"""Every generator's answer key, cross-checked over 240 seeds by brute-force enumeration,
exact linear algebra or Monte Carlo, computed from the problem's parameters alone."""
from __future__ import annotations

import random
import statistics
from collections import Counter
from fractions import Fraction as F
from functools import lru_cache
from itertools import combinations, permutations, product
from math import erf, sqrt

import pytest
import sympy

from kitty.core.math import checking, problems
from kitty.core.math import topics as graph

SEEDS = range(240)
problems._load()


def mean_of(values):
    values = list(values)
    return F(sum(values), len(values))


@lru_cache(maxsize=None)
def arrangements(word):
    return len(set(permutations(word)))


@lru_cache(maxsize=None)
def dice(n, m):
    return list(product(range(1, m + 1), repeat=n))


def weighted(outcomes):
    """Sum of probability weights over (weight, event) pairs."""
    return sum(weight for weight, hit in outcomes if hit)


def bernoulli_space(n, p):
    for flips in product((0, 1), repeat=n):
        weight = F(1)
        for flip in flips:
            weight *= p if flip else 1 - p
        yield weight, flips


@lru_cache(maxsize=None)
def monte_carlo_pattern(pattern, runs=20000):
    rng = random.Random(pattern)
    total = 0
    for _ in range(runs):
        text = ""
        while not text.endswith(pattern):
            text += rng.choice("HT")
            total += 1
    return total / runs


def ruin_by_linear_system(start, target, p):
    # h_0 = 0, h_N = 1, h_i = p h_{i+1} + (1-p) h_{i-1}: solve by exact iteration on a matrix.
    m = sympy.Matrix.zeros(target + 1)
    rhs = sympy.Matrix.zeros(target + 1, 1)
    m[0, 0] = 1
    m[target, target] = 1
    rhs[target] = 1
    for i in range(1, target):
        m[i, i] = 1
        m[i, i + 1] = -sympy.Rational(p.numerator, p.denominator)
        m[i, i - 1] = -(1 - sympy.Rational(p.numerator, p.denominator))
    solution = m.LUsolve(rhs)
    return F(str(solution[start]))


def best_reroll_value(m, rerolls):
    """Brute force over every threshold strategy (keep a roll >= t at each stage)."""
    def value(thresholds):
        if not thresholds:
            return mean_of(range(1, m + 1))
        rest = value(thresholds[1:])
        return mean_of(x if x >= thresholds[0] else rest for x in range(1, m + 1))
    return max(value(ts) for ts in product(range(1, m + 2), repeat=rerolls))


def sample_means_sd(sigma, n, reps=3000, seed=0):
    rng = random.Random(seed)
    means = [statistics.fmean(rng.choice((-sigma, sigma)) for _ in range(n)) for _ in range(reps)]
    return statistics.pstdev(means)


def var_uniform(m):
    return statistics.pvariance([F(x) for x in range(m + 1)])


ORACLES = {
    "arith.add-sub/three-digit": lambda p: p["a"] + p["b"] if p["op"] == "+" else p["a"] - p["b"],
    "arith.times-tables/to-20": lambda p: sum(p["a"] for _ in range(p["b"])),
    "arith.mul-2x1/split": lambda p: sum(p["a"] for _ in range(p["b"])),
    "arith.mul-2x2/split": lambda p: sum(p["a"] for _ in range(p["b"])),
    "arith.squares/near-base": lambda p: sum(p["n"] for _ in range(p["n"])),
    "arith.division/exact": lambda p: next(q for q in range(p["a"] + 1) if q * p["b"] == p["a"]),
    "arith.powers-of-two/power": lambda p: len(list(product((0, 1), repeat=p["k"]))),  # subsets of k items
    "arith.fractions/add-sub": lambda p: F(p["a"], p["b"]) + (1 if p["op"] == "+" else -1) * F(p["c"], p["d"]),
    "arith.frac-decimal/terminating": lambda p: F(p["n"]) / p["d"],
    "arith.percentages/of": lambda p: F(p["p"]) * p["x"] / 100,
    "arith.percentages/change": lambda p: (F(p["new"]) - p["old"]) * 100 / p["old"],
    "arith.estimation/divide-2sf": lambda p: F(p["a"], p["b"]),
    "arith.odds/to-probability": lambda p: mean_of(int((i < p["b"]) if p["against"] else (i < p["a"])) for i in range(p["a"] + p["b"])),
    "count.product-rule/outfits": lambda p: len(list(product(range(p["s"]), range(p["p"]), range(p["h"])))),
    "count.permutations/podium": lambda p: len(list(permutations(range(p["n"]), p["k"]))),
    "count.multisets/word": lambda p: arrangements(p["word"]),
    "count.combinations/committee": lambda p: len(list(combinations(range(p["n"]), p["k"]))),
    "count.complement/at-least-one": lambda p: sum(1 for s in product(range(p["m"]), repeat=p["k"]) if 0 in s),
    "count.inclusion-exclusion/divisible": lambda p: sum(1 for i in range(1, p["n"] + 1) if i % p["a"] == 0 or i % p["b"] == 0),
    "count.stars-bars/solutions": lambda p: sum(1 for xs in product(range(p["n"] + 1), repeat=p["k"])
                                               if sum(xs) == p["n"] and (not p["positive"] or min(xs) >= 1)),
    "count.binomial-theorem/coefficient": lambda p: int(sympy.Poly((p["a"] * sympy.Symbol("x") + p["b"]) ** p["n"]).coeff_monomial(sympy.Symbol("x") ** p["k"])),
    "prob.basic/dice-sum": lambda p: mean_of(int(sum(r) == p["s"]) for r in dice(2, p["m"])),
    "prob.complement/at-least-one-six": lambda p: mean_of(int(6 in r) for r in dice(p["n"], 6)),
    # A grid of equally likely cells: A is the first rows, B the first columns.
    "prob.independence/union": lambda p: mean_of(int(i < p["a"].numerator or j < p["b"].numerator)
                                                for i in range(p["a"].denominator) for j in range(p["b"].denominator)),
    "prob.hypergeometric/urn": lambda p: mean_of(int(sum(1 for ball in hand if ball < p["red"]) == p["j"])
                                                 for hand in combinations(range(p["red"] + p["blue"]), p["k"])),
    "prob.conditional/dice-given": lambda p: (lambda given: mean_of(int(len(set(r)) == 1) for r in given))([r for r in dice(p["n"], 6) if sum(r) >= p["s"]]),
    "prob.total-probability/urns": lambda p: weighted(
        [(p["q"] / (p["r1"] + p["b1"]), ball < p["r1"]) for ball in range(p["r1"] + p["b1"])]
        + [((1 - p["q"]) / (p["r2"] + p["b2"]), ball < p["r2"]) for ball in range(p["r2"] + p["b2"])]),
    "prob.bayes/test": lambda p: (lambda sick_pos, well_pos: sick_pos / (sick_pos + well_pos))(
        p["prevalence"] * 10 ** 6 * p["sensitivity"], (1 - p["prevalence"]) * 10 ** 6 * p["false_positive"]),
    "prob.binomial/exactly-k": lambda p: weighted((w, sum(flips) == p["k"]) for w, flips in bernoulli_space(p["n"], p["p"])),
    "prob.geometric/first-success": lambda p: weighted((w, sum(flips) == 1 and flips[-1] == 1)
                                                       for w, flips in bernoulli_space(p["k"], F(1, p["m"]))),
    "prob.symmetry/higher-die": lambda p: mean_of(int(a > b) for a, b in dice(2, p["m"])),
    "prob.order-stats/max-of-dice": lambda p: mean_of(int(max(r) == p["k"]) for r in dice(p["n"], p["m"])),
    "prob.gamblers-ruin/reach-target": lambda p: ruin_by_linear_system(p["start"], p["target"], p["p"]),
    "ev.expected-value/threshold-game": lambda p: mean_of(x if x >= p["t"] else -p["cost"] for x in range(1, p["m"] + 1)),
    "ev.linearity/hh-pairs": lambda p: mean_of(sum(1 for a, b in zip(s, s[1:]) if a and b) for s in product((0, 1), repeat=p["n"])),
    "ev.linearity/distinct-faces": lambda p: mean_of(len(set(r)) for r in dice(p["k"], 6)),
    "ev.max-dice/two": lambda p: mean_of(max(r) for r in dice(2, p["m"])),
    "ev.optimal-stopping/rerolls": lambda p: best_reroll_value(p["m"], p["rerolls"]),
    "ev.variance/distribution": lambda p: statistics.pvariance([F(v) for v, c in zip(p["values"], p["counts"]) for _ in range(c)]),
    "ev.variance-sum/linear-combination": lambda p: statistics.pvariance(
        [F(p["a"] * x + p["b"] * y + p["c"]) for x in range(p["m1"] + 1) for y in range(p["m2"] + 1)]),
    "ev.waiting-time/until-face": lambda p: 1 / mean_of(int(x <= p["k"]) for x in range(1, p["m"] + 1)),
    # Equally likely outcomes: the wins must pay back every losing outcome's stake.
    "ev.fair-price/break-even": lambda p: (lambda outcomes: F(p["loss"] * outcomes[False], outcomes[True]))(
        Counter(i < p["p"].numerator for i in range(p["p"].denominator))),
    "stats.mean-median/mean": lambda p: statistics.mean(F(x) for x in p["data"]),
    "stats.mean-median/median": lambda p: F(statistics.median(p["data"])),
    "stats.spread/variance": lambda p: (statistics.variance if p["sample"] else statistics.pvariance)([F(x) for x in p["data"]]),
    "stats.z-score/value": lambda p: (F(p["x"]) - p["mu"]) / p["sigma"],
    "stats.standard-error/mean": lambda p: F(p["sigma"]) / F(int(sqrt(p["n"]))),
    "stats.confidence/upper-95": lambda p: F(p["xbar"]) + (1 if p["upper"] else -1) * F(statistics.NormalDist().inv_cdf(0.975)) * p["sigma"] / int(sqrt(p["n"])),
}

#: Symbolic answers: checked against enumeration at many concrete parameter values.
SYMBOLIC = {
    "count.combinations/polynomial": lambda p, n: len(list(permutations(range(n), p["k"]) if p["ordered"] else combinations(range(n), p["k"]))),
    "prob.complement/symbolic": lambda p, q: weighted((w, any(flips)) for w, flips in bernoulli_space(p["n"], q)),
    "ev.expected-value/die-n": lambda p, n: mean_of(p["a"] * x + p["b"] for x in range(1, n + 1)),
    "ev.variance-sum/symbolic": lambda p, m: statistics.pvariance([F(p["a"] * x - p["b"] * y) for x in range(m[0] + 1) for y in range(m[1] + 1)]),
}


def test_every_template_has_a_cross_check_and_every_topic_a_generator():
    assert set(ORACLES) | set(SYMBOLIC) | {"stats.normal-rule/share", "ev.waiting-time/coin-pattern"} == set(problems.TEMPLATES)
    assert all(problems.for_topic(topic.id) for topic in graph.TOPICS)
    assert 40 <= len(graph.TOPICS) <= 50


@pytest.mark.parametrize("template_id", sorted(ORACLES))
def test_exact_answer_matches_brute_force(template_id):
    oracle, cache = ORACLES[template_id], {}
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        key = repr(sorted(problem.params.items()))
        if key not in cache:
            cache[key] = F(oracle(problem.params))
        expected = cache[key]
        if problem.tolerance is not None:
            assert abs(problem.answer - expected) <= F(1, 1000), (seed, problem.params)
        else:
            assert problem.answer == expected, (seed, problem.params, problem.answer, expected)


@pytest.mark.parametrize("template_id", sorted(SYMBOLIC))
def test_symbolic_answer_matches_enumeration_at_concrete_values(template_id):
    oracle = SYMBOLIC[template_id]
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        expr = sympy.sympify(problem.answer, locals=problems.symbols(problem.variables))
        if template_id == "prob.complement/symbolic":
            points = [({"p": q}, q) for q in (F(1, 3), F(1, 2), F(2, 7))]
        elif template_id == "ev.variance-sum/symbolic":
            points = [({"u": var_uniform(a), "v": var_uniform(b)}, (a, b)) for a, b in ((1, 2), (3, 1), (4, 4))]
        else:
            points = [({"n": n}, n) for n in range(3, 9)]
        for values, argument in points:
            got = expr.subs({sympy.Symbol(k): sympy.Rational(v.numerator, v.denominator) if isinstance(v, F) else v for k, v in values.items()})
            assert F(str(got)) == F(oracle(problem.params, argument)), (seed, problem.params, values)


def test_coin_pattern_wait_matches_monte_carlo():
    for seed in SEEDS:
        problem = problems.generate("ev.waiting-time/coin-pattern", seed)
        simulated = monte_carlo_pattern(problem.params["pattern"])
        assert abs(float(problem.answer) - simulated) < 0.04 * float(problem.answer), problem.params


def test_normal_rule_choice_matches_the_normal_distribution():
    for seed in SEEDS:
        problem = problems.generate("stats.normal-rule/share", seed)
        k = problem.params["k"]
        share = (1 - erf(k / sqrt(2))) / 2 if problem.params["outside"] else erf(k / sqrt(2))
        nearest = min(problem.choices, key=lambda choice: abs(float(choice.rstrip("%")) / 100 - share))
        assert problem.answer == nearest and len(set(problem.choices)) == 4 and problem.answer in problem.choices, problem.params


def test_standard_error_matches_monte_carlo():
    for seed in range(0, 240, 12):
        problem = problems.generate("stats.standard-error/mean", seed)
        sd = sample_means_sd(problem.params["sigma"], problem.params["n"], seed=seed)
        assert abs(sd - float(problem.answer)) < 0.06 * float(problem.answer), problem.params


@pytest.mark.parametrize("template_id", sorted(problems.TEMPLATES))
def test_templates_are_deterministic_and_self_consistent(template_id):
    kinds = Counter()
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        assert problem == problems.generate(template_id, seed)
        kinds[problem.answer_kind] += 1
        assert problem.prompt_md and problem.steps
        assert problem.answer not in [wrong for wrong, _ in problem.mistakes]
        assert all(message for _, message in problem.mistakes)
        [step.md() for step in problem.steps]
        if problem.answer_kind != "expression":
            # The exact key, in its canonical form, passes its own checker.
            exact = problem.answer if problem.answer_kind == "choice" else (
                f"{problem.answer.numerator}/{problem.answer.denominator}")
            assert checking.check(problem, exact).correct is True
            for wrong, message in problem.mistakes:
                verdict = checking.check(problem, wrong if isinstance(wrong, str) else f"{wrong.numerator}/{wrong.denominator}")
                assert verdict.kind == "mistake" and verdict.feedback == message
        for index, step in enumerate(problem.steps):
            if step.value is not None:
                faded = problem.step_problem(index)
                assert checking.check(faded, f"{step.value.numerator}/{step.value.denominator}").correct is True
                assert "[[blank]]" in step.md(blank=True)
    assert len(kinds) == 1, f"{template_id} answer kind leaks the answer: {kinds}"


def test_most_templates_have_fadeable_steps_for_guided_lessons():
    for topic in graph.TOPICS:
        assert any(any(step.value is not None for step in problems.generate(item.id, 0).steps)
                   for item in problems.for_topic(topic.id)) or topic.id == "stats.normal-rule", topic.id

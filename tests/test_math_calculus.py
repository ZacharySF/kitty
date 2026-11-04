"""Calculus generators cross-checked over 240 seeds with SymPy's own calculus (``limit``,
``diff``, ``integrate``, ``series``) on functions rebuilt from the problem's parameters
alone, plus end-to-end checks that the answer checker accepts each expression key."""
from __future__ import annotations

from fractions import Fraction as F

import pytest
import sympy

from kitty.core.math import checking, problems
from kitty.core.math import topics as graph

SEEDS = range(240)
x = sympy.Symbol("x")
problems._load()


def poly(coeffs):
    return sum(sympy.Integer(c) * x ** k for k, c in enumerate(coeffs))


def function(spec):
    """A ``(name, argument)`` parameter as a SymPy function of x."""
    name, arg = spec
    if name == "poly":
        return poly(arg)
    return sympy.log(x) if name == "ln" else getattr(sympy, name)(arg * x)


def maclaurin_base(name):
    return {"exp": sympy.exp(x), "sin": sympy.sin(x), "cos": sympy.cos(x), "ln": sympy.log(1 + x), "geometric": 1 / (1 - x)}[name]


def limit_target(p):
    a = p["a"]
    top = sympy.expand((x - a) * (x + p["b"])) if p["form"] == "quadratic" else x ** 3 - a ** 3
    return sympy.limit(top / (x - a), x, a)


def chain_target(p):
    inner = p["a"] * x + p["b"]
    outer = {"power": lambda u: u ** p["n"], "exp": sympy.exp, "sin": sympy.sin, "cos": sympy.cos}[p["outer"]]
    return sympy.diff(p["k"] * outer(inner), x)


def rational(value):
    value = sympy.simplify(sympy.sympify(value))
    assert value.is_Rational, value
    return F(int(value.p), int(value.q))


#: Numeric answers: the exact value from SymPy's calculus.
NUMBERS = {
    "calc.limits/factor": limit_target,
    "calc.limits-infinity/rational": lambda p: sympy.limit(poly(p["top"]) / poly(p["bottom"]), x, sympy.oo),
    "calc.slopes/at-point": lambda p: sympy.diff(poly(p["f"]), x).subs(x, p["a"]),
    "calc.definite/polynomial": lambda p: sympy.integrate(poly(p["f"]), (x, p["a"], p["b"])),
    "calc.taylor/approximate": lambda p: sympy.series(maclaurin_base(p["fn"]), x, 0, p["n"] + 1).removeO().subs(
        x, sympy.Rational(p["h"].numerator, p["h"].denominator)),
    "calc.taylor/coefficient": lambda p: sympy.series(maclaurin_base(p["fn"]).subs(x, p["a"] * x), x, 0, p["k"] + 1).removeO().coeff(x, p["k"]),
}

#: Derivative answers: SymPy's derivative of the rebuilt function.
DERIVATIVES = {
    "calc.power-rule/polynomial": lambda p: sympy.diff(poly(p["f"]), x),
    "calc.product-rule/times-function": lambda p: sympy.diff(function(p["u"]) * function(p["v"]), x),
    "calc.quotient-rule/over-polynomial": lambda p: sympy.diff(function(p["top"]) / poly(p["bottom"]), x),
    "calc.chain-rule/linear-inner": chain_target,
    "calc.standard-derivatives/combination": lambda p: sympy.diff(
        p["exp"] * sympy.exp(x) + p["ln"] * sympy.log(x) + p["sin"] * sympy.sin(x) + p["cos"] * sympy.cos(x), x),
}

#: Antiderivative answers: the integrand they must differentiate back to.
INTEGRANDS = {
    "calc.antiderivatives/polynomial": lambda p: poly(p["f"]),
    "calc.integrals-exp-trig/linear-argument": lambda p: p["k"] * {"exp": sympy.exp, "sin": sympy.sin, "cos": sympy.cos}[p["fn"]](p["a"] * x),
}

EXPRESSIONS = sorted(DERIVATIVES) + sorted(INTEGRANDS)


def answer_of(problem):
    return sympy.sympify(problem.answer, locals=problems.symbols(problem.variables))


def test_every_calculus_topic_has_a_template_and_every_template_an_oracle():
    calculus = {item.id for item in problems.TEMPLATES.values() if graph.BY_ID[item.topic_id].strand == "calculus"}
    assert set(NUMBERS) | set(DERIVATIVES) | set(INTEGRANDS) == calculus
    assert all(problems.for_topic(topic.id) for topic in graph.TOPICS if topic.strand == "calculus")


@pytest.mark.parametrize("template_id", sorted(NUMBERS))
def test_numeric_answers_match_sympy(template_id):
    oracle, cache = NUMBERS[template_id], {}
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        key = repr(sorted(problem.params.items()))
        if key not in cache:
            cache[key] = rational(oracle(problem.params))
        assert problem.answer == cache[key], (seed, problem.params, problem.answer, cache[key])
        assert problem.answer_kind != "expression" and not problem.up_to_constant


@pytest.mark.parametrize("template_id", sorted(DERIVATIVES))
def test_derivatives_match_sympy_diff(template_id):
    oracle = DERIVATIVES[template_id]
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        assert problem.answer_kind == "expression" and problem.variables == ("x",) and not problem.up_to_constant
        assert sympy.simplify(answer_of(problem) - oracle(problem.params)) == 0, (seed, problem.params, problem.answer)


@pytest.mark.parametrize("template_id", sorted(INTEGRANDS))
def test_antiderivatives_differentiate_back_to_the_integrand(template_id):
    oracle = INTEGRANDS[template_id]
    for seed in SEEDS:
        problem = problems.generate(template_id, seed)
        assert problem.answer_kind == "expression" and problem.variables == ("x",) and problem.up_to_constant
        integrand = oracle(problem.params)
        assert sympy.simplify(sympy.diff(answer_of(problem), x) - integrand) == 0, (seed, problem.params, problem.answer)
        assert sympy.simplify(sympy.integrate(integrand, x) - answer_of(problem)).is_constant(x)


@pytest.mark.parametrize("template_id", EXPRESSIONS)
def test_the_checker_accepts_expression_keys_and_flags_a_known_mistake(template_id):
    for seed in range(0, 240, 40):
        problem = problems.generate(template_id, seed)
        typed = sympy.latex(answer_of(problem))
        assert checking.check(problem, typed) == checking.EQUAL, (seed, typed)
        assert checking.check(problem, problem.canonical) == checking.EQUAL, (seed, problem.canonical)
        wrong, message = problem.mistakes[0]
        verdict = checking.check(problem, sympy.latex(sympy.sympify(wrong, locals=problems.symbols(problem.variables))))
        assert verdict == checking.Verdict(False, "mistake", message), (seed, wrong, verdict)


@pytest.mark.parametrize("template_id", sorted(INTEGRANDS))
def test_antiderivatives_accept_any_constant(template_id):
    problem = problems.generate(template_id, 7)
    typed = sympy.latex(answer_of(problem))
    assert problem.canonical == f"{typed} + C"
    for suffix in (" + C", " - 5", " + K"):
        assert checking.check(problem, typed + suffix) == checking.EQUAL, suffix

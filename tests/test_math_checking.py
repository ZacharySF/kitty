"""Answer checking: exact forms, significant figures, tolerance, choices, and SymPy
equivalence including ambiguous readings and a timeout that kills the worker."""
from __future__ import annotations

from fractions import Fraction as F

import pytest

from kitty.core.math import cas, checking
from kitty.core.math.problems import Problem, Step


def numeric(answer, kind="fraction", *, sigfigs=None, tolerance=None, mistakes=()):
    return Problem("t/x", "t", 0, {}, "prompt", F(answer), kind, (Step("s"),), tuple(mistakes),
                   tolerance=tolerance, sigfigs=sigfigs)


def expression(answer, variables, mistakes=()):
    return Problem("t/x", "t", 0, {}, "prompt", answer, "expression", (Step("s"),), tuple(mistakes), variables=tuple(variables))


@pytest.mark.parametrize("typed", ["3/7", "6/14", "\\frac{3}{7}", "\\dfrac{6}{14}", " 3 / 7 ", "(3/7)"])
def test_equivalent_exact_fractions_are_equal(typed):
    assert checking.check(numeric(F(3, 7)), typed) == checking.EQUAL


@pytest.mark.parametrize("typed", ["0.25", ".25", "25%", "1/4", "0,25", 0.25, "2.5e-1", "\\frac14"])
def test_every_exact_form_of_a_quarter(typed):
    assert checking.check(numeric(F(1, 4), "number"), typed).correct is True


def test_significant_figures_accept_rounded_decimals_and_ask_for_more_figures():
    third = numeric(F(1, 3), sigfigs=3)
    assert checking.check(third, "0.333").correct is True
    assert checking.check(third, "0.3333333").correct is True
    assert checking.check(third, "0.334").kind == "different"
    rough = checking.check(third, "0.33")
    assert rough.correct is None and rough.kind == "undecided" and "3 significant figures" in rough.feedback
    assert checking.check(third, "0.4").kind == "different"
    assert checking.check(numeric(F(4367, 59), sigfigs=2), "74").correct is True
    assert checking.check(numeric(F(4367, 59), sigfigs=2), "7.4").correct is False


def test_exact_only_answers_ask_for_the_exact_value_instead_of_marking_close_decimals_wrong():
    verdict = checking.check(numeric(F(11, 30)), "0.3667")
    assert verdict.correct is None and "exact" in verdict.feedback
    assert checking.check(numeric(F(11, 30)), "0.5").correct is False
    assert checking.check(numeric(F(35), "number"), "35.0").correct is True
    assert checking.check(numeric(F(35), "number"), "36").correct is False


def test_tolerance_and_thousands_separators():
    upper = numeric(F(8296, 100), "number", tolerance=F(6, 1000))
    assert checking.check(upper, "82.96").correct is True
    assert checking.check(upper, "82.97").correct is False
    assert checking.check(numeric(F(4096), "number"), "4,096").correct is True


def test_mistake_detectors_give_their_feedback():
    problem = numeric(F(11, 30), mistakes=[(F(2, 11), "Adding numerators and denominators doesn't add fractions.")])
    verdict = checking.check(problem, "2/11")
    assert verdict == checking.Verdict(False, "mistake", "Adding numerators and denominators doesn't add fractions.")


@pytest.mark.parametrize("typed", ["", "abc", "1/0", "--3", True, None, [1], "x" * 600, "nan", "inf"])
def test_unreadable_numbers_are_undecided_not_wrong(typed):
    verdict = checking.check(numeric(F(1, 2)), typed)
    assert verdict.correct is None and verdict.kind == "undecided"


def test_multiple_choice_by_text_or_index():
    problem = Problem("t/x", "t", 0, {}, "p", "95%", "choice", (Step("s"),), (("2.5%", "That's one tail."),),
                      choices=("68%", "95%", "2.5%", "50%"))
    assert checking.check(problem, "95%").correct is True
    assert checking.check(problem, 1).correct is True
    assert checking.check(problem, "2.5%") == checking.Verdict(False, "mistake", "That's one tail.")
    assert checking.check(problem, "68%").kind == "different"
    assert checking.check(problem, "12%").kind == "undecided"


@pytest.mark.parametrize("typed", ["np(1-p)", "n p \\left(1-p\\right)", "n\\cdot p-n\\cdot p^{2}", "np-np^2", "-n p (p-1)"])
def test_equivalent_expressions_are_equal(typed):
    assert checking.check(expression("n*p*(1-p)", "np"), typed).kind == "equal"


@pytest.mark.parametrize("typed", ["\\frac{n(n-1)}{2}", "\\binom{n}{2}", "\\frac{1}{2}n^{2}-\\frac{1}{2}n", "\\frac{n^2-n}{2}"])
def test_equivalent_counting_formulas(typed):
    assert checking.check(expression("n*(n-1)/2", "n"), typed).correct is True


def test_non_equivalent_expressions_are_wrong_and_known_mistakes_explained():
    problem = expression("9*u + v", "uv", [("9*u - v", "Variances add even when the variables are subtracted.")])
    assert checking.check(problem, "9u+2v") == checking.Verdict(False, "different", None)
    assert checking.check(problem, "9u-v") == checking.Verdict(False, "mistake", "Variances add even when the variables are subtracted.")
    foreign = checking.check(problem, "9u+v+y")
    assert foreign.correct is False and "y" in foreign.feedback


def test_ambiguous_readings_say_so_instead_of_marking_wrong():
    # lark reads n^n^2 as n^(n^2), ANTLR as (n^n)^2: the readings disagree.
    verdict = checking.check(expression("n**(n**2)", "n"), "n^n^2")
    assert verdict.correct is None and verdict.kind == "undecided" and "more than one way" in verdict.feedback


@pytest.mark.parametrize("typed", ["\\frac{(", "x=2", "", "1+"])
def test_unreadable_or_equation_answers_are_undecided(typed):
    verdict = checking.check(expression("1-(1-p)**3", "p"), typed)
    assert verdict.correct is None and verdict.kind == "undecided" and verdict.feedback


def test_timeout_kills_the_worker_and_the_next_check_recovers():
    problem = expression("(x + 1)**40", "x")
    assert checking.check(problem, "(x+1)^{40}").correct is True  # warm worker
    first = cas._worker[0]
    slow = checking.check(problem, "(x+1)^{40}", timeout=0.0001)
    assert slow == checking.Verdict(None, "undecided", "Checking took too long; try a simpler equivalent form.")
    assert cas._worker is None and not first.is_alive()
    assert checking.check(problem, "(1+x)^{40}").correct is True
    assert cas._worker is not None and cas._worker[0] is not first

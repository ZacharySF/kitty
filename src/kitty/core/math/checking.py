"""Answer checking: exact numbers and fractions, stated tolerance or significant figures
for decimals, multiple choice, and SymPy equivalence for expressions (:mod:`.cas`),
up to an added constant for antiderivatives.

A verdict is ``equal``, ``mistake`` (a template's known wrong answer, with its feedback),
``different`` or ``undecided``. Undecided never marks an answer wrong: it covers input
that can't be read, LaTeX that reads two ways, too few significant figures and checks
that time out, and the learner simply tries again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from kitty.core.math.problems import Problem

MAX_ANSWER = 500
_DECIMAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_FRACTION = re.compile(r"([+-]?)(\d+)/(\d+)")
_TEX_FRACTION = re.compile(r"([+-]?)\\[dt]?frac\{?([+-]?\d+)\}?\{?(\d+)\}?")
_THOUSANDS = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")


@dataclass(frozen=True)
class Verdict:
    correct: bool | None
    kind: str
    feedback: str | None = None


EQUAL = Verdict(True, "equal")
DIFFERENT = Verdict(False, "different")


@dataclass(frozen=True)
class Number:
    value: Fraction
    #: Significant figures as typed; None for an exact fraction.
    sigfigs: int | None


def parse_number(answer) -> Number | None:
    """Integers, decimals, a/b, TeX \\frac, percentages and JSON numbers; None if unreadable."""
    if isinstance(answer, bool):
        return None
    if isinstance(answer, int):
        return Number(Fraction(answer), _digits(str(abs(answer))))
    if isinstance(answer, float):
        answer = repr(answer)
    if not isinstance(answer, str) or len(answer) > MAX_ANSWER:
        return None
    text = answer.strip().strip("$").strip()
    for old, new in (("\\left", ""), ("\\right", ""), ("−", "-"), ("–", "-"), ("{,}", "."), ("\\%", "%"),
                     ("\\,", ""), ("\\!", ""), ("\\ ", ""), ("\u2009", ""), ("\u202f", ""), (" ", ""), ("_", "")):
        text = text.replace(old, new)
    percent = text.endswith("%")
    text = text.removesuffix("%")
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    if _THOUSANDS.fullmatch(text):
        text = text.replace(",", "")
    elif text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    number = None
    if match := (_FRACTION.fullmatch(text) or _TEX_FRACTION.fullmatch(text)):
        sign, top, bottom = match.groups()
        if int(bottom) == 0:
            return None
        value = Fraction(int(top), int(bottom)) * (-1 if sign == "-" else 1)
        number = Number(value, None)
    elif _DECIMAL.fullmatch(text):
        try:
            decimal = Decimal(text)
        except InvalidOperation:
            return None
        if not decimal.is_finite():
            return None
        mantissa = text.lstrip("+-").split("e")[0].split("E")[0]
        number = Number(Fraction(decimal), _digits(mantissa))
    if number is None:
        return None
    return Number(number.value / 100, number.sigfigs) if percent else number


def _digits(mantissa: str) -> int:
    """Significant digits typed: leading zeros don't count; every other digit does."""
    digits = mantissa.replace(".", "").lstrip("0")
    return max(len(digits), 1)


def _unit(target: Fraction, sigfigs: int) -> Fraction:
    """Half a unit in the last place of ``target`` at ``sigfigs`` significant figures."""
    if target == 0:
        return Fraction(0)
    exponent = len(str(abs(target.numerator) // abs(target.denominator))) - 1 if abs(target) >= 1 else \
        -next(k for k in range(1, 400) if abs(target) * 10 ** k >= 1)
    return Fraction(10) ** (exponent - sigfigs + 1) / 2


def _close(number: Number, target: Fraction, problem: Problem) -> str:
    """'exact', 'close', 'rough' (right but too few figures) or 'far'."""
    if number.value == target:
        return "exact"
    if problem.tolerance is not None and abs(number.value - target) <= problem.tolerance:
        return "close"
    if number.sigfigs is None or problem.sigfigs is None:
        return "far"
    if number.sigfigs >= problem.sigfigs:
        return "close" if abs(number.value - target) <= _unit(target, problem.sigfigs) else "far"
    return "rough" if abs(number.value - target) <= _unit(target, number.sigfigs) else "far"


def _check_number(problem: Problem, answer) -> Verdict:
    number = parse_number(answer)
    if number is None:
        return Verdict(None, "undecided", "Couldn't read that as a number; try 42, 0.25, 3/7 or 25%.")
    closeness = _close(number, problem.answer, problem)
    if closeness in ("exact", "close"):
        return EQUAL
    for wrong, message in problem.mistakes:
        if not isinstance(wrong, str) and _close(number, wrong, problem) in ("exact", "close"):
            return Verdict(False, "mistake", message)
    if closeness == "rough":
        return Verdict(None, "undecided", f"Give at least {problem.sigfigs} significant figures.")
    if (number.sigfigs is not None and problem.tolerance is None and problem.sigfigs is None
            and problem.answer.denominator != 1
            and abs(number.value - problem.answer) <= _unit(problem.answer, max(number.sigfigs, 1))):
        return Verdict(None, "undecided", "Give the exact value, for example as a fraction.")
    return DIFFERENT


def _check_choice(problem: Problem, answer) -> Verdict:
    choices = problem.choices or ()
    if type(answer) is int and 0 <= answer < len(choices):
        answer = choices[answer]
    if not isinstance(answer, str) or answer.strip() not in choices:
        return Verdict(None, "undecided", "Pick one of the choices.")
    answer = answer.strip()
    if answer == problem.answer:
        return EQUAL
    for wrong, message in problem.mistakes:
        if wrong == answer:
            return Verdict(False, "mistake", message)
    return DIFFERENT


def _check_expression(problem: Problem, answer, timeout: float | None) -> Verdict:
    from kitty.core.math import cas

    if not isinstance(answer, str) or not answer.strip():
        return Verdict(None, "undecided", "Enter an expression.")
    if len(answer) > MAX_ANSWER:
        return Verdict(None, "undecided", f"Keep the expression under {MAX_ANSWER} characters.")
    kind, feedback = cas.equivalent(answer, str(problem.answer), problem.variables,
                                    [(wrong, message) for wrong, message in problem.mistakes if isinstance(wrong, str)],
                                    timeout=timeout, up_to_constant=problem.up_to_constant)
    return Verdict({"equal": True, "mistake": False, "different": False}.get(kind), kind, feedback)


def check(problem: Problem, answer, *, timeout: float | None = None) -> Verdict:
    if problem.answer_kind == "choice":
        return _check_choice(problem, answer)
    if problem.answer_kind == "expression":
        return _check_expression(problem, answer, timeout)
    return _check_number(problem, answer)

"""Parametrised problem templates: seeded parameters, an exact answer, worked steps and
STACK-style common-mistake detectors.

A template is a function ``(random.Random) -> Spec`` registered with :func:`template`.
``generate(template_id, seed)`` is a pure function of its arguments, so storing
``(template_id, seed)`` reproduces any problem. Answers are exact: ``Fraction`` for
numbers and fractions, a SymPy expression string for algebra, a string for choices.
Every template has a cross-check test (enumeration or Monte Carlo) over many seeds.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable

KINDS = ("number", "fraction", "choice", "expression")
Value = Fraction | str


@dataclass(frozen=True)
class Step:
    """One worked-solution line, ``**why**: $tex = value$``. A step with a ``value`` is
    fadeable: a guided lesson blanks it and checks the learner's value."""
    why: str
    tex: str = ""
    value: Fraction | None = None

    def md(self, blank: bool = False) -> str:
        if self.value is None:
            return f"**{self.why}:** ${self.tex}$" if self.tex else f"**{self.why}**"
        result = "[[blank]]" if blank else f"${num(self.value)}$"
        if not self.tex:
            return f"**{self.why}:** {result}"
        if blank:
            return f"**{self.why}:** ${self.tex} =$ {result}"
        return f"**{self.why}:** ${self.tex} = {num(self.value)}$"


@dataclass(frozen=True)
class Spec:
    """What a template returns for one seed."""
    params: dict
    prompt: str
    answer: Fraction | int | str
    steps: tuple[Step, ...] | list[Step]
    kind: str | None = None
    #: (wrong answer, feedback) pairs; a wrong answer equal to the right one is dropped.
    mistakes: tuple | list = ()
    choices: tuple[str, ...] | list[str] | None = None
    tolerance: Fraction | None = None
    sigfigs: int | None = None
    variables: tuple[str, ...] | list[str] = ()
    #: An antiderivative: any added constant (``+ C`` or a number) is accepted.
    up_to_constant: bool = False


@dataclass(frozen=True)
class Problem:
    template_id: str
    topic_id: str
    seed: int
    params: dict
    prompt_md: str
    answer: Value
    answer_kind: str
    steps: tuple[Step, ...]
    mistakes: tuple[tuple[Value, str], ...] = ()
    choices: tuple[str, ...] | None = None
    tolerance: Fraction | None = None
    sigfigs: int | None = None
    variables: tuple[str, ...] = ()
    #: Seconds a fluent learner needs; reviews grade time against it.
    target_s: int = 30
    #: An antiderivative: any added constant (``+ C`` or a number) is accepted.
    up_to_constant: bool = False

    @property
    def canonical(self) -> str:
        """The right answer as TeX (numbers also get a decimal echo)."""
        if self.answer_kind == "choice":
            return str(self.answer)
        if self.answer_kind == "expression":
            import sympy

            latex = sympy.latex(sympy.sympify(self.answer, locals=symbols(self.variables)))
            return f"{latex} + C" if self.up_to_constant else latex
        value = self.answer
        exact = decimal_text(value)
        if value.denominator == 1 or (exact is not None and self.answer_kind == "number"):
            return exact or str(value.numerator)
        if exact is not None:
            return f"{tex(value)} = {exact}"
        return f"{tex(value)} \\approx {approx(value)}"

    def step_problem(self, index: int) -> "Problem":
        """The guided-lesson problem for faded step ``index``: answer its value."""
        step = self.steps[index]
        if step.value is None:
            raise ValueError("That step can't be faded")
        value = step.value
        whole = value.denominator == 1
        return Problem(self.template_id, self.topic_id, self.seed, self.params, self.prompt_md, value,
                       "number" if whole else "fraction", self.steps[:index + 1],
                       sigfigs=None if whole else (self.sigfigs or 3), target_s=self.target_s)


@dataclass(frozen=True)
class Template:
    id: str
    topic_id: str
    make: Callable[[random.Random], Spec]
    target_s: int = field(default=30)


TEMPLATES: dict[str, Template] = {}


def template(template_id: str, *, target_s: int = 30):
    """Register a generator; its topic is the id before ``/``."""
    topic_id, _, name = template_id.partition("/")
    if not name or template_id in TEMPLATES:
        raise ValueError(f"Template id {template_id!r} must be '<topic>/<name>' and unique")

    def register(make):
        TEMPLATES[template_id] = Template(template_id, topic_id, make, target_s)
        return make
    return register


def for_topic(topic_id: str) -> list[Template]:
    _load()
    return [item for item in TEMPLATES.values() if item.topic_id == topic_id]


def generate(template_id: str, seed: int) -> Problem:
    _load()
    item = TEMPLATES[template_id]
    spec = item.make(random.Random(f"{template_id}:{seed}"))
    answer = spec.answer if isinstance(spec.answer, str) else Fraction(spec.answer)
    # Templates whose answers are sometimes non-integers declare their kind, so the
    # kind never hints at the answer; the checker accepts every exact form anyway.
    kind = spec.kind or ("expression" if isinstance(answer, str) else "fraction" if (
        spec.sigfigs or spec.tolerance or answer.denominator != 1) else "number")
    if kind not in KINDS:
        raise ValueError(f"Unknown answer kind {kind}")
    mistakes, seen = [], {answer}
    for wrong, message in spec.mistakes:
        wrong = wrong if isinstance(wrong, str) else Fraction(wrong)
        if wrong not in seen:
            seen.add(wrong)
            mistakes.append((wrong, message))
    return Problem(template_id, item.topic_id, seed, dict(spec.params), spec.prompt, answer, kind,
                   tuple(spec.steps), tuple(mistakes), tuple(spec.choices) if spec.choices else None,
                   spec.tolerance, spec.sigfigs, tuple(spec.variables), item.target_s, spec.up_to_constant)


def pick(topic_id: str, seed: int) -> Template:
    """The template a topic uses for ``seed`` (topics with several rotate among them)."""
    options = sorted(for_topic(topic_id), key=lambda item: item.id)
    if not options:
        raise KeyError(topic_id)
    return options[random.Random(f"{topic_id}:{seed}").randrange(len(options))]


def symbols(names) -> dict:
    import sympy

    return {name: sympy.Symbol(name) for name in names}


def tex(value: Fraction | int) -> str:
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    sign = "-" if value < 0 else ""
    return f"{sign}\\frac{{{abs(value.numerator)}}}{{{value.denominator}}}"


def decimal_text(value: Fraction) -> str | None:
    """Exact decimal text for a terminating fraction with at most 6 places, else None."""
    value = Fraction(value)
    for places in range(0, 7):
        scaled = value * 10 ** places
        if scaled.denominator == 1:
            if places == 0:
                return str(scaled.numerator)
            sign = "-" if scaled < 0 else ""
            digits = str(abs(scaled.numerator)).rjust(places + 1, "0")
            return f"{sign}{digits[:-places]}.{digits[-places:]}"
    return None


def approx(value: Fraction, places: int = 4) -> str:
    return f"{float(value):.{places}g}" if abs(value) < 1 else f"{float(value):.{places + 2}g}"


def num(value) -> str:
    """A parameter or intermediate value as compact TeX (decimals stay decimal)."""
    value = Fraction(value)
    return decimal_text(value) or tex(value)


_loaded = False


def _load() -> None:
    global _loaded
    if not _loaded:
        from kitty.core.math import templates  # noqa: F401  registers every generator
        _loaded = True

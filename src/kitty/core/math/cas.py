"""SymPy equivalence for LaTeX answers, in a worker process that a timeout can kill.

:func:`decide` reads the answer with both of SymPy's LaTeX parsers (lark, which reports
ambiguity, and ANTLR, which is more forgiving), rewrites ``p(1-p)`` as multiplication
when ``p`` is one of the problem's variables, and judges every distinct reading:
``simplify`` proves equivalence, random numeric probes are the second opinion. If the
readings disagree, or nothing could be shown either way, the verdict is ``undecided``.
Each function application (``\\sin(x)``, ``\\ln x``, ``\\cos^2 2x``) is bracketed first:
its argument is its bracket group or a single term, so lark doesn't also read
``\\sin(x) + x`` as ``\\sin(x + x)``.

An antiderivative is checked up to a constant: a trailing ``+ C`` (``c``, ``K``) or
``+ 7`` is dropped before parsing (lark would also read ``\\sin x + C`` as ``\\sin(x + C)``),
a free ``C``/``c``/``K`` is allowed anywhere, and the answer, the key and the mistakes are
compared by their derivatives in the problem's single variable.

The worker is spawned (never forked from the threaded server) and serves one check at a
time; a check that outlives its timeout kills the worker, and the next check starts a
fresh one.
"""
from __future__ import annotations

import multiprocessing
import random
import re
import threading

TIMEOUT_S = 5.0
STARTUP_S = 60.0
PROBES = 24
#: Valid numeric probes needed to call two expressions equal without a proof.
AGREEING = 6
_SPACING = re.compile(r"\\(?:left|right|mleft|mright|big|Big|bigg|Bigg)(?![A-Za-z])|\\[,;:! ]|\\displaystyle")
#: Symbols an antiderivative may use for its constant of integration.
CONSTANTS = ("C", "c", "K")
_TRAILING = re.compile(r"\s*[+-]\s*(?:\d+(?:\.\d*)?|\.\d+|[CcK])\s*$")
#: Text a trailing ``+ C`` can't follow at top level, e.g. ``x^-3`` or ``x \cdot -2``.
_OPERATORS = ("^", "_", "{", "(", "[", "*", "/", "+", "-", "=", ",", "\\cdot", "\\times")
_FUNCTION = re.compile(r"\\(?:arcsin|arccos|arctan|sinh|cosh|tanh|sin|cos|tan|sec|csc|cot|ln|log|exp)(?![A-Za-z])")
_SCRIPT = re.compile(r"\s*[_^]\s*(?:\{[^{}]*\}|\w)")
#: An unbracketed argument: a number and/or one letter with an optional power (``2x``, ``x^2``).
_BARE = re.compile(r"\s*(?:\d+(?:\.\d*)?\s*)?(?:[A-Za-z](?![A-Za-z])(?:\s*\^\s*(?:\{[^{}]*\}|\w))?)?")
_CLOSE = {"(": ")", "{": "}", "[": "]"}

_lock = threading.Lock()
_worker: tuple | None = None


def equivalent(latex: str, expected: str, variables, mistakes, *, timeout: float | None = None,
               up_to_constant: bool = False) -> tuple[str, str | None]:
    """(kind, feedback) for ``latex`` against the SymPy string ``expected``."""
    request = (latex, expected, tuple(variables), tuple(mistakes), up_to_constant)
    with _lock:
        try:
            process, conn = _ensure()
        except Exception:
            _reset()
            return "undecided", "The algebra checker couldn't start; try again."
        try:
            conn.send(request)
            if conn.poll(TIMEOUT_S if timeout is None else timeout):
                return conn.recv()
        except (EOFError, OSError):
            pass
        _reset()
    return "undecided", "Checking took too long; try a simpler equivalent form."


def _ensure():
    global _worker
    if _worker is not None and _worker[0].is_alive():
        return _worker
    _reset()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_serve, args=(child,), name="rika-math-cas", daemon=True)
    process.start()
    child.close()
    if not parent.poll(STARTUP_S) or parent.recv() != "ready":
        process.kill()
        raise RuntimeError("CAS worker did not start")
    _worker = (process, parent)
    return _worker


def close() -> None:
    with _lock:
        _reset()


def _reset() -> None:
    global _worker
    if _worker is not None:
        process, conn = _worker
        process.kill()
        process.join(5)
        conn.close()
    _worker = None


def _serve(conn) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    except (ImportError, ValueError, OSError):
        pass
    import sympy  # noqa: F401  warm the import before reporting ready
    from sympy.parsing.latex import parse_latex  # noqa: F401

    conn.send("ready")
    while True:
        try:
            request = conn.recv()
        except (EOFError, OSError):
            return
        try:
            conn.send(decide(*request))
        except Exception:
            conn.send(("undecided", "Couldn't check that expression; try writing it another way."))


def readings(latex: str, variables) -> tuple[list, bool]:
    """Distinct SymPy readings of ``latex`` and whether any parser could read it."""
    import sympy
    from lark import Tree
    from sympy.core.function import AppliedUndef
    from sympy.parsing.latex import parse_latex

    text = _SPACING.sub(" ", latex.strip().strip("$")).replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    text = bracket_functions(text)
    raw = []
    try:
        raw.extend(_flatten(parse_latex(text, backend="lark"), Tree))
    except Exception:
        pass
    try:
        raw.append(parse_latex(text, strict=True, backend="antlr"))
    except Exception:
        pass
    names = set(variables)
    constants = {"e": sympy.E, "pi": sympy.pi}
    found, seen = [], set()
    for expr in raw:
        if not isinstance(expr, sympy.Basic) or isinstance(expr, (sympy.core.relational.Relational, sympy.logic.boolalg.Boolean)):
            continue
        expr = expr.replace(lambda node: isinstance(node, AppliedUndef) and node.func.__name__ in names,
                            lambda node: sympy.Symbol(node.func.__name__) * sympy.Mul(*node.args))
        if expr.atoms(AppliedUndef):
            continue
        expr = expr.subs({sympy.Symbol(name): value for name, value in constants.items() if name not in names})
        key = sympy.srepr(expr)
        if key not in seen:
            seen.add(key)
            found.append(expr)
    return found, bool(raw)


def bracket_functions(text: str) -> str:
    """``text`` with every function application wrapped in brackets: ``(\\sin(x)) + x``."""
    out, start = [], 0
    while match := _FUNCTION.search(text, start):
        end = match.end()
        while script := _SCRIPT.match(text, end):
            end = script.end()
        group = end + len(text[end:]) - len(text[end:].lstrip())
        if group < len(text) and text[group] in _CLOSE:
            close = _group_end(text, group)
            argument = None if close is None else text[end:group] + bracket_functions(text[group:close])
        else:
            bare = _BARE.match(text, end)
            close, argument = (bare.end(), bare.group()) if bare.group().strip() else (None, None)
        if argument is None:
            out.append(text[start:end])
            start = end
            continue
        out.append(f"{text[start:match.start()]}({text[match.start():end]}{argument})")
        start = close
    return "".join(out) + text[start:]


def _group_end(text: str, opening: int) -> int | None:
    """Index just past the bracket group opening at ``opening``; None if it never closes."""
    opener, closer, depth = text[opening], _CLOSE[text[opening]], 0
    for index in range(opening, len(text)):
        depth += (text[index] == opener) - (text[index] == closer)
        if depth == 0:
            return index + 1
    return None


def drop_constant(latex: str, variables) -> str:
    """``latex`` without trailing added constants (``+ C``, ``- K``, ``+ 7``)."""
    text = _SPACING.sub(" ", latex.strip().strip("$"))
    while match := _TRAILING.search(text):
        term, head = match.group().strip(" +-"), text[:match.start()].rstrip()
        if term in variables or not head or head.endswith(_OPERATORS):
            break
        text = head
    return text


def _flatten(node, tree_type):
    if isinstance(node, tree_type):
        for child in node.children:
            yield from _flatten(child, tree_type)
    else:
        yield node


def same(a, b, variables) -> bool | None:
    """True if a ≡ b (proof or agreeing probes), False on a clear numeric counterexample."""
    import sympy

    symbols = sorted(a.free_symbols | b.free_symbols, key=lambda s: s.name)
    rng = random.Random(f"{sympy.srepr(a)}|{sympy.srepr(b)}")
    agree = 0
    for index in range(PROBES):
        point = {s: sympy.Rational(rng.randint(5, 95), 100) if index % 2 else sympy.Rational(rng.randint(110, 900), 100) for s in symbols}
        try:
            left, right = complex(a.evalf(30, subs=point)), complex(b.evalf(30, subs=point))
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            continue
        if any(value != value or abs(value) == float("inf") for value in (left, right)):
            continue
        if abs(left - right) > 1e-9 * max(1.0, abs(left), abs(right)):
            return False
        agree += 1
    if agree >= AGREEING:
        return True
    try:
        return True if sympy.simplify(a - b) == 0 else None
    except Exception:
        return None


def decide(latex: str, expected: str, variables, mistakes, up_to_constant: bool = False) -> tuple[str, str | None]:
    import sympy

    locals_ = {name: sympy.Symbol(name) for name in variables}
    allowed = set(locals_)
    if up_to_constant:
        (name,) = variables
        latex = drop_constant(latex, variables)
        allowed.update(CONSTANTS)
        level = lambda expr: sympy.diff(expr, locals_[name])  # equal derivatives: equal up to a constant
    else:
        level = lambda expr: expr
    target = level(sympy.sympify(expected, locals=locals_))
    options, parsed = readings(latex, variables)
    if not options:
        if parsed:
            return "undecided", "Enter one expression, not an equation, using only the problem's variables."
        return "undecided", "Couldn't read that expression; check brackets and use \\cdot for multiplication."
    wrongs = [(level(sympy.sympify(wrong, locals=locals_)), message) for wrong, message in mistakes]
    verdicts = set()
    for expr in options:
        extra = sorted(s.name for s in expr.free_symbols if s.name not in allowed)
        if extra:
            verdicts.add(("different", f"Uses {', '.join(extra)}, which the problem doesn't define."))
            continue
        expr = level(expr)
        result = same(expr, target, variables)
        if result is True:
            verdicts.add(("equal", None))
            continue
        for wrong, message in wrongs:
            if same(expr, wrong, variables) is True:
                verdicts.add(("mistake", message))
                break
        else:
            verdicts.add(("different", None) if result is False else ("undecided", "Couldn't verify that form; try expanding or simplifying it."))
    if len(verdicts) == 1:
        return verdicts.pop()
    return "undecided", "That expression can be read more than one way; add explicit multiplication (\\cdot) or brackets."

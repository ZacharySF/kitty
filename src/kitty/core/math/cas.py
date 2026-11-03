"""SymPy equivalence for LaTeX answers, in a worker process that a timeout can kill.

:func:`decide` reads the answer with both of SymPy's LaTeX parsers (lark, which reports
ambiguity, and ANTLR, which is more forgiving), rewrites ``p(1-p)`` as multiplication
when ``p`` is one of the problem's variables, and judges every distinct reading:
``simplify`` proves equivalence, random numeric probes are the second opinion. If the
readings disagree, or nothing could be shown either way, the verdict is ``undecided``.

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

_lock = threading.Lock()
_worker: tuple | None = None


def equivalent(latex: str, expected: str, variables, mistakes, *, timeout: float | None = None) -> tuple[str, str | None]:
    """(kind, feedback) for ``latex`` against the SymPy string ``expected``."""
    request = (latex, expected, tuple(variables), tuple(mistakes))
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


def decide(latex: str, expected: str, variables, mistakes) -> tuple[str, str | None]:
    import sympy

    locals_ = {name: sympy.Symbol(name) for name in variables}
    target = sympy.sympify(expected, locals=locals_)
    options, parsed = readings(latex, variables)
    if not options:
        if parsed:
            return "undecided", "Enter one expression, not an equation, using only the problem's variables."
        return "undecided", "Couldn't read that expression; check brackets and use \\cdot for multiplication."
    wrongs = [(sympy.sympify(wrong, locals=locals_), message) for wrong, message in mistakes]
    verdicts = set()
    for expr in options:
        extra = sorted(s.name for s in expr.free_symbols if s.name not in locals_)
        if extra:
            verdicts.add(("different", f"Uses {', '.join(extra)}, which the problem doesn't define."))
            continue
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

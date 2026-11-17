"""The worker side of rika! math: rika-server answers every other ``/api/math`` route itself
(server/src/math/). Two parts stay here:

- ``POST /api/math/explain``: the "Explain my mistake" AI review (purpose ``math-explain``,
  an effect proposed in this window session and approved through /api/ai/run; never for
  textbook exercises).
- ``POST /api/internal/math/expression {expected, variables, up_to_constant, mistakes,
  answer?}`` -> ``{canonical, kind?, feedback?}``: SymPy for an expression problem, which only
  this process has (:mod:`kitty.core.math.cas`, in the ``math`` pack): the answer's verdict
  when one is given, and the expected answer's TeX (``Problem.canonical``; ``None`` if SymPy
  could not write it). Pages never reach it: the front refuses every ``/api/internal`` route.
"""
from __future__ import annotations

from kitty.core.math import checking, practice, problems
from kitty.core.math import topics as graph

EXPRESSION = "/api/internal/math/expression"


def _call(function, *args, **kwargs):
    from kitty.web_server import APIError
    try:
        return function(*args, **kwargs)
    except practice.NotFound as exc:
        raise APIError(404, str(exc)) from None
    except practice.Conflict as exc:
        raise APIError(409, str(exc)) from None
    except graph.GraphError as exc:
        raise APIError(500, str(exc)) from None
    except ValueError as exc:
        raise APIError(400, str(exc)) from None


def _keys(body, allowed, required=()):
    from kitty.web_server import APIError
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise APIError(400, "Expected " + ", ".join(sorted(allowed)))


def stop(app):
    """Stop the SymPy worker with the server; the next check starts a fresh one."""
    from kitty.core.math import cas

    cas.close()


def get(app, conn, path, query):
    return None


def _expression(body: dict) -> dict:
    required = {"expected", "variables", "up_to_constant", "mistakes"}
    _keys(body, required | {"answer"}, required)
    problem = problems.Problem("", "", 0, {}, "", body["expected"], "expression", (),
                               tuple((wrong, message) for wrong, message in body["mistakes"]),
                               variables=tuple(body["variables"]), up_to_constant=body["up_to_constant"])
    out = {}
    if "answer" in body:
        verdict = checking.check(problem, body["answer"])
        out.update(kind=verdict.kind, feedback=verdict.feedback)
    try:
        out["canonical"] = problem.canonical
    except Exception:  # only needed once revealed: the front answers 500 then, as this did
        out["canonical"] = None
    return out


def post(app, conn, path, body):
    if path == EXPRESSION:
        return _expression(body)
    if path != "/api/math/explain":
        return None
    from kitty import web_ai

    _call(graph.sync, conn)
    _keys(body, {"problem_id", "working"}, {"problem_id", "working"})
    text = _call(practice.explain_text, conn, body["problem_id"], body["working"])
    return web_ai._review(app, conn, "math-explain", text)

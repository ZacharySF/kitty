"""Loopback rika! math API: topics, problems, answers, lessons, sprints, mixed review,
Grinstead & Snell exercises, own-book problems and the "Explain my mistake" AI review
(purpose ``math-explain``, approved through /api/ai/run; never for textbook exercises)."""
from __future__ import annotations

import re

from kitty.core.math import library, mastery, mixed, practice, sprints
from kitty.core.math import topics as graph

_TOPIC = r"([a-z][a-z0-9.-]{0,63})"
_ID = r"([0-9a-f]{32})"
_EXERCISE = r"([a-z]{1,8}(?:\.[0-9a-z]{1,4}){1,4})"


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
    if not path.startswith("/api/math/"):
        return None
    _call(graph.sync, conn)
    if path == "/api/math/topics":
        app._no_query(query)
        return {"topics": _call(mastery.states, conn)}
    if path == "/api/math/next":
        app._no_query(query)
        return _call(mastery.next_task, conn)
    if path == "/api/math/sprints/history":
        return _call(sprints.history, conn, query)
    if path == "/api/math/mixed":
        app._no_query(query)
        return _call(mixed.queue, conn)
    if match := re.fullmatch(rf"/api/math/lessons/{_TOPIC}", path):
        app._no_query(query)
        return _call(practice.lesson, conn, match[1])
    if path == "/api/math/textbook":
        return _call(library.textbook_list, conn, query)
    if match := re.fullmatch(rf"/api/math/textbook/{_EXERCISE}", path):
        app._no_query(query)
        return _call(library.textbook_item, conn, match[1])
    if path == "/api/math/books":
        app._no_query(query)
        return _call(library.books, conn)
    return None


def post(app, conn, path, body):
    if not path.startswith("/api/math/"):
        return None
    _call(graph.sync, conn)
    if path == "/api/math/problems":
        _keys(body, {"topic_id", "seed"}, {"topic_id"})
        return _call(practice.issue, conn, body["topic_id"], body.get("seed"))
    if path == "/api/math/answers":
        _keys(body, {"problem_id", "answer", "elapsed_ms"}, {"problem_id", "answer", "elapsed_ms"})
        return _call(practice.answer, conn, body["problem_id"], body["answer"], body["elapsed_ms"])
    if path == "/api/math/sprints":
        return _call(sprints.start, conn, body)
    if match := re.fullmatch(rf"/api/math/sprints/{_ID}/result", path):
        return _call(sprints.finish, conn, match[1], body)
    if path == "/api/math/explain":
        from kitty import web_ai

        _keys(body, {"problem_id", "working"}, {"problem_id", "working"})
        text = _call(practice.explain_text, conn, body["problem_id"], body["working"])
        return web_ai._review(app, conn, "math-explain", text)
    if path == "/api/math/textbook/problems":
        _keys(body, {"exercise_id"}, {"exercise_id"})
        return _call(library.textbook_issue, conn, body["exercise_id"])
    if path == "/api/math/textbook/answers":
        _keys(body, {"problem_id", "answer", "elapsed_ms"}, {"problem_id", "answer", "elapsed_ms"})
        return _call(library.textbook_answer, conn, body["problem_id"], body["answer"], body["elapsed_ms"])
    if path == "/api/math/textbook/ratings":
        return _call(library.textbook_rate, conn, body)
    if path == "/api/math/books":
        return _call(library.add_book_problem, conn, body)
    if match := re.fullmatch(rf"/api/math/books/{_ID}", path):
        return _call(library.edit_book_problem, conn, match[1], body)
    if match := re.fullmatch(rf"/api/math/books/{_ID}/ratings", path):
        return _call(library.rate_book_problem, conn, match[1], body)
    if match := re.fullmatch(rf"/api/math/books/{_ID}/delete", path):
        _keys(body, set())
        return _call(library.delete_book_problem, conn, match[1])
    return None

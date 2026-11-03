"""Issued problems, graded answers, lessons and the "Explain my mistake" payload.

A problem is one ``math_attempts`` row; the expected answer never leaves the server
until the learner solves it or uses up :data:`MAX_TRIES` decided tries. Only the first
decided answer is graded into mastery; undecided answers (unreadable, ambiguous, too
few significant figures) cost nothing.
"""
from __future__ import annotations

import json
import random
import secrets
import sqlite3
from datetime import datetime

from kitty.core.math import checking, mastery, problems
from kitty.core.math import topics as graph
from kitty.db.connection import transaction

MAX_TRIES = 3
SEED_MAX = 2 ** 31 - 1
STOP_AFTER_MISSES = 3
MAX_WORKING = 4000
GUIDED_STEPS = 2


class Conflict(Exception):
    """The request is valid but not allowed in the current state (HTTP 409)."""


class NotFound(Exception):
    """No such topic or problem (HTTP 404)."""


def _topic(conn, topic_id, moment) -> dict:
    if not isinstance(topic_id, str) or topic_id not in graph.BY_ID:
        raise NotFound("Unknown math topic")
    state = mastery.state_of(conn, topic_id, moment)
    if state["state"] == "locked":
        missing = [graph.BY_ID[p].title for p in graph.BY_ID[topic_id].prereqs
                   if mastery.state_of(conn, p, moment)["state"] != "mastered"]
        raise Conflict("Master " + ", ".join(missing) + " first")
    return state


def _seed(seed) -> int:
    if seed is None:
        return secrets.randbelow(SEED_MAX + 1)
    if type(seed) is not int or not 0 <= seed <= SEED_MAX:
        raise ValueError(f"Seed must be an integer from 0 to {SEED_MAX}")
    return seed


def _public(problem: problems.Problem, attempt_id: str, purpose: str) -> dict:
    out = {"problem_id": attempt_id, "topic_id": problem.topic_id, "purpose": purpose,
           "prompt_md": problem.prompt_md, "answer_kind": problem.answer_kind}
    if problem.choices:
        out["choices"] = list(problem.choices)
    if problem.tolerance is not None:
        out["tolerance"] = float(problem.tolerance)
    if problem.sigfigs is not None:
        out["sigfigs"] = problem.sigfigs
    if problem.variables:
        out["variables"] = list(problem.variables)
    return out


def _insert(conn, problem: problems.Problem, purpose: str, moment: datetime, step: int | None = None) -> str:
    ident = secrets.token_hex(16)
    conn.execute("INSERT INTO math_attempts(id, topic_id, template_id, seed, step, purpose, created_at) VALUES (?,?,?,?,?,?,?)",
                 (ident, problem.topic_id, problem.template_id, problem.seed, step, purpose, moment.isoformat()))
    return ident


def issue(conn: sqlite3.Connection, topic_id, seed=None, moment: datetime | None = None) -> dict:
    """A fresh problem: a review once the topic is mastered, a lesson problem before."""
    moment = moment or mastery.now()
    state = _topic(conn, topic_id, moment)
    seed = _seed(seed)
    problem = problems.generate(problems.pick(topic_id, seed).id, seed)
    purpose = "review" if state["state"] == "mastered" else "lesson"
    with transaction(conn):
        ident = _insert(conn, problem, purpose, moment)
    return _public(problem, ident, purpose)


def _problem_for(row) -> problems.Problem:
    problem = problems.generate(row["template_id"], row["seed"])
    return problem.step_problem(row["step"]) if row["step"] is not None else problem


def _attempt(conn, problem_id):
    if not isinstance(problem_id, str):
        raise ValueError("problem_id must be text")
    row = conn.execute("SELECT * FROM math_attempts WHERE id=?", (problem_id,)).fetchone()
    if row is None:
        raise NotFound("Unknown math problem")
    return row


def _decided(entries) -> list[dict]:
    return [entry for entry in entries if entry["kind"] != "undecided"]


def _summary(state: dict) -> dict:
    return {key: state[key] for key in ("state", "progress", "due_at") if key in state}


def answer(conn: sqlite3.Connection, problem_id, value, elapsed_ms, moment: datetime | None = None) -> dict:
    moment = moment or mastery.now()
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("answer must be text or a number")
    if type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 86_400_000:
        raise ValueError("elapsed_ms must be an integer from 0 to 86400000")
    row = _attempt(conn, problem_id)
    entries = json.loads(row["answers"])
    if row["solved_at"] or len(_decided(entries)) >= MAX_TRIES:
        raise Conflict("This problem is finished; ask for a new one")
    problem = _problem_for(row)
    verdict = checking.check(problem, value)  # outside the write lock: SymPy can take seconds
    entry = {"answer": value if not isinstance(value, str) else value[:checking.MAX_ANSWER], "kind": verdict.kind,
             "feedback": verdict.feedback, "elapsed_ms": elapsed_ms, "at": moment.isoformat()}
    with transaction(conn, immediate=True):
        current = conn.execute("SELECT answers, graded_at FROM math_attempts WHERE id=?", (problem_id,)).fetchone()
        if current["answers"] != row["answers"]:
            raise Conflict("This problem was answered in another window; reload it")
        entries.append(entry)
        updates = {"answers": json.dumps(entries)}
        if verdict.correct is not None and current["graded_at"] is None:
            updates.update(correct=int(verdict.correct), elapsed_ms=elapsed_ms, graded_at=moment.isoformat())
            if row["purpose"] != "guided":
                updates["rating"] = mastery.record(conn, row["topic_id"], verdict.correct, elapsed_ms, problem.target_s, moment)
        if verdict.correct:
            updates["solved_at"] = moment.isoformat()
        conn.execute(f"UPDATE math_attempts SET {', '.join(f'{key}=?' for key in updates)} WHERE id=?",
                     (*updates.values(), problem_id))
    tries = len(_decided(entries))
    revealed = bool(verdict.correct) or (verdict.correct is False and tries >= MAX_TRIES)
    out = {"correct": verdict.correct, "kind": verdict.kind, "tries_left": 0 if verdict.correct else MAX_TRIES - tries,
           "revealed": revealed, "mastery": _summary(mastery.state_of(conn, row["topic_id"], moment))}
    if verdict.feedback:
        out["feedback"] = verdict.feedback
    if revealed:
        out["canonical"] = problem.canonical
    return out


def _fadeable(problem: problems.Problem) -> list[int]:
    return [index for index, step in enumerate(problem.steps) if step.value is not None]


def lesson(conn: sqlite3.Connection, topic_id, moment: datetime | None = None) -> dict:
    """A worked example, then the same shape with its last steps faded, then solo problems."""
    moment = moment or mastery.now()
    state = _topic(conn, topic_id, moment)
    rng = random.Random(secrets.randbits(64))
    # Example and guided share a template; prefer one whose solution has fadeable steps.
    templates = sorted(problems.for_topic(topic_id), key=lambda item: (not _fadeable(problems.generate(item.id, 0)), rng.random()))
    chosen = templates[0]
    example = problems.generate(chosen.id, rng.randrange(SEED_MAX))
    guided_problem = problems.generate(chosen.id, rng.randrange(SEED_MAX))
    guided = []
    with transaction(conn):
        for index in _fadeable(guided_problem)[-GUIDED_STEPS:]:
            step = guided_problem.step_problem(index)
            ident = _insert(conn, guided_problem, "guided", moment, index)
            item = _public(step, ident, "guided")
            item.update(steps=[line.md() for line in guided_problem.steps[:index]] + [guided_problem.steps[index].md(blank=True)],
                        blank_step=index)
            guided.append(item)
    recent = [row["correct"] for row in conn.execute(
        "SELECT correct FROM math_attempts WHERE topic_id=? AND purpose != 'guided' AND graded_at IS NOT NULL "
        "ORDER BY graded_at DESC LIMIT 5", (topic_id,))]
    solo = 5
    if state["state"] in ("learning", "mastered") and state.get("progress", 0) > 0:
        solo = 3  # expertise reversal: less scaffolding once it's going well
    if len(recent) >= 3 and sum(recent) / len(recent) < 0.5:
        solo = 8
    return {"topic_id": topic_id, "title": graph.BY_ID[topic_id].title,
            "example": {"prompt_md": example.prompt_md, "steps": [step.md() for step in example.steps],
                        "answer_md": example.canonical},
            "guided": guided, "solo_count": solo, "stop_after_misses": STOP_AFTER_MISSES}


def explain_text(conn: sqlite3.Connection, problem_id, working) -> str:
    """The reviewed text for a ``math-explain`` AI request. It carries the problem, the
    learner's wrong answer and working, never the expected answer."""
    row = _attempt(conn, problem_id)
    if not isinstance(working, str) or not working.strip() or len(working) > MAX_WORKING:
        raise ValueError(f"Working must be 1–{MAX_WORKING} characters")
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in working):
        raise ValueError("Working must not contain control characters")
    wrong = [entry for entry in json.loads(row["answers"]) if entry["kind"] in ("different", "mistake")]
    if not wrong:
        raise Conflict("Explain my mistake is offered after a wrong answer")
    problem = _problem_for(row)
    payload = {"problem": problem.prompt_md, "learner_answer": wrong[-1]["answer"],
               "checker_feedback": wrong[-1].get("feedback"), "learner_working": working}
    if row["step"] is not None:
        payload["faded_step"] = problem.steps[row["step"]].md(blank=True)
    return json.dumps(payload, ensure_ascii=False, indent=2)

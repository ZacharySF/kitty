"""The math routes answered by rika-server (server/src/math) against `kitty.core.math`.

Generation first, through `rika-server math` (test-only): every template over many seeds (and
every faded step), each topic's template pick, number and choice checks over thousands of
typed answers, a lesson's draws, sprint problems and the textbook's topic links must equal
Python's to the character. Then one scenario over every route, with the refusals between, run
through the front and through the Python core on two copies of the same workspace at the same
clock readings and the same `secrets` draws (`KITTY_TEST_MATH_RANDOM` on the front, a
`random.Random` of the same seed in place of `secrets` on the reference): every answer and the
math tables left behind must be the same (random ids and py-fsrs' wall-clock card ids aside).
Expression answers reach SymPy on both sides: the reference in-process, the front through the
worker's internal route. Again without the math module (every route refused)."""
from __future__ import annotations

import json
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from urllib.parse import urlencode

import pytest
from terminal_support import RIKA_SERVER, front_site

from kitty.core.math import checking, library, mastery, mixed, practice, problems, sprints, textbook
from kitty.core.math import topics as graph
from kitty.db import connect
from kitty.modules import MODULE_IDS, ModuleDisabledError
from kitty.web_server import APIError, WebApplication

UTC = timezone.utc
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
RANDOM_SEED = 20261116
HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
#: py-fsrs numbers a new card by the wall clock's milliseconds, on either side.
CARD_ID = re.compile(r'"card_id": \d+')
TABLES = {"math_topics": "position", "math_prereqs": "topic_id, prereq_id", "math_mastery": "topic_id", "math_attempts": "rowid",
          "math_sprints": "rowid", "math_textbook_cards": "rowid", "math_textbook_attempts": "rowid", "math_book_problems": "rowid"}
#: LaTeX for each answer of count.combinations/polynomial: an equivalent form and a mistake.
POLYNOMIAL = {"n*(n-1)": ("n\\left(n-1\\right)", "n^{2}"), "n*(n-1)/2": ("\\frac{n^2-n}{2}", "n(n-1)"),
              "n*(n-1)*(n-2)": ("n^3-3n^2+2n", "n^{3}"), "n*(n-1)*(n-2)/6": ("\\binom{n}{3}", "n(n-1)(n-2)")}

problems._load()


def binary():
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER}); run cargo build --release in server/")
    return str(RIKA_SERVER)


def math_command(requests):
    done = subprocess.run([binary(), "math"], input="\n".join(json.dumps(request) for request in requests) + "\n",
                          capture_output=True, text=True, check=True, timeout=600)
    answered = [json.loads(line) for line in done.stdout.split("\n") if line]
    assert len(answered) == len(requests)
    return answered


def value(answer):
    return ["text", answer] if isinstance(answer, str) else ["num", str(Fraction(answer))]


def described(problem):
    """A problem as `rika-server math` describes it."""
    return {"template_id": problem.template_id, "topic_id": problem.topic_id, "prompt_md": problem.prompt_md, "answer": value(problem.answer),
            "answer_kind": problem.answer_kind, "steps": [[s.why, s.tex, None if s.value is None else str(s.value)] for s in problem.steps],
            "steps_md": [s.md() for s in problem.steps], "blank_md": [s.md(blank=True) for s in problem.steps],
            "mistakes": [[value(wrong), message] for wrong, message in problem.mistakes],
            "choices": list(problem.choices) if problem.choices else None, "tolerance": None if problem.tolerance is None else str(problem.tolerance),
            "sigfigs": problem.sigfigs, "variables": list(problem.variables), "target_s": problem.target_s,
            "up_to_constant": problem.up_to_constant, "canonical": None if problem.answer_kind == "expression" else problem.canonical}


def test_generation_matches_python():
    cases = [{"generate": template, "seed": seed} for template in problems.TEMPLATES for seed in range(240)]
    cases += [{"generate": template, "seed": seed, "step": index} for template in problems.TEMPLATES for seed in range(30)
              for index, step in enumerate(problems.generate(template, seed).steps) if step.value is not None]
    picks = [{"pick": topic.id, "seed": seed} for topic in graph.TOPICS for seed in range(120)]
    answered = math_command(cases + picks)
    for case, answer in zip(cases, answered):
        problem = problems.generate(case["generate"], case["seed"])
        if "step" in case:
            problem = problem.step_problem(case["step"])
        assert answer == described(problem), case
    for case, answer in zip(picks, answered[len(cases):]):
        assert answer == problems.pick(case["pick"], case["seed"]).id, case
    assert len(problems.TEMPLATES) >= 75 and len(cases) > 20_000


def typed(problem, rng):
    """Answers a learner might type for `problem`: exact and decimal forms, near misses at
    several figures, its known mistakes, TeX, percentages, other scripts' digits, nonsense."""
    if problem.answer_kind == "choice":
        choices = list(problem.choices)
        return [*choices, *(f"  {choice} " for choice in choices), *range(-1, len(choices) + 1), "nonsense", "0", 1.0, None, True, [0]]
    answer = problem.answer
    out = [f"{answer.numerator}/{answer.denominator}", str(answer), float(answer), f"\\frac{{{answer.numerator}}}{{{answer.denominator}}}",
           f"-\\dfrac{abs(answer.numerator)}{answer.denominator}", f"$\\tfrac{{{answer.numerator}}}{{{answer.denominator}}}$",
           f"{float(answer * 100)}%", f"{float(answer * 100)}\\%", f"({float(answer)})", f"{float(answer):.3e}", f"{float(answer):,}",
           f"{float(answer)}".replace(".", ","), f"{float(answer)}".replace("-", "−"), f" {answer.numerator} / {answer.denominator} "]
    if answer.denominator == 1:
        out += [int(answer), f"{int(answer):,}", f"{int(answer):_}", f"{int(answer)}.0", f"{int(answer)}e0", f"{int(answer) * 10}e-1"]
    for figures in range(1, 8):
        out += [f"{float(answer):.{figures}g}", f"{float(answer) * (1 + rng.uniform(-1, 1) * 10 ** -figures):.{figures}g}"]
    for wrong, _ in problem.mistakes:
        if not isinstance(wrong, str):
            out += [f"{wrong.numerator}/{wrong.denominator}", f"{float(wrong):.4g}"]
    digits = str(abs(answer.numerator))
    out += ["".join(chr(0x660 + int(d)) for d in digits), "".join("²" if d == "2" else d for d in digits), f"{digits}/0", "\\frac{1}{0}",
            "1e9999999999999999999", f"{digits}e400", f"{digits}e-400", f"0.{'3' * 60}", "1" * 45 + "/" + "7" * 44, "", "  ", "twelve",
            "+.5", ".", "-", "1e", "0x10", "1/2/3", "(", ")", "()", "50%%", "%", "1,234,567.5", "12,34", "1.5.6", "∞", "nan", "inf",
            "x" * 501, None, [1], {"a": 1}, True, 10 ** 15, -(10 ** 15), 1e-9, 1e22, -0.0]
    return out


def test_number_and_choice_checks_match_python():
    rng = random.Random(7)
    cases, expected = [], []
    for template in problems.TEMPLATES:
        for seed in range(12):
            problem = problems.generate(template, seed)
            if problem.answer_kind == "expression":
                continue
            steps = [None] + [index for index, step in enumerate(problem.steps) if step.value is not None][-2:]
            for step in steps:
                checked = problem if step is None else problem.step_problem(step)
                for answer in typed(checked, rng):
                    cases.append({"check": template, "seed": seed, **({"step": step} if step is not None else {}), "answer": answer})
                    verdict = checking.check(checked, answer)
                    expected.append({"correct": verdict.correct, "kind": verdict.kind, "feedback": verdict.feedback})
    answered = math_command(cases)
    mismatches = [(case, answer, wanted) for case, answer, wanted in zip(cases, answered, expected) if answer != wanted]
    assert not mismatches, mismatches[:5]
    kinds = {wanted["kind"] for wanted in expected}
    assert kinds == {"equal", "mistake", "different", "undecided"} and len(cases) > 50_000


def test_lesson_draws_match_python():
    """A lesson's example and guided problem for `random.Random(n)`: `practice.lesson`'s draws."""
    cases = [{"lesson": topic.id, "rng": n} for topic in graph.TOPICS for n in (0, 1, 2 ** 64 - 1, 123456789, *range(1000, 1006))]
    for case, answer in zip(cases, math_command(cases)):
        rng = random.Random(case["rng"])
        ranked = sorted(problems.for_topic(case["lesson"]),
                        key=lambda item: (not practice._fadeable(problems.generate(item.id, 0)), rng.random()))
        chosen = ranked[0].id
        assert answer == [[chosen, rng.randrange(practice.SEED_MAX)], [chosen, rng.randrange(practice.SEED_MAX)]], case


def test_sprint_problems_match_python():
    cases = [{"sprint": mode, "seed": seed, "ops": ops, "count": 240} for mode in sprints.MODES for seed in (0, 1, 7, 2 ** 31 - 1, 99991)
             for ops in (list(sprints.OPS), ["add"], ["sub"], ["mul"], ["div"], ["add", "div"], ["sub", "mul"])]
    for case, answer in zip(cases, math_command(cases)):
        assert answer == sprints.problems(case["sprint"], case["seed"], case["ops"], case["count"]), case


def test_textbook_links_match_python():
    [answer] = math_command([{"textbook": str(textbook.directory())}])
    exercises = textbook.exercises()
    assert [item["id"] for item in answer] == [exercise.id for exercise in exercises]
    for item, exercise in zip(answer, exercises):
        assert item["topics"] == list(exercise.topics), exercise.id
        assert item["problem"] == (described(textbook.problem(exercise)) if exercise.answer else None), exercise.id
    assert sum(1 for item in answer if item["problem"]) == len(textbook.ANSWERS)


# ------------------------------------------------------------------------------ the routes

class Refused(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Clock:
    value = T0


class Draws:
    """`secrets` for the reference: the draws the front makes under `KITTY_TEST_MATH_RANDOM`."""

    def __init__(self):
        self.rng = random.Random(RANDOM_SEED)

    def randbelow(self, bound):
        return self.rng._randbelow(bound)

    def randbits(self, bits):
        return self.rng.getrandbits(bits)

    token_hex = staticmethod(secrets.token_hex)


def no_query(query):
    if query:
        raise Refused(400, "This route does not accept query parameters")


def keys(body, allowed, required=()):
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise Refused(400, "Expected " + ", ".join(sorted(allowed)))


class Reference:
    """The routes as `kitty.web_math` answered them over the core before the port (its handlers
    are deleted): the module guard, `_no_query`, `_keys` and `_call`'s errors."""

    TOPIC = r"([a-z][a-z0-9.-]{0,63})"
    ID = r"([0-9a-f]{32})"
    EXERCISE = r"([a-z]{1,8}(?:\.[0-9a-z]{1,4}){1,4})"

    def __init__(self, db_path, modules=MODULE_IDS):
        self.db_path, self.modules, self.conn = db_path, modules, connect(db_path)

    def get(self, path, query):
        conn = self.conn
        graph.sync(conn)
        if path == "/api/math/topics":
            no_query(query)
            return {"topics": mastery.states(conn)}
        if path == "/api/math/next":
            no_query(query)
            return mastery.next_task(conn)
        if path == "/api/math/sprints/history":
            return sprints.history(conn, query)
        if path == "/api/math/mixed":
            no_query(query)
            return mixed.queue(conn)
        if match := re.fullmatch(rf"/api/math/lessons/{self.TOPIC}", path):
            no_query(query)
            return practice.lesson(conn, match[1])
        if path == "/api/math/textbook":
            return library.textbook_list(conn, query)
        if match := re.fullmatch(rf"/api/math/textbook/{self.EXERCISE}", path):
            no_query(query)
            return library.textbook_item(conn, match[1])
        if path == "/api/math/books":
            no_query(query)
            return library.books(conn)
        return None

    def post(self, path, body):
        conn = self.conn
        graph.sync(conn)
        if path == "/api/math/problems":
            keys(body, {"topic_id", "seed"}, {"topic_id"})
            return practice.issue(conn, body["topic_id"], body.get("seed"))
        if path == "/api/math/answers":
            keys(body, {"problem_id", "answer", "elapsed_ms"}, {"problem_id", "answer", "elapsed_ms"})
            return practice.answer(conn, body["problem_id"], body["answer"], body["elapsed_ms"])
        if path == "/api/math/sprints":
            return sprints.start(conn, body)
        if match := re.fullmatch(rf"/api/math/sprints/{self.ID}/result", path):
            return sprints.finish(conn, match[1], body)
        if path == "/api/math/textbook/problems":
            keys(body, {"exercise_id"}, {"exercise_id"})
            return library.textbook_issue(conn, body["exercise_id"])
        if path == "/api/math/textbook/answers":
            keys(body, {"problem_id", "answer", "elapsed_ms"}, {"problem_id", "answer", "elapsed_ms"})
            return library.textbook_answer(conn, body["problem_id"], body["answer"], body["elapsed_ms"])
        if path == "/api/math/textbook/ratings":
            return library.textbook_rate(conn, body)
        if path == "/api/math/books":
            return library.add_book_problem(conn, body)
        if match := re.fullmatch(rf"/api/math/books/{self.ID}", path):
            return library.edit_book_problem(conn, match[1], body)
        if match := re.fullmatch(rf"/api/math/books/{self.ID}/ratings", path):
            return library.rate_book_problem(conn, match[1], body)
        if match := re.fullmatch(rf"/api/math/books/{self.ID}/delete", path):
            keys(body, set())
            return library.delete_book_problem(conn, match[1])
        return None

    def __call__(self, path, body, now, query):
        Clock.value = now
        try:
            if body is not None and query:
                raise Refused(400, "This route does not accept query parameters")
            if "math" not in self.modules:
                raise Refused(403, str(ModuleDisabledError("math")))
            answer = None
            if path.startswith("/api/math/"):
                answer = self.get(path, query) if body is None else self.post(path, body)
            if answer is None:
                raise Refused(404, "API route not found")
        except Refused as refused:
            return refused.status, {"error": refused.message}
        except practice.NotFound as refused:
            return 404, {"error": str(refused)}
        except practice.Conflict as refused:
            return 409, {"error": str(refused)}
        except graph.GraphError as refused:
            return 500, {"error": str(refused)}
        except ValueError as refused:
            return 400, {"error": str(refused)}
        except (TimeoutError, sqlite3.OperationalError):
            return 503, {"error": "Local workspace is busy or unavailable; try again"}
        except Exception:  # what `RequestHandler._dispatch` answers for anything else
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))


class Through:
    """The front, its clock a file it re-reads (`KITTY_TEST_CLOCK`), its draws seeded."""

    def __init__(self, settings, clock):
        self.db_path, self.clock = settings.db_path, clock
        clock.write_text(T0.isoformat())
        self.context = front_site(settings, {"KITTY_TEST_CLOCK": str(clock), "KITTY_TEST_MATH_RANDOM": str(RANDOM_SEED)})
        self.site = self.context.__enter__()

    def __call__(self, path, body, now, query):
        self.clock.write_text(now.isoformat())
        return self.site.request(path + ("?" + urlencode(query) if query else ""), body)

    def close(self):
        self.context.__exit__(None, None, None)


class Masked:
    """Random ids by order of first appearance, so two runs compare; py-fsrs card ids as 0."""

    def __init__(self):
        self.seen: dict[str, str] = {}

    def __call__(self, value):
        if isinstance(value, dict):
            return {self(key): self(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self(item) for item in value]
        if isinstance(value, str):
            return CARD_ID.sub('"card_id": 0', HEX.sub(lambda found: self.seen.setdefault(found[0], f"id-{len(self.seen)}"), value))
        return value


def tables(db_path, mask):
    with closing(connect(db_path)) as conn:
        stored = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")] for table, order in TABLES.items()}
        events = [list(row) for row in conn.execute("SELECT section, entity, entity_id, action, route FROM record_events ORDER BY seq")]
    return mask(stored), events


class Session:
    """One side's run: each request with its status checked, and the answers in order."""

    def __init__(self, call):
        self.call, self.answers, self.now = call, [], T0

    def do(self, path, body=None, expect=200, **query):
        status, answer = self.call(path, body, self.now, query)
        assert status == expect, (path, body, query, status, answer)
        self.answers.append((path, status, answer))
        return answer

    def expected(self, problem):
        """The problem behind an issued id, from this side's own database."""
        with closing(connect(self.call.db_path)) as conn:
            row = conn.execute("SELECT template_id, seed, step FROM math_attempts WHERE id=?", (problem["problem_id"],)).fetchone()
        generated = problems.generate(row["template_id"], row["seed"])
        return generated.step_problem(row["step"]) if row["step"] is not None else generated

    def exact(self, problem):
        answer = self.expected(problem).answer
        if isinstance(answer, str):
            return POLYNOMIAL[answer][0] if answer in POLYNOMIAL else answer
        return f"{answer.numerator}/{answer.denominator}"

    def answer(self, problem, given, elapsed=4000, expect=200):
        return self.do("/api/math/answers", {"problem_id": problem["problem_id"], "answer": given, "elapsed_ms": elapsed}, expect=expect)

    def master(self, topic_id):
        """Three correct first tries in a row."""
        for _ in range(mastery.MASTERY_RUN):
            problem = self.do("/api/math/problems", {"topic_id": topic_id})
            self.answer(problem, self.exact(problem))

    def later(self, **delta):
        self.now = self.now + timedelta(**delta)


def scenario(call):
    """Every math route, with the refusals between: each (path, status, answer) in order."""
    run = Session(call)
    do, answer, master, exact, expected = run.do, run.answer, run.master, run.exact, run.expected

    # Reads of a fresh workspace and the refusals of every query.
    do("/api/math/topics")
    do("/api/math/next")
    do("/api/math/mixed")
    do("/api/math/books")
    do("/api/math/sprints/history")
    listed = do("/api/math/textbook")
    do("/api/math/textbook", topic_id="prob.basic")
    first = listed["exercises"][0]["id"]
    do(f"/api/math/textbook/{first}")
    for path in ("/api/math/topics", "/api/math/next", "/api/math/mixed", "/api/math/books", f"/api/math/textbook/{first}",
                 "/api/math/lessons/arith.add-sub"):
        do(path, expect=400, x="1")
    do("/api/math/textbook", expect=400, page="2")
    do("/api/math/textbook", expect=404, topic_id="nope")
    for path in ("/api/math/textbook/gs.99.9.999", "/api/math/textbook/BAD", "/api/math/nothing", "/api/math", "/api/math/",
                 "/api/math/lessons/Bad!", "/api/math/lessons/nope", "/api/math/explain", "/api/math/sprints/history/x"):
        do(path, expect=404)
    do("/api/math/lessons/prob.bayes", expect=409)
    do("/api/math/topics", {}, expect=404)
    do("/api/math/problems", {"topic_id": "arith.add-sub"}, expect=400, q="1")

    # Problems: the gate, the seed and the fields.
    for body, status in (({"topic_id": "prob.bayes"}, 409), ({"topic_id": "nope"}, 404), ({"topic_id": 5}, 404), ({}, 400),
                         ({"topic_id": "arith.add-sub", "seed": -1}, 400), ({"topic_id": "arith.add-sub", "seed": 2 ** 31}, 400),
                         ({"topic_id": "arith.add-sub", "seed": "7"}, 400), ({"topic_id": "arith.add-sub", "seed": True}, 400),
                         ({"topic_id": "arith.add-sub", "seed": 1.5}, 400), ({"topic_id": "arith.add-sub", "extra": 1}, 400),
                         ({"topic_id": "prob.bayes", "seed": -1}, 409), ({"topic_id": "arith.add-sub", "seed": 2 ** 31 - 1}, 200),
                         ({"topic_id": "arith.add-sub", "seed": None}, 200)):
        do("/api/math/problems", body, expect=status)
    one = do("/api/math/problems", {"topic_id": "arith.add-sub", "seed": 3})
    do("/api/math/problems", {"topic_id": "arith.add-sub", "seed": 3})
    # Tries: undecided costs nothing, three wrong reveal, then the problem is finished.
    answer(one, "twelve", 900)
    answer(one, "-5", 900)
    answer(one, -5.0, 900)
    answer(one, "-6", 900)
    answer(one, "1", expect=409)
    for body in ({"problem_id": "0" * 32, "answer": "1", "elapsed_ms": 1}, {"problem_id": 5, "answer": "1", "elapsed_ms": 1},
                 {"problem_id": one["problem_id"], "answer": "1", "elapsed_ms": -1},
                 {"problem_id": "x", "answer": "1", "elapsed_ms": 1.5}, {"problem_id": "x", "answer": "1", "elapsed_ms": True},
                 {"problem_id": "x", "answer": "1", "elapsed_ms": 86_400_001}, {"problem_id": "x", "answer": None, "elapsed_ms": 1},
                 {"problem_id": "x", "answer": [1], "elapsed_ms": 1}, {"problem_id": "x", "answer": True, "elapsed_ms": 1},
                 {"problem_id": "x", "answer": "1"}, {"problem_id": "x", "answer": "1", "elapsed_ms": 1, "extra": 1}):
        status = 404 if body.get("problem_id") == "0" * 32 else 400
        do("/api/math/answers", body, expect=status)
    # Typed forms of right and wrong answers on fresh problems.
    for seed, forms in ((10, ["1,000,000", "x"]), (11, ["\\frac{2}{0}", "exact"]), (12, ["exact-float"]), (13, ["%"]), (14, ["exact-int"])):
        problem = do("/api/math/problems", {"topic_id": "arith.add-sub", "seed": seed})
        for form in forms:
            right = expected(problem).answer
            given = {"exact": exact(problem), "exact-float": float(right), "exact-int": int(right), "%": f"{right * 100}%"}.get(form, form)
            answer(problem, given, 2000)
    # Mastery: three correct first tries in a row; the next topic unlocks; a review is graded.
    master("arith.add-sub")
    master("arith.times-tables")
    do("/api/math/topics")
    do("/api/math/next")
    review = do("/api/math/problems", {"topic_id": "arith.add-sub"})
    answer(review, "-1")
    review = do("/api/math/problems", {"topic_id": "arith.times-tables"})
    answer(review, exact(review), 1000)

    # Lessons: an example, guided faded steps (never graded into mastery), solo counts.
    lesson = do("/api/math/lessons/arith.mul-2x1")
    for item in lesson["guided"]:
        answer(item, "1/7", 20000)
        answer(item, exact(item), 20000)
    do("/api/math/lessons/arith.add-sub")
    for topic in ("arith.division", "count.product-rule", "count.permutations"):
        master(topic)
    do("/api/math/topics")
    # An expression topic's example is written by SymPy.
    do("/api/math/lessons/calc.power-rule")
    do("/api/math/lessons/count.combinations")

    # Expressions, checked by SymPy: equal, a known mistake, different, unreadable, empty.
    seeds = [seed for seed in range(80) if problems.pick("count.combinations", seed).id == "count.combinations/polynomial"]
    for seed, attempts in zip(seeds, (["right"], ["mistake", "n^{9}", "\\frac{", "", 5, "x" * 501, "right"],
                                      ["n^{9}", "n^{8}", "n^{7}"], ["k^{2}", "n = 3", "right"])):
        problem = do("/api/math/problems", {"topic_id": "count.combinations", "seed": seed})
        key = expected(problem).answer
        for given in attempts:
            given = {"right": POLYNOMIAL[key][0], "mistake": POLYNOMIAL[key][1]}.get(given, given) if isinstance(given, str) else given
            answer(problem, given, 9000)
    power = do("/api/math/problems", {"topic_id": "calc.power-rule", "seed": 5})
    for given in ("x^{2}", "7", "3x"):
        answer(power, given, 9000)

    # Sprints.
    zetamac = do("/api/math/sprints", {})
    multiply = do("/api/math/sprints", {"mode": "mc", "duration_s": 60, "ops": ["mul"]})
    do("/api/math/sprints", {"mode": "mc", "ops": ["div", "add"]})
    for body in ({"mode": "speed"}, {"mode": None}, {"duration_s": 5}, {"duration_s": 901}, {"duration_s": 60.0}, {"ops": []},
                 {"ops": ["add", "add"]}, {"ops": "add"}, {"ops": ["pow"]}, {"ops": [1]}, {"x": 1}):
        do("/api/math/sprints", body, expect=400)
    result = f"/api/math/sprints/{zetamac['sprint_id']}/result"
    for body in ({"score": 500, "wrong": 0, "elapsed_ms": 120000}, {"score": 30, "wrong": 0, "elapsed_ms": 999999},
                 {"score": 30, "wrong": -1, "elapsed_ms": 1000}, {"score": "3", "elapsed_ms": 1000}, {"elapsed_ms": 1000},
                 {"score": 1, "elapsed_ms": 1000, "x": 1}, {"score": -1, "wrong": 0, "elapsed_ms": 1000}):
        do(result, body, expect=400)
    do(result, {"score": 38, "wrong": 0, "elapsed_ms": 120000})
    do(result, {"score": 38, "wrong": 0, "elapsed_ms": 120000}, expect=409)
    do(f"/api/math/sprints/{'0' * 32}/result", {"score": 1, "elapsed_ms": 1}, expect=404)
    do("/api/math/sprints/xyz/result", {"score": 1, "elapsed_ms": 1}, expect=404)
    do(f"/api/math/sprints/{multiply['sprint_id']}/result", {"score": -2, "wrong": 5, "elapsed_ms": 60000})
    run.later(days=1, hours=2)
    do("/api/math/sprints", {"duration_s": 10})
    for query, status in (({}, 200), ({"duration_s": "60"}, 200), ({"duration_s": "\u0666\u0660"}, 200), ({"duration_s": "abc"}, 400),
                          ({"duration_s": "\u00b2"}, 400), ({"duration_s": ""}, 400), ({"duration_s": "99999999999999999999"}, 500),
                          ({"timezone": "Mars/Base"}, 400), ({"timezone": "Europe/Berlin"}, 200), ({"timezone": "Pacific/Kiritimati"}, 200),
                          ({"x": "1"}, 400)):
        do("/api/math/sprints/history", expect=status, **query)

    # Grinstead & Snell: a self-graded exercise rated, numeric ones answered and graded.
    exercises = textbook.exercises()
    graded = [exercise for exercise in exercises if exercise.answer]
    self_graded = next(exercise for exercise in exercises if exercise.answer is None)
    for body, status in (({"exercise_id": self_graded.id, "rating": 2}, 200), ({"exercise_id": self_graded.id, "rating": 7}, 400),
                         ({"exercise_id": self_graded.id, "rating": True}, 400), ({"exercise_id": graded[0].id, "rating": 3}, 409),
                         ({"exercise_id": 5, "rating": 3}, 400), ({"exercise_id": "gs.99", "rating": 3}, 404),
                         ({"exercise_id": self_graded.id, "rating": 2, "x": 1}, 400), ({"exercise_id": self_graded.id}, 400)):
        do("/api/math/textbook/ratings", body, expect=status)
    for body, status in (({"exercise_id": self_graded.id}, 409), ({"exercise_id": "gs.99"}, 404), ({}, 400), ({"exercise_id": 1}, 400),
                         ({"exercise_id": graded[0].id, "x": 1}, 400)):
        do("/api/math/textbook/problems", body, expect=status)
    figures = next(exercise for exercise in graded if exercise.answer.sigfigs)
    for exercise, tries in ((graded[0], ["banana", "123456789", "exact", "1"]), (graded[1], ["2", "3", "4", "5"]),
                            (figures, [f"{float(figures.answer.value):.1g}", f"{float(figures.answer.value):.{figures.answer.sigfigs}g}"])):
        issued = do("/api/math/textbook/problems", {"exercise_id": exercise.id})
        for index, given in enumerate(tries):
            given = f"{exercise.answer.value.numerator}/{exercise.answer.value.denominator}" if given == "exact" else given
            status = 409 if index == 3 else 200
            do("/api/math/textbook/answers", {"problem_id": issued["problem_id"], "answer": given, "elapsed_ms": 40000 * (index + 1)},
               expect=status)
    do("/api/math/textbook/answers", {"problem_id": "0" * 32, "answer": "1", "elapsed_ms": 1}, expect=404)
    do("/api/math/textbook", topic_id=self_graded.topics[0])
    do(f"/api/math/textbook/{self_graded.id}")
    do(f"/api/math/textbook/{graded[0].id}")

    # Own books.
    book = {"book": "Zhou", "section": "2.3", "problem": "14", "answer": "1/3", "confidence": 2, "topic_id": "prob.bayes"}
    entry = do("/api/math/books", book)
    do("/api/math/books", {"book": "Zhou", "problem": "15", "confidence": 1, "topic_id": ""})
    do("/api/math/books", {"book": " Joshi ", "section": "", "problem": "3", "confidence": 4, "answer": "\u00e9t\u00e9 \u2014 ok"})
    for body, status in ((book, 409), ({**book, "problem": "16", "text": "quoted"}, 400), ({**book, "problem": "16", "topic_id": "nope"}, 404),
                         ({**book, "problem": "16", "confidence": 0}, 400), ({**book, "problem": "16", "book": ""}, 400),
                         ({**book, "problem": "16", "book": "a\nb"}, 400), ({**book, "problem": "16", "section": "s" * 61}, 400),
                         ({"book": "Zhou", "confidence": 2}, 400), ({**book, "problem": "16", "answer": "a" * 201}, 400),
                         ({**book, "problem": 16}, 400), ({**book, "problem": "16", "topic_id": 5}, 404)):
        do("/api/math/books", body, expect=status)
    do("/api/math/books")
    entry_id = entry["id"]
    do(f"/api/math/books/{entry_id}", {"section": "2.4"})
    for body, status in (({}, 400), ({"bogus": 1}, 400), ({"problem": "15", "section": ""}, 409), ({"book": "  "}, 400)):
        do(f"/api/math/books/{entry_id}", body, expect=status)
    do(f"/api/math/books/{'1' * 32}", {"section": "x"}, expect=404)
    do(f"/api/math/books/{entry_id}/ratings", {"rating": 4, "answer": "2/7"})
    do(f"/api/math/books/{entry_id}/ratings", {"rating": 1})
    for body in ({"rating": 0}, {"answer": "x"}, {"rating": 2, "answer": 5}, {"rating": 2, "x": 1}):
        do(f"/api/math/books/{entry_id}/ratings", body, expect=400)
    do(f"/api/math/books/{entry_id}/delete", {"x": 1}, expect=400)
    do(f"/api/math/books/{entry_id}/delete", {})
    do(f"/api/math/books/{entry_id}/delete", {}, expect=404)

    # Time passes: reviews fall due, implicit credit, mixed review with books and the textbook.
    run.later(days=10)
    do("/api/math/topics")
    do("/api/math/next")
    do("/api/math/mixed")
    for topic, elapsed in (("arith.add-sub", 1000), ("arith.times-tables", 60000), ("arith.mul-2x1", 8000), ("arith.mul-2x1", 8000)):
        problem = do("/api/math/problems", {"topic_id": topic})
        answer(problem, exact(problem), elapsed)
    master("arith.mul-2x1")
    run.later(days=400)
    do("/api/math/topics")
    do("/api/math/next")
    do("/api/math/mixed")
    do("/api/math/books")
    do("/api/math/textbook", topic_id=self_graded.topics[0])
    do("/api/math/lessons/arith.mul-2x2")
    return run.answers


def disabled_scenario(call):
    """Without the math module: every route's 403, after the refusals of a query and a body."""
    run = Session(call)
    for path in ("/api/math/topics", "/api/math/next", "/api/math/mixed", "/api/math/books", "/api/math/sprints/history",
                 "/api/math/textbook", "/api/math/textbook/gs.1.1.1", "/api/math/lessons/arith.add-sub", "/api/math", "/api/math/nothing"):
        run.do(path, expect=403)
    run.do("/api/math/topics", expect=403, x="1")
    for path, body in (("/api/math/problems", {"topic_id": "arith.add-sub"}), ("/api/math/answers", {"problem_id": "0" * 32}),
                       ("/api/math/sprints", {}), (f"/api/math/sprints/{'0' * 32}/result", {"score": 1}),
                       ("/api/math/textbook/problems", {"exercise_id": "gs.1.2.6"}), ("/api/math/textbook/answers", {}),
                       ("/api/math/textbook/ratings", {}), ("/api/math/books", {}), (f"/api/math/books/{'0' * 32}", {}),
                       (f"/api/math/books/{'0' * 32}/ratings", {}), (f"/api/math/books/{'0' * 32}/delete", {})):
        run.do(path, body, expect=403)
        run.do(path, body, expect=400, q="1")
    return run.answers


def compare(workspace, tmp_path, monkeypatch, run=scenario, modules=MODULE_IDS):
    if modules != MODULE_IDS:
        (workspace.settings.root / "config" / "build.json").write_text(json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    graph.sync(workspace.conn)
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    copy = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, copy)
    monkeypatch.setattr(mastery, "now", lambda: Clock.value)
    draws = Draws()
    monkeypatch.setattr(practice, "secrets", draws)
    monkeypatch.setattr(sprints, "secrets", draws)

    reference = Reference(copy, modules)
    front = Through(workspace.settings, tmp_path / "clock")
    try:
        expected = run(reference)
        answered = run(front)
    finally:
        front.close()
        reference.conn.close()
    left, right = Masked(), Masked()
    assert len(answered) == len(expected)
    for (path, status, answer), (_, wanted_status, wanted) in zip(answered, expected, strict=True):
        assert (status, left(answer)) == (wanted_status, right(wanted)), path
    stored, events = tables(workspace.settings.db_path, left)
    reference_rows, reference_events = tables(copy, right)
    for table in TABLES:
        assert stored[table] == reference_rows[table], table
    assert events == reference_events == []
    return expected, stored


def test_the_front_answers_math_as_the_python_core_does(workspace, tmp_path, monkeypatch):
    expected, stored = compare(workspace, tmp_path, monkeypatch)
    ok = [(path, answer) for path, status, answer in expected if status == 200]
    # The scenario reached what it is about: every verdict kind, reveals, SymPy, reviews, credit.
    verdicts = [answer for path, answer in ok if path.endswith("/answers")]
    assert {verdict["kind"] for verdict in verdicts} == {"equal", "mistake", "different", "undecided"}
    assert any("n" in verdict.get("canonical", "") for verdict in verdicts) and any("x^{" in verdict.get("canonical", "") for verdict in verdicts)
    lessons = [answer for path, answer in ok if "/lessons/" in path]
    assert any("x^{" in lesson["example"]["answer_md"] for lesson in lessons) and all(lesson["guided"] for lesson in lessons)
    assert any(row["implicit_reviews"] for row in stored["math_mastery"])
    assert {mastery.AGAIN, mastery.HARD, mastery.EASY} <= {row["rating"] for row in stored["math_attempts"]}
    mixes = [answer for path, answer in ok if path == "/api/math/mixed"]
    assert mixes[-1]["default"] is True and {item["kind"] for item in mixes[-1]["items"]} >= {"topic", "textbook", "book"}
    assert len(ok) > 150


def test_without_the_math_module_every_route_is_refused(workspace, tmp_path, monkeypatch):
    expected, stored = compare(workspace, tmp_path, monkeypatch, disabled_scenario, ("applyai", "workspace"))
    assert "disabled" in next(answer for _, status, answer in expected if status == 403)["error"]
    assert not stored["math_attempts"] and not stored["math_sprints"]


def test_the_worker_answers_no_moved_route_and_keeps_explain_and_sympy(workspace, tmp_path):
    """No Python handler is left for a route the front answers; explain is the worker's, behind
    the front, and its SymPy route is not a page's."""
    graph.sync(workspace.conn)
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for path, query in (("/api/math/topics", {}), ("/api/math/textbook", {}), ("/api/math/lessons/arith.add-sub", {})):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(workspace.conn, path, query)
        for path, body in (("/api/math/problems", {"topic_id": "arith.add-sub"}), ("/api/math/books", {"book": "B", "problem": "1", "confidence": 1})):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(workspace.conn, path, body)
    finally:
        app.close()
    workspace.conn.commit()
    with front_site(workspace.settings) as site:
        status, problem = site.request("/api/math/problems", {"topic_id": "arith.times-tables", "seed": 11})
        assert status == 200
        body = {"problem_id": problem["problem_id"], "working": "17 x 16 as 17 x 10 + 6"}
        assert site.request("/api/math/explain", body) == (409, {"error": "Explain my mistake is offered after a wrong answer"})
        site.request("/api/math/answers", {"problem_id": problem["problem_id"], "answer": "7", "elapsed_ms": 3000})
        status, refused = site.request("/api/math/explain", body)
        assert status == 409 and "OpenRouter" in refused["error"]
        sympy = {"expected": "x**2", "variables": ["x"], "up_to_constant": False, "mistakes": [], "answer": "x^2"}
        assert site.request("/api/internal/math/expression", sympy)[0] == 404
        assert site.request("/api/math/topics")[0] == 200

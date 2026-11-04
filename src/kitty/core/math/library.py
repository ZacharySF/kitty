"""Book sources on their own FSRS schedules: Grinstead & Snell exercises and problems from
the learner's own books.

- **Textbook.** Exercises come from :mod:`.textbook` (GNU FDL data outside the MIT tree).
  One with an authored numeric answer is issued and graded like a topic problem: three
  tries, the first decided answer rates its card by correctness and time. A self-graded
  one (no numeric answer) is rated Again/Hard/Good/Easy by the learner after a reveal.
  Rows here hold exercise ids, never the book's text, and nothing here goes to AI.
- **Own books.** A problem is logged by reference only (book, section, problem number),
  with the learner's answer and confidence. Confidence is the card's first rating, so a
  shaky answer comes back sooner; each later review is a rating of its own.

A card's ``reviews`` counts every rating; ``lapses`` counts Again after the first.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime

from kitty.core.math import checking, mastery
from kitty.core.math import topics as graph
from kitty.core.math.practice import MAX_TRIES, Conflict, NotFound
from kitty.db.connection import transaction

#: Confidence when logging an own-book problem, as its first FSRS rating.
CONFIDENCE = {1: "Couldn't do it", 2: "Shaky", 3: "Confident", 4: "Certain"}
LIMITS = {"book": (1, 120), "section": (0, 60), "problem": (1, 40), "answer": (0, 200)}
_FIELDS = ("book", "section", "problem", "answer", "topic_id")


def _now(moment: datetime | None) -> datetime:
    return moment or mastery.now()


def _rating(value, name: str = "rating") -> int:
    if type(value) is not int or value not in CONFIDENCE:
        raise ValueError(f"{name} must be 1 (Again), 2 (Hard), 3 (Good) or 4 (Easy)")
    return value


def _card(row, moment: datetime) -> dict:
    return {"due_at": row["due_at"], "due": datetime.fromisoformat(row["due_at"]) <= moment,
            "reviews": row["reviews"], "lapses": row["lapses"], "last_rating": row["last_rating"]}


def _scheduled(card_json: str | None, rating: int, moment: datetime) -> tuple[str, str]:
    card = mastery._review(card_json, rating, moment)
    return card.to_json(), card.due.isoformat()


# Textbook ---------------------------------------------------------------------------------

def _exercise(exercise_id):
    from kitty.core.math import textbook

    if not isinstance(exercise_id, str):
        raise ValueError("exercise_id must be text")
    try:
        return textbook.get(exercise_id)
    except KeyError:
        raise NotFound("Unknown textbook exercise") from None


def _textbook_cards(conn) -> dict[str, sqlite3.Row]:
    return {row["exercise_id"]: row for row in conn.execute("SELECT * FROM math_textbook_cards")}


def _summary(exercise, card, moment: datetime) -> dict:
    return {"id": exercise.id, "label": exercise.label, "section": exercise.section, "section_title": exercise.section_title,
            "topics": list(exercise.topics), "graded": exercise.graded, "program": exercise.program,
            "figure": exercise.figure, "starred": exercise.starred, "card": _card(card, moment) if card else None}


def source() -> dict:
    from kitty.core.math import textbook

    return {key: textbook.SOURCE[key] for key in ("title", "short", "version", "licence", "url")}


def textbook_list(conn: sqlite3.Connection, query: dict, moment: datetime | None = None) -> dict:
    """Every exercise, or a topic's: never reviewed first, then due, then the rest."""
    from kitty.core.math import textbook

    moment = _now(moment)
    if set(query) - {"topic_id"}:
        raise ValueError("Unknown textbook parameter")
    topic_id = query.get("topic_id")
    if topic_id is None:
        chosen = list(textbook.exercises())
    elif topic_id in graph.BY_ID:
        chosen = textbook.for_topic(topic_id)
    else:
        raise NotFound("Unknown math topic")
    cards = _textbook_cards(conn)
    if topic_id is not None:
        def rank(exercise):
            card = cards.get(exercise.id)
            return 0 if card is None else 1 if datetime.fromisoformat(card["due_at"]) <= moment else 2
        chosen.sort(key=rank)  # stable: book order within each group
    return {"source": source(), "exercises": [_summary(item, cards.get(item.id), moment) for item in chosen]}


def textbook_item(conn: sqlite3.Connection, exercise_id, moment: datetime | None = None) -> dict:
    exercise = _exercise(exercise_id)
    card = conn.execute("SELECT * FROM math_textbook_cards WHERE exercise_id=?", (exercise.id,)).fetchone()
    out = _summary(exercise, card, _now(moment))
    out.update(text_md=exercise.text_md, part=exercise.answer.part if exercise.answer else None, source=source())
    return out


def _rate_textbook(conn, exercise_id: str, rating: int, moment: datetime) -> sqlite3.Row:
    """Apply one rating to an exercise's card inside the caller's transaction."""
    stamp = moment.isoformat()
    row = conn.execute("SELECT * FROM math_textbook_cards WHERE exercise_id=?", (exercise_id,)).fetchone()
    card_json, due_at = _scheduled(row["fsrs"] if row else None, rating, moment)
    if row is None:
        conn.execute("INSERT INTO math_textbook_cards(exercise_id, fsrs, due_at, reviews, last_rating, created_at, updated_at) "
                     "VALUES (?,?,?,1,?,?,?)", (exercise_id, card_json, due_at, rating, stamp, stamp))
    else:
        conn.execute("UPDATE math_textbook_cards SET fsrs=?, due_at=?, reviews=reviews+1, lapses=lapses+?, last_rating=?, "
                     "updated_at=? WHERE exercise_id=?",
                     (card_json, due_at, int(rating == mastery.AGAIN), rating, stamp, exercise_id))
    return conn.execute("SELECT * FROM math_textbook_cards WHERE exercise_id=?", (exercise_id,)).fetchone()


def textbook_rate(conn: sqlite3.Connection, body, moment: datetime | None = None) -> dict:
    """A self-graded exercise's rating after the reveal."""
    moment = _now(moment)
    if not isinstance(body, dict) or set(body) != {"exercise_id", "rating"}:
        raise ValueError("Expected exercise_id and rating")
    exercise = _exercise(body["exercise_id"])
    rating = _rating(body["rating"])
    if exercise.answer is not None:
        raise Conflict("This exercise has an answer to check; answer it instead")
    with transaction(conn, immediate=True):
        row = _rate_textbook(conn, exercise.id, rating, moment)
    return {"card": _card(row, moment)}


def textbook_issue(conn: sqlite3.Connection, exercise_id, moment: datetime | None = None) -> dict:
    from kitty.core.math import textbook

    moment = _now(moment)
    exercise = _exercise(exercise_id)
    if exercise.answer is None:
        raise Conflict("This exercise is self-graded; rate it after the reveal")
    problem = textbook.problem(exercise)
    ident = secrets.token_hex(16)
    with transaction(conn):
        conn.execute("INSERT INTO math_textbook_attempts(id, exercise_id, created_at) VALUES (?,?,?)",
                     (ident, exercise.id, moment.isoformat()))
    out = {"problem_id": ident, "exercise_id": exercise.id, "prompt_md": problem.prompt_md, "answer_kind": problem.answer_kind}
    if problem.sigfigs is not None:
        out["sigfigs"] = problem.sigfigs
    if problem.tolerance is not None:
        out["tolerance"] = float(problem.tolerance)
    return out


def textbook_answer(conn: sqlite3.Connection, problem_id, value, elapsed_ms, moment: datetime | None = None) -> dict:
    """Check one try; the first decided answer rates the exercise's card."""
    from kitty.core.math import textbook

    moment = _now(moment)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("answer must be text or a number")
    if type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 86_400_000:
        raise ValueError("elapsed_ms must be an integer from 0 to 86400000")
    if not isinstance(problem_id, str):
        raise ValueError("problem_id must be text")
    row = conn.execute("SELECT * FROM math_textbook_attempts WHERE id=?", (problem_id,)).fetchone()
    if row is None:
        raise NotFound("Unknown textbook problem")
    entries = json.loads(row["answers"])
    decided = [entry for entry in entries if entry["kind"] != "undecided"]
    if row["solved_at"] or len(decided) >= MAX_TRIES:
        raise Conflict("This exercise is finished; start it again for another try")
    problem = textbook.problem(_exercise(row["exercise_id"]))
    verdict = checking.check(problem, value)
    entry = {"answer": value if not isinstance(value, str) else value[:checking.MAX_ANSWER], "kind": verdict.kind,
             "feedback": verdict.feedback, "elapsed_ms": elapsed_ms, "at": moment.isoformat()}
    with transaction(conn, immediate=True):
        current = conn.execute("SELECT answers, graded_at FROM math_textbook_attempts WHERE id=?", (problem_id,)).fetchone()
        if current["answers"] != row["answers"]:
            raise Conflict("This exercise was answered in another window; reload it")
        entries.append(entry)
        updates = {"answers": json.dumps(entries)}
        if verdict.correct is not None and current["graded_at"] is None:
            rating = mastery.rating(verdict.correct, elapsed_ms, problem.target_s)
            updates.update(correct=int(verdict.correct), elapsed_ms=elapsed_ms, rating=rating, graded_at=moment.isoformat())
            _rate_textbook(conn, row["exercise_id"], rating, moment)
        if verdict.correct:
            updates["solved_at"] = moment.isoformat()
        conn.execute(f"UPDATE math_textbook_attempts SET {', '.join(f'{key}=?' for key in updates)} WHERE id=?",
                     (*updates.values(), problem_id))
        card = conn.execute("SELECT * FROM math_textbook_cards WHERE exercise_id=?", (row["exercise_id"],)).fetchone()
    tries = len(decided) + (verdict.correct is not None)
    revealed = bool(verdict.correct) or (verdict.correct is False and tries >= MAX_TRIES)
    out = {"correct": verdict.correct, "kind": verdict.kind, "tries_left": 0 if verdict.correct else MAX_TRIES - tries,
           "revealed": revealed, "card": _card(card, moment) if card else None}
    if verdict.feedback:
        out["feedback"] = verdict.feedback
    if revealed:
        out["canonical"] = problem.canonical
    return out


def due_textbook(conn: sqlite3.Connection, moment: datetime) -> list[tuple]:
    """(exercise, card row) for every due textbook card, oldest due first."""
    rows = conn.execute("SELECT * FROM math_textbook_cards WHERE due_at <= ? ORDER BY due_at", (moment.isoformat(),)).fetchall()
    if not rows:
        return []  # the book's data loads only once it is in use
    from kitty.core.math import textbook

    out = []
    for row in rows:
        try:
            out.append((textbook.get(row["exercise_id"]), row))
        except KeyError:
            continue  # an exercise dropped from a later edition of the data
    return out


# Own books --------------------------------------------------------------------------------

def _text(name: str, value) -> str:
    low, high = LIMITS[name]
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = value.strip()
    if not low <= len(value) <= high:
        raise ValueError(f"{name} must be {low}–{high} characters" if low else f"{name} must be at most {high} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must be one line without control characters")
    return value


def _topic_id(value) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in graph.BY_ID:
        raise NotFound("Unknown math topic")
    return value


def _clean(body: dict) -> dict:
    out = {}
    for name in _FIELDS:
        if name in body:
            out[name] = _topic_id(body[name]) if name == "topic_id" else _text(name, body[name])
    return out


def _entry(row, moment: datetime) -> dict:
    topic = graph.BY_ID.get(row["topic_id"]) if row["topic_id"] else None
    return {"id": row["id"], "book": row["book"], "section": row["section"], "problem": row["problem"], "answer": row["answer"],
            "confidence": row["confidence"], "topic_id": row["topic_id"], "topic_title": topic.title if topic else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"], "card": _card(row, moment)}


def _book_row(conn, entry_id):
    if not isinstance(entry_id, str):
        raise ValueError("id must be text")
    row = conn.execute("SELECT * FROM math_book_problems WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        raise NotFound("Unknown book problem")
    return row


def _duplicate() -> Conflict:
    return Conflict("That problem is already logged; review it instead")


def books(conn: sqlite3.Connection, moment: datetime | None = None) -> dict:
    moment = _now(moment)
    rows = conn.execute("SELECT * FROM math_book_problems ORDER BY due_at, created_at").fetchall()
    names = [row[0] for row in conn.execute(
        "SELECT book FROM math_book_problems GROUP BY book ORDER BY count(*) DESC, max(updated_at) DESC, book")]
    problems = [_entry(row, moment) for row in rows]
    return {"problems": problems, "books": names, "due": sum(item["card"]["due"] for item in problems)}


def add_book_problem(conn: sqlite3.Connection, body, moment: datetime | None = None) -> dict:
    moment = _now(moment)
    allowed = {*_FIELDS, "confidence"}
    if not isinstance(body, dict) or set(body) - allowed or {"book", "problem", "confidence"} - set(body):
        raise ValueError("Expected book, problem, confidence and optional section, answer and topic_id")
    fields = {"section": "", "answer": "", "topic_id": None, **_clean(body)}
    confidence = _rating(body["confidence"], "confidence")
    card_json, due_at = _scheduled(None, confidence, moment)
    ident, stamp = secrets.token_hex(16), moment.isoformat()
    try:
        with transaction(conn, immediate=True):
            conn.execute("INSERT INTO math_book_problems(id, book, section, problem, answer, confidence, topic_id, fsrs, due_at, "
                         "reviews, last_rating, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
                         (ident, fields["book"], fields["section"], fields["problem"], fields["answer"], confidence,
                          fields["topic_id"], card_json, due_at, confidence, stamp, stamp))
    except sqlite3.IntegrityError:
        raise _duplicate() from None
    return _entry(_book_row(conn, ident), moment)


def edit_book_problem(conn: sqlite3.Connection, entry_id, body, moment: datetime | None = None) -> dict:
    moment = _now(moment)
    if not isinstance(body, dict) or not body or set(body) - set(_FIELDS):
        raise ValueError("Expected some of book, section, problem, answer and topic_id")
    _book_row(conn, entry_id)
    fields = _clean(body)
    try:
        with transaction(conn, immediate=True):
            conn.execute(f"UPDATE math_book_problems SET {', '.join(f'{key}=?' for key in fields)}, updated_at=? WHERE id=?",
                         (*fields.values(), moment.isoformat(), entry_id))
    except sqlite3.IntegrityError:
        raise _duplicate() from None
    return _entry(_book_row(conn, entry_id), moment)


def rate_book_problem(conn: sqlite3.Connection, entry_id, body, moment: datetime | None = None) -> dict:
    moment = _now(moment)
    if not isinstance(body, dict) or "rating" not in body or set(body) - {"rating", "answer"}:
        raise ValueError("Expected rating and an optional answer")
    rating = _rating(body["rating"])
    answer = _text("answer", body["answer"]) if "answer" in body else None
    row = _book_row(conn, entry_id)
    card_json, due_at = _scheduled(row["fsrs"], rating, moment)
    with transaction(conn, immediate=True):
        conn.execute("UPDATE math_book_problems SET fsrs=?, due_at=?, reviews=reviews+1, lapses=lapses+?, last_rating=?, "
                     "answer=coalesce(?, answer), updated_at=? WHERE id=?",
                     (card_json, due_at, int(rating == mastery.AGAIN), rating, answer, moment.isoformat(), entry_id))
    return _entry(_book_row(conn, entry_id), moment)


def delete_book_problem(conn: sqlite3.Connection, entry_id) -> dict:
    _book_row(conn, entry_id)
    with transaction(conn, immediate=True):
        conn.execute("DELETE FROM math_book_problems WHERE id=?", (entry_id,))
    return {"ok": True}


def due_books(conn: sqlite3.Connection, moment: datetime) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM math_book_problems WHERE due_at <= ? ORDER BY due_at", (moment.isoformat(),)).fetchall()


def book_label(row) -> str:
    """'Zhou §2.3 #14': the reference, nothing quoted."""
    return " ".join(part for part in (row["book"], f"§{row['section']}" if row["section"] else "", f"#{row['problem']}") if part)

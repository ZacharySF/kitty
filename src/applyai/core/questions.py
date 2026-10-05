"""The unanswered-questions queue.

The queue records questions I hit on application forms. Answers themselves live
in config/answers.yaml, which I write: nothing here ever generates an answer.

`question_jobs` links each question to the jobs that ask it, so "how many of my
jobs ask this" is a count of distinct jobs and re-reading a form never inflates it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from applyai.config import Config
from applyai.dates import now_iso
from applyai.models import ScreeningQuestion
from applyai.normalize import question_hash


@dataclass(frozen=True)
class Recorded:
    """A stored question plus whether this call added it or its job link."""

    question: ScreeningQuestion
    new_question: bool
    new_link: bool


def _upsert_question(conn: sqlite3.Connection, text: str, job_id: int | None) -> tuple[ScreeningQuestion, bool]:
    """The question row for `text`, inserting it (times_seen 1) when new."""
    digest = question_hash(text)
    timestamp = now_iso()
    inserted = conn.execute(
        """
        INSERT INTO screening_questions (text, norm_hash, first_seen_job_id, times_seen,
                                        answered, created_at, updated_at)
        VALUES (?, ?, ?, 1, 0, ?, ?)
        ON CONFLICT(norm_hash) DO NOTHING
        """,
        (text.strip(), digest, job_id, timestamp, timestamp),
    ).rowcount == 1
    row = conn.execute("SELECT * FROM screening_questions WHERE norm_hash = ?", (digest,)).fetchone()
    return ScreeningQuestion.from_row(row), inserted


def _bump(conn: sqlite3.Connection, question_id: int) -> None:
    conn.execute(
        "UPDATE screening_questions SET times_seen = times_seen + 1, updated_at = ? WHERE id = ?",
        (now_iso(), question_id),
    )


def _link(conn: sqlite3.Connection, question_id: int, job_id: int) -> bool:
    """Link a question to a job; True when the pair is new."""
    return conn.execute(
        "INSERT OR IGNORE INTO question_jobs (question_id, job_id, seen_at) VALUES (?, ?, ?)",
        (question_id, job_id, now_iso()),
    ).rowcount == 1


def log_question(
    conn: sqlite3.Connection, text: str, *, job_id: int | None = None
) -> ScreeningQuestion:
    """Record a screening question I was asked; repeats bump times_seen."""
    question, inserted = _upsert_question(conn, text, job_id)
    if not inserted:
        _bump(conn, question.id)
    if job_id is not None:
        _link(conn, question.id, job_id)
    row = conn.execute("SELECT * FROM screening_questions WHERE id = ?", (question.id,)).fetchone()
    return ScreeningQuestion.from_row(row)


def record_form_question(
    conn: sqlite3.Connection,
    text: str,
    job_id: int,
    *,
    field_kind: str,
    required: bool,
    choices: tuple[str, ...],
    demographic: bool,
) -> Recorded:
    """Store a question read from a job's form, with that form's choices.

    times_seen only grows the first time this job is linked, so collecting the
    same forms again changes nothing but the stored choices.
    """
    question, inserted = _upsert_question(conn, text, job_id)
    new_link = _link(conn, question.id, job_id)
    if new_link and not inserted:
        _bump(conn, question.id)
    conn.execute(
        """
        UPDATE question_jobs SET field_kind = ?, required = ?, choices = ?, demographic = ?, seen_at = ?
        WHERE question_id = ? AND job_id = ?
        """,
        (field_kind, int(required), json.dumps(list(choices)) if choices else None,
         int(demographic), now_iso(), question.id, job_id),
    )
    return Recorded(question, inserted, new_link)


def list_questions(
    conn: sqlite3.Connection, *, unanswered_only: bool = True
) -> list[ScreeningQuestion]:
    """Questions in the queue, most-seen first."""
    sql = "SELECT * FROM screening_questions"
    if unanswered_only:
        sql += " WHERE answered = 0"
    sql += " ORDER BY answered, times_seen DESC, id"
    return [ScreeningQuestion.from_row(row) for row in conn.execute(sql)]


def mark_answered(conn: sqlite3.Connection, question_id: int, answered: bool = True) -> None:
    """Flag a question as answered in answers.yaml (or not, on a re-check)."""
    conn.execute(
        "UPDATE screening_questions SET answered = ?, updated_at = ? WHERE id = ?",
        (int(answered), now_iso(), question_id),
    )


def refresh_answered_flags(conn: sqlite3.Connection, config: Config) -> int:
    """Re-check the queue against answers.yaml; return how many are now answered.

    Run after I edit answers.yaml: anything the file now covers drops out of the
    queue without my touching the database.
    """
    answered = 0
    for question in list_questions(conn, unanswered_only=False):
        found = config.answer_for(question.text) is not None
        if found != question.answered:
            mark_answered(conn, question.id, found)
        answered += int(found)
    return answered

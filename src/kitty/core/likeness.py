"""More like these: how much each job looks like my favorites, minus my skips.

Every job becomes a TF-IDF vector built from three fields, each normalised on
its own and then weighted, so a long description cannot drown out the title:

    title  words of the job title
    jd     words of the job description
    meta   the company and the job's tracks

A job's score is its cosine similarity to the average of my favorites' vectors,
minus its similarity to the average of the jobs I turned down by hand (skipped,
or marked not eligible by hand). Plain Python and deterministic: the same
database always gives the same scores.

Scores are stored (jobs.like_score, jobs.like_terms) and recomputed after a
favorite, unfavorite or hand skip, and at the end of every discover. Best-first
ordering reads them after eligibility, tier and watchlist priority and before
place and age (core/jobs._best_order), so they never beat a rule. With no
favorites every score is NULL and the ordering is exactly what it was before.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from typing import Iterable

from kitty.core.jobs import HAND_NOT_ELIGIBLE
from kitty.db.connection import transaction

#: How much each field counts in a job's vector (they sum to 1).
FIELD_WEIGHTS: dict[str, float] = {"title": 0.5, "jd": 0.3, "meta": 0.2}
#: How many shared terms a job keeps to explain its score.
TOP_TERMS = 5

_WORD = re.compile(r"[a-z][a-z0-9+#]+")

#: Words too common in postings to say anything about a job.
STOPWORDS = frozenset("""
a about all also an and any are as at be been but by can do for from has have how if in
into is it its may more most must not of on or our out over so such than that the their
them then there these they this those through to up us was we were what when where which
while who will with within without would you your yours intern interns internship
internships summer fall spring winter co op coop student students
""".split())


def tokens(text: str | None) -> list[str]:
    """Lower-case words of two or more characters, stopwords left out."""
    return [word for word in _WORD.findall((text or "").lower()) if word not in STOPWORDS]


def _documents(conn: sqlite3.Connection) -> dict[int, dict[str, Counter[str]]]:
    """Every job's term counts per field, in job id order."""
    tracks: dict[int, list[str]] = {}
    for row in conn.execute("SELECT job_id, track FROM job_tracks ORDER BY job_id, track"):
        tracks.setdefault(row["job_id"], []).append(row["track"])
    docs: dict[int, dict[str, Counter[str]]] = {}
    for row in conn.execute(
        "SELECT j.id, j.title, j.jd_text, c.display_name FROM jobs j "
        "JOIN companies c ON c.id = j.company_id ORDER BY j.id"
    ):
        meta = [f"company {row['display_name']}"] + [f"track {name}" for name in tracks.get(row["id"], [])]
        docs[row["id"]] = {
            "title": Counter(tokens(row["title"])),
            "jd": Counter(tokens(row["jd_text"])),
            "meta": Counter(meta),
        }
    return docs


def _normalised(vector: dict[str, float]) -> dict[str, float]:
    """`vector` scaled to length 1 (empty stays empty)."""
    length = math.sqrt(sum(value * value for value in vector.values()))
    return {key: value / length for key, value in vector.items()} if length else {}


def vectors(docs: dict[int, dict[str, Counter[str]]]) -> dict[int, dict[str, float]]:
    """One weighted TF-IDF vector per job; keys are "field:term".

    idf = log(N / df), so a term every job has counts for nothing; tf is
    1 + log(count), so a word repeated twenty times is not twenty times as telling.
    """
    total = len(docs)
    document_frequency: dict[str, Counter[str]] = {name: Counter() for name in FIELD_WEIGHTS}
    for fields in docs.values():
        for name, counts in fields.items():
            document_frequency[name].update(counts.keys())
    result: dict[int, dict[str, float]] = {}
    for job_id, fields in docs.items():
        combined: dict[str, float] = {}
        for name, counts in fields.items():
            weights = {
                term: (1 + math.log(count)) * math.log(total / document_frequency[name][term])
                for term, count in counts.items()
            }
            for term, value in _normalised(weights).items():
                combined[f"{name}:{term}"] = FIELD_WEIGHTS[name] * value
        result[job_id] = _normalised(combined)
    return result


def centroid(vecs: dict[int, dict[str, float]], ids: Iterable[int]) -> dict[str, float]:
    """The normalised average of these jobs' vectors."""
    total: dict[str, float] = {}
    for job_id in ids:
        for key, value in vecs.get(job_id, {}).items():
            total[key] = total.get(key, 0.0) + value
    return _normalised(total)


def cosine(vector: dict[str, float], other: dict[str, float]) -> float:
    """Cosine similarity of two normalised vectors."""
    if len(other) < len(vector):
        vector, other = other, vector
    return sum(value * other.get(key, 0.0) for key, value in vector.items())


def shared_terms(vector: dict[str, float], favorites: dict[str, float], limit: int = TOP_TERMS) -> list[str]:
    """The terms that add most to this job's similarity with my favorites, readable."""
    contributions: dict[str, float] = {}
    for key, value in vector.items():
        weight = value * favorites.get(key, 0.0)
        if weight > 0:
            term = key.split(":", 1)[1]                # "title:fpga" -> "fpga"
            contributions[term] = contributions.get(term, 0.0) + weight
    ranked = sorted(contributions.items(), key=lambda item: (-item[1], item[0]))
    return [term for term, _ in ranked[:limit]]


def _ids(conn: sqlite3.Connection, where: str, params: tuple = ()) -> list[int]:
    return [row["id"] for row in conn.execute(f"SELECT id FROM jobs WHERE {where} ORDER BY id", params)]


def _store(conn: sqlite3.Connection, rows: list[tuple[float, str | None, int]]) -> None:
    """Write (score, terms, job id) rows; with no rows, clear every score."""
    if rows:
        conn.executemany("UPDATE jobs SET like_score = ?, like_terms = ? WHERE id = ?", rows)
    else:
        conn.execute("UPDATE jobs SET like_score = NULL, like_terms = NULL "
                     "WHERE like_score IS NOT NULL OR like_terms IS NOT NULL")


def _scores(conn: sqlite3.Connection, favorites: list[int]) -> list[tuple[float, str | None, int]]:
    """(score, terms as json, job id) for every job, given my favorites."""
    disliked = _ids(
        conn,
        "favorited_at IS NULL AND (queue_state = 'skipped' OR eligibility_reason = ?)",
        (HAND_NOT_ELIGIBLE,),
    )
    vecs = vectors(_documents(conn))
    liked_centre = centroid(vecs, favorites)
    disliked_centre = centroid(vecs, disliked)
    rows = []
    for job_id, vector in vecs.items():
        score = cosine(vector, liked_centre) - cosine(vector, disliked_centre)
        terms = shared_terms(vector, liked_centre)
        rows.append((round(score, 4), json.dumps(terms) if terms else None, job_id))
    return rows


def rescore(conn: sqlite3.Connection) -> int:
    """Recompute and store every job's like score; return the number of favorites.

    Takes about half a second on two thousand jobs, so callers run it once
    after a batch of changes, not per job. Joins the caller's transaction when
    one is open.
    """
    favorites = _ids(conn, "favorited_at IS NOT NULL")
    rows = _scores(conn, favorites) if favorites else []
    if conn.in_transaction:
        _store(conn, rows)
    else:
        with transaction(conn):
            _store(conn, rows)
    return len(favorites)

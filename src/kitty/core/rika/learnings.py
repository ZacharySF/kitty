"""Rika's career memory (migration 065): what the user's job search and resume reviews teach.

Deterministic and local; nothing here calls a model. :func:`refresh` derives three kinds of
learning, rebuilding them only when its inputs' signature changed (so it is cheap before
every Rika turn and every Workshop preview):

* ``posting_skill``: the config/resume_terms.yaml vocabulary terms (the resume coach's
  matcher) that the ApplyAI postings the user acted on ask for, weighted by what the user
  did (an interview or offer > applied > queued or starred > review later; skipping counts
  against), halved every :data:`HALF_LIFE_DAYS` days, with the job ids as evidence and
  whether a confirmed Resume Workshop fact backs the term;
* ``posting_pattern``: seasons, remote work, locations, tracks, seniority words and
  companies the user keeps choosing (at least :data:`PATTERN_MIN` postings);
* ``resume_edit``: the Workshop's review record (``workshop.ai.review_groups``, read from
  ``workshop_ai_reviews``, referenced by proposal id, never copied): edit reasons the user
  accepts or rejects.

``note`` rows are what the user asked Rika to remember. Pins and forgets are the user's: a
pinned learning stays even when its evidence goes, and a forgotten one stays as a tombstone
that refresh never revives, so it is never shown or used again (a forgotten note is
deleted). :func:`summary_for_prompt` is the compact text the Workshop preview and Rika's
``career_memory`` tool hand to a model, framed as observed patterns, never applicant facts.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Collection
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from kitty.config import ResumeTermsFile
from kitty.core import resume_coach
from kitty.core.rika import store
from kitty.db.connection import transaction

KINDS = ("posting_skill", "posting_pattern", "resume_edit", "note")
DERIVED = ("posting_skill", "posting_pattern", "resume_edit")
VERSION = "1"
#: How much one posting counts, by the strongest thing the user did with it.
SIGNALS = {"offer": 4.0, "interview": 4.0, "oa": 3.5, "applied": 3.0, "queued": 2.0, "favorite": 2.0, "later": 1.0}
#: A posting the user skipped counts against the terms it asks for.
SKIPPED = -1.0
HALF_LIFE_DAYS = 120
#: The postings read, newest signal first.
JOBS_MAX = 2000
SKILLS_MAX = 40
PATTERNS_MAX = 12
PATTERN_MIN = 2
EDITS_MAX = 30
NOTES_MAX = 40
NOTE_MAX = 500
EVIDENCE_MAX = 12
SUMMARY_CHARS = 1800
SUMMARY_SKILLS = 12
SUMMARY_PATTERNS = 6
SUMMARY_EDITS = 4
SUMMARY_NOTES = 10
FACETS = {"season": "Season", "remote": "Remote", "location": "Location", "track": "Track", "level": "Level",
          "company": "Company"}
_LEVELS = (("intern", re.compile(r"\bintern(?:ship)?s?\b", re.I)), ("co-op", re.compile(r"\bco-?op\b", re.I)),
           ("new grad", re.compile(r"\bnew[ -]grad(?:uate)?\b", re.I)), ("junior", re.compile(r"\b(?:junior|jr\.?)\b", re.I)),
           ("entry level", re.compile(r"\bentry[ -]level\b", re.I)), ("senior", re.compile(r"\b(?:senior|sr\.?)\b", re.I)),
           ("staff", re.compile(r"\bstaff\b", re.I)), ("principal", re.compile(r"\bprincipal\b", re.I)))
_FRAME = ("Rika's career memory, from the postings the user acted on and their resume reviews. These are observed "
          "patterns, not applicant facts: claim a skill only when a confirmed applicant fact backs it.")

_SIGNAL_JOBS = (
    "SELECT j.id, j.title, j.location, j.is_remote, j.season, j.jd_text, j.queue_state, j.favorited_at, j.updated_at,"
    " j.company_id, c.display_name AS company, a.status AS app_status, a.last_activity_at AS app_at"
    " FROM jobs j JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id"
    " WHERE a.id IS NOT NULL OR j.queue_state IN ('queued','later','skipped','applied') OR j.favorited_at IS NOT NULL"
    f" ORDER BY max(ifnull(a.last_activity_at,''), ifnull(j.favorited_at,''), j.updated_at) DESC LIMIT {JOBS_MAX}")


class Refused(ValueError):
    """A valid request the memory's current state refuses (a duplicate note, the note cap)."""


# Refresh ----------------------------------------------------------------------

def _signature(conn: sqlite3.Connection, vocabulary: ResumeTermsFile) -> str:
    digest = hashlib.sha256()
    digest.update(f"{VERSION}|{datetime.now(timezone.utc).date()}|".encode())
    digest.update(vocabulary.model_dump_json().encode())
    for query in (
        "SELECT j.id||':'||j.queue_state||':'||ifnull(j.favorited_at,'')||':'||j.updated_at||':'||ifnull(j.jd_hash,'')"
        " FROM jobs j WHERE j.queue_state IN ('queued','later','skipped','applied') OR j.favorited_at IS NOT NULL"
        " ORDER BY j.id",
        "SELECT job_id||':'||status||':'||last_activity_at||':'||updated_at FROM applications ORDER BY job_id",
        "SELECT id||':'||revision||':'||confirmed FROM workshop_facts ORDER BY id",
        "SELECT proposal_id||':'||created_at FROM workshop_ai_reviews ORDER BY proposal_id",
    ):
        for (row,) in conn.execute(query):
            digest.update(f"{row}\n".encode())
        digest.update(b"|")
    return digest.hexdigest()


def _age_days(stamp: str | None, today: datetime) -> float:
    try:
        when = datetime.fromisoformat(stamp or "")
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (today - when).total_seconds() / 86400)


def _signal(row: sqlite3.Row) -> float:
    found = [SIGNALS.get(row["app_status"], SIGNALS["applied"])] if row["app_status"] else []
    if row["queue_state"] in SIGNALS:
        found.append(SIGNALS[row["queue_state"]])
    if row["favorited_at"]:
        found.append(SIGNALS["favorite"])
    if found:
        return max(found)
    return SKIPPED if row["queue_state"] == "skipped" else 0.0


def _evidence(contributions: list[tuple[float, int]]) -> list[int]:
    return [job_id for _, job_id in sorted(contributions, key=lambda item: (-item[0], -item[1]))[:EVIDENCE_MAX]]


def _skills(rows, scores, matchers, facts_text) -> dict[tuple[str, str], dict]:
    weight: dict[int, float] = defaultdict(float)
    positive: dict[int, list[tuple[float, int]]] = defaultdict(list)
    against: Counter[int] = Counter()
    for row in rows:
        score = scores[row["id"]]
        if not score or not (row["jd_text"] or "").strip():
            continue
        posting = SimpleNamespace(jd_text=row["jd_text"], company_name=row["company"] or "")
        for index in resume_coach._scan(posting, matchers).hits:
            weight[index] += score
            if score > 0:
                positive[index].append((score, row["id"]))
            else:
                against[index] += 1
    facts = resume_coach.normalize_text(facts_text)
    lowered = facts.lower()
    ranked = sorted((index for index in positive if weight[index] > 0), key=lambda index: (-weight[index], index))
    found = {}
    for index in ranked[:SKILLS_MAX]:
        matcher = matchers[index]
        found[("posting_skill", matcher.term)] = {
            "text": matcher.term, "weight": weight[index], "evidence": _evidence(positive[index]),
            "detail": {"group": matcher.group, "postings": len(positive[index]), "against": against[index],
                       "fact": bool(facts and matcher.find(facts, lowered))}}
    return found


def _patterns(conn, rows, scores) -> dict[tuple[str, str], dict]:
    chosen = [row for row in rows if scores[row["id"]] > 0]
    tracks: dict[int, list[str]] = defaultdict(list)
    if chosen:
        marks = ",".join("?" * len(chosen))
        for job_id, track in conn.execute(f"SELECT job_id, track FROM job_tracks WHERE job_id IN ({marks})",
                                          [row["id"] for row in chosen]):
            tracks[job_id].append(track)
    facets: dict[tuple[str, str], dict] = {}

    def add(facet: str, value: str, label: str, row) -> None:
        entry = facets.setdefault((facet, value.lower()), {"label": label, "weight": 0.0, "jobs": []})
        entry["weight"] += scores[row["id"]]
        entry["jobs"].append((scores[row["id"]], row["id"]))

    for row in chosen:
        if (row["season"] or "").strip():
            add("season", row["season"].strip(), row["season"].strip(), row)
        if row["is_remote"] == 1:
            add("remote", "remote", "Remote", row)
        elif (row["location"] or "").strip():
            add("location", " ".join(row["location"].split()), " ".join(row["location"].split()), row)
        for track in tracks[row["id"]]:
            add("track", track, track, row)
        for level, pattern in _LEVELS:
            if pattern.search(row["title"] or ""):
                add("level", level, level.capitalize(), row)
        add("company", str(row["company_id"]), row["company"] or "", row)
    ranked = sorted(((key, entry) for key, entry in facets.items() if len(entry["jobs"]) >= PATTERN_MIN),
                    key=lambda item: (-item[1]["weight"], item[0]))
    return {("posting_pattern", f"{facet}:{value}" if facet != "remote" else "remote"): {
        "text": entry["label"], "weight": entry["weight"], "evidence": _evidence(entry["jobs"]),
        "detail": {"facet": facet, "postings": len(entry["jobs"])}} for (facet, value), entry in ranked[:PATTERNS_MAX]}


def edit_key(reason: str) -> str:
    """The resume_edit key of a Workshop review reason."""
    return hashlib.sha256(" ".join(reason.split()).lower().encode()).hexdigest()[:32]


def _edits(conn) -> dict[tuple[str, str], dict]:
    from kitty.core.workshop import ai  # ai imports this module for its previews.

    found = {}
    for group in ai.review_groups(conn)[:EDITS_MAX]:
        if not (group["accepted"] or group["rejected"]) or not group["text"].strip():
            continue
        found[("resume_edit", edit_key(group["text"]))] = {
            "text": " ".join(group["text"].split())[:NOTE_MAX], "weight": float(group["accepted"] - group["rejected"]),
            "evidence": group["proposals"][:EVIDENCE_MAX],
            "detail": {"accepted": group["accepted"], "rejected": group["rejected"]}}
    return found


def refresh(conn: sqlite3.Connection, vocabulary: ResumeTermsFile, *, modules: Collection[str] | None = None) -> bool:
    """Re-derive the posting and resume-edit learnings when their inputs changed; True when it ran.

    Career memory belongs to ApplyAI: with that module inactive nothing is touched.
    """
    if modules is not None and "applyai" not in modules:
        return False
    signature = _signature(conn, vocabulary)
    stored = conn.execute("SELECT signature FROM rika_learning_state WHERE id=1").fetchone()
    if stored is not None and stored[0] == signature:
        return False
    today = datetime.now(timezone.utc)
    rows = conn.execute(_SIGNAL_JOBS).fetchall()
    scores = {row["id"]: _signal(row) * 0.5 ** (_age_days(max(row["app_at"] or "", row["favorited_at"] or "",
                                                              row["updated_at"] or ""), today) / HALF_LIFE_DAYS)
              for row in rows}
    facts_text = "\n".join(text for (text,) in conn.execute("SELECT text FROM workshop_facts WHERE confirmed=1"))
    derived = {**_skills(rows, scores, resume_coach.compile_vocabulary(vocabulary), facts_text),
               **_patterns(conn, rows, scores), **_edits(conn)}
    stamp = store.now()
    with transaction(conn, immediate=True):
        existing = {(row["kind"], row["key"]): row for row in conn.execute(
            f"SELECT id, kind, key, pinned, forgotten FROM rika_learnings WHERE kind IN ({','.join('?' * len(DERIVED))})",
            DERIVED)}
        for (kind, key), item in derived.items():
            values = (item["text"], round(item["weight"], 4), json.dumps(item["evidence"]), json.dumps(item["detail"]))
            row = existing.get((kind, key))
            if row is None:
                conn.execute("INSERT INTO rika_learnings(kind,key,text,weight,evidence,detail,created_at,updated_at)"
                             " VALUES(?,?,?,?,?,?,?,?)", (kind, key, *values, stamp, stamp))
            elif not row["forgotten"]:
                conn.execute("UPDATE rika_learnings SET text=?, weight=?, evidence=?, detail=?, updated_at=? WHERE id=?"
                             " AND (text, weight, evidence, detail) IS NOT (?, ?, ?, ?)", (*values, stamp, row["id"], *values))
        gone = [row["id"] for key, row in existing.items() if key not in derived and not row["pinned"] and not row["forgotten"]]
        conn.executemany("DELETE FROM rika_learnings WHERE id=?", ((ident,) for ident in gone))
        conn.execute("INSERT INTO rika_learning_state(id,signature,refreshed_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE"
                     " SET signature=excluded.signature, refreshed_at=excluded.refreshed_at", (signature, stamp))
    return True


# Reading and the user's changes -----------------------------------------------

def _item(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "key": row["key"], "text": row["text"], "weight": row["weight"],
            "evidence": json.loads(row["evidence"]), "detail": json.loads(row["detail"]), "pinned": bool(row["pinned"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def learnings(conn: sqlite3.Connection) -> list[dict]:
    """Every learning not forgotten, by kind; pinned first, then the heaviest, then the newest."""
    order = " ".join(f"WHEN '{kind}' THEN {index}" for index, kind in enumerate(KINDS))
    return [_item(row) for row in conn.execute(
        f"SELECT * FROM rika_learnings WHERE forgotten=0 ORDER BY CASE kind {order} END, pinned DESC, weight DESC, id DESC")]


def learning(conn: sqlite3.Connection, ident: int) -> dict:
    row = conn.execute("SELECT * FROM rika_learnings WHERE id=? AND forgotten=0", (ident,)).fetchone()
    if row is None:
        raise LookupError("No such learning")
    return _item(row)


def overview(conn: sqlite3.Connection) -> dict:
    """The Memory page's view: learnings, the jobs their evidence names, the last refresh."""
    items = learnings(conn)
    ids = sorted({job_id for item in items if item["kind"].startswith("posting_") for job_id in item["evidence"]})
    jobs = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for row in conn.execute(f"SELECT j.id, j.title, c.display_name FROM jobs j JOIN companies c ON c.id=j.company_id"
                                f" WHERE j.id IN ({','.join('?' * len(chunk))})", chunk):
            jobs[str(row[0])] = {"title": row[1], "company": row[2]}
    state = conn.execute("SELECT refreshed_at FROM rika_learning_state WHERE id=1").fetchone()
    forgotten = conn.execute("SELECT count(*) FROM rika_learnings WHERE forgotten=1").fetchone()[0]
    return {"learnings": items, "jobs": jobs, "refreshed_at": state[0] if state else None, "forgotten": forgotten}


def forgotten_edits(conn: sqlite3.Connection) -> set[str]:
    """Keys (:func:`edit_key`) of the review reasons the user told Rika to forget."""
    return {key for (key,) in conn.execute("SELECT key FROM rika_learnings WHERE kind='resume_edit' AND forgotten=1")}


def forget(conn: sqlite3.Connection, ident: int) -> int:
    """Forget a learning for good (a note is deleted); LookupError when there is none."""
    with transaction(conn, immediate=True):
        found = learning(conn, ident)
        if found["kind"] == "note":
            conn.execute("DELETE FROM rika_learnings WHERE id=?", (ident,))
        else:
            conn.execute("UPDATE rika_learnings SET forgotten=1, pinned=0, updated_at=? WHERE id=?", (store.now(), ident))
    return ident


def pin(conn: sqlite3.Connection, ident: int, pinned: bool) -> dict:
    """Pin (or unpin) a learning: pinned ones lead the summary and outlive their evidence."""
    if type(pinned) is not bool:
        raise ValueError("pinned must be true or false")
    with transaction(conn, immediate=True):
        learning(conn, ident)
        conn.execute("UPDATE rika_learnings SET pinned=?, updated_at=? WHERE id=?", (int(pinned), store.now(), ident))
    return learning(conn, ident)


def add_note(conn: sqlite3.Connection, text) -> dict:
    """Remember something the user wrote (1–500 characters, at most 40 notes)."""
    if not isinstance(text, str) or "\x00" in text or not 1 <= len(text.strip()) <= NOTE_MAX:
        raise ValueError(f"A note is 1–{NOTE_MAX} characters")
    text = " ".join(text.split())
    stamp = store.now()
    with transaction(conn, immediate=True):
        notes = [row[0].lower() for row in conn.execute("SELECT text FROM rika_learnings WHERE kind='note'")]
        if text.lower() in notes:
            raise Refused("Rika already remembers that")
        if len(notes) >= NOTES_MAX:
            raise Refused(f"Rika keeps at most {NOTES_MAX} notes; forget one first")
        cursor = conn.execute("INSERT INTO rika_learnings(kind,key,text,created_at,updated_at) VALUES('note',?,?,?,?)",
                              (uuid4().hex, text, stamp, stamp))
    return learning(conn, cursor.lastrowid)


# For a model ------------------------------------------------------------------

def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _job_terms(conn, job_id: int | None, vocabulary: ResumeTermsFile | None) -> set[str] | None:
    if job_id is None or vocabulary is None:
        return None
    row = conn.execute("SELECT j.jd_text, c.display_name FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?",
                       (job_id,)).fetchone()
    if row is None or not (row[0] or "").strip():
        return None
    matchers = resume_coach.compile_vocabulary(vocabulary)
    posting = SimpleNamespace(jd_text=row[0], company_name=row[1] or "")
    return {matchers[index].term for index in resume_coach._scan(posting, matchers).hits}


def _skill(item: dict, *, count: bool = True) -> str:
    fact = "confirmed fact" if item["detail"].get("fact") else "no confirmed fact"
    said = f"{_plural(item['detail'].get('postings', 0), 'posting')}; {fact}" if count else fact
    return f"{item['text']} ({said}{'; pinned' if item['pinned'] else ''})"


def summary_for_prompt(conn: sqlite3.Connection, *, job_id: int | None = None,
                       vocabulary: ResumeTermsFile | None = None) -> str:
    """Rika's career memory as a few capped lines for a model ('' when there is nothing).

    With `job_id` (and the vocabulary to read its posting) the skills that posting asks for
    come first, on their own line.
    """
    items = learnings(conn)
    kinds: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        kinds[item["kind"]].append(item)
    lines = []
    asked = _job_terms(conn, job_id, vocabulary)
    skills = kinds["posting_skill"]
    if asked:
        relevant = [item for item in skills if item["text"] in asked]
        if relevant:
            lines.append("- This posting asks for what the user's targets keep asking for: "
                         + ", ".join(_skill(item, count=False) for item in relevant[:SUMMARY_SKILLS]))
        skills = sorted(skills, key=lambda item: (not item["pinned"], item["text"] not in asked))
    if skills:
        lines.append("- Skills the employers the user targets keep asking for: "
                     + ", ".join(_skill(item) for item in skills[:SUMMARY_SKILLS]))
    if kinds["posting_pattern"]:
        lines.append("- Jobs the user keeps choosing: " + ", ".join(
            f"{FACETS.get(item['detail'].get('facet'), 'Pattern').lower()} {item['text']}"
            f" ({_plural(item['detail'].get('postings', 0), 'posting')})"
            for item in kinds["posting_pattern"][:SUMMARY_PATTERNS]))
    accepted = [item for item in kinds["resume_edit"] if item["detail"]["accepted"] >= item["detail"]["rejected"]]
    rejected = [item for item in kinds["resume_edit"] if item["detail"]["accepted"] < item["detail"]["rejected"]]
    for label, group in (("Resume edits the user accepts", accepted), ("Resume edits the user rejects", rejected)):
        if group:
            lines.append(f"- {label}: " + "; ".join(
                f"“{item['text'][:160]}” ({item['detail']['accepted']} accepted, {item['detail']['rejected']} rejected)"
                for item in group[:SUMMARY_EDITS]))
    if kinds["note"]:
        lines.append("- The user asked Rika to remember: " + "; ".join(item["text"] for item in kinds["note"][:SUMMARY_NOTES]))
    if not lines:
        return ""
    text = _FRAME
    for line in lines:
        room = SUMMARY_CHARS - len(text) - 1
        if room <= 20:
            break
        text += "\n" + (line if len(line) <= room else line[:room - 1].rstrip(" ,;") + "…")
    return text

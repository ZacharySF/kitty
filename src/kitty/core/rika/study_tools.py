"""Rika's study tools beyond capture: flashcard decks, quizzing in chat and card edits,
study subjects and logged sessions, math progress, quick notes and documents.

Reads return compact rows. Document text reaches the model only when the notes privacy
setting allows AI (``note_preferences.allow_ai``), as note text does. A quiz shows cards
without recording reviews: scheduling stays with the Flashcards view (rika-server's FSRS).
Changes undo exactly what the tool did and refuse once the item changed since.
"""
from __future__ import annotations

import html
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from kitty.core import documents, flashcards, notes, quick_notes, study
from kitty.core.math import mastery
from kitty.core.math import topics as graph
from kitty.core.rika.tools import (
    Context,
    Result,
    Tool,
    ToolError,
    boolean,
    integer,
    obj,
    string,
)
from kitty.core.rika.workspace_tools import (
    _allow_ai,
    _calendar_zone,
    _like,
    _local,
    _subject_id,
)

LIST_LIMIT = 40
EXCERPT = 4000
#: Due cards come learning first, then reviews, each most overdue first (rika-server's queue order).
_RANK = {"learning": 0, "relearning": 0, "review": 1}
_BREAKS = re.compile(r"<(?:br|hr|/?(?:p|div|li|tr|h[1-6]|blockquote|pre|ul|ol|table))\b[^>]*>", re.IGNORECASE)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# Flashcards -------------------------------------------------------------------

def _plain(markup: str) -> str:
    """A rendered card side as plain text, one line per block."""
    text = html.unescape(re.sub(r"<[^>]+>", "", _BREAKS.sub("\n", markup)))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def _inside(deck: str, name: str) -> bool:
    return name == deck or name.startswith(deck + "::")


def _cards(conn) -> list[dict]:
    """Every card with its deck, and whether a review could show it now (``live``) and whether it is due."""
    flashcards.bootstrap(conn)
    now = flashcards.now().isoformat()
    archived = [row["name"] for row in conn.execute("SELECT name FROM flash_decks WHERE archived=1")]
    cards = []
    for row in conn.execute("SELECT c.id,c.note_id,c.ordinal,c.state,c.due,c.suspended,c.buried_until,"
                            "n.archived AS note_archived,d.name AS deck FROM flash_cards c "
                            "JOIN flash_notes n ON n.id=c.note_id JOIN flash_decks d ON d.id=n.deck_id"):
        card = dict(row)
        card["live"] = not (card["suspended"] or card["note_archived"] or (card["buried_until"] or "") > now
                            or any(_inside(name, card["deck"]) for name in archived))
        card["is_due"] = card["live"] and card["state"] != "new" and card["due"] <= now
        cards.append(card)
    return cards


def _list_decks(ctx: Context, args: dict) -> Result:
    cards = _cards(ctx.conn)
    decks = []
    for row in ctx.conn.execute("SELECT id,name,archived FROM flash_decks ORDER BY name LIMIT ?", (LIST_LIMIT,)):
        members = [card for card in cards if _inside(row["name"], card["deck"])]
        decks.append({"id": row["id"], "name": row["name"], "cards": len(members),
                      "due": sum(card["is_due"] for card in members),
                      "new": sum(card["live"] and card["state"] == "new" for card in members),
                      **({"archived": True} if row["archived"] else {})})
    due = sum(card["is_due"] for card in cards)
    return Result(f"{_plural(len(decks), 'deck')}, {_plural(due, 'card')} due", {"decks": decks, "due": due})


def _deck_name(conn, args: dict) -> str | None:
    """The deck named by deck_id or deck (case-insensitive), or None for every deck."""
    if "deck_id" in args:
        row = conn.execute("SELECT name FROM flash_decks WHERE id=?", (args["deck_id"],)).fetchone()
    elif "deck" in args:
        row = conn.execute("SELECT name FROM flash_decks WHERE name=? COLLATE NOCASE", (args["deck"].strip(),)).fetchone()
    else:
        return None
    if row is None:
        raise ToolError("No flashcard deck matches " + (f"id {args['deck_id']}" if "deck_id" in args else repr(args["deck"])))
    return row["name"]


def _quiz_cards(ctx: Context, args: dict) -> Result:
    conn = ctx.conn
    deck = _deck_name(conn, args)
    cards = [card for card in _cards(conn) if deck is None or _inside(deck, card["deck"])]
    chosen = sorted((card for card in cards if card["is_due"]), key=lambda card: (_RANK.get(card["state"], 2), card["due"], card["id"]))
    if args.get("include_new"):
        chosen += sorted((card for card in cards if card["live"] and card["state"] == "new"), key=lambda card: (card["due"], card["id"]))
    quiz = []
    for card in chosen[:args.get("limit", 5)]:
        front = _plain(flashcards.render(conn, card))
        answer = _plain(flashcards.render(conn, card, answer=True))
        # A basic card's back repeats its front ({{FrontSide}}); the answer is what follows.
        if answer.startswith(front):
            answer = answer[len(front):].strip()
        quiz.append({"card_id": card["id"], "note_id": card["note_id"], "deck": card["deck"], "state": card["state"],
                     "front": front[:2000], "answer": answer[:2000]})
    where = f" in {deck}" if deck else ""
    summary = f"{_plural(len(quiz), 'card')} to quiz{where}" if quiz else f"No cards are due{where}"
    return Result(summary, {"cards": quiz, "due": sum(card["is_due"] for card in cards), "reviews_recorded": False})


def _note(conn, note_id: int) -> dict:
    try:
        return flashcards.require(conn, "flash_notes", note_id)
    except ValueError:
        raise ToolError("No flashcard has that note id") from None


def _save_fields(conn, note: dict, fields: dict) -> dict:
    return flashcards.save_note(conn, {"id": note["id"], "revision": note["revision"], "deck_id": note["deck_id"],
                                       "type_id": note["type_id"], "fields": fields, "tags": json.loads(note["tags"]),
                                       "archived": bool(note["archived"])})


def _edit_card(ctx: Context, args: dict) -> Result:
    if "front" not in args and "back" not in args:
        raise ToolError("Give the card a new front, back or both")
    note = _note(ctx.conn, args["note_id"])
    names = json.loads(flashcards.require(ctx.conn, "flash_types", note["type_id"])["fields"])
    before = json.loads(note["fields"])
    fields = dict(before)
    for key, index in (("front", 0), ("back", 1)):
        if key in args:
            if index >= len(names):
                raise ToolError(f"This card has no {key} field")
            fields[names[index]] = args[key]
    saved = _save_fields(ctx.conn, note, fields)
    return Result(f"Edited the card {_plain(fields[names[0]])[:80]}", {"note_id": note["id"]},
                  undo={"note_id": note["id"], "revision": saved["revision"], "fields": before})


def _undo_edit_card(ctx: Context, undo: dict) -> None:
    row = ctx.conn.execute("SELECT 1 FROM flash_notes WHERE id=?", (undo["note_id"],)).fetchone()
    if row is None:
        raise ToolError("That card was deleted since, so there is nothing to put back")
    note = _note(ctx.conn, undo["note_id"])
    if note["revision"] != undo["revision"]:
        raise ToolError("That card changed since Rika edited it, so it was kept")
    _save_fields(ctx.conn, note, undo["fields"])


# Study ------------------------------------------------------------------------

def _today(conn) -> str:
    return datetime.now(ZoneInfo(_calendar_zone(conn))).date().isoformat()


def _list_subjects(ctx: Context, args: dict) -> Result:
    conn = ctx.conn
    if "subject_id" in args:
        if not conn.execute("SELECT 1 FROM study_subjects WHERE id=?", (args["subject_id"],)).fetchone():
            raise ToolError("No study subject has that id")
        only = args["subject_id"]
    else:
        only = _subject_id(conn, args.get("subject"))
    where, params = (" WHERE id=?", (only,)) if only is not None else ("", ())
    subjects = [dict(row) for row in conn.execute(f"SELECT id,name FROM study_subjects{where} ORDER BY name,id LIMIT ?",
                                                  (*params, LIST_LIMIT))]
    scope, scope_params = (" AND t.subject_id=?", (only,)) if only is not None else ("", ())
    open_counts = dict(conn.execute("SELECT subject_id,COUNT(*) FROM study_tasks WHERE done=0 GROUP BY subject_id").fetchall())
    tasks = [dict(row) for row in conn.execute(
        "SELECT t.id,t.title,t.due_date,t.priority,s.name AS subject FROM study_tasks t LEFT JOIN study_subjects s ON s.id=t.subject_id"
        f" WHERE t.done=0{scope} ORDER BY t.due_date IS NULL,t.due_date,t.id LIMIT ?", (*scope_params, LIST_LIMIT))]
    exams = [dict(row) for row in conn.execute(
        "SELECT t.id,t.title,t.exam_date,t.subject_id,s.name AS subject FROM study_exams t LEFT JOIN study_subjects s"
        f" ON s.id=t.subject_id WHERE t.exam_date>=?{scope} ORDER BY t.exam_date,t.id", (_today(conn), *scope_params))]
    for subject in subjects:
        upcoming = next((exam for exam in exams if exam["subject_id"] == subject["id"]), None)
        subject.update(open_tasks=open_counts.get(subject["id"], 0),
                       next_exam={"title": upcoming["title"], "date": upcoming["exam_date"]} if upcoming else None)
    exams = [{key: exam[key] for key in ("id", "title", "exam_date", "subject")} for exam in exams[:10]]
    return Result(f"{_plural(len(subjects), 'subject')}, {_plural(len(tasks), 'open study task')}, "
                  f"{_plural(len(exams), 'upcoming exam')}", {"subjects": subjects, "tasks": tasks, "exams": exams})


def _log_study_session(ctx: Context, args: dict) -> Result:
    conn = ctx.conn
    tz = conn.execute("SELECT timezone FROM study_settings WHERE id=1").fetchone()["timezone"]
    ended = (datetime.fromisoformat(_local(args["ended_at"], _calendar_zone(conn))) if "ended_at" in args
             else datetime.now(UTC))
    started = ended - timedelta(minutes=args["minutes"])
    body = {"request_id": "rika-" + uuid.uuid4().hex, "subject_id": _subject_id(conn, args.get("subject")),
            "started_at": started.isoformat(), "timezone": tz, "duration_seconds": args["minutes"] * 60,
            "notes": args.get("notes", "")}
    item = study.save_session(conn, body)["item"]
    where = f" of {args['subject']}" if args.get("subject") else ""
    return Result(f"Logged {args['minutes']} minutes{where}", {"session_id": item["id"]},
                  undo={"id": item["id"], "revision": item["revision"]})


def _undo_study_session(ctx: Context, undo: dict) -> None:
    if not ctx.conn.execute("SELECT 1 FROM study_sessions WHERE id=?", (undo["id"],)).fetchone():
        return
    try:
        study.save_session(ctx.conn, {"action": "delete", "id": undo["id"], "revision": undo["revision"],
                                      "request_id": "rika-" + uuid.uuid4().hex})
    except study.StudyConflict:
        raise ToolError("That study session changed since, so it was kept") from None


# Math -------------------------------------------------------------------------

def _topic(item: dict) -> dict:
    keys = ("id", "title", "strand", "state", "why", "progress", "due_at")
    return {key: item[key] for key in keys if key in item}


def _math_progress(ctx: Context, args: dict) -> Result:
    items = mastery.states(ctx.conn)
    counts = dict.fromkeys(("mastered", "learning", "ready", "locked"), 0)
    for item in items:
        counts[item["state"]] += 1
    now = mastery.now()
    due = sorted((item for item in items if item["state"] == "mastered" and datetime.fromisoformat(item["due_at"]) <= now),
                 key=lambda item: item["due_at"])
    upcoming = mastery.next_task(ctx.conn, now)
    return Result(f"Next in math: {upcoming['title']} ({upcoming['kind']})", {
        "counts": counts, "next": upcoming, "due_reviews": [_topic(item) for item in due[:10]], "due_review_count": len(due),
        "learning": [_topic(item) for item in items if item["state"] == "learning"][:10]})


def _find_math_topics(ctx: Context, args: dict) -> Result:
    items = mastery.states(ctx.conn)
    if "topic_id" in args:
        if args["topic_id"] not in graph.BY_ID:
            raise ToolError(f"No math topic has the id {args['topic_id']!r}")
        item = next(item for item in items if item["id"] == args["topic_id"])
        found = [{**_topic(item), "prereqs": [graph.BY_ID[ident].title for ident in item["prereqs"]]}]
        return Result(f"{item['title']}: {item['state']}", {"topics": found})
    query = args.get("query", "").casefold()
    matches = [item for item in items if (not query or query in item["title"].casefold() or query in item["id"])
               and args.get("strand") in (None, item["strand"])]
    return Result(f"Found {_plural(len(matches), 'math topic')}",
                  {"topics": [_topic(item) for item in matches[:LIST_LIMIT]], "more": max(0, len(matches) - LIST_LIMIT)})


# Quick notes ------------------------------------------------------------------

def _create_quick_note(ctx: Context, args: dict) -> Result:
    note = quick_notes.create(ctx.conn, args["text"], args.get("tint", "rose"))
    return Result(f"Added the quick note {args['text'][:60]}", {"note_id": note["id"]},
                  undo={"note_id": note["id"], "revision": note["revision"]})


def _undo_quick_note(ctx: Context, undo: dict) -> None:
    try:
        note = quick_notes.get(ctx.conn, undo["note_id"])
    except quick_notes.QuickNoteError:
        return
    if note["revision"] != undo["revision"]:
        raise ToolError("That quick note changed since Rika made it, so it was kept")
    quick_notes.delete(ctx.conn, undo["note_id"], undo["revision"])


# Documents --------------------------------------------------------------------

def _find_documents(ctx: Context, args: dict) -> Result:
    pattern = _like(args.get("query", ""))
    rows = ctx.conn.execute(
        "SELECT json_extract(r.properties,'$.number') AS id,r.text AS title,json_extract(r.properties,'$.format') AS format,"
        "json_extract(r.properties,'$.notebook_id') AS notebook_id,r.updated_at FROM blocks r"
        " WHERE r.type='document' AND r.parent_id IS NULL AND r.deleted_at IS NULL AND (r.text LIKE ? ESCAPE '\\'"
        " OR EXISTS (SELECT 1 FROM blocks c WHERE c.page_id=r.id AND c.id<>r.id AND c.deleted_at IS NULL"
        " AND c.text LIKE ? ESCAPE '\\')) ORDER BY r.updated_at DESC LIMIT ?", (pattern, pattern, LIST_LIMIT))
    found = [dict(row) for row in rows]
    return Result(f"Found {_plural(len(found), 'document')}", {"documents": found})


def _read_document(ctx: Context, args: dict) -> Result:
    try:
        item = documents.document(ctx.conn, args["document_id"])
    except notes.NotesError as exc:
        raise ToolError(str(exc)) from None
    limit, allowed = args.get("max_chars", EXCERPT), _allow_ai(ctx.conn)
    content = item["content"]
    return Result(f"Read {item['title']}", {
        "id": item["id"], "title": item["title"], "format": item["format"], "notebook_id": item["notebook_id"],
        "chars": len(content), "text": content[:limit] if allowed else None,
        "truncated": allowed and len(content) > limit, "text_withheld": not allowed})


# Declarations -----------------------------------------------------------------

TOOLS = (
    Tool("list_decks", "List flashcard decks with how many cards each holds and how many are due now or new "
         "(subdecks included). Use it before quizzing or when the user asks what to review.",
         obj(), "read", _list_decks, module="workspace"),
    Tool("quiz_cards", "Get due flashcards, most overdue first, with front and answer, to quiz the user in chat: "
         "ask the front, wait for their answer, then reveal. It does not record reviews; the Flashcards view does.",
         obj(deck=string("Deck name; its subdecks are included", maximum=200),
             deck_id=integer("Deck id, e.g. the open deck's, instead of its name"),
             limit=integer("How many cards (default 5)", maximum=20),
             include_new=boolean("Also add new, never-reviewed cards after the due ones")),
         "read", _quiz_cards, module="workspace"),
    Tool("edit_card", "Change a flashcard's front (a cloze card's text) or back; note_id comes from search, "
         "quiz_cards or the open card.",
         obj(("note_id",), note_id=integer("Flashcard note id"), front=string("New front", maximum=10000),
             back=string("New back", minimum=0, maximum=10000)),
         "change", _edit_card, _undo_edit_card, module="workspace"),
    Tool("list_subjects", "List study subjects with their open study tasks and next exam, plus open study tasks "
         "(soonest due first) and upcoming exams.",
         obj(subject=string("Only this subject (its name)", maximum=250),
             subject_id=integer("Only this subject, by id (e.g. the open subject's)")),
         "read", _list_subjects, module="workspace"),
    Tool("log_study_session", "Record study time the user already did, e.g. \"I studied chemistry for 45 minutes\". "
         "It ends now unless ended_at says when.",
         obj(("minutes",), minutes=integer("Minutes studied", maximum=720), subject=string("Subject name", maximum=250),
             ended_at=string("ISO time it ended, e.g. 2026-11-03T15:00; defaults to now", maximum=100),
             notes=string("What was covered", minimum=0, maximum=2000)),
         "change", _log_study_session, _undo_study_session, module="workspace"),
    Tool("math_progress", "The user's math progress: topics mastered, learning, ready and locked, reviews due, "
         "and the one thing to practise next with why.",
         obj(), "read", _math_progress, module="math"),
    Tool("find_math_topics", "Find math topics by title or strand with their state (locked, ready, learning, mastered) "
         "and why; topic_id reads one topic with its prerequisites.",
         obj(query=string("Title words", minimum=0, maximum=200), strand=string("Strand", enum=tuple(graph.STRANDS)),
             topic_id=string("Topic id, e.g. from math_progress", maximum=100)),
         "read", _find_math_topics, module="math"),
    Tool("create_quick_note", "Jot a quick note (a short scratch note in Quick notes), for a passing thought that "
         "is not a task, event or note page.",
         obj(("text",), text=string("Note text", maximum=20000), tint=string("Colour", enum=quick_notes.TINTS)),
         "change", _create_quick_note, _undo_quick_note, module="workspace"),
    Tool("find_documents", "List documents (imported or written Markdown and text files) whose title or text matches; "
         "newest first.", obj(query=string("Words to match", minimum=0, maximum=300)),
         "read", _find_documents, module="workspace"),
    Tool("read_document", "Read a document's text, from the start up to max_chars. The text is withheld while the "
         "notes privacy setting keeps notes from AI.",
         obj(("document_id",), document_id=integer("Document id"),
             max_chars=integer(f"Most characters to read (default {EXCERPT})", maximum=20000)),
         "read", _read_document, module="workspace"),
)

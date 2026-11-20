"""Rika's reach into the workspace modules: flashcard decks and quizzes, the calendar's
week, study subjects and sessions, math progress, quick notes, documents,
opportunities and the Brain, plus the views open_view can open. Synthetic data only."""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from kitty.core import block_api, documents, notes, opportunities, quick_notes, study
from kitty.core.math import mastery
from kitty.core.math import topics as graph
from kitty.core.rika import tools
from kitty.core.rika.tools import Context, ToolError, validate
from kitty.core.rika.workspace_tools import VIEWS, view_for

REGISTRY = tools.registry()
NEW = {"list_decks": ("read", "workspace"), "quiz_cards": ("read", "workspace"), "edit_card": ("change", "workspace"),
       "list_events": ("read", "workspace"), "list_subjects": ("read", "workspace"),
       "log_study_session": ("change", "workspace"), "math_progress": ("read", "math"),
       "find_math_topics": ("read", "math"), "create_quick_note": ("change", "workspace"),
       "find_documents": ("read", "workspace"), "read_document": ("read", "workspace"),
       "list_opportunities": ("read", "workspace"), "find_brain_nodes": ("read", "brain")}


@pytest.fixture
def ctx(workspace):
    app = SimpleNamespace(settings=workspace.settings, modules=("workspace", "math", "brain"),
                          mutation_lock=threading.Lock())
    return Context(app, workspace.conn)


def run(ctx, name, /, **args):
    tool = REGISTRY[name]
    validate(tool.parameters, args)
    return tool.run(ctx, args)


def undo(ctx, name, result):
    REGISTRY[name].undo(ctx, result.undo)


def local_today(conn):
    zone = conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"]
    return datetime.now(ZoneInfo(zone)).date()


def test_new_tools_declare_their_effect_module_and_undo():
    for name, (effect, module) in NEW.items():
        tool = REGISTRY[name]
        assert (tool.effect, tool.module) == (effect, module), name
        assert tool.reversible == (effect == "change"), name
    workspace_only = SimpleNamespace(modules=("workspace",))
    available = tools.available(workspace_only)
    assert "list_decks" in available and "math_progress" not in available and "find_brain_nodes" not in available


# Views --------------------------------------------------------------------------

def test_open_view_reaches_the_brain_math_and_rika(ctx):
    assert (VIEWS["brain"], VIEWS["math"], VIEWS["rika"]) == ("brain", "math", None)
    assert view_for("the brain") == "brain" and view_for("maths") == "math" and view_for("rika chat") == "rika"
    for view in ("brain", "math", "rika"):
        assert run(ctx, "open_view", view=view).client == {"type": "navigate", "view": view}
    off = Context(SimpleNamespace(settings=ctx.app.settings, modules=("workspace",)), ctx.conn)
    with pytest.raises(ToolError, match="math module"):
        run(off, "open_view", view="math")
    assert run(off, "open_view", view="rika").data == {"view": "rika"}


# Flashcards ---------------------------------------------------------------------

def make_due(conn, note_id, *, days=1):
    past = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    conn.execute("UPDATE flash_cards SET state='review', due=?, reviews=1 WHERE note_id=?", (past, note_id))


def test_decks_list_their_cards_and_due_counts_with_subdecks(ctx):
    first = run(ctx, "add_card", front="Capital of France?", back="Paris", deck="Geo")
    second = run(ctx, "add_card", front="Capital of Peru?", back="Lima", deck="Geo::Americas")
    run(ctx, "add_card", front="H2O is?", back="Water", deck="Chem")
    make_due(ctx.conn, first.data["note_id"])
    make_due(ctx.conn, second.data["note_id"])
    suspended = run(ctx, "add_card", front="Hidden", back="Card", deck="Chem")
    make_due(ctx.conn, suspended.data["note_id"])
    ctx.conn.execute("UPDATE flash_cards SET suspended=1 WHERE note_id=?", (suspended.data["note_id"],))
    decks = {deck["name"]: deck for deck in run(ctx, "list_decks").data["decks"]}
    assert (decks["Geo"]["cards"], decks["Geo"]["due"], decks["Geo"]["new"]) == (2, 2, 0)
    assert (decks["Geo::Americas"]["cards"], decks["Geo::Americas"]["due"]) == (1, 1)
    assert (decks["Chem"]["cards"], decks["Chem"]["due"], decks["Chem"]["new"]) == (2, 0, 1)


def test_quiz_cards_gives_due_cards_front_and_answer_most_overdue_first(ctx):
    older = run(ctx, "add_card", front="<b>Capital</b> of France?", back="Paris", deck="Geo")
    newer = run(ctx, "add_card", front="Capital of Peru?", back="Lima", deck="Geo::Americas")
    fresh = run(ctx, "add_card", front="Capital of Chad?", back="N'Djamena", deck="Geo")
    make_due(ctx.conn, older.data["note_id"], days=3)
    make_due(ctx.conn, newer.data["note_id"], days=1)
    quiz = run(ctx, "quiz_cards", deck="geo")
    cards = quiz.data["cards"]
    assert [(card["front"], card["answer"]) for card in cards] == [("Capital of France?", "Paris"), ("Capital of Peru?", "Lima")]
    assert cards[0]["note_id"] == older.data["note_id"] and cards[1]["deck"] == "Geo::Americas"
    assert quiz.data["reviews_recorded"] is False
    assert [card["front"] for card in run(ctx, "quiz_cards", deck="Geo::Americas").data["cards"]] == ["Capital of Peru?"]
    americas = ctx.conn.execute("SELECT id FROM flash_decks WHERE name='Geo::Americas'").fetchone()["id"]
    assert [card["front"] for card in run(ctx, "quiz_cards", deck_id=americas).data["cards"]] == ["Capital of Peru?"]
    with_new = run(ctx, "quiz_cards", deck="Geo", limit=3, include_new=True).data["cards"]
    assert with_new[-1]["note_id"] == fresh.data["note_id"] and with_new[-1]["state"] == "new"
    assert run(ctx, "quiz_cards", deck="Geo", limit=1).data["cards"][0]["note_id"] == older.data["note_id"]
    with pytest.raises(ToolError):
        run(ctx, "quiz_cards", deck="Nowhere")


def test_edit_card_changes_front_or_back_and_undo_refuses_after_a_later_edit(ctx):
    card = run(ctx, "add_card", front="Mitochondria?", back="Powerhouse", deck="Bio")
    note_id = card.data["note_id"]
    edited = run(ctx, "edit_card", note_id=note_id, back="Makes most of the cell's ATP")

    def fields():
        return json.loads(ctx.conn.execute("SELECT fields FROM flash_notes WHERE id=?", (note_id,)).fetchone()["fields"])

    assert fields() == {"Front": "Mitochondria?", "Back": "Makes most of the cell's ATP"}
    undo(ctx, "edit_card", edited)
    assert fields() == {"Front": "Mitochondria?", "Back": "Powerhouse"}
    again = run(ctx, "edit_card", note_id=note_id, front="What do mitochondria do?")
    run(ctx, "edit_card", note_id=note_id, back="Later answer")
    with pytest.raises(ToolError, match="changed"):
        undo(ctx, "edit_card", again)
    with pytest.raises(ToolError):
        run(ctx, "edit_card", note_id=note_id)
    with pytest.raises(ToolError):
        run(ctx, "edit_card", note_id=99999, front="x")


# Calendar -----------------------------------------------------------------------

def test_list_events_shows_the_week_ahead_with_open_and_overdue_tasks(ctx):
    today = local_today(ctx.conn)
    tomorrow, later = today + timedelta(days=1), today + timedelta(days=12)
    run(ctx, "create_event", title="Lab meeting", start=f"{tomorrow}T10:00")
    run(ctx, "create_event", title="Far away", start=f"{later}T10:00")
    run(ctx, "create_event", title="Holiday", start=today.isoformat(), all_day=True)
    run(ctx, "create_task", title="Submit essay", due=tomorrow.isoformat())
    run(ctx, "create_task", title="Overdue reading", due=(today - timedelta(days=2)).isoformat())
    done = run(ctx, "create_task", title="Finished", due=tomorrow.isoformat())
    run(ctx, "complete_task", task_id=done.data["task_id"])
    week = run(ctx, "list_events")
    assert [event["title"] for event in week.data["events"]] == ["Holiday", "Lab meeting"]
    assert (week.data["start"], week.data["end"]) == (today.isoformat(), (today + timedelta(days=6)).isoformat())
    tasks = {task["title"]: task for task in week.data["tasks"]}
    assert set(tasks) == {"Submit essay", "Overdue reading"}
    assert tasks["Overdue reading"]["overdue"] is True and tasks["Submit essay"]["overdue"] is False
    ranged = run(ctx, "list_events", start=later.isoformat(), end=later.isoformat())
    assert [event["title"] for event in ranged.data["events"]] == ["Far away"] and ranged.data["tasks"] == []
    with pytest.raises(ToolError):
        run(ctx, "list_events", start="next week")
    with pytest.raises(ToolError):
        run(ctx, "list_events", start=later.isoformat(), end=today.isoformat())


# Study --------------------------------------------------------------------------

def test_list_subjects_shows_open_tasks_and_upcoming_exams(ctx):
    today = local_today(ctx.conn)
    chem = run(ctx, "create_subject", name="Chemistry").data["subject_id"]
    run(ctx, "create_subject", name="History")
    run(ctx, "create_study_task", title="Problem set 4", subject="Chemistry", due_date=(today + timedelta(days=2)).isoformat())
    finished = run(ctx, "create_study_task", title="Old set", subject="Chemistry")
    run(ctx, "complete_task", task_id=str(finished.data["task_id"]), kind="study_task")
    study.save_entity(ctx.conn, "exams", {"title": "Midterm", "subject_id": chem,
                                          "exam_date": (today + timedelta(days=9)).isoformat(), "timezone": "UTC"})
    study.save_entity(ctx.conn, "exams", {"title": "Last year", "subject_id": chem,
                                          "exam_date": (today - timedelta(days=300)).isoformat(), "timezone": "UTC"})
    data = run(ctx, "list_subjects").data
    subjects = {item["name"]: item for item in data["subjects"]}
    assert subjects["Chemistry"]["open_tasks"] == 1 and subjects["History"]["open_tasks"] == 0
    assert subjects["Chemistry"]["next_exam"] == {"title": "Midterm", "date": (today + timedelta(days=9)).isoformat()}
    assert [task["title"] for task in data["tasks"]] == ["Problem set 4"] and data["tasks"][0]["subject"] == "Chemistry"
    assert [exam["title"] for exam in data["exams"]] == ["Midterm"]
    assert [item["name"] for item in run(ctx, "list_subjects", subject="history").data["subjects"]] == ["History"]
    assert [item["name"] for item in run(ctx, "list_subjects", subject_id=chem).data["subjects"]] == ["Chemistry"]
    with pytest.raises(ToolError):
        run(ctx, "list_subjects", subject_id=99999)


def test_log_study_session_records_minutes_for_a_subject_and_undo_removes_it(ctx):
    run(ctx, "create_subject", name="Chemistry")
    logged = run(ctx, "log_study_session", minutes=45, subject="Chemistry", notes="Chapter 3")
    row = ctx.conn.execute("SELECT * FROM study_sessions WHERE id=?", (logged.data["session_id"],)).fetchone()
    assert (row["duration_seconds"], row["source"], row["notes"]) == (2700, "manual", "Chapter 3")
    assert ctx.conn.execute("SELECT name FROM study_subjects WHERE id=?", (row["subject_id"],)).fetchone()["name"] == "Chemistry"
    undo(ctx, "log_study_session", logged)
    assert ctx.conn.execute("SELECT COUNT(*) FROM study_sessions").fetchone()[0] == 0
    with pytest.raises(ToolError):
        run(ctx, "log_study_session", minutes=10, subject="Nope")
    with pytest.raises(ToolError):
        validate(REGISTRY["log_study_session"].parameters, {"minutes": 0})


# Math ---------------------------------------------------------------------------

def test_math_progress_names_the_next_task_and_counts_states(ctx):
    graph.sync(ctx.conn)
    first = graph.TOPICS[0]
    for _ in range(mastery.MASTERY_RUN):
        mastery.record(ctx.conn, first.id, True, 5000, 30)
    data = run(ctx, "math_progress").data
    assert data["counts"]["mastered"] == 1 and sum(data["counts"].values()) == len(graph.TOPICS)
    assert data["next"]["kind"] in ("lesson", "review") and data["next"]["topic_id"] in graph.BY_ID
    topics = run(ctx, "find_math_topics", topic_id=first.id).data["topics"]
    assert topics == [{**topics[0], "id": first.id, "title": first.title, "state": "mastered"}]
    found = run(ctx, "find_math_topics", query=first.title.split()[0]).data["topics"]
    assert first.id in [item["id"] for item in found] and len(found) <= 40
    with pytest.raises(ToolError):
        run(ctx, "find_math_topics", topic_id="no.such-topic")


# Quick notes --------------------------------------------------------------------

def test_create_quick_note_and_undo_unless_edited(ctx):
    made = run(ctx, "create_quick_note", text="Call the lab about Friday", tint="sky")
    note = quick_notes.get(ctx.conn, made.data["note_id"])
    assert (note["body"], note["tint"]) == ("Call the lab about Friday", "sky")
    undo(ctx, "create_quick_note", made)
    assert quick_notes.list_notes(ctx.conn) == []
    kept = run(ctx, "create_quick_note", text="Keep me")
    quick_notes.save(ctx.conn, kept.data["note_id"], 1, body="Edited by hand")
    with pytest.raises(ToolError, match="changed"):
        undo(ctx, "create_quick_note", kept)


# Documents ----------------------------------------------------------------------

def test_documents_are_found_by_title_or_text_and_read_only_when_ai_may_see_notes(ctx):
    conn = ctx.conn
    saved = documents.save(conn, {"title": "Thesis outline", "content": "# Aims\n\nStudy enzyme kinetics.",
                                  "format": "markdown", "notebook_id": None})
    documents.save(conn, {"title": "Shopping", "content": "Eggs", "format": "text", "notebook_id": None})
    assert [item["title"] for item in run(ctx, "find_documents", query="kinetics").data["documents"]] == ["Thesis outline"]
    assert len(run(ctx, "find_documents").data["documents"]) == 2
    withheld = run(ctx, "read_document", document_id=saved["id"]).data
    assert withheld["title"] == "Thesis outline" and withheld["text"] is None and withheld["text_withheld"] is True
    conn.execute("UPDATE note_preferences SET allow_ai=1 WHERE id=1")
    shown = run(ctx, "read_document", document_id=saved["id"], max_chars=10).data
    assert shown["text"] == "# Aims\n\nSt" and shown["truncated"] is True and shown["chars"] > 10
    with pytest.raises(ToolError):
        run(ctx, "read_document", document_id=9999)


# Opportunities ------------------------------------------------------------------

def test_list_opportunities_is_compact_and_filters(ctx):
    conn = ctx.conn
    opportunities.create(conn, {"title": "Summer research fellowship", "category": "research",
                                "description": "Enzymes lab", "deadline": "2099-01-15"})
    opportunities.create(conn, {"title": "Hackathon", "category": "competition"})
    listed = run(ctx, "list_opportunities").data["opportunities"]
    assert {item["title"] for item in listed} == {"Summer research fellowship", "Hackathon"}
    assert set(listed[0]) <= {"id", "title", "category", "status", "deadline", "location", "remote", "url",
                              "favorite", "availability", "start", "end"}
    found = run(ctx, "list_opportunities", query="enzymes").data["opportunities"]
    assert [item["title"] for item in found] == ["Summer research fellowship"]
    assert found[0]["deadline"] == "2099-01-15"


# Brain --------------------------------------------------------------------------

def test_find_brain_nodes_by_label_and_reads_one_node_with_its_children(ctx):
    book = notes.create_notebook(ctx.conn, "Biology")
    block_api.create_page(ctx.conn, "Enzymes", notebook=book["id"], blocks=["Lower activation energy"])
    block_api.create_page(ctx.conn, "Quasars", notebook=book["id"])
    found = run(ctx, "find_brain_nodes", query="enzym").data["nodes"]
    assert [(node["section"], node["type"], node["label"]) for node in found] == [("notes", "page", "Enzymes")]
    assert found[0]["view"] == "notes"
    notebook = run(ctx, "find_brain_nodes", query="Biology").data["nodes"][0]
    read = run(ctx, "find_brain_nodes", node_id=notebook["id"]).data
    assert read["node"]["label"] == "Biology" and {child["label"] for child in read["children"]} == {"Enzymes", "Quasars"}
    assert run(ctx, "find_brain_nodes", query="x", section="calendar").data["nodes"] == []
    with pytest.raises(ToolError):
        run(ctx, "find_brain_nodes", node_id="notes.page:99999")
    with pytest.raises(ToolError):
        run(ctx, "find_brain_nodes")

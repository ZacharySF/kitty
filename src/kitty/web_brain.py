"""Read-only /api/brain: every record and every edit as one node of the Brain view.

``GET /api/brain?limit=<n>`` (default 20000, at most 50000) answers::

    {"generated_at", "sections": [{id, label, group, records, edits}],
     "nodes": [{id, kind, section, type, label, at, parent}],
     "total": {"records", "edits"}, "truncated"}

Records are the things that exist, read from live tables (one query per source).
Edits are captured browser mutations (``record_events``) plus the append-only logs the
database already keeps: ``flash_reviews``, ``status_events``, ``study_sessions`` and,
with the block backend, ``op_log``. ``calendar_history`` contributes the changes made
before capture began (when migration 034 was applied); captured mutations cover the
rest, so no change is counted twice. Block writes are never captured (record_events
leaves a mutation that appended op_log rows to op_log), and op_log also holds the edits
that never pass through a browser POST: the editor's live transactions, peer-sync merges
and paired captures. Its rows are labelled create, delete, restore or edit from their
operations on the page's root; the rows the block migrations wrote are conversions
(migration 043) and are left out; history older than the retention window survives as
one ``page_snapshots`` row per page per day and counts as one edit.

When the block backend's ``blocks`` table exists it is the source of note pages,
documents and quick notes (and their blocks); the frozen legacy tables are not read.
Sections of disabled modules are neither listed nor queried. Missing tables contribute
nothing. Labels are at most 80 characters: titles and names only, never bodies, paths,
URLs or secrets; an edit of a deleted entity reads e.g. "Deleted page".

Records are never dropped before edits: records fill the limit first, then the newest
edits. ``total`` and the per-section counts cover everything, shown or not; a parent
that is not part of the response is null.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from kitty.core import calendar
from kitty.modules import MODULE_IDS
from kitty.web_atlas import _section_rows as _rows

DEFAULT_LIMIT = 20000
MAX_LIMIT = 50000
LABEL_LIMIT = 80
#: id -> (label, navigation group, owning module); order is display order.
SECTIONS = {
    "calendar": ("Calendar", "Plan", "workspace"),
    "reminders": ("Reminders", "Plan", "applyai"),
    "opportunities": ("Opportunities", "Plan", "workspace"),
    "study": ("Study", "Study", "workspace"),
    "flashcards": ("Flashcards", "Study", "workspace"),
    "notes": ("Notes", "Study", "workspace"),
    "quick-notes": ("Quick notes", "Study", "workspace"),
    "schema": ("Mind maps", "Study", "workspace"),
    "documents": ("Documents", "Study", "workspace"),
    "jobs": ("Jobs", "Job search", "applyai"),
    "applications": ("Applications", "Job search", "applyai"),
}
#: Legacy record sources that the block backend replaces.
BLOCK_SOURCES = {("notes", "page"), ("notes", "block"), ("documents", "document"), ("quick-notes", "note")}
#: (section, type, SQL selecting ref, label, at, parent node id).
RECORDS = (
    ("calendar", "calendar", "SELECT id, name, NULL, NULL FROM calendar_calendars WHERE deleted = 0 ORDER BY id"),
    ("calendar", "event", """SELECT e.id, e.title, h.first, 'calendar.calendar:' || e.calendar_id, e.source_key
        FROM calendar_events e LEFT JOIN (SELECT entity_id, MIN(created_at) AS first FROM calendar_history
        WHERE entity = 'event' GROUP BY entity_id) h ON h.entity_id = e.id WHERE e.deleted = 0 ORDER BY e.id"""),
    ("calendar", "task", """SELECT t.id, json_extract(t.data, '$.title'), h.first, NULL FROM calendar_tasks t
        LEFT JOIN (SELECT entity_id, MIN(created_at) AS first FROM calendar_history WHERE entity = 'task'
        GROUP BY entity_id) h ON h.entity_id = t.id WHERE t.deleted = 0 ORDER BY t.id"""),
    ("reminders", "reminder", """SELECT r.id, CASE r.kind WHEN 'custom' THEN 'Reminder'
        ELSE upper(substr(r.kind, 1, 1)) || replace(substr(r.kind, 2), '_', ' ') END
        || COALESCE(' · ' || c.display_name, ''), r.created_at,
        COALESCE('applications.application:' || r.application_id, 'jobs.job:' || r.job_id)
        FROM reminders r LEFT JOIN jobs j ON j.id = r.job_id LEFT JOIN companies c ON c.id = j.company_id
        ORDER BY r.id"""),
    ("opportunities", "opportunity",
     "SELECT id, title, created_at, NULL FROM opportunities WHERE status <> 'skipped' ORDER BY id"),
    ("study", "subject", "SELECT id, name, NULL, NULL FROM study_subjects ORDER BY id"),
    ("study", "project", "SELECT id, name, NULL, 'study.subject:' || subject_id FROM study_projects ORDER BY id"),
    ("study", "task", """SELECT id, title, NULL, COALESCE('study.project:' || project_id,
        'study.subject:' || subject_id) FROM study_tasks ORDER BY id"""),
    ("study", "exam", "SELECT id, title, NULL, 'study.subject:' || subject_id FROM study_exams ORDER BY id"),
    ("study", "session", """SELECT id, 'Focus session', started_at, COALESCE('study.project:' || project_id,
        'study.subject:' || subject_id) FROM study_sessions ORDER BY id"""),
    ("flashcards", "deck", "SELECT id, name, created_at, 'flashcards.deck:' || parent_id FROM flash_decks ORDER BY id"),
    ("flashcards", "note", """SELECT n.id, COALESCE(t.name, 'Card') || ' · Note ' || n.id, n.created_at,
        'flashcards.deck:' || n.deck_id FROM flash_notes n LEFT JOIN flash_types t ON t.id = n.type_id ORDER BY n.id"""),
    ("notes", "folder", "SELECT id, title, created_at, 'notes.folder:' || parent_id FROM note_folders ORDER BY id"),
    ("notes", "notebook",
     "SELECT id, title, created_at, 'notes.folder:' || folder_id FROM note_notebooks ORDER BY id"),
    ("notes", "page", "SELECT id, title, created_at, 'notes.notebook:' || notebook_id FROM note_pages ORDER BY id"),
    ("notes", "block", """SELECT id, 'Outline item', NULL, COALESCE('notes.block:' || parent_id,
        'notes.page:' || page_id) FROM note_blocks ORDER BY page_id, ordering, id"""),
    ("quick-notes", "note", "SELECT id, 'Quick note', created_at, NULL FROM quick_notes ORDER BY id"),
    ("schema", "map", "SELECT id, title, NULL, NULL FROM schema_maps ORDER BY id"),
    ("documents", "document", "SELECT id, title, created_at, NULL FROM documents ORDER BY id"),
    ("jobs", "job", """SELECT j.id, COALESCE(c.display_name || ' — ', '') || j.title, j.first_seen_at, NULL
        FROM jobs j LEFT JOIN companies c ON c.id = j.company_id ORDER BY j.id"""),
    ("applications", "application", """SELECT a.id, COALESCE(c.display_name || ' — ', '') || j.title,
        COALESCE(a.applied_at, a.created_at), 'jobs.job:' || a.job_id FROM applications a
        JOIN jobs j ON j.id = a.job_id LEFT JOIN companies c ON c.id = j.company_id ORDER BY a.id"""),
)
#: Block root type -> (section, record type, label word).
BLOCK_ROOTS = {"page": ("notes", "page", "page"), "document": ("documents", "document", "document"),
               "quick_note": ("quick-notes", "note", "quick note")}
#: Record types whose labels are generic rather than a title.
UNTITLED = {("quick-notes", "note"), ("notes", "block"), ("study", "session")}
ENTITY_WORDS = {("quick-notes", "note"): "quick note", ("flashcards", "note"): "card note",
                ("schema", "map"): "mind map", ("study", "plan"): "study plan"}
VERBS = {"create": "Created", "save": "Saved", "edit": "Edited", "delete": "Deleted", "outline": "Edited outline of",
         "organize": "Organized", "reorder": "Reordered", "import": "Imported", "undo": "Undid change to",
         "complete": "Completed", "favorite": "Favorited", "archive": "Archived", "suspend": "Suspended",
         "bury": "Buried", "upload": "Uploaded", "link": "Linked", "generate": "Generated",
         "to-note": "Made a note of", "add-node": "Added a node to", "import-jobs": "Imported jobs into",
         "notes": "Edited notes of", "queue": "Queued", "skip": "Skipped", "restore": "Restored",
         "resume": "Chose a resume for", "tier": "Ranked", "state": "Moved", "add_track": "Added a track to",
         "not_eligible": "Marked ineligible", "deadline": "Set a deadline for", "next_action": "Planned",
         "tracks": "Changed tracks of", "project": "Started a project from", "items": "Edited items of",
         "project-details": "Edited project of", "calendar": "Scheduled"}
RATINGS = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
#: One op_log row's action on its page: its root inserted (create), moved to Trash
#: (delete) or brought back (restore); anything else edits the page.
_BLOCK_OPS = "json_each(CAST(o.ops AS TEXT), '$.ops')"
_BLOCK_ACTION = f"""CASE WHEN NOT json_valid(CAST(o.ops AS TEXT)) THEN 'edit'
    WHEN EXISTS (SELECT 1 FROM {_BLOCK_OPS} WHERE json_extract(value, '$.id') = o.page_id
                 AND json_extract(value, '$.op') = 'insert') THEN 'create'
    WHEN EXISTS (SELECT 1 FROM {_BLOCK_OPS} WHERE json_extract(value, '$.id') = o.page_id
                 AND json_extract(value, '$.op') = 'delete') THEN 'delete'
    WHEN EXISTS (SELECT 1 FROM {_BLOCK_OPS} WHERE json_extract(value, '$.id') = o.page_id
                 AND json_extract(value, '$.op') = 'restore') THEN 'restore'
    ELSE 'edit' END"""


def _api_error(status: int, message: str):
    from kitty.web_server import APIError  # web_server imports this module
    return APIError(status, message)


def _text(value) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= LABEL_LIMIT else text[:LABEL_LIMIT - 1] + "…"


def _moment(at) -> float:
    """Sort key of a stored ISO time; naive times are UTC and unreadable ones sort oldest."""
    try:
        moment = datetime.fromisoformat(str(at))
    except ValueError:
        return float("-inf")
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def _record(section, kind, ref, label, at, parent):
    return {"id": f"{section}.{kind}:{ref}", "kind": "record", "section": section, "type": kind,
            "label": _text(label) or kind.capitalize(), "at": at, "parent": parent}


def _edit(ident, section, kind, label, at, parent):
    return {"id": ident, "kind": "edit", "section": section, "type": kind, "label": _text(label),
            "at": at, "parent": parent}


def _table_exists(conn, name) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _block_records(conn, active):
    """Block backend records: roots keep the numbers the Notes API addresses them by."""
    rows = _rows(conn, """SELECT b.id, b.type, b.parent_id, b.page_id, b.text, b.created_at,
        json_extract(b.properties, '$.number'), json_extract(b.properties, '$.notebook_id'), r.type
        FROM blocks b LEFT JOIN blocks r ON r.id = b.page_id
        WHERE b.deleted_at IS NULL AND r.deleted_at IS NULL ORDER BY b.page_id, b.parent_id IS NOT NULL, b.order_key""")
    node_ids, roots = {}, {}
    for ident, kind, parent, _, _, _, number, _, root_type in rows:
        if parent is None:
            section, record, _ = BLOCK_ROOTS.get(kind, ("notes", kind, kind))
            roots[ident] = section, record
            node_ids[ident] = f"{section}.{record}:{number if number is not None else ident}"
        else:
            section = BLOCK_ROOTS.get(root_type, ("notes",))[0]
            node_ids[ident] = f"{section}.block:{ident}"
    # A block below a trashed (deleted) block is trashed with it.
    parents = {row[0]: row[2] for row in rows}
    live = {ident for ident in node_ids if parents[ident] is None}
    pending = [ident for ident in node_ids if parents[ident] is not None]
    while pending:
        settled = [ident for ident in pending if parents[ident] in live]
        if not settled:
            break
        live.update(settled)
        pending = [ident for ident in pending if ident not in live]
    records = []
    for ident, kind, parent, _, text, created, number, notebook, root_type in rows:
        if ident not in live:
            continue
        node_id = node_ids[ident]
        section, record = node_id.split(":", 1)[0].split(".")
        if section not in active:
            continue
        if parent is None:
            label = "Quick note" if kind == "quick_note" else text
            parent_id = f"notes.notebook:{notebook}" if notebook is not None and kind == "page" else None
        else:
            label = kind.replace("_", " ").capitalize() + " block"
            parent_id = node_ids[parent]
        records.append({"id": node_id, "kind": "record", "section": section, "type": record,
                        "label": _text(label) or record.capitalize(), "at": created, "parent": parent_id})
    return records


def _records(conn, active, applyai, blocks):
    records = []
    for section, kind, sql in RECORDS:
        if section not in active or (blocks and (section, kind) in BLOCK_SOURCES):
            continue
        for row in _rows(conn, sql):
            if kind == "event" and not applyai and calendar.applyai_sourced(row[4]):
                continue
            records.append(_record(section, kind, row[0], row[1], row[2], row[3]))
    if blocks:
        records.extend(_block_records(conn, active))
    return records


def _horizon(conn):
    """When browser capture began: calendar_history after it duplicates captured edits."""
    rows = _rows(conn, "SELECT applied_at FROM schema_version WHERE version = 34")
    return rows[0][0] if rows and _table_exists(conn, "record_events") else None


def _entity_word(section, entity):
    return ENTITY_WORDS.get((section, entity), entity.replace("_", " ").replace("-", " "))


def _edit_label(action, word, record):
    verb = VERBS.get(action, action.replace("_", " ").replace("-", " ").capitalize())
    label = f"{verb} {word}"
    if record is not None and (record["section"], record["type"]) not in UNTITLED:
        label += f" · {record['label']}"
    return label


def _edits(conn, active, limit, records, blocks):
    """Newest edits of every source (at most `limit` each) and per-section totals."""
    edits, counts = [], dict.fromkeys(active, 0)
    sections = sorted(active)
    marks = ",".join("?" for _ in sections) or "NULL"

    def add_counts(rows):
        for section, count in rows:
            if section in counts:
                counts[section] += count

    add_counts(_rows(conn, f"SELECT section, COUNT(*) FROM record_events WHERE section IN ({marks}) GROUP BY section",
                     sections))
    captured = _rows(conn, f"""SELECT seq, at, section, entity, entity_id, action FROM record_events
        WHERE section IN ({marks}) ORDER BY seq DESC LIMIT ?""", (*sections, limit))
    cards = [row[4] for row in captured if row[2] == "flashcards" and row[3] == "card"]
    card_notes = dict(_rows(conn, "SELECT id, note_id FROM flash_cards WHERE id IN (SELECT value FROM json_each(?))",
                            (json.dumps([int(card) for card in cards if str(card).isdigit()]),))) if cards else {}
    for seq, at, section, entity, entity_id, action in captured:
        target = f"{section}.{entity}:{entity_id}"
        if section == "flashcards" and entity == "card" and entity_id is not None and entity_id.isdigit():
            note = card_notes.get(int(entity_id))
            target = f"flashcards.note:{note}" if note is not None else None
        record = records.get(target) if entity_id is not None else None
        edits.append(_edit(f"edit:{seq}", section, action,
                           _edit_label(action, _entity_word(section, entity), record),
                           at, record["id"] if record else None))
    if "flashcards" in active:
        add_counts(_rows(conn, "SELECT 'flashcards', COUNT(*) FROM flash_reviews"))
        for ident, at, rating, undone, note in _rows(conn, """SELECT r.id, r.reviewed_at, r.rating, r.undone, c.note_id
                FROM flash_reviews r LEFT JOIN flash_cards c ON c.id = r.card_id ORDER BY r.id DESC LIMIT ?""", (limit,)):
            parent = records.get(f"flashcards.note:{note}")
            label = f"Reviewed card · {RATINGS.get(rating, rating)}" + (" (undone)" if undone else "")
            edits.append(_edit(f"edit:flash_reviews:{ident}", "flashcards", "review", label, at,
                               parent["id"] if parent else None))
    if "calendar" in active:
        horizon = _horizon(conn)
        window = "WHERE julianday(created_at) < julianday(?)" if horizon else "WHERE ?1 IS NULL"
        add_counts(_rows(conn, f"SELECT 'calendar', COUNT(*) FROM calendar_history {window}", (horizon,)))
        for ident, at, entity, entity_id, before, after in _rows(conn, f"""SELECT id, created_at, entity, entity_id,
                before_json IS NULL OR before_json = 'null', after_json IS NULL OR after_json = 'null'
                FROM calendar_history {window} ORDER BY id DESC LIMIT ?""", (horizon, limit)):
            action = "create" if before else "delete" if after else "save"
            record = records.get(f"calendar.{entity}:{entity_id}")
            edits.append(_edit(f"edit:calendar_history:{ident}", "calendar", action,
                               _edit_label(action, _entity_word("calendar", str(entity)), record), at,
                               record["id"] if record else None))
    if "applications" in active:
        add_counts(_rows(conn, "SELECT 'applications', COUNT(*) FROM status_events WHERE state = 'confirmed'"))
        for ident, at, before, after, undoes, application in _rows(conn, """SELECT id, occurred_at, from_status,
                to_status, undoes_event_id, application_id FROM status_events WHERE state = 'confirmed'
                ORDER BY id DESC LIMIT ?""", (limit,)):
            label = ("Undid a status change" if undoes is not None else "Applied" if before is None
                     else f"Status → {after}")
            parent = records.get(f"applications.application:{application}")
            edits.append(_edit(f"edit:status_events:{ident}", "applications", "status", label, at,
                               parent["id"] if parent else None))
    if "study" in active:
        add_counts(_rows(conn, "SELECT 'study', COUNT(*) FROM study_sessions"))
        for ident, at, seconds in _rows(conn, """SELECT id, ended_at, duration_seconds FROM study_sessions
                ORDER BY ended_at DESC, id DESC LIMIT ?""", (limit,)):
            parent = records.get(f"study.session:{ident}")
            edits.append(_edit(f"edit:study_sessions:{ident}", "study", "study",
                               f"Studied {max(1, round((seconds or 0) / 60))} min", at,
                               parent["id"] if parent else None))
    if blocks and "notes" in active:
        def section_of(root_type):
            return BLOCK_ROOTS.get(root_type, ("notes", None, "page"))

        # Rows the block migrations wrote are conversions (migration 043); compacted history
        # (older than the retention window) survives as one snapshot per page per day.
        migrated = _rows(conn, "SELECT last_seq FROM op_log_migrated") if _table_exists(conn, "op_log_migrated") else []
        after = migrated[0][0] if migrated else 0
        for root_type, count in _rows(conn, """SELECT r.type, COUNT(*) FROM (
                SELECT page_id FROM op_log WHERE seq > ?1 UNION ALL SELECT page_id FROM page_snapshots WHERE seq > ?1) o
                LEFT JOIN blocks r ON r.id = o.page_id GROUP BY r.type""", (after,)):
            add_counts([(section_of(root_type)[0], count)])
        for ident, at, root_type, number, deleted, action, _ in _rows(conn, f"""SELECT * FROM (
                SELECT 'op_log:' || o.seq AS ident, o.at, r.type, json_extract(r.properties, '$.number'), r.deleted_at,
                       {_BLOCK_ACTION} AS action, o.seq AS seq
                FROM op_log o LEFT JOIN blocks r ON r.id = o.page_id WHERE o.seq > ?1
                UNION ALL
                SELECT 'page_snapshots:' || s.page_id || ':' || s.day, s.day || 'T23:59:59+00:00', r.type,
                       json_extract(r.properties, '$.number'), r.deleted_at, 'edit', s.seq
                FROM page_snapshots s LEFT JOIN blocks r ON r.id = s.page_id WHERE s.seq > ?1)
                ORDER BY seq DESC LIMIT ?2""", (after, limit)):
            section, record_type, word = section_of(root_type)
            if section not in active:
                continue
            record = (records.get(f"{section}.{record_type}:{number}")
                      if record_type and number is not None and deleted is None else None)
            edits.append(_edit(f"edit:{ident}", section, "save" if action == "edit" else action,
                               _edit_label(action, word, record), at, record["id"] if record else None))
    edits.sort(key=lambda edit: _moment(edit["at"]), reverse=True)
    return edits, counts


def brain(conn, limit: int = DEFAULT_LIMIT, modules=MODULE_IDS, now: datetime | None = None) -> dict:
    active = [section for section, (_, _, module) in SECTIONS.items() if module in modules]
    blocks = "notes" in active and _table_exists(conn, "blocks")
    records = _records(conn, set(active), "applyai" in modules, blocks)
    by_id = {}
    for record in records:
        by_id.setdefault(record["id"], record)
    records = list(by_id.values())
    edits, edit_counts = _edits(conn, set(active), limit, by_id, blocks and _table_exists(conn, "op_log"))
    record_counts = dict.fromkeys(active, 0)
    for record in records:
        record_counts[record["section"]] += 1
    total = {"records": len(records), "edits": sum(edit_counts.values())}
    nodes = records[:limit] + edits[:max(0, limit - len(records))]
    shown = {node["id"] for node in nodes}
    for node in nodes:
        if node["parent"] is not None and node["parent"] not in shown:
            node["parent"] = None
    return {
        "generated_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "sections": [{"id": section, "label": SECTIONS[section][0], "group": SECTIONS[section][1],
                      "records": record_counts[section], "edits": edit_counts[section]} for section in active],
        "nodes": nodes,
        "total": total,
        "truncated": len(nodes) < total["records"] + total["edits"],
    }


def _limit(query) -> int:
    if set(query) - {"limit"}:
        raise _api_error(400, "Only limit is accepted")
    value = query.get("limit", str(DEFAULT_LIMIT))
    if not re.fullmatch(r"[0-9]{1,6}", value) or not 1 <= int(value) <= MAX_LIMIT:
        raise _api_error(400, f"limit must be between 1 and {MAX_LIMIT}")
    return int(value)


def get(app, conn, path, query):
    if path != "/api/brain":
        return None
    return brain(conn, _limit(query), app.modules)


def post(app, conn, path, body):
    return None

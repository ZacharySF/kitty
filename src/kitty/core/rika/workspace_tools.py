"""Rika's in-app tools: notes and blocks, canvas nodes, mind maps, flashcards,
calendar, study, navigation and search (design Q10 A–B).

Every change is reversible. Undo restores exactly what the tool did and refuses
with a clear message when the record changed since, so it never overwrites the
user's later edits. Where the core keeps its own history the undo reuses it:
calendar's ``undo_id`` and the mind map's revision-checked undo snapshot.

Note text reaches the model only when the notes privacy setting allows AI
(``note_preferences.allow_ai``); otherwise tools return titles and ids only.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from kitty.core import calendar, flashcards, note_blocks, notes, schema_maps, study
from kitty.core.rika.tools import Context, Result, Tool, ToolError, boolean, integer, number, obj, string

#: Views Rika may open (web/src/lib/workspace.ts) and the module that owns each.
VIEWS: dict[str, str | None] = {
    "today": None, "ai": None, "settings": None,
    "calendar": "workspace", "opportunities": "workspace", "study": "workspace", "learnharness": "workspace",
    "flashcards": "workspace", "notes": "workspace", "quick-notes": "workspace", "documents": "workspace",
    "schema": "workspace",
    "herdr": "terminal", "tern": "terminal", "agent-skills": "terminal",
    "inbox": "applyai", "queue": "applyai", "jobs": "applyai", "pipeline": "applyai", "applications": "applyai",
    "favorites": "applyai", "reminders": "applyai", "sources": "applyai", "resumes": "applyai",
    "profile": "applyai", "finder": "applyai", "reports": "applyai", "mail": "applyai", "triage": "applyai",
    "questions": "applyai",
}
#: Spoken names of views, for "open my mind maps" and the like.
VIEW_ALIASES = {
    "atlas": "today", "home": "today", "ai settings": "ai", "mind maps": "schema", "mind map": "schema",
    "maps": "schema", "canvas": "notes", "notes": "notes", "cards": "flashcards", "terminal": "tern",
    "agent skills": "agent-skills", "all agent skills": "agent-skills",
    "jobs": "jobs", "applications": "pipeline", "documents": "documents", "pdf": "documents",
}
#: Canvas defaults of a new sticky note, matching the Notes view's (web/src/lib/Notes.svelte).
STICKY = {"color": "#e7dfef", "fill": "#fff2a8", "width": 3, "w": 240, "h": 180, "fontSize": 20}
SEARCH_LIMIT = 10
#: Matches the search summary names, in order: (result kind, label, title field).
_NAMED = (("pages", "page", "title"), ("events", "event", "title"), ("tasks", "task", "title"),
          ("cards", "card", "front"), ("maps", "mind map", "title"), ("subjects", "subject", "name"),
          ("study_tasks", "study task", "title"), ("jobs", "job", "title"))
SUMMARY_NAMES = 4


def _allow_ai(conn) -> bool:
    return bool(conn.execute("SELECT allow_ai FROM note_preferences WHERE id=1").fetchone()[0])


def _like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _private(conn, text: str) -> str | None:
    """Note text for the model, or None while the notes privacy setting keeps it local."""
    return text[:2000] if _allow_ai(conn) else None


# Notes and blocks -------------------------------------------------------------

def _newest_page(conn) -> int | None:
    row = conn.execute("SELECT id FROM note_pages ORDER BY updated_at DESC,id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def _notebook(conn, title: str | None) -> int:
    if title:
        row = conn.execute("SELECT id FROM note_notebooks WHERE title=? COLLATE NOCASE ORDER BY id LIMIT 1", (title,)).fetchone()
        if row is None:
            raise ToolError(f"No notebook is called {title!r}")
        return row["id"]
    row = conn.execute("SELECT id FROM note_notebooks ORDER BY position,id LIMIT 1").fetchone()
    return row["id"] if row else notes.create_notebook(conn, "Notes")["id"]


def _page_by_title(conn, title: str) -> dict:
    row = conn.execute("SELECT id FROM note_pages WHERE title=? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1",
                       (title,)).fetchone()
    if row is None:
        row = conn.execute("SELECT id FROM note_pages WHERE title LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT 1",
                           (_like(title),)).fetchone()
    if row is None:
        raise ToolError(f"No page is called {title!r}")
    return notes.page(conn, row["id"])


def _find_pages(ctx: Context, args: dict) -> Result:
    pages = notes.listing(ctx.conn, args.get("query", ""))["pages"][:20]
    found = [{"id": page["id"], "title": page["title"], "notebook_id": page["notebook_id"]} for page in pages]
    return Result(f"Found {len(found)} page{'s' if len(found) != 1 else ''}", {"pages": found})


def _read_page(ctx: Context, args: dict) -> Result:
    page = notes.page(ctx.conn, args["page_id"])
    blocks = note_blocks.get_blocks(ctx.conn, page["id"])["blocks"]
    elements = page["document"]["elements"]
    return Result(f"Read {page['title']}", {
        "id": page["id"], "title": page["title"],
        "blocks": [{"id": block["id"], "parent_id": block["parent_id"], "text": _private(ctx.conn, block["text"])}
                   for block in blocks],
        "nodes": [{"id": item["id"], "kind": item["kind"], "x": item["x"], "y": item["y"],
                   "text": _private(ctx.conn, item.get("text", ""))}
                  for item in elements if item["kind"] in {"text", "sticky", "rect", "ellipse", "image"}],
        "connectors": [{"id": item["id"], "from": item["from"], "to": item["to"]}
                       for item in elements if item["kind"] == "connector"],
        "note_text_withheld": not _allow_ai(ctx.conn),
    })


def _create_page(ctx: Context, args: dict) -> Result:
    page = notes.create_page(ctx.conn, _notebook(ctx.conn, args.get("notebook")), args["title"])
    return Result(f"Created the page {page['title']}", {"page_id": page["id"]},
                  undo={"page_id": page["id"], "revision": page["revision"]})


def _undo_create_page(ctx: Context, undo: dict) -> None:
    try:
        notes.delete_page(ctx.conn, undo["page_id"], undo["revision"])
    except notes.NotesError:
        raise ToolError("The page changed since Rika created it, so it was kept") from None


def _block_target(ctx: Context, args: dict) -> dict:
    if "page_id" in args:
        return notes.page(ctx.conn, args["page_id"])
    if "page" in args:
        return _page_by_title(ctx.conn, args["page"])
    newest = _newest_page(ctx.conn)
    if newest is None:
        raise ToolError("There is no page yet; create one first")
    return notes.page(ctx.conn, newest)


def _append_block(ctx: Context, args: dict) -> Result:
    page = _block_target(ctx, args)
    current = note_blocks.get_blocks(ctx.conn, page["id"])
    blocks = current["blocks"]
    block = {"id": str(uuid.uuid4()), "parent_id": None, "text": args["text"],
             "ordering": max((item["ordering"] for item in blocks), default=-1) + 1, "collapsed": False}
    note_blocks.save_blocks(ctx.conn, page["id"], current["revision"], [*blocks, block])
    return Result(f"Added a block to {page['title']}", {"page_id": page["id"], "block_id": block["id"]},
                  undo={"page_id": page["id"], "block_id": block["id"], "text": block["text"]})


def _undo_append_block(ctx: Context, undo: dict) -> None:
    current = note_blocks.get_blocks(ctx.conn, undo["page_id"])
    block = next((item for item in current["blocks"] if item["id"] == undo["block_id"]), None)
    if block is None:
        return
    if block["text"] != undo["text"] or any(item["parent_id"] == block["id"] for item in current["blocks"]):
        raise ToolError("That block changed since Rika added it, so it was kept")
    note_blocks.save_blocks(ctx.conn, undo["page_id"], current["revision"],
                            [item for item in current["blocks"] if item["id"] != block["id"]])


def _block(ctx: Context, block_id: str) -> dict:
    row = ctx.conn.execute("SELECT * FROM note_blocks WHERE id=?", (note_blocks.block_id(block_id),)).fetchone()
    if row is None:
        raise ToolError("Block not found")
    return dict(row)


def _set_block_text(ctx: Context, page_id: int, block_id: str, expected: str, text: str) -> None:
    current = note_blocks.get_blocks(ctx.conn, page_id)
    blocks = [dict(item) for item in current["blocks"]]
    target = next((item for item in blocks if item["id"] == block_id), None)
    if target is None or target["text"] != expected:
        raise ToolError("That block changed since, so it was kept")
    target["text"] = text
    note_blocks.save_blocks(ctx.conn, page_id, current["revision"], blocks)


def _edit_block(ctx: Context, args: dict) -> Result:
    block = _block(ctx, args["block_id"])
    _set_block_text(ctx, block["page_id"], block["id"], block["text"], args["text"])
    return Result("Edited a block", {"block_id": block["id"]},
                  undo={"page_id": block["page_id"], "block_id": block["id"], "before": block["text"], "after": args["text"]})


def _undo_edit_block(ctx: Context, undo: dict) -> None:
    _set_block_text(ctx, undo["page_id"], undo["block_id"], undo["after"], undo["before"])


def _find_blocks(ctx: Context, args: dict) -> Result:
    rows = ctx.conn.execute(
        "SELECT b.id,b.page_id,b.text,p.title FROM note_blocks b JOIN note_pages p ON p.id=b.page_id"
        " WHERE b.text LIKE ? ESCAPE '\\' ORDER BY p.updated_at DESC,b.ordering LIMIT 20", (_like(args["query"]),)).fetchall()
    found = [{"id": row["id"], "page_id": row["page_id"], "page": row["title"], "text": _private(ctx.conn, row["text"])}
             for row in rows]
    return Result(f"Found {len(found)} block{'s' if len(found) != 1 else ''}", {"blocks": found})


# Canvas nodes -----------------------------------------------------------------

def _canvas_page(ctx: Context, args: dict) -> tuple[dict, bool]:
    """The page to draw on and whether Rika created it for this node."""
    if "page_id" in args or "page" in args:
        return _block_target(ctx, args), False
    newest = _newest_page(ctx.conn)
    if newest is not None:
        return notes.page(ctx.conn, newest), False
    return notes.create_page(ctx.conn, _notebook(ctx.conn, None), "Canvas"), True


def _save_document(ctx: Context, page: dict, document: dict) -> int:
    return notes.save_page(ctx.conn, page["id"], page["revision"], document, page["title"])["revision"]


def _element(document: dict, ident: str) -> dict:
    found = next((item for item in document["elements"] if item["id"] == ident), None)
    if found is None:
        found = next((item for item in document["elements"] if item["kind"] in {"text", "sticky"}
                      and item.get("text", "").strip().casefold() == ident.strip().casefold()), None)
    if found is None:
        raise ToolError(f"No canvas node {ident!r} on that page")
    return found


def _add_canvas_node(ctx: Context, args: dict) -> Result:
    page, created = _canvas_page(ctx, args)
    document = page["document"]
    nodes = [item for item in document["elements"] if item["kind"] in {"text", "sticky"}]
    x = args.get("x", 40 + (len(nodes) % 4) * (STICKY["w"] + 40))
    y = args.get("y", 40 + (len(nodes) // 4) * (STICKY["h"] + 40))
    node = {"id": uuid.uuid4().hex[:16], "kind": "sticky", "layer": document["layers"][0]["id"], "x": x, "y": y,
            **STICKY, "text": args["text"]}
    document["elements"].append(node)
    revision = _save_document(ctx, page, document)
    return Result(f"Added the node {args['text']} to {page['title']}", {"page_id": page["id"], "node_id": node["id"]},
                  undo={"page_id": page["id"], "node": node, "created_page": created, "revision": revision})


def _undo_add_canvas_node(ctx: Context, undo: dict) -> None:
    page = notes.page(ctx.conn, undo["page_id"])
    if undo["created_page"] and page["revision"] == undo["revision"]:
        notes.delete_page(ctx.conn, page["id"], page["revision"])
        return
    elements = page["document"]["elements"]
    current = next((item for item in elements if item["id"] == undo["node"]["id"]), None)
    if current is None:
        return
    if current != undo["node"] or any(item.get("from") == current["id"] or item.get("to") == current["id"] for item in elements):
        raise ToolError("That node changed or was connected since, so it was kept")
    page["document"]["elements"] = [item for item in elements if item["id"] != current["id"]]
    _save_document(ctx, page, page["document"])


def _move_canvas_node(ctx: Context, args: dict) -> Result:
    page = _block_target(ctx, args)
    node = _element(page["document"], args["node"])
    before = {"x": node["x"], "y": node["y"]}
    node.update(x=float(args["x"]), y=float(args["y"]))
    _save_document(ctx, page, page["document"])
    return Result(f"Moved {node.get('text') or 'a node'} on {page['title']}", {"node_id": node["id"]},
                  undo={"page_id": page["id"], "node_id": node["id"], "before": before, "after": {"x": node["x"], "y": node["y"]}})


def _undo_move_canvas_node(ctx: Context, undo: dict) -> None:
    page = notes.page(ctx.conn, undo["page_id"])
    node = next((item for item in page["document"]["elements"] if item["id"] == undo["node_id"]), None)
    if node is None or {"x": node["x"], "y": node["y"]} != undo["after"]:
        raise ToolError("That node moved again since, so it was kept where it is")
    node.update(undo["before"])
    _save_document(ctx, page, page["document"])


def _connect_canvas_nodes(ctx: Context, args: dict) -> Result:
    page = _block_target(ctx, args)
    document = page["document"]
    start, end = _element(document, args["from"]), _element(document, args["to"])
    if start["id"] == end["id"]:
        raise ToolError("Connect two different nodes")
    connector = {"id": uuid.uuid4().hex[:16], "kind": "connector", "layer": start["layer"],
                 "x": min(start["x"], end["x"]), "y": min(start["y"], end["y"]),
                 "w": abs(end["x"] - start["x"]), "h": abs(end["y"] - start["y"]),
                 "color": STICKY["color"], "fill": "none", "width": STICKY["width"],
                 "points": [[0, 0, 1], [1, 1, 1]], "from": start["id"], "to": end["id"]}
    document["elements"].append(connector)
    _save_document(ctx, page, document)
    return Result(f"Connected {start.get('text') or 'a node'} to {end.get('text') or 'a node'}",
                  {"connector_id": connector["id"]}, undo={"page_id": page["id"], "connector_id": connector["id"]})


def _undo_connect_canvas_nodes(ctx: Context, undo: dict) -> None:
    page = notes.page(ctx.conn, undo["page_id"])
    elements = page["document"]["elements"]
    if not any(item["id"] == undo["connector_id"] for item in elements):
        return
    page["document"]["elements"] = [item for item in elements if item["id"] != undo["connector_id"]]
    _save_document(ctx, page, page["document"])


# Mind maps --------------------------------------------------------------------

def _map(ctx: Context, args: dict, *, create: bool = False) -> tuple[dict, bool]:
    if "map_id" in args:
        return schema_maps.load(ctx.conn, args["map_id"]), False
    if "map" in args:
        row = ctx.conn.execute("SELECT id FROM schema_maps WHERE title=? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1",
                               (args["map"],)).fetchone()
        if row is None:
            raise ToolError(f"No mind map is called {args['map']!r}")
        return schema_maps.load(ctx.conn, row["id"]), False
    row = ctx.conn.execute("SELECT id FROM schema_maps ORDER BY updated_at DESC,id DESC LIMIT 1").fetchone()
    if row is not None:
        return schema_maps.load(ctx.conn, row["id"]), False
    if not create:
        raise ToolError("There is no mind map yet")
    return schema_maps.create(ctx.conn, {"title": "Mind map", "mode": "general"}), True


def _topic(document: dict, reference: str) -> dict:
    for node in document["nodes"]:
        if node["id"] == reference:
            return node
    wanted = " ".join(reference.casefold().split())
    for node in document["nodes"]:
        if " ".join(node["label"].casefold().split()) == wanted:
            return node
    raise ToolError(f"No topic {reference!r} in that map")


def _map_undo(item: dict, created: bool) -> dict:
    return {"map_id": item["id"], "revision": item["revision"], "created_map": created}


def _undo_map(ctx: Context, undo: dict) -> None:
    """The map's own revision-checked undo; a map Rika created for the change is deleted."""
    try:
        if undo["created_map"]:
            schema_maps.delete(ctx.conn, {"id": undo["map_id"], "revision": undo["revision"], "confirm": True})
        else:
            schema_maps.undo(ctx.conn, {"id": undo["map_id"], "revision": undo["revision"]})
    except schema_maps.SchemaConflict:
        raise ToolError("The mind map changed since, so Rika's change was kept") from None


def _save_graph(ctx: Context, item: dict, graph: dict) -> dict:
    return schema_maps.save(ctx.conn, {"id": item["id"], "revision": item["revision"], "title": item["title"],
                                       "graph": graph, "viewport": item["viewport"]})


def _find_maps(ctx: Context, args: dict) -> Result:
    maps = schema_maps.workspace(ctx.conn)["maps"]
    query = args.get("query", "").casefold()
    found = [{"id": item["id"], "title": item["title"]} for item in maps if query in item["title"].casefold()][:20]
    if "map_id" in args:
        item = schema_maps.load(ctx.conn, args["map_id"])
        return Result(f"Read {item['title']}", {"id": item["id"], "title": item["title"],
                      "topics": [{"id": node["id"], "label": node["label"]} for node in item["graph"]["nodes"]],
                      "connections": [{"from": edge["from"], "to": edge["to"], "label": edge["label"]}
                                      for edge in item["graph"]["edges"]]})
    return Result(f"Found {len(found)} mind map{'s' if len(found) != 1 else ''}", {"maps": found})


def _add_map_topic(ctx: Context, args: dict) -> Result:
    item, created = _map(ctx, args, create=True)
    parent = _topic(item["graph"], args["parent"])["id"] if "parent" in args else None
    saved = schema_maps.add_node(ctx.conn, {"map_id": item["id"], "revision": item["revision"],
                                            "label": args["label"], "kind": "topic", "parent_id": parent})
    return Result(f"Added the topic {args['label']} to {saved['title']}",
                  {"map_id": saved["id"], "topic_id": saved["graph"]["nodes"][-1]["id"]}, undo=_map_undo(saved, created))


def _connect_map_topics(ctx: Context, args: dict) -> Result:
    item, _ = _map(ctx, args)
    graph = item["graph"]
    start, end = _topic(graph, args["from"]), _topic(graph, args["to"])
    if start["id"] == end["id"]:
        raise ToolError("Connect two different topics")
    graph["edges"].append({"id": uuid.uuid4().hex, "from": start["id"], "to": end["id"],
                           "label": args.get("label", ""), "kind": "cross"})
    saved = _save_graph(ctx, item, graph)
    return Result(f"Connected {start['label']} to {end['label']}", {"map_id": saved["id"]}, undo=_map_undo(saved, False))


def _rename_map_topic(ctx: Context, args: dict) -> Result:
    item, _ = _map(ctx, args)
    node = _topic(item["graph"], args["topic"])
    before = node["label"]
    node["label"] = args["label"]
    saved = _save_graph(ctx, item, item["graph"])
    return Result(f"Renamed {before} to {args['label']}", {"map_id": saved["id"]}, undo=_map_undo(saved, False))


# Flashcards -------------------------------------------------------------------

def _add_card(ctx: Context, args: dict) -> Result:
    conn = ctx.conn
    flashcards.bootstrap(conn)
    deck_name = args.get("deck", "Default")
    deck = conn.execute("SELECT id FROM flash_decks WHERE name=? COLLATE NOCASE", (deck_name,)).fetchone()
    deck_id = deck["id"] if deck else flashcards.save_deck(conn, {"name": deck_name})["id"]
    basic = conn.execute("SELECT id FROM flash_types WHERE kind='basic' ORDER BY id LIMIT 1").fetchone()
    note = flashcards.save_note(conn, {"deck_id": deck_id, "type_id": basic["id"],
                                       "fields": {"Front": args["front"], "Back": args["back"]}, "tags": []})
    return Result(f"Added the card {args['front']}", {"note_id": note["id"], "deck_id": deck_id},
                  undo={"note_id": note["id"], "revision": note["revision"]})


def _undo_add_card(ctx: Context, undo: dict) -> None:
    conn = ctx.conn
    row = conn.execute("SELECT revision FROM flash_notes WHERE id=?", (undo["note_id"],)).fetchone()
    if row is None:
        return
    if row["revision"] != undo["revision"] or conn.execute(
            "SELECT 1 FROM flash_cards WHERE note_id=? AND reviews>0", (undo["note_id"],)).fetchone():
        raise ToolError("That card was edited or reviewed since, so it was kept")
    flashcards.action(conn, {"id": undo["note_id"], "action": "delete", "revision": undo["revision"]})


# Calendar ---------------------------------------------------------------------

def _calendar_zone(conn) -> str:
    return conn.execute("SELECT timezone FROM calendar_calendars WHERE id='personal'").fetchone()["timezone"]


def _local(value: str, tz: str) -> str:
    """An ISO instant, adding the calendar's zone to a local wall time."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError("Use an ISO date and time such as 2026-11-03T15:00") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed.isoformat()


def _create_event(ctx: Context, args: dict) -> Result:
    tz = args.get("timezone") or _calendar_zone(ctx.conn)
    all_day = args.get("all_day", False)
    if all_day:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args["start"]):
            raise ToolError("An all-day event starts on a YYYY-MM-DD date")
        start = args["start"]
        end = args.get("end") or (datetime.fromisoformat(start) + timedelta(days=1)).date().isoformat()
    else:
        start = _local(args["start"], tz)
        end = _local(args["end"], tz) if "end" in args else (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    event = calendar.create_event(ctx.conn, {"title": args["title"], "start": start, "end": end, "all_day": all_day,
                                             "timezone": tz, "location": args.get("location", ""),
                                             "description": args.get("description", "")})
    when = event["start"] if all_day else datetime.fromisoformat(event["start"]).strftime("%a %d %b, %H:%M")
    return Result(f"Added {event['title']} to the calendar ({when})", {"event_id": event["id"], "start": event["start"]},
                  undo={"undo_id": event["undo_id"]})


def _create_task(ctx: Context, args: dict) -> Result:
    body = {"title": args["title"], "priority": args.get("priority", "normal"),
            "description": args.get("description", ""), "timezone": _calendar_zone(ctx.conn)}
    if "due" in args:
        body["due"] = args["due"] if len(args["due"]) == 10 else _local(args["due"], body["timezone"])
    task = calendar.save_task(ctx.conn, body)
    return Result(f"Added the task {task['title']}", {"task_id": task["id"]}, undo={"undo_id": task["undo_id"]})


def _undo_calendar(ctx: Context, undo: dict) -> None:
    try:
        calendar.undo(ctx.conn, undo["undo_id"])
    except Exception as exc:  # calendar.fail raises the web layer's APIError
        raise ToolError(str(exc)) from None


# Study ------------------------------------------------------------------------

def _subject_id(conn, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM study_subjects WHERE name=? COLLATE NOCASE ORDER BY id LIMIT 1", (name,)).fetchone()
    if row is None:
        raise ToolError(f"No study subject is called {name!r}")
    return row["id"]


def _create_subject(ctx: Context, args: dict) -> Result:
    body = {"name": args["name"], **({"color": args["color"]} if "color" in args else {})}
    item = study.save_entity(ctx.conn, "subjects", body)["item"]
    return Result(f"Added the subject {item['name']}", {"subject_id": item["id"]},
                  undo={"kind": "subjects", "id": item["id"], "revision": item["revision"]})


def _create_study_task(ctx: Context, args: dict) -> Result:
    body = {"title": args["title"], "subject_id": _subject_id(ctx.conn, args.get("subject")),
            "priority": args.get("priority", "normal")}
    if "due_date" in args:
        body["due_date"] = args["due_date"]
    item = study.save_entity(ctx.conn, "tasks", body)["item"]
    return Result(f"Added the study task {item['title']}", {"task_id": item["id"]},
                  undo={"kind": "tasks", "id": item["id"], "revision": item["revision"]})


def _undo_study(ctx: Context, undo: dict) -> None:
    if not ctx.conn.execute(f"SELECT 1 FROM {study.TABLES[undo['kind']]} WHERE id=?", (undo["id"],)).fetchone():
        return
    try:
        study.save_entity(ctx.conn, undo["kind"], {"action": "delete", "id": undo["id"], "revision": undo["revision"]})
    except study.StudyConflict:
        raise ToolError("That study item changed since, so it was kept") from None


# Navigation and search --------------------------------------------------------

def view_for(name: str) -> str | None:
    """The view id for a view id or spoken name."""
    key = " ".join(name.casefold().replace("-", " ").split())
    if key.replace(" ", "-") in VIEWS:
        return key.replace(" ", "-")
    return VIEW_ALIASES.get(key) or VIEW_ALIASES.get(key.removeprefix("my ").removeprefix("the "))


def _open_view(ctx: Context, args: dict) -> Result:
    view = args["view"]
    owner = VIEWS[view]
    if owner is not None and owner not in ctx.app.modules:
        raise ToolError(f"The {view} view belongs to the {owner} module, which is off")
    return Result(f"Opened {view}", {"view": view}, client={"type": "navigate", "view": view})


def _search(ctx: Context, args: dict) -> Result:
    conn, pattern, modules = ctx.conn, _like(args["query"]), ctx.app.modules
    results: dict[str, list] = {}
    if "workspace" in modules:
        results["pages"] = [{"id": row["id"], "title": row["title"]} for row in conn.execute(
            "SELECT id,title FROM note_pages WHERE title LIKE ? ESCAPE '\\' OR search_text LIKE ? ESCAPE '\\'"
            " ORDER BY updated_at DESC LIMIT ?", (pattern, pattern, SEARCH_LIMIT))]
        results["blocks"] = [{"id": row["id"], "page_id": row["page_id"], "text": _private(conn, row["text"])}
                             for row in conn.execute("SELECT id,page_id,text FROM note_blocks WHERE text LIKE ? ESCAPE '\\'"
                                                     " LIMIT ?", (pattern, SEARCH_LIMIT))]
        results["events"] = [{"id": row["id"], "title": row["title"], "start": row["start"]} for row in conn.execute(
            "SELECT id,title,start FROM calendar_events WHERE deleted=0 AND title LIKE ? ESCAPE '\\'"
            " ORDER BY start DESC LIMIT ?", (pattern, SEARCH_LIMIT))]
        results["tasks"] = [{"id": item["id"], "title": item["title"], "due": item.get("due", ""), "status": item["status"]}
                            for item in calendar.tasks(conn, args["query"])[:SEARCH_LIMIT]]
        results["cards"] = [{"id": row["id"], "front": next(iter(json.loads(row["fields"]).values()), "")[:300]}
                            for row in conn.execute("SELECT id,fields FROM flash_notes WHERE archived=0 AND fields LIKE ?"
                                                    " ESCAPE '\\' LIMIT ?", (pattern, SEARCH_LIMIT))]
        query = args["query"].casefold()
        maps = []
        for row in conn.execute("SELECT id,title,graph_json FROM schema_maps ORDER BY updated_at DESC"):
            topics = [node["label"] for node in json.loads(row["graph_json"])["nodes"] if query in node["label"].casefold()]
            if topics or query in row["title"].casefold():
                maps.append({"id": row["id"], "title": row["title"], "topics": topics[:5]})
        results["maps"] = maps[:SEARCH_LIMIT]
        results["subjects"] = [dict(row) for row in conn.execute(
            "SELECT id,name FROM study_subjects WHERE name LIKE ? ESCAPE '\\' LIMIT ?", (pattern, SEARCH_LIMIT))]
        results["study_tasks"] = [dict(row) for row in conn.execute(
            "SELECT id,title FROM study_tasks WHERE title LIKE ? ESCAPE '\\' LIMIT ?", (pattern, SEARCH_LIMIT))]
    if "applyai" in modules:
        results["jobs"] = [dict(row) for row in conn.execute(
            "SELECT j.id,j.title,c.display_name AS company FROM jobs j JOIN companies c ON c.id=j.company_id"
            " WHERE j.title LIKE ? ESCAPE '\\' OR c.display_name LIKE ? ESCAPE '\\' ORDER BY j.last_seen_at DESC LIMIT ?",
            (pattern, pattern, SEARCH_LIMIT))]
    total = sum(len(items) for items in results.values())
    if not total:
        return Result(f"Nothing matches {args['query']}", results)
    names = [f"{item[field]} ({label})" for kind, label, field in _NAMED for item in results.get(kind, [])]
    shown = names[:SUMMARY_NAMES]
    rest = total - len(shown)
    return Result(f"Found {total} match{'es' if total != 1 else ''} for {args['query']}: " + ", ".join(shown)
                  + (f" and {rest} more" if rest else ""), results)


# Declarations -----------------------------------------------------------------

_PAGE = {"page_id": integer("Note page id"), "page": string("Note page title, when the id is unknown", maximum=200)}
_MAP = {"map_id": integer("Mind map id; defaults to the most recent map"),
        "map": string("Mind map title, when the id is unknown", maximum=200)}
_DATE = r"YYYY-MM-DD"

TOOLS = (
    Tool("search", "Search the whole workspace: note pages and blocks, calendar events and tasks, flashcards, "
         "mind maps, study subjects and tasks, and jobs.", obj(("query",), query=string("Words to look for", maximum=300)),
         "read", _search),
    Tool("find_pages", "List note pages whose title or text matches.", obj(query=string("Words to match", minimum=0, maximum=300)),
         "read", _find_pages, module="workspace"),
    Tool("read_page", "Read one note page: its outline blocks and canvas nodes with their ids.",
         obj(("page_id",), page_id=integer("Note page id")), "read", _read_page, module="workspace"),
    Tool("find_blocks", "Find outline blocks containing some text.", obj(("query",), query=string("Text", maximum=300)),
         "read", _find_blocks, module="workspace"),
    Tool("create_page", "Create a note page.",
         obj(("title",), title=string("Page title", maximum=200), notebook=string("Notebook title", maximum=200)),
         "change", _create_page, _undo_create_page, module="workspace"),
    Tool("append_block", "Append a block to a page's outline; defaults to the most recently edited page.",
         obj(("text",), text=string("Block text", maximum=20000), **_PAGE),
         "change", _append_block, _undo_append_block, module="workspace"),
    Tool("edit_block", "Replace the text of an outline block.",
         obj(("block_id", "text"), block_id=string("Block UUID", maximum=36), text=string("New text", minimum=0, maximum=20000)),
         "change", _edit_block, _undo_edit_block, module="workspace"),
    Tool("add_canvas_node", "Add a sticky-note node to a page's canvas; defaults to the most recently edited page.",
         obj(("text",), text=string("Node text", maximum=20000), x=number("Canvas x"), y=number("Canvas y"), **_PAGE),
         "change", _add_canvas_node, _undo_add_canvas_node, module="workspace"),
    Tool("move_canvas_node", "Move a canvas node to a new position.",
         obj(("node", "x", "y"), node=string("Node id or its exact text", maximum=2000), x=number("Canvas x"),
             y=number("Canvas y"), **_PAGE),
         "change", _move_canvas_node, _undo_move_canvas_node, module="workspace"),
    Tool("connect_canvas_nodes", "Draw a connector between two canvas nodes on one page.",
         obj(("from", "to"), **{"from": string("Start node id or text", maximum=2000),
                                "to": string("End node id or text", maximum=2000)}, **_PAGE),
         "change", _connect_canvas_nodes, _undo_connect_canvas_nodes, module="workspace"),
    Tool("find_maps", "List mind maps, or read one map's topics and connections with map_id.",
         obj(query=string("Title words", minimum=0, maximum=200), map_id=integer("Mind map id to read")),
         "read", _find_maps, module="workspace"),
    Tool("add_map_topic", "Add a topic to a mind map, optionally under a parent topic.",
         obj(("label",), label=string("Topic label", maximum=200), parent=string("Parent topic id or label", maximum=200), **_MAP),
         "change", _add_map_topic, _undo_map, module="workspace"),
    Tool("connect_map_topics", "Connect two topics of a mind map.",
         obj(("from", "to"), **{"from": string("Topic id or label", maximum=200), "to": string("Topic id or label", maximum=200)},
             label=string("Connection label", minimum=0, maximum=200), **_MAP),
         "change", _connect_map_topics, _undo_map, module="workspace"),
    Tool("rename_map_topic", "Rename a topic of a mind map.",
         obj(("topic", "label"), topic=string("Topic id or current label", maximum=200), label=string("New label", maximum=200), **_MAP),
         "change", _rename_map_topic, _undo_map, module="workspace"),
    Tool("add_card", "Add a basic flashcard.",
         obj(("front", "back"), front=string("Question", maximum=10000), back=string("Answer", maximum=10000),
             deck=string("Deck name; Parent::Child for subdecks", maximum=200)),
         "change", _add_card, _undo_add_card, module="workspace"),
    Tool("create_event", "Add a calendar event. Times without an offset are in the calendar's time zone.",
         obj(("title", "start"), title=string("Title", maximum=500),
             start=string(f"ISO start, e.g. 2026-11-03T15:00, or {_DATE} when all_day", maximum=100),
             end=string("ISO end; defaults to one hour (or one day) later", maximum=100),
             all_day=boolean("An all-day event"), timezone=string("IANA zone", maximum=100),
             location=string("Location", minimum=0, maximum=500), description=string("Notes", minimum=0, maximum=5000)),
         "change", _create_event, _undo_calendar, module="workspace"),
    Tool("create_task", "Add a calendar task.",
         obj(("title",), title=string("Title", maximum=500), due=string(f"Due {_DATE} or ISO time", maximum=100),
             priority=string("Priority", enum=("low", "normal", "high", "urgent")),
             description=string("Notes", minimum=0, maximum=5000)),
         "change", _create_task, _undo_calendar, module="workspace"),
    Tool("create_subject", "Add a study subject.",
         obj(("name",), name=string("Subject name", maximum=250), color=string("#RRGGBB colour", maximum=7)),
         "change", _create_subject, _undo_study, module="workspace"),
    Tool("create_study_task", "Add a study task, optionally for a subject.",
         obj(("title",), title=string("Title", maximum=500), subject=string("Subject name", maximum=250),
             due_date=string(f"Due {_DATE}", maximum=10), priority=string("Priority", enum=("low", "normal", "high"))),
         "change", _create_study_task, _undo_study, module="workspace"),
    Tool("open_view", "Open a view of the app for the user.",
         obj(("view",), view=string("View id", enum=tuple(VIEWS))), "change", _open_view),
)

"""The full-text search index: exact, incremental, and checked against a rebuild (paper §16).

One external-content table, ``search_docs``, holds a document per searchable record;
two FTS5 indexes read it (migration 046):

- ``search_fts``: words. ``unicode61 remove_diacritics 2`` folds case and diacritics
  ("cafe" finds "Café") with Unicode word boundaries; prefix indexes of 2 and 3
  characters make type-ahead prefix queries cheap; bm25 ranks with the title column
  weighted ``TITLE_WEIGHT`` times the body.
- ``search_grams``: trigrams. Substring matches (``LIKE '%q%'`` semantics, which Notes
  find always had) and runs of CJK text that ``unicode61`` keeps as one token. A query of
  three or more characters adds the substring matches the word index did not find.

External content rather than contentless: snippets and highlights need the text, the
oracle compares documents column by column, and ``search_docs`` carries the metadata
(kind, record, page, flags) queries filter and group on. The text is stored once more
than in its source table; ``tests/test_search_index.py`` and docs/platform-design.md
report the measured size.

Documents come from two writers, both inside the writing transaction:

- blocks: kitty.core.blocks.store folds every block transaction (kitty.core.blocks.searchdocs);
- every other source table: SQL triggers queue ``(kind, ref)`` in ``search_pending``,
  and `sync` (run before every query and check) recomputes those documents. The queue
  commits with the write that caused it, so no change can be lost, and no application
  function has to be registered for a write to succeed.

`check` is the oracle: it rebuilds every document from scratch (blocks by replaying each
page's snapshot and op log, the rest from their tables) into a temporary table, compares
it with the incremental documents, runs FTS5's integrity check on both indexes, and
repairs and logs any difference. ``kitty index check`` runs it; the server runs it a few
minutes after start and every six hours (kitty.web_search).

A migration that rebuilds one of the source tables drops its triggers: it must create
them again (`TRIGGERS` lists them; `check` reports a missing one).
"""
from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from typing import Iterable, Iterator

from kitty.core import calendar
from kitty.core.blocks import store
from kitty.core.blocks.searchdocs import COLUMNS, TEXT_TYPES, Doc, block_doc, has_index, write
from kitty.db.connection import transaction
from kitty.modules import MODULE_IDS

log = logging.getLogger("kitty.search")

TITLE_WEIGHT = 8.0
#: Flag bit: a calendar copy imported from ApplyAI data (hidden while ApplyAI is off).
APPLYAI = 1
TOKEN = re.compile(r"[^\W_]+")
MAX_QUERY = 300
MAX_TOKENS = 16
MAX_LIMIT = 200
HIT, END = "\x02", "\x03"
TAG = re.compile(r"<[^>]{0,2000}>")
CHECKS_KEPT = 50
#: Every trigger that queues a source-table change (migration 046).
TRIGGERS = tuple(f"search_{table}_{event}" for table, events in (
    ("study_subjects", "iud"), ("study_projects", "iud"), ("study_tasks", "iud"), ("calendar_events", "iud"),
    ("calendar_tasks", "iud"), ("flash_decks", "iud"), ("flash_notes", "iud"), ("flash_types", "u"),
    ("jobs", "iud"), ("companies", "u"),
) for event in ({"i": "insert", "u": "update", "d": "delete"}[letter] for letter in events)) + (
    "search_docs_insert", "search_docs_delete", "search_docs_update")


class SearchUnavailable(RuntimeError):
    """The database has no search index (a schema before migration 046)."""


def require(conn: sqlite3.Connection) -> None:
    if not has_index(conn):
        raise SearchUnavailable("This database has no search index; open it with rika! to apply migration 046")


def clean(value: str | None) -> str:
    """Text without HTML tags or entities (flashcard fields and job descriptions are HTML)."""
    value = value or ""
    return html.unescape(TAG.sub(" ", value)) if "<" in value or "&" in value else value


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ documents


def _chunks(refs: list[str] | None, numeric: bool) -> Iterator[tuple[str, list]]:
    """``(where, params)`` for every record (`refs` None) or for chunks of the named ones."""
    if refs is None:
        yield "1", []
        return
    values = [int(ref) for ref in refs if ref.isdigit()] if numeric else list(refs)
    for start in range(0, len(values), 500):
        chunk = values[start:start + 500]
        yield f"IN ({','.join('?' * len(chunk))})", chunk


def _select(conn, sql: str, column: str, refs, numeric=True):
    for where, params in _chunks(refs, numeric):
        yield from conn.execute(sql.format(where=f"{column} {where}" if params else where), params)


def _subjects(conn, refs):
    for row in _select(conn, "SELECT id,name FROM study_subjects WHERE {where}", "id", refs):
        yield Doc("study_subject", str(row["id"]), None, "", row["name"], "", row["name"][:200])


def _projects(conn, refs):
    for row in _select(conn, "SELECT id,name FROM study_projects WHERE {where}", "id", refs):
        yield Doc("study_project", str(row["id"]), None, "", row["name"], "", row["name"][:200])


def _study_tasks(conn, refs):
    for row in _select(conn, "SELECT id,title,checklist_json FROM study_tasks WHERE {where}", "id", refs):
        items = _json(row["checklist_json"], [])
        parts = [item["text"] for item in items if isinstance(item, dict) and isinstance(item.get("text"), str)] \
            if isinstance(items, list) else []
        yield Doc("study_task", str(row["id"]), None, "", row["title"], "\n".join(parts), row["title"][:200])


def _events(conn, refs):
    for row in _select(conn, "SELECT id,title,data,source_key FROM calendar_events WHERE deleted=0 AND {where}",
                       "id", refs, numeric=False):
        data = _json(row["data"], {})
        description = data.get("description") if isinstance(data, dict) and isinstance(data.get("description"), str) else ""
        flags = APPLYAI if calendar.applyai_sourced(row["source_key"]) else 0
        yield Doc("calendar_event", row["id"], None, "", row["title"], description, row["title"][:200], flags)


def _calendar_tasks(conn, refs):
    for row in _select(conn, "SELECT id,data FROM calendar_tasks WHERE deleted=0 AND {where}", "id", refs, numeric=False):
        data = _json(row["data"], {})
        if isinstance(data, dict) and isinstance(data.get("title"), str):
            description = data.get("description") if isinstance(data.get("description"), str) else ""
            yield Doc("calendar_task", row["id"], None, "", data["title"], description, data["title"][:200])


def _decks(conn, refs):
    for row in _select(conn, "SELECT id,name FROM flash_decks WHERE archived=0 AND {where}", "id", refs):
        yield Doc("flashcard_deck", str(row["id"]), None, "", row["name"], "", row["name"][:200])


def _flash_notes(conn, refs):
    """A note's first field (the front) is its title; the other fields are its body."""
    for row in _select(conn, """SELECT n.id,n.deck_id,n.fields,t.fields AS names,d.name AS deck FROM flash_notes n
            JOIN flash_types t ON t.id=n.type_id JOIN flash_decks d ON d.id=n.deck_id
            WHERE n.archived=0 AND d.archived=0 AND {where}""", "n.id", refs):
        fields, names = _json(row["fields"], {}), _json(row["names"], [])
        if not isinstance(fields, dict):
            continue
        front = names[0] if isinstance(names, list) and names and isinstance(names[0], str) else None
        title = clean(fields[front]) if isinstance(fields.get(front), str) else ""
        rest = [clean(value) for key, value in fields.items() if key != front and isinstance(value, str)]
        label = next((" ".join(part.split()) for part in (title, *rest) if part.strip()), row["deck"])[:200]
        yield Doc("flashcard_note", str(row["id"]), str(row["deck_id"]), "", title, "\n".join(rest), label)


def _jobs(conn, refs):
    for row in _select(conn, """SELECT j.id,j.title,j.location,j.jd_text,c.display_name AS company FROM jobs j
            LEFT JOIN companies c ON c.id=j.company_id WHERE {where}""", "j.id", refs):
        company = row["company"] or ""
        body = "\n".join(part for part in (company, row["location"] or "", clean(row["jd_text"])) if part)
        label = f"{row['title']} · {company}" if company else row["title"]
        yield Doc("job", str(row["id"]), None, "", row["title"], body, label[:200])


#: Every non-block kind and how its documents are made from its source table.
SOURCES = {"study_subject": _subjects, "study_project": _projects, "study_task": _study_tasks,
           "calendar_event": _events, "calendar_task": _calendar_tasks, "flashcard_deck": _decks,
           "flashcard_note": _flash_notes, "job": _jobs}
_BLOCK_ROWS = (f"SELECT * FROM blocks WHERE deleted_at IS NULL AND (parent_id IS NULL OR type IN "
               f"({','.join(repr(kind) for kind in sorted(TEXT_TYPES))}))")


def _projection_blocks(conn, ids: list[str] | None = None) -> Iterator[Doc]:
    rows = conn.execute(_BLOCK_ROWS) if ids is None else (
        row for start in range(0, len(ids), 500) for row in conn.execute(
            f"SELECT * FROM blocks WHERE id IN ({','.join('?' * len(ids[start:start + 500]))})", ids[start:start + 500]))
    for row in rows:
        doc = block_doc(store._block(row))
        if doc is not None:
            yield doc


def _replayed_blocks(conn, report: dict) -> Iterator[Doc]:
    """Every block document as replaying each page's newest snapshot plus its op log makes it."""
    pages = [row[0] for row in conn.execute(
        "SELECT page_id FROM op_log UNION SELECT page_id FROM page_snapshots UNION SELECT page_id FROM blocks")]
    for page_id in pages:
        try:
            doc = store.replay(conn, page_id)
        except Exception as exc:  # noqa: BLE001 - one unreplayable page must not hide the others
            report["unreplayable"].append({"page_id": page_id, "error": f"{type(exc).__name__}: {exc}"[:300]})
            yield from (item for item in _projection_blocks(conn, [row[0] for row in conn.execute(
                "SELECT id FROM blocks WHERE page_id=?", (page_id,))]))
            continue
        for block in doc.blocks.values():
            item = block_doc(block)
            if item is not None:
                yield item


def documents(conn: sqlite3.Connection, *, blocks: str = "projection", report: dict | None = None) -> Iterator[Doc]:
    """Every document the index should hold: blocks from the projection (or ``blocks="log"``: replayed), then the rest."""
    yield from (_replayed_blocks(conn, report if report is not None else {"unreplayable": []})
                if blocks == "log" else _projection_blocks(conn))
    for source in SOURCES.values():
        yield from source(conn, None)


def _current(conn, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], Doc]:
    """The documents the named records should have now (projection and source tables)."""
    wanted: dict[str, list[str]] = defaultdict(list)
    for kind, ref in keys:
        wanted[kind].append(ref)
    found = {}
    for kind, refs in wanted.items():
        made = _projection_blocks(conn, refs) if kind == "block" else SOURCES[kind](conn, refs) if kind in SOURCES else ()
        found.update(((doc.kind, doc.ref), doc) for doc in made)
    return found


# ------------------------------------------------------------------ maintenance


def sync(conn: sqlite3.Connection) -> int:
    """Fold the queued source-table changes into the index; returns how many records were refreshed."""
    require(conn)
    if conn.execute("SELECT 1 FROM search_pending LIMIT 1").fetchone() is None:
        return 0
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        pending = [(row[0], row[1]) for row in conn.execute("SELECT kind,ref FROM search_pending")]
        docs = _current(conn, pending)
        write(conn, docs.values(), [key for key in pending if key not in docs])
        conn.executemany("DELETE FROM search_pending WHERE kind=? AND ref=?", pending)
    return len(pending)


def build(conn: sqlite3.Connection) -> int:
    """Replace every document with one made from the current data (migration 046, ``kitty index rebuild``)."""
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        # Rebuilding both indexes from the stored documents first makes them consistent even if
        # they were corrupt, so the deletes below (each one a trigger-driven FTS delete) are exact.
        for table in ("search_fts", "search_grams"):
            conn.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")
        conn.execute("DELETE FROM search_pending")
        conn.execute("DELETE FROM search_docs")
        docs = list(documents(conn))
        write(conn, docs, [])
    return len(docs)


def _fts_ok(conn) -> dict[str, bool]:
    result = {}
    for table in ("search_fts", "search_grams"):
        try:
            conn.execute(f"INSERT INTO {table}({table}, rank) VALUES('integrity-check', 1)")
            result[table] = True
        except sqlite3.DatabaseError:
            result[table] = False
    return result


_REBUILD = ("CREATE TEMP TABLE search_rebuild(kind TEXT NOT NULL, ref TEXT NOT NULL, page_id TEXT, type TEXT, "
            "title TEXT, body TEXT, label TEXT, flags INTEGER, PRIMARY KEY(kind, ref))")
_DIFFERENT = " OR ".join(f"r.{column} IS NOT d.{column}" for column in COLUMNS[2:])


def _compare(conn, blocks: str, report: dict) -> list[tuple[str, str, str]]:
    """``(kind, ref, problem)`` for every document the rebuild and the incremental index disagree on."""
    conn.execute("DROP TABLE IF EXISTS temp.search_rebuild")
    conn.execute(_REBUILD)
    try:
        conn.executemany(f"INSERT OR REPLACE INTO temp.search_rebuild({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                         documents(conn, blocks=blocks, report=report))
        report["docs"] = conn.execute("SELECT COUNT(*) FROM temp.search_rebuild").fetchone()[0]
        return [tuple(row) for row in conn.execute(f"""
            SELECT r.kind,r.ref,'missing' FROM temp.search_rebuild r
                WHERE NOT EXISTS (SELECT 1 FROM main.search_docs d WHERE d.kind=r.kind AND d.ref=r.ref)
            UNION ALL SELECT d.kind,d.ref,'extra' FROM main.search_docs d
                WHERE NOT EXISTS (SELECT 1 FROM temp.search_rebuild r WHERE r.kind=d.kind AND r.ref=d.ref)
            UNION ALL SELECT r.kind,r.ref,'changed' FROM temp.search_rebuild r
                JOIN main.search_docs d ON d.kind=r.kind AND d.ref=r.ref WHERE {_DIFFERENT}
            ORDER BY 1,2""")]
    finally:
        conn.execute("DROP TABLE temp.search_rebuild")


def check(conn: sqlite3.Connection, *, repair: bool = True, blocks: str = "log",
          lock: AbstractContextManager | None = None) -> dict:
    """Rebuild the index from scratch, compare it with the incremental one and repair any difference.

    ``blocks="log"`` (the default) rebuilds block documents by replaying every page's
    snapshot and op log, so the check also covers the projection the index folds;
    ``"projection"`` reads the projection directly. A document the incremental index got
    wrong is rewritten from the current data; a difference that remains (the replayed
    log disagrees with the projection itself) is reported as ``drift`` and not
    "repaired" away. ``lock`` is held around the writes (the server's mutation lock).
    """
    started = time.perf_counter()
    require(conn)
    lock = lock or nullcontext()
    with lock:
        synced = sync(conn)
    report: dict = {"ok": True, "docs": 0, "synced": synced, "mismatches": [], "repaired": 0, "drift": [],
                    "unreplayable": [], "fts": {}, "missing_triggers": []}
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'search_%'")}
    report["missing_triggers"] = [name for name in TRIGGERS if name not in present]
    with (nullcontext() if conn.in_transaction else transaction(conn)):
        problems = _compare(conn, blocks, report)
    report["fts"] = _fts_ok(conn)
    report["mismatches"] = [{"kind": kind, "ref": ref, "problem": problem} for kind, ref, problem in problems]
    if (problems or not all(report["fts"].values())) and repair:
        with lock, (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
            keys = [(kind, ref) for kind, ref, _ in problems]
            docs = _current(conn, keys)
            stored = {(row["kind"], row["ref"]): Doc(*(row[column] for column in COLUMNS)) for key in keys
                      for row in conn.execute(f"SELECT {','.join(COLUMNS)} FROM search_docs WHERE kind=? AND ref=?", key)}
            fixes = [key for key in keys if docs.get(key) != stored.get(key)]
            report["drift"] = [{"kind": kind, "ref": ref} for kind, ref in keys if docs.get((kind, ref)) == stored.get((kind, ref))]
            write(conn, [docs[key] for key in fixes if key in docs], [key for key in fixes if key not in docs])
            report["repaired"] = len(fixes)
            for table, ok in report["fts"].items():
                if not ok:
                    conn.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")
    report["ok"] = not problems and all(report["fts"].values()) and not report["missing_triggers"] and not report["unreplayable"]
    report["seconds"] = round(time.perf_counter() - started, 3)
    if not report["ok"]:
        log.warning("search index check: %d mismatched documents (%d repaired, %d drift), fts %s, missing triggers %s, "
                    "unreplayable pages %d", len(problems), report["repaired"], len(report["drift"]), report["fts"],
                    report["missing_triggers"], len(report["unreplayable"]))
    with lock:
        _record(conn, report)
    return report


def _record(conn, report: dict) -> None:
    detail = {key: report[key][:20] for key in ("mismatches", "drift", "unreplayable")}
    detail.update(fts=report["fts"], missing_triggers=report["missing_triggers"], synced=report["synced"])
    with (nullcontext() if conn.in_transaction else transaction(conn, immediate=True)):
        conn.execute("INSERT INTO search_checks(at,docs,mismatches,repaired,drift,fts_ok,seconds,detail) VALUES (?,?,?,?,?,?,?,?)",
                     (datetime.now(timezone.utc).isoformat(), report["docs"], len(report["mismatches"]), report["repaired"],
                      len(report["drift"]), int(all(report["fts"].values())), report["seconds"], json.dumps(detail)))
        conn.execute("DELETE FROM search_checks WHERE id <= (SELECT MAX(id) FROM search_checks) - ?", (CHECKS_KEPT,))


def last_check(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM search_checks ORDER BY id DESC LIMIT 1").fetchone()
    return None if row is None else {**dict(row), "detail": json.loads(row["detail"])}


# ------------------------------------------------------------------ queries


def words(text: str) -> list[str]:
    """The word tokens of a query as the index sees them (case folded, Unicode-aware)."""
    return [token.casefold() for token in TOKEN.findall(text)]


def prefix_query(text: str) -> str | None:
    """Every word must match the start of an indexed word: type-ahead (``"mito"`` finds "Mitochondria")."""
    tokens = words(text)[:MAX_TOKENS]
    return " AND ".join(f'"{token}"*' for token in tokens) or None


def substring_query(text: str) -> str | None:
    """The whole query as a substring (three characters or more; the trigram index)."""
    text = " ".join(text.split())
    return f'"{text.replace(chr(34), chr(34) * 2)}"' if len(text) >= 3 else None


def segments(marked: str) -> list[list]:
    """A snippet with HIT/END markers as ``[[text, is_hit], ...]``."""
    result, hit = [], False
    for part in re.split(f"([{HIT}{END}])", marked or ""):
        if part == HIT:
            hit = True
        elif part == END:
            hit = False
        elif part:
            result.append([part, hit])
    return result


#: Result kinds: the SQL condition on a document (``d``) and its page root (``r``), and the view that opens it.
KINDS = {
    "note_page": ("d.kind='block' AND r.type='page'", "notes", "workspace"),
    "document": ("d.kind='block' AND r.type='document'", "documents", "workspace"),
    "quick_note": ("d.kind='block' AND r.type='quick_note'", "quick-notes", "workspace"),
    "tag": ("d.kind='block' AND r.type='tag'", "settings", "workspace"),
    "study_subject": ("d.kind='study_subject'", "study", "workspace"),
    "study_project": ("d.kind='study_project'", "study", "workspace"),
    "study_task": ("d.kind='study_task'", "study", "workspace"),
    "calendar_event": ("d.kind='calendar_event'", "calendar", "workspace"),
    "calendar_task": ("d.kind='calendar_task'", "calendar", "workspace"),
    "flashcard_deck": ("d.kind='flashcard_deck'", "flashcards", "workspace"),
    "flashcard_note": ("d.kind='flashcard_note'", "flashcards", "workspace"),
    "job": ("d.kind='job'", "jobs", "applyai"),
}
_HITS = """SELECT d.id,d.kind,d.ref,d.page_id,d.title AS doc_title,d.label,r.type AS root_type,r.text AS root_title,
    CAST(json_extract(r.properties,'$.number') AS INTEGER) AS number,{rank} AS score
    FROM {table} JOIN search_docs d ON d.id={table}.rowid LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id
    WHERE {table} MATCH ? AND ({kinds}) ORDER BY score, d.id"""
#: The record key of every matching document (a page's blocks count once), for ``total``.
_KEYS = """SELECT CASE WHEN d.kind='block' THEN 'block:'||d.page_id ELSE d.kind||':'||d.ref END
    FROM {table} JOIN search_docs d ON d.id={table}.rowid LEFT JOIN blocks r ON d.kind='block' AND r.id=d.page_id
    WHERE {table} MATCH ? AND ({kinds})"""


def kind_filter(kinds: Iterable[str] | None = None, modules: Iterable[str] = MODULE_IDS) -> str:
    """SQL over ``d``/``r`` admitting the result kinds the active modules show (and `kinds`, when given)."""
    modules = set(modules)
    allowed = [kind for kind, (_, _, module) in KINDS.items() if module in modules and (kinds is None or kind in kinds)]
    clauses = [KINDS[kind][0] + (" AND d.flags & 1 = 0" if kind == "calendar_event" and "applyai" not in modules else "")
               for kind in allowed]
    return " OR ".join(f"({clause})" for clause in clauses) or "0"


def _record_of(row) -> tuple[str, str, str | int, str]:
    """``(kind, key, id, title)`` of the record a document belongs to."""
    if row["kind"] == "block":
        kind = {"page": "note_page", "document": "document", "quick_note": "quick_note", "tag": "tag"}[row["root_type"]]
        ident = row["page_id"] if kind == "tag" else row["number"]
        title = row["root_title"].strip().split("\n", 1)[0][:200] if kind == "quick_note" else row["root_title"][:200]
        return kind, f"block:{row['page_id']}", ident, title
    ident = int(row["ref"]) if row["ref"].isdigit() and row["kind"] not in {"calendar_event", "calendar_task"} else row["ref"]
    return row["kind"], f"{row['kind']}:{row['ref']}", ident, row["label"]


def _marks(conn, table: str, query: str, rowid: int, tokens: int) -> tuple[list, list]:
    row = conn.execute(f"SELECT highlight({table},0,?,?),snippet({table},1,?,?,'…',?) FROM {table} WHERE {table} MATCH ? AND rowid=?",
                       (HIT, END, HIT, END, tokens, query, rowid)).fetchone()
    return (segments(row[0]), segments(row[1])) if row else ([], [])


def search(conn: sqlite3.Connection, text: str, *, limit: int = 20, kinds: Iterable[str] | None = None,
           modules: Iterable[str] = MODULE_IDS) -> dict:
    """Ranked records for a typed query, one hit per record (a page's best block), with highlighted snippets.

    Word-prefix matches come first in bm25 order (titles weigh ``TITLE_WEIGHT`` times
    the body); then, for three characters or more, substring matches the word index
    missed. ``truncated`` says more records matched than ``limit``; ``total`` counts them.
    """
    if not isinstance(text, str) or len(text) > MAX_QUERY or "\x00" in text:
        raise ValueError(f"Search text must be a string of at most {MAX_QUERY} characters")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"Limit must be 1-{MAX_LIMIT}")
    if kinds is not None and (kinds := set(kinds)) - set(KINDS):
        raise ValueError(f"Kinds are {', '.join(KINDS)}")
    sync(conn)
    allowed = kind_filter(kinds, modules)
    hits, seen, more = [], set(), set()
    plans = [(table, query, rank, match, tokens) for table, query, rank, match, tokens in (
        ("search_fts", prefix_query(text), f"bm25(search_fts,{TITLE_WEIGHT},1.0)", "word", 12),
        ("search_grams", substring_query(text), f"bm25(search_grams,{TITLE_WEIGHT},1.0)", "substring", 48),
    ) if query]
    with (nullcontext() if conn.in_transaction else transaction(conn)):
        for table, query, rank, match, tokens in plans:
            if more:
                break
            for row in conn.execute(_HITS.format(table=table, rank=rank, kinds=allowed), (query,)):
                kind, key, ident, title = _record_of(row)
                if key in seen:
                    continue
                if len(hits) >= limit:
                    more.add(key)
                    break
                seen.add(key)
                hits.append((row["id"], table, query, tokens, row["doc_title"],
                             {"kind": kind, "id": ident, "title": title, "view": KINDS[kind][1], "match": match}))
        total = len(hits)
        if more:
            total = conn.execute("SELECT COUNT(*) FROM (" + " UNION ".join(_KEYS.format(table=table, kinds=allowed) for table, *_ in plans)
                                 + ")", [query for _, query, *_ in plans]).fetchone()[0]
        result = []
        for rowid, table, query, tokens, doc_title, hit in hits:
            title, snippet = _marks(conn, table, query, rowid, tokens)
            # The record's title is marked where the hit document's own title is it (a page root,
            # a job's title before " · company"); a block inside a page shows its page title plainly.
            rest = hit["title"][len(doc_title):] if doc_title and hit["title"].startswith(doc_title) else None
            marked = rest is not None and any(part[1] for part in title)
            hit["title_marks"] = [*title, *([[rest, False]] if rest else [])] if marked else [[hit["title"], False]]
            hit["snippet"] = snippet
            result.append(hit)
    return {"query": text, "hits": result, "total": total, "truncated": bool(more)}


def page_matches(conn: sqlite3.Connection, text: str) -> set[str]:
    """Root block IDs of note pages whose title or text matches (word prefixes, or substrings of 3+ characters)."""
    sync(conn)
    found: set[str] = set()
    for table, query in (("search_fts", prefix_query(text)), ("search_grams", substring_query(text))):
        if query:
            found.update(row[0] for row in conn.execute(
                f"""SELECT DISTINCT d.page_id FROM {table} JOIN search_docs d ON d.id={table}.rowid
                JOIN blocks r ON r.id=d.page_id WHERE {table} MATCH ? AND d.kind='block' AND r.type='page'""", (query,)))
    return found


def page_titles_within(conn: sqlite3.Connection, tokens: set[str], *, exclude: str | None = None) -> list[dict]:
    """``{id, block_id, title}`` of every live note page whose title's words all occur in `tokens`.

    The title column is searched for any of the words (200 per query), then each title is
    kept only when every one of its words is among them. A title with no word characters
    at all (only punctuation) is never found.
    """
    require(conn)
    found: dict[str, dict] = {}
    ordered = sorted(tokens)
    for start in range(0, len(ordered), 200):
        expression = "{title} : (" + " OR ".join(f'"{token}"' for token in ordered[start:start + 200]) + ")"
        for row in conn.execute("""SELECT d.ref,d.title,CAST(json_extract(r.properties,'$.number') AS INTEGER) AS number
                FROM search_fts JOIN search_docs d ON d.id=search_fts.rowid JOIN blocks r ON r.id=d.ref
                WHERE search_fts MATCH ? AND d.kind='block' AND d.type='page' AND d.ref=d.page_id""", (expression,)):
            if row["ref"] != exclude and row["ref"] not in found and set(words(row["title"])) <= tokens:
                found[row["ref"]] = {"id": row["number"], "block_id": row["ref"], "title": row["title"]}
    return list(found.values())


def stats(conn: sqlite3.Connection) -> dict:
    """Documents per kind and the pages each index uses."""
    require(conn)
    kinds = {row[0]: row[1] for row in conn.execute("SELECT kind,COUNT(*) FROM search_docs GROUP BY kind ORDER BY kind")}
    pages = {}
    for table in ("search_docs", "search_fts", "search_grams"):
        try:
            pages[table] = conn.execute("SELECT SUM(pgsize) FROM dbstat WHERE name LIKE ?", (f"{table}%",)).fetchone()[0] or 0
        except sqlite3.OperationalError:  # dbstat is optional in SQLite builds
            pages[table] = None
    return {"documents": kinds, "bytes": pages, "pending": conn.execute("SELECT COUNT(*) FROM search_pending").fetchone()[0]}

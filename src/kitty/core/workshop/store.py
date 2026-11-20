"""SQLite source snapshots with writer-locked compare-and-swap edits."""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import json
from pathlib import PurePosixPath
import re
import uuid

from kitty.dates import now_iso

MAX_SOURCE = 1024 * 1024
MAX_UPLOAD = 16 * 1024 * 1024
MAX_FILES = 128
FORMATS = {"html": ("resume.html", "chromium"), "typst": ("resume.typ", "typst"),
           "latex": ("resume.tex", "tectonic")}
#: Engines a format may choose; the FORMATS engine is the default.
ENGINES = {"html": {"chromium"}, "typst": {"typst"}, "latex": {"tectonic", "pdflatex", "lualatex"}}


class WorkshopError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text(value, name, *, maximum=4096, empty=False):
    if not isinstance(value, str) or "\x00" in value or len(value.encode("utf-8")) > maximum or (not empty and not value.strip()):
        raise WorkshopError(f"Invalid {name}")
    return value


def positive_int(value, name="revision"):
    if type(value) is not int or value < 1 or value > 2**63 - 1:
        raise WorkshopError(f"{name} must be a positive integer")
    return value


def safe_path(value):
    text(value, "file path", maximum=512)
    if ("\\" in value or ":" in value or value.startswith("/") or
            any(part in ("", ".", "..") for part in value.split("/")) or
            any(ord(char) < 32 for char in value)):
        raise WorkshopError("Files must have safe, relative project paths")
    if len(PurePosixPath(value).parts) > 16:
        raise WorkshopError("Project path is too deep")
    return value


def decode_data(value):
    if not isinstance(value, str) or len(value) > ((MAX_UPLOAD + 2) // 3) * 4:
        raise WorkshopError("File data exceeds the 16 MiB upload limit")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise WorkshopError("Invalid base64 file data") from exc


def validate_assets(assets, entrypoint):
    if assets is None:
        return {}
    if not isinstance(assets, dict) or len(assets) > MAX_FILES:
        raise WorkshopError(f"Assets must be an object with at most {MAX_FILES} files")
    total = 0
    result = {}
    paths = {safe_path(entrypoint)}
    for name, data in assets.items():
        safe_path(name)
        if name == entrypoint:
            raise WorkshopError("Entrypoint belongs in source, not assets")
        raw = decode_data(data)
        total += len(raw)
        if total > MAX_UPLOAD:
            raise WorkshopError("Project assets exceed 16 MiB")
        result[name] = base64.b64encode(raw).decode("ascii")
        paths.add(name)
    for name in paths:
        if any(str(parent) in paths for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise WorkshopError("A project file cannot also be a directory")
    return result


@contextmanager
def atomic(conn):
    """Compose with callers' consent/fact transaction; lock before any CAS read."""
    nested = conn.in_transaction
    name = "workshop_" + uuid.uuid4().hex
    conn.execute(f"SAVEPOINT {name}" if nested else "BEGIN IMMEDIATE")
    try:
        if nested:
            conn.execute("UPDATE workshop_docs SET revision=revision WHERE 0")
        yield
    except BaseException:
        if nested:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
        else:
            conn.rollback()
        raise
    else:
        conn.execute(f"RELEASE {name}" if nested else "COMMIT")


def _doc(row):
    doc = dict(row)
    doc["assets"] = json.loads(doc["assets"])
    doc["archived"] = bool(doc["archived"])
    return doc


def get_doc(conn, doc_id):
    if not isinstance(doc_id, str):
        raise WorkshopError("Invalid document id")
    row = conn.execute("SELECT * FROM workshop_docs WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        raise WorkshopError("Workshop document not found", 404)
    return _doc(row)


def get_revision(conn, doc_id, revision):
    positive_int(revision)
    row = conn.execute("SELECT snapshot FROM workshop_revisions WHERE doc_id=? AND revision=?",
                       (doc_id, revision)).fetchone()
    if row is None:
        raise WorkshopError("Workshop revision not found", 404)
    return json.loads(row["snapshot"])


def list_docs(conn):
    columns = "id,title,format,entrypoint,engine,revision,kind,base_doc_id,base_revision,job_id,company,role,archived,created_at,updated_at"
    return [{**dict(row), "archived": bool(row["archived"])} for row in conn.execute(
        f"SELECT {columns} FROM workshop_docs ORDER BY updated_at DESC,id")]


def _snapshot(conn, doc, author, note):
    text(author, "author", maximum=200)
    text(note, "note", maximum=4096, empty=True)
    conn.execute("INSERT INTO workshop_revisions(doc_id,revision,snapshot,author,note,created_at) VALUES(?,?,?,?,?,?)",
                 (doc["id"], doc["revision"], json.dumps(doc, ensure_ascii=False, sort_keys=True),
                  author, note, doc["updated_at"]))


def _job(conn, job_id):
    if job_id is not None:
        positive_int(job_id, "job_id")
        if not conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
            raise WorkshopError("Job not found", 404)


def create_doc(conn, *, title, format, source, assets=None, entrypoint=None, engine=None,
               kind="master", base_doc_id=None, base_revision=None, job_id=None,
               company="", role="", author="user"):
    text(title, "title", maximum=200)
    text(source, "source", maximum=MAX_SOURCE, empty=True)
    if not isinstance(format, str) or format not in FORMATS:
        raise WorkshopError("Format must be html, typst, or latex")
    default_path, default_engine = FORMATS[format]
    entrypoint = safe_path(entrypoint if entrypoint is not None else default_path)
    engine = default_engine if engine is None else engine
    if engine not in ENGINES[format]:
        raise WorkshopError(f"{format} supports the {', '.join(sorted(ENGINES[format]))} engines")
    assets = validate_assets(assets, entrypoint)
    if kind not in ("master", "tailored"):
        raise WorkshopError("Kind must be master or tailored")
    text(company, "company", maximum=300, empty=True)
    text(role, "role", maximum=300, empty=True)
    with atomic(conn):
        _job(conn, job_id)
        if base_doc_id is not None:
            base = get_doc(conn, base_doc_id)
            base_revision = base["revision"] if base_revision is None else positive_int(base_revision)
            pinned = get_revision(conn, base_doc_id, base_revision)
            for key, value in {"source": source, "assets": assets, "format": format,
                               "entrypoint": entrypoint, "engine": engine}.items():
                if pinned[key] != value:
                    raise WorkshopError("Initial tailored document must match its pinned master snapshot", 409)
        elif base_revision is not None:
            raise WorkshopError("base_revision requires a base document")
        timestamp = now_iso()
        doc = dict(id=str(uuid.uuid4()), title=title, format=format, source=source, assets=assets,
                   entrypoint=entrypoint, engine=engine, revision=1, kind=kind,
                   base_doc_id=base_doc_id, base_revision=base_revision, job_id=job_id,
                   company=company, role=role, archived=False, created_at=timestamp, updated_at=timestamp)
        values = {**doc, "assets": json.dumps(assets, sort_keys=True)}
        conn.execute(f"INSERT INTO workshop_docs({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
        _snapshot(conn, doc, author, "Created document")
    return doc


def _save(conn, old, content, *, base_revision, author, note, force=False):
    positive_int(base_revision, "base_revision")
    if old["archived"]:
        raise WorkshopError("Archived documents cannot be edited; copy to a new project", 409)
    if old["revision"] != base_revision:
        raise WorkshopError("Document changed; preserve your draft and reload before saving", 409)
    if not force and all(old[key] == value for key, value in content.items()):
        return old
    new = {**old, **content, "revision": old["revision"] + 1, "updated_at": now_iso()}
    values = {**content, "revision": new["revision"], "updated_at": new["updated_at"]}
    if "assets" in values:
        values["assets"] = json.dumps(values["assets"], sort_keys=True)
    conn.execute(f"UPDATE workshop_docs SET {','.join(key+'=?' for key in values)} WHERE id=? AND revision=?",
                 (*values.values(), old["id"], base_revision))
    _snapshot(conn, new, author, note)
    return new


def save_source(conn, doc_id, *, source, base_revision, author="user", note="", title=None, assets=None):
    text(source, "source", maximum=MAX_SOURCE, empty=True)
    positive_int(base_revision, "base_revision")
    with atomic(conn):
        old = get_doc(conn, doc_id)
        content = {"source": source}
        if title is not None:
            content["title"] = text(title, "title", maximum=200)
        if assets is not None:
            content["assets"] = validate_assets(assets, old["entrypoint"])
        return _save(conn, old, content, base_revision=base_revision, author=author, note=note)


def copy_doc(conn, doc_id, *, title, company="", role="", job_id=None):
    with atomic(conn):
        old = get_doc(conn, doc_id)
        return create_doc(conn, title=title, format=old["format"], source=old["source"],
                          assets=old["assets"], entrypoint=old["entrypoint"], engine=old["engine"],
                          kind="tailored", base_doc_id=doc_id, base_revision=old["revision"],
                          company=company, role=role, job_id=job_id)


def revisions(conn, doc_id):
    get_doc(conn, doc_id)
    return [dict(row) for row in conn.execute(
        "SELECT revision,author,note,created_at FROM workshop_revisions WHERE doc_id=? ORDER BY revision DESC", (doc_id,))]


def restore_revision(conn, doc_id, *, revision, base_revision):
    positive_int(revision)
    positive_int(base_revision, "base_revision")
    with atomic(conn):
        old = get_doc(conn, doc_id)
        target = get_revision(conn, doc_id, revision)
        content = {key: target[key] for key in ("source", "assets", "title", "format", "entrypoint", "engine")}
        return _save(conn, old, content, base_revision=base_revision, author="user",
                     note=f"Restored revision {revision}", force=True)


def archive_doc(conn, doc_id):
    with atomic(conn):
        doc = get_doc(conn, doc_id)
        conn.execute("UPDATE workshop_docs SET archived=1,updated_at=? WHERE id=?", (now_iso(), doc_id))
        return get_doc(conn, doc_id)

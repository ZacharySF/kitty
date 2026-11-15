"""Resume Workshop source routes: revision-safe edits, sandboxed compiles and explicit exports.

Compiles and imports run as background tasks with their own connection. Downloads
always name a saved revision, so a preview can never show text the user has not saved.
"""
from __future__ import annotations

from contextlib import closing
import re

from kitty.core.workshop import ats, imports, render, store, templates
from kitty.db import connect

PREFIX = "/api/workshop"
_DOC = PREFIX + r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"


def _error(exc):
    from kitty.web_server import APIError
    return APIError(exc.status, str(exc))


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise store.WorkshopError("Invalid Resume Workshop request fields")


def _confirm(body):
    if body.get("confirm") is not True:
        raise store.WorkshopError("Explicit confirmation is required")


def _optional_job(value):
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise store.WorkshopError("job_id must be a positive integer")
    return value


def _revision(conn, doc_id, query):
    """The saved snapshot a download names; the current revision when none is named."""
    _keys(query, ("revision",))
    if "revision" not in query:
        return store.get_doc(conn, doc_id)
    if not re.fullmatch(r"[1-9][0-9]{0,15}", query["revision"]):
        raise store.WorkshopError("revision must be a positive integer")
    return store.get_revision(conn, doc_id, int(query["revision"]))


def _task(app, kind, work, *, write=False):
    def run():
        try:
            if write:
                with app.mutation_lock, closing(connect(app.settings.db_path)) as conn:
                    return work(conn)
            with closing(connect(app.settings.db_path)) as conn:
                return work(conn)
        except store.WorkshopError as exc:
            raise _error(exc) from None
        finally:
            if write:
                app.mutated()
    return {"task": app.tasks.start(kind, run)}


def _download_name(title, extension):
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip()[:80] or "resume"
    return f"{stem}.{extension}"


def get(app, conn, path, query):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    try:
        if path == PREFIX:
            _keys(query, ())
            return {"documents": store.list_docs(conn), "templates": templates.list_templates(),
                    "capabilities": render.capabilities(app.settings)}
        match = re.fullmatch(_DOC, path)
        if match:
            _keys(query, ())
            return {"document": store.get_doc(conn, match[1]), "revisions": store.revisions(conn, match[1])}
        match = re.fullmatch(_DOC + "/ats", path)
        if match:
            # The engine's parser view of a compiled revision, plus the linked posting's terms
            # compared now (the posting and vocabulary can change after the compile).
            doc = _revision(conn, match[1], query)
            try:
                checks = render.compiled_checks(app.settings, doc)
            except store.WorkshopError:
                raise store.WorkshopError("Compile this revision first; the ATS check reads its PDF", 409) from None
            return {"revision": doc["revision"], "report": checks.get("ats"),
                    "job_terms": ats.job_terms(conn, app.config.resume_terms, doc, checks.get("extracted_text", ""))}
        return None
    except store.WorkshopError as exc:
        raise _error(exc) from None


def download(app, conn, path, query):
    match = re.fullmatch(_DOC + r"/(pdf|source|page/([1-9][0-9]{0,2})\.png)", path)
    if not match:
        return None
    try:
        doc = _revision(conn, match[1], query)
        if match[2] == "pdf":
            return render.compiled_pdf(app.settings, doc), "application/pdf", None
        if match[2] == "source":
            return imports.source_zip(doc), "application/zip", _download_name(doc["title"], "zip")
        return render.compiled_page(app.settings, doc, int(match[3])), "image/png", None
    except store.WorkshopError as exc:
        raise _error(exc) from None


def post(app, conn, path, body):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    try:
        if path == PREFIX:
            _keys(body, ("title", "format", "source", "template_id", "engine"), ("title",))
            if body.get("template_id") is not None:
                if "source" in body:
                    raise store.WorkshopError("Choose a template or a source, not both")
                chosen = templates.get_template(body["template_id"])
                if body.get("format", chosen["format"]) != chosen["format"]:
                    raise store.WorkshopError("Template format does not match the requested format")
                fmt, source = chosen["format"], chosen["source"]
                template_engine = chosen.get("engine")
            else:
                if "format" not in body:
                    raise store.WorkshopError("Choose a format or a template")
                fmt, source = body["format"], body.get("source", "")
                template_engine = None
            fmt_engine = body.get("engine") or template_engine
            if fmt_engine is None and isinstance(fmt, str) and fmt in store.FORMATS:
                fmt_engine = render.preferred_engine(app.settings, fmt)
            return {"document": store.create_doc(conn, title=body["title"], format=fmt, source=source,
                                                 engine=fmt_engine)}
        if path == PREFIX + "/import":
            _keys(body, ("files",), ("files",))
            # Decode and bound uploads now; parsing and OCR run in the sandboxed task.
            files = body["files"]
            return _task(app, "workshop-import", lambda own: imports.import_files(own, app.settings, files), write=True)
        match = re.fullmatch(_DOC + r"(?:/(copy|restore|archive|compile|export))?", path)
        if not match:
            return None
        doc_id, action = match[1], match[2]
        if action is None:
            _keys(body, ("source", "base_revision", "title", "assets"), ("source", "base_revision"))
            return {"document": store.save_source(conn, doc_id, source=body["source"],
                                                  base_revision=body["base_revision"],
                                                  title=body.get("title"), assets=body.get("assets"))}
        if action == "copy":
            _keys(body, ("title", "company", "role", "job_id"), ("title",))
            return {"document": store.copy_doc(conn, doc_id, title=body["title"],
                                               company=body.get("company", ""), role=body.get("role", ""),
                                               job_id=_optional_job(body.get("job_id")))}
        if action == "restore":
            _keys(body, ("revision", "base_revision"), ("revision", "base_revision"))
            return {"document": store.restore_revision(conn, doc_id, revision=body["revision"],
                                                       base_revision=body["base_revision"])}
        if action == "archive":
            _keys(body, ("confirm",), ("confirm",))
            _confirm(body)
            return {"document": store.archive_doc(conn, doc_id)}
        if action == "compile":
            _keys(body, ("revision",), ("revision",))
            snapshot = store.get_revision(conn, doc_id, store.positive_int(body["revision"]))
            # One running compile per saved revision; the render cache serializes identical work.
            return _task(app, f"workshop-compile-{doc_id}-{snapshot['revision']}",
                         lambda _own: render.compile_doc(app.settings, snapshot))
        _keys(body, ("revision", "label", "job_id", "confirm"), ("revision", "label", "confirm"))
        _confirm(body)
        revision = store.positive_int(body["revision"])
        snapshot = store.get_revision(conn, doc_id, revision)
        compiled = render.compile_doc(app.settings, snapshot)  # cached when the preview is current
        if not compiled["ok"]:
            raise store.WorkshopError("This revision does not compile: " + "; ".join(compiled["diagnostics"])[:1500], 409)
        return render.export_resume(conn, app.settings, doc_id, revision=revision, label=body["label"],
                                    job_id=_optional_job(body.get("job_id")))
    except store.WorkshopError as exc:
        raise _error(exc) from None

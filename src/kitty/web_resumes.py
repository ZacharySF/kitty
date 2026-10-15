"""Local resume registration and immutable, explicitly reviewed candidate workflow."""
from __future__ import annotations

import base64
import binascii
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import tempfile

from kitty.core import resume_coach, resumes, resume_versions
from kitty.db import connect

_VERSION = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
MAX_UPLOAD = 8 * 1024 * 1024


def _keys(body, allowed):
    from kitty.web_server import APIError
    if set(body) - set(allowed):
        raise APIError(400, "Unknown resume request fields")


def _text(body, key, *, optional=False, maximum=4096):
    from kitty.web_server import APIError
    value = body.get(key)
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise APIError(400, f"Invalid {key}")
    return value.strip()


def _confirmed(body):
    from kitty.web_server import APIError
    if body.get("confirm") is not True:
        raise APIError(400, "Explicit confirmation is required")


def _track(app, body):
    from kitty.web_server import APIError
    track = _text(body, "track", maximum=200)
    if app.config.track(track) is None:
        raise APIError(400, "Unknown resume track")
    return track


def _version(conn, settings, version_id):
    from kitty.web_server import APIError
    if not conn.execute("SELECT 1 FROM resume_versions WHERE id=?", (version_id,)).fetchone():
        raise APIError(404, "Resume version not found")
    try:
        return resume_versions.get_version(conn, settings, version_id)
    except (ValueError, OSError) as exc:
        raise APIError(409, "Resume audit is unavailable or has changed; restore immutable artifacts") from exc


def get(app, conn, path, query):
    from kitty.web_server import _pdf_available
    if path == "/api/resumes/workflow":
        _keys(query, ())
        defaults = {name: resume_versions.default_resume(conn, app.config, name)
                    for name in app.config.track_names}
        return {"tracks": app.config.track_names,
                "defaults": {name: item.label if item else None for name, item in defaults.items()},
                "resumes": [{"id": item.id, "label": item.label, "description": item.description,
                             "available": _pdf_available(app.settings, item),
                             "default_tracks": [name for name, selected in defaults.items()
                                                if selected and selected.id == item.id]}
                            for item in resumes.list_resumes(conn)],
                "versions": [dict(row) for row in conn.execute(
                    "SELECT id,track,status,created_at FROM resume_versions ORDER BY created_at DESC,rowid DESC")]}
    if path == "/api/resumes/coach":
        _keys(query, ("track",))
        track = _track(app, query) if query.get("track") else None
        return {"reports": [asdict(report) for report in resume_coach.coach(
            conn, app.config, app.settings, track=track)]}
    match = re.fullmatch(r"/api/resumes/versions/" + _VERSION, path)
    if match:
        _keys(query, ())
        return {"version": _version(conn, app.settings, match[1])}
    return None


def post(app, conn, path, body):
    from kitty.web_server import APIError
    if path == "/api/resumes/register":
        _keys(body, ("label", "description", "path", "upload", "filename", "confirm"))
        _confirmed(body)
        label = _text(body, "label", maximum=200)
        description = _text(body, "description", optional=True)
        local = _text(body, "path", optional=True)
        upload = body.get("upload")
        if bool(local) == (upload is not None):
            raise APIError(400, "Choose exactly one local PDF path or PDF upload")
        try:
            if local:
                source = Path(local).expanduser()
                if not source.is_absolute():
                    source = app.settings.root / source
                if source.suffix.lower() != ".pdf" or not source.is_file() or source.stat().st_size > MAX_UPLOAD:
                    raise APIError(400, "Choose an existing PDF of at most 8 MiB")
                with source.open("rb") as stream:
                    if stream.read(5) != b"%PDF-":
                        raise APIError(400, "Selected file is not a PDF")
                item = resumes.import_resume(conn, app.settings.root, source, label, description=description)
            else:
                if not isinstance(upload, str) or len(upload) > ((MAX_UPLOAD + 2) // 3) * 4:
                    raise APIError(400, "PDF upload exceeds 8 MiB")
                try:
                    data = base64.b64decode(upload, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise APIError(400, "Invalid PDF upload encoding") from exc
                if len(data) > MAX_UPLOAD or not data.startswith(b"%PDF-"):
                    raise APIError(400, "Upload must be a PDF of at most 8 MiB")
                with tempfile.TemporaryDirectory(prefix="kitty-resume-") as folder:
                    source = Path(folder) / (hashlib.sha256(data).hexdigest() + ".pdf")
                    source.write_bytes(data)
                    item = resumes.import_resume(conn, app.settings.root, source, label, description=description)
            return {"resume": {"id": item.id, "label": item.label}}
        except ValueError as exc:
            raise APIError(409, str(exc)) from exc
        except OSError as exc:
            raise APIError(400, "Cannot read or import the selected local PDF") from exc
    if path == "/api/resumes/generate":
        _keys(body, ("track", "source", "min_postings", "confirm"))
        _confirmed(body)
        track = _track(app, body)
        source_text = _text(body, "source", optional=True)
        source = Path(source_text).expanduser() if source_text else None
        if source is not None and not source.is_absolute():
            source = app.settings.root / source
        minimum = body.get("min_postings", 5)
        if type(minimum) is not int or not 1 <= minimum <= 1000:
            raise APIError(400, "min_postings must be an integer from 1 to 1000")
        config, settings = app.config, app.settings

        def generate():
            # Rendering must not retain a WAL read snapshot before the candidate INSERT.
            # Core owns its atomic persistence and multi-write review decisions.
            with closing(connect(settings.db_path)) as own:
                version = resume_versions.create_candidate(own, config, settings, track, source,
                                                           min_postings=minimum)
                return {"version_id": version["id"], "valid": version["validation"]["valid"]}

        return {"task": app.tasks.start("resume-generation", generate)}
    if path == "/api/resumes/rollback":
        _keys(body, ("track", "confirm"))
        _confirmed(body)
        track = _track(app, body)
        try:
            return {"default": resume_versions.rollback(conn, app.config, app.settings, track)}
        except ValueError as exc:
            raise APIError(409, str(exc)) from exc
    match = re.fullmatch(r"/api/resumes/versions/" + _VERSION + r"/(promote|reject)", path)
    if match:
        _keys(body, ("confirm",))
        _confirmed(body)
        _version(conn, app.settings, match[1])
        try:
            if match[2] == "promote":
                result = resume_versions.promote(conn, app.config, app.settings, match[1])
            else:
                result = resume_versions.reject(conn, app.settings, match[1])
            return {"version": result}
        except ValueError as exc:
            raise APIError(409, str(exc)) from exc
        except OSError as exc:
            raise APIError(409, "Resume artifacts unavailable; decision was not completed") from exc
    return None


def download(app, conn, path, query):
    from kitty.web_server import APIError
    match = re.fullmatch(r"/api/resumes/versions/" + _VERSION + r"/(pdf|audit|diff)", path)
    if not match:
        return None
    _keys(query, ())
    version = _version(conn, app.settings, match[1])
    if match[2] == "audit":
        return json.dumps(version, ensure_ascii=False, indent=2).encode(), "application/json", f"resume-{match[1]}-audit.json"
    if match[2] == "diff":
        return version["diff"].encode(), "text/plain; charset=utf-8", f"resume-{match[1]}.diff"
    try:
        pdf = Path(version["pdf_path"])
        if pdf.stat().st_size > MAX_UPLOAD:
            raise APIError(409, "Candidate PDF exceeds preview limit")
        data = pdf.read_bytes()
    except OSError as exc:
        raise APIError(409, "Candidate PDF is unavailable; inspect validation errors") from exc
    if hashlib.sha256(data).hexdigest() != version["artifacts"].get("candidate.pdf") or not data.startswith(b"%PDF-"):
        raise APIError(409, "Candidate PDF identity changed; preview refused")
    return data, "application/pdf", None

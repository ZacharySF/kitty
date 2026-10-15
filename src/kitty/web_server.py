"""Loopback-only website adapter over the shared kitty! core.

Run ``kitty-web`` after building ``web/dist``. The API never submits forms,
starts paid discovery, or exposes configuration/credentials. Demo workspaces use
only public example configuration and are discarded when the process exits.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import shutil
import sqlite3
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from kitty.config import Config, ConfigError
from kitty.core import applications, demo, favorites, jobs, resumes, resume_versions
from kitty.core import followups, today, tracks
from kitty.core.apply.filler import choose_resume, resume_file
from kitty.core.workspace import open_workspace
from kitty.dates import parse_user_date
from kitty.db import connect
from kitty.db.connection import transaction
from kitty.models import Job, Resume
from kitty.settings import Settings, find_project_root
from kitty.web_tasks import TaskRunner

MAX_BODY = 20 * 1024 * 1024  # Bounded canvas JSON + base64 raster import.
MAX_FILE = 32 * 1024 * 1024
LATER_STATUSES = frozenset({"oa", "interview", "offer", "rejected", "ghosted", "withdrawn"})
# Deliberately an allowlist: adding a future paid adapter cannot enable it here.
FREE_SOURCES = frozenset({
    "simplify2027", "nuft2027", "vansh2027", "speedyapply2027", "derec4", "greenhouse",
    "lever", "ashby", "smartrecruiters", "workable", "workday", "employers",
    "local_employers", "usajobs", "research", "yc_internships", "hackernews",
    "starbucks", "jobspy",
})
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
}


class APIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _integer(value: str, name: str, minimum: int, maximum: int) -> int:
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise APIError(400, f"{name} must be an integer")
    number = int(value)
    if not minimum <= number <= maximum:
        raise APIError(400, f"{name} must be between {minimum} and {maximum}")
    return number


def _job_json(job: Job) -> dict[str, Any]:
    return {
        "id": job.id, "company": job.company_name, "title": job.title,
        "location": job.location, "is_remote": bool(job.is_remote), "tier": job.tier,
        "tracks": job.tracks, "url": job.url, "eligibility": job.eligibility,
        "eligibility_reason": job.eligibility_reason, "queue_state": job.queue_state,
        "status": job.status, "favorite": job.is_favorite, "posted_at": job.posted_at,
        "deadline_at": job.deadline_at, "fill_state": job.fill_state,
    }


def _pdf_bytes(settings: Settings, resume: Resume) -> bytes:
    path = resume_file(settings, resume)
    try:
        if path.suffix.lower() != ".pdf" or not path.is_file() or path.stat().st_size > MAX_FILE:
            raise APIError(404, "Registered PDF is unavailable")
        data = path.read_bytes()
    except OSError:
        raise APIError(404, "Registered PDF is unavailable") from None
    if not data.startswith(b"%PDF-") or len(data) > MAX_FILE:
        raise APIError(404, "Registered PDF is unavailable")
    if hashlib.sha256(data).hexdigest() != resume.file_hash:
        raise APIError(409, "Registered PDF has changed; review it with the CLI")
    return data


def _pdf_available(settings: Settings, resume: Resume) -> bool:
    try:
        _pdf_bytes(settings, resume)
    except APIError:
        return False
    return True


class WebApplication:
    """Local services and configuration; each HTTP request owns its connection."""

    def __init__(self, settings: Settings, config: Config, static_dir: Path):
        self.settings = settings
        self.config = config
        self.static_dir = static_dir.resolve()
        self.mutation_lock = threading.Lock()
        self.discovery_lock = threading.Lock()
        self.discovery: dict[str, Any] = {"running": False, "error": None, "result": None}
        from kitty import web_automation, web_insights, web_jobs, web_profile, web_resumes

        from kitty import web_ai, web_calendar, web_flashcards, web_notes
        from kitty import web_opportunities, web_study
        from kitty import web_atlas, web_kanban, web_schema

        self.extensions = (
            web_jobs, web_automation, web_resumes, web_profile, web_insights,
            web_calendar, web_opportunities, web_study, web_flashcards, web_notes, web_ai,
            web_schema, web_kanban, web_atlas,
        )
        self.tasks = TaskRunner()
        self.automation = web_automation.AutomationController(settings)
        for extension in self.extensions:
            start = getattr(extension, "start", None)
            if start:
                start(self)

    def close(self) -> None:
        """Stop schedulers first, then drain accepted operations and persistence."""
        for extension in reversed(self.extensions):
            stop = getattr(extension, "stop", None) or getattr(extension, "close", None)
            if stop:
                stop(self)
        self.automation.close()
        self.tasks.close()

    def reload_config(self) -> None:
        """Refresh validated config after an explicit, serialized settings change."""
        with open_workspace(self.settings) as workspace:
            self.config = workspace.config

    def job_filter(self, view: str) -> jobs.JobFilter:
        if view not in {"jobs", "inbox", "queue", "applications", "favorites"}:
            raise APIError(400, "Unknown job view")
        return jobs.JobFilter(
            queue_states=["queued"] if view == "queue" else ["new"] if view == "inbox" else None,
            not_applied=view in {"queue", "inbox"}, applied_only=view == "applications",
            favorites_only=view == "favorites", include_closed=view in {"applications", "favorites"},
            order="best", prefer_cities=self.config.prefer_cities,
            prefer_remote=self.config.profile.open_to_remote,
        )

    def get_json(self, conn: sqlite3.Connection, path: str, query: dict[str, str]) -> dict:
        match = re.fullmatch(r"/api/tasks/([a-f0-9]{32})", path)
        if match:
            self._no_query(query)
            return self.tasks.snapshot(match[1])
        for extension in self.extensions:
            result = extension.get(self, conn, path, query)
            if result is not None:
                return result
        if path == "/api/bootstrap":
            self._no_query(query)
            digest = today.build_digest(conn, self.config)
            return {
                "demo": self.settings.demo,
                "tracks": [{"name": name, "label": name.replace("_", " ").title(),
                            "weekly_target": target} for name, target in tracks.weekly_targets(conn).items()],
                "counts": {key: jobs.count_jobs(conn, self.job_filter(view)) for key, view in (
                    ("jobs", "jobs"), ("queued", "queue"),
                    ("applications", "applications"), ("favorites", "favorites"))},
                "weekly_total": digest.weekly_total, "streak_days": digest.streak_days,
                "today": today.digest_text(digest),
            }
        match = re.fullmatch(r"/api/jobs/([1-9][0-9]{0,17})", path)
        if match:
            self._no_query(query)
            job = self._job(conn, int(match[1]))
            application = applications.get_application_for_job(conn, job.id)
            chosen = (resumes.get_resume(conn, application.resume_id)
                      if application and application.resume_id is not None
                      else choose_resume(conn, self.config, job) if not application else None)
            result = _job_json(job)
            result.update({
                "description": job.jd_text, "my_notes": job.my_notes,
                "tier_reason": job.tier_reason, "fill_blocker": job.fill_blocker,
                "resume": {"id": chosen.id, "label": chosen.label} if chosen else None,
                "application": {
                    **dataclasses.asdict(application),
                    "history": [dataclasses.asdict(event)
                                for event in applications.history(conn, application.id)],
                } if application else None,
            })
            return result
        if path == "/api/resumes":
            self._no_query(query)
            defaults: dict[int, list[str]] = {}
            for track in self.config.tracks.tracks:
                selected = resume_versions.default_resume(conn, self.config, track.name)
                if selected:
                    defaults.setdefault(selected.id, []).append(track.name)
            return {
                "resumes": [{"id": resume.id, "label": resume.label,
                             "track": next(iter(defaults.get(resume.id, resume.default_tracks)), None),
                             "available": _pdf_available(self.settings, resume),
                             "is_default": resume.id in defaults}
                            for resume in resumes.list_resumes(conn)],
                "versions": [{key: version[key] for key in
                              ("id", "track", "status", "is_default", "created_at")}
                             for version in resume_versions.list_versions(conn, self.settings)],
            }
        raise APIError(404, "API route not found")

    @staticmethod
    def _no_query(query: dict) -> None:
        if query:
            raise APIError(400, "This route does not accept query parameters")

    @staticmethod
    def _job(conn: sqlite3.Connection, job_id: int) -> Job:
        job = jobs.get_job(conn, job_id)
        if job is None:
            raise APIError(404, "Job not found")
        return job

    def mutate(self, conn: sqlite3.Connection, path: str, body: dict) -> dict:
        if path == "/api/reload":
            if body:
                raise APIError(400, "Reload expects an empty object")
            self.reload_config()
            return {"ok": True}
        for extension in self.extensions:
            result = extension.post(self, conn, path, body)
            if result is not None:
                return result
        match = re.fullmatch(r"/api/jobs/([1-9][0-9]{0,17})", path)
        if not match:
            raise APIError(404, "API route not found")
        if set(body) - {"action", "value", "when", "note"} or not isinstance(body.get("action"), str):
            raise APIError(400, "Expected an action and optional value")
        action, value = body["action"], body.get("value")
        occurred_at = None
        note = None
        if {"when", "note"} & body.keys():
            if action not in {"status", "applied"}:
                raise APIError(400, "Date and evidence are only valid for application records")
            when = body.get("when", "")
            note = body.get("note", "")
            if not isinstance(when, str) or len(when) > 100 or not isinstance(note, str) or len(note) > 20000:
                raise APIError(400, "Invalid application date or evidence")
            occurred_at = parse_user_date(when) if when.strip() else None
            if when.strip() and occurred_at is None:
                raise APIError(400, "Use a valid application date")
        if action in {"queue", "skip", "restore"}:
            if "value" in body:
                raise APIError(400, "Queue actions do not accept a value")
        elif action == "favorite":
            if type(value) is not bool:
                raise APIError(400, "Favorite value must be a boolean")
        elif action == "notes":
            if not isinstance(value, str) or len(value) > 20000 or "\x00" in value:
                raise APIError(400, "Notes must be text of at most 20000 characters without NUL")
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise APIError(400, "Notes must contain valid Unicode text") from None
        elif action == "applied":
            if value is not True:
                raise APIError(400, "Explicit confirmation that you submitted is required")
        elif action == "status":
            if not isinstance(value, str) or value not in LATER_STATUSES:
                raise APIError(400, "Choose a later application status")
        elif action == "resume":
            if value is not None and (type(value) is not int or not 1 <= value <= 2**63 - 1):
                raise APIError(400, "Resume value must be a registered resume ID or null for the track default")
        else:
            raise APIError(400, "Unknown action")
        job = self._job(conn, int(match[1]))
        if action == "favorite":
            (favorites.favorite if value else favorites.unfavorite)(conn, [job.id])
        elif action in {"queue", "skip", "restore"}:
            if job.application_id is not None:
                raise APIError(409, "An applied job cannot change queue state")
            favorites.set_state(conn, [job.id], {"queue": "queued", "skip": "skipped", "restore": "new"}[action])
        else:
            with transaction(conn):
                if action == "notes":
                    jobs.set_notes(conn, job.id, value)
                elif action == "status":
                    if job.application_id is None:
                        raise APIError(409, "Record your submission before changing application status")
                    applications.set_status(conn, job.application_id, value, source="manual",
                                            occurred_at=occurred_at, evidence=note)
                elif action == "resume":
                    if job.application_id is not None:
                        raise APIError(409, "Historical application resume is pinned")
                    if value is not None:
                        resume = resumes.get_resume(conn, value)
                        if resume is None or not resume.active:
                            raise APIError(400, "Choose an active registered resume")
                        _pdf_bytes(self.settings, resume)
                    jobs.set_resume(conn, job.id, value)
                elif action == "applied":
                    chosen = choose_resume(conn, self.config, job)
                    followups.mark_applied(
                        conn, self.config, job.id, resume_id=chosen.id if chosen else None,
                        track=job.tracks[0] if job.tracks else None,
                        applied_at=occurred_at, evidence=note,
                    )
        return {"ok": True}



class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: WebApplication, port: int = 8765):
        self.app = app
        super().__init__(("127.0.0.1", port), RequestHandler)
        self.authority = f"127.0.0.1:{self.server_address[1]}"
        self.origin = f"http://{self.authority}"


class RequestHandler(BaseHTTPRequestHandler):
    server: LocalHTTPServer
    server_version = "kitty!"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: Any) -> None:
        # URLs/search strings and private job IDs do not belong in access logs.
        pass

    def _security(self, *, mutation: bool = False) -> None:
        hosts = self.headers.get_all("Host", [])
        if hosts != [self.server.authority]:
            raise APIError(403, "Use the server's loopback address")
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [self.server.origin]:
            raise APIError(403, "Cross-origin requests are not allowed")
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            raise APIError(403, "Cross-site requests are not allowed")
        if mutation:
            if self.headers.get_all("X-Kitty-Request", []) != ["1"]:
                raise APIError(403, "Missing local request header")
            if self.headers.get_all("Content-Type", []) != ["application/json"]:
                raise APIError(415, "Use application/json")

    def _target(self) -> tuple[str, dict[str, str]]:
        if len(self.path) > 8192 or not self.path.startswith("/") or self.path.startswith("//"):
            raise APIError(400, "Invalid request target")
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise APIError(400, "Invalid request target")
        try:
            pairs = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
            path = unquote(parsed.path, errors="strict")
        except (ValueError, UnicodeError):
            raise APIError(400, "Invalid request target") from None
        if any(len(values) != 1 for values in pairs.values()):
            raise APIError(400, "Repeated query parameters are not allowed")
        if "\x00" in path or "\\" in path or any(part in {".", ".."} for part in path.split("/")):
            raise APIError(400, "Invalid request path")
        return path, {key: values[0] for key, values in pairs.items()}

    def _send(self, status: int, data: bytes, content_type: str, *, download: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=True).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        self._dispatch(False)

    def do_HEAD(self) -> None:
        self._dispatch(False)

    def do_POST(self) -> None:
        self._dispatch(True)

    def do_OPTIONS(self) -> None:
        self._json(405, {"error": "Cross-origin preflight is not supported"})

    def _dispatch(self, mutation: bool) -> None:
        try:
            self._security(mutation=mutation)
            path, query = self._target()
            if mutation:
                self.server.app._no_query(query)
                body = self._body()
                with self.server.app.mutation_lock:
                    with closing(connect(self.server.app.settings.db_path)) as conn:
                        result = self.server.app.mutate(conn, path, body)
                self._json(200, result)
            elif path == "/api" or path.startswith("/api/"):
                self._get_api(path, query)
            else:
                self.server.app._no_query(query)
                self._static(path)
        except APIError as exc:
            self.close_connection = True
            self._json(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (TimeoutError, sqlite3.OperationalError):
            self.close_connection = True
            self._json(503, {"error": "Local workspace is busy or unavailable; try again"})
        except Exception:
            self.close_connection = True
            self._json(500, {"error": "Local operation failed; inspect CLI diagnostics"})

    def _body(self) -> dict:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1:
            raise APIError(400, "A single Content-Length is required")
        length = _integer(lengths[0], "Content-Length", 1, MAX_BODY)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise APIError(400, "Incomplete JSON body")

        def unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError("Repeated JSON key")
                obj[key] = value
            return obj

        def reject_constant(value: str) -> None:
            raise ValueError("Non-finite JSON number")

        try:
            body = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, "Malformed JSON body") from None
        if not isinstance(body, dict):
            raise APIError(400, "JSON body must be an object")
        return body

    def _get_api(self, path: str, query: dict[str, str]) -> None:
        app = self.server.app
        with closing(connect(app.settings.db_path)) as conn:
            for extension in app.extensions:
                download = getattr(extension, "download", None)
                result = download(app, conn, path, query) if download else None
                if result is not None:
                    data, kind, filename = result
                    self._send(200, data, kind, download=filename)
                    return
            match = re.fullmatch(r"/api/resumes/([1-9][0-9]{0,17})/pdf", path)
            if match:
                app._no_query(query)
                resume = resumes.get_resume(conn, int(match[1]))
                if resume is None:
                    raise APIError(404, "Resume not found")
                self._send(200, _pdf_bytes(app.settings, resume), "application/pdf")
            else:
                self._json(200, app.get_json(conn, path, query))


    def _static(self, path: str) -> None:
        root = self.server.app.static_dir
        if any(part.startswith(".") for part in path.split("/") if part):
            raise APIError(404, "Not found")
        target = (root / path.lstrip("/")).resolve()
        if not target.is_relative_to(root):
            raise APIError(404, "Not found")
        if not target.is_file() and (path == "/" or not Path(path).suffix):
            target = (root / "index.html").resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise APIError(404, "Website not built; run npm run build in web/")
        kind = STATIC_TYPES.get(target.suffix.lower())
        if not kind:
            raise APIError(404, "Not found")
        self._send(200, target.read_bytes(), kind)


def _serve(settings: Settings, static_dir: Path, port: int) -> None:
    with open_workspace(settings) as workspace:
        if settings.demo:
            demo.seed(workspace.conn, settings, workspace.config)
        app = WebApplication(settings, workspace.config, static_dir)
    with LocalHTTPServer(app, port) as server:
        print(f"kitty! {'synthetic demo ' if settings.demo else ''}website: {server.origin}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, help="kitty! workspace root")
    parser.add_argument("--static-dir", type=Path, help="Built SPA directory (default: web/dist)")
    parser.add_argument("--demo", action="store_true", help="Disposable synthetic workspace; no live discovery")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    root = (args.root or find_project_root()).expanduser().resolve()
    static_dir = args.static_dir or Path(__file__).resolve().parents[2] / "web" / "dist"
    try:
        if args.demo:
            with TemporaryDirectory(prefix="kitty-web-demo-") as folder:
                demo_root = Path(folder)
                config_dir = demo_root / "config"
                config_dir.mkdir()
                example_dir = root / "config"
                if not (example_dir / "tracks.yaml.example").is_file():
                    example_dir = Path(__file__).resolve().parents[2] / "config"
                for name in ("profile", "tracks", "sources", "watchlist", "answers", "views"):
                    shutil.copyfile(example_dir / f"{name}.yaml.example", config_dir / f"{name}.yaml")
                settings = Settings(root=demo_root, db_path=demo_root / "data" / "demo.db",
                                    demo=True, anthropic_key_set=False)
                _serve(settings, static_dir, args.port)
        else:
            _serve(Settings.load(root=root), static_dir, args.port)
    except (ConfigError, OSError, ValueError):
        parser.exit(1, "Unable to open the local website. Check the port, workspace configuration and public example files with the CLI.\n")


if __name__ == "__main__":
    main()

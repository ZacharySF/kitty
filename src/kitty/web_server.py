"""Loopback-only website adapter over the shared rika! core.

Run ``kitty-web`` after building ``web/dist``. The API never submits forms,
starts paid discovery, or exposes configuration/credentials. Demo workspaces use
only public example configuration and are discarded when the process exits.

Features belong to the ``applyai``, ``workspace`` and ``terminal`` modules
(kitty.modules). The active set is frozen at start-up; routes of a disabled module
answer 403 and none of its extensions, workers, processes or credentials are loaded.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import re
import shutil
import signal
import sqlite3
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from kitty import web_auth, web_clients
from kitty.config import Config, ConfigError
from kitty.core import endpoint, record_events
from kitty.core.bootstrap import configure_modules
from kitty.core.builds.project import startup_modules
from kitty.core.builds.store import BuildStoreError
from kitty.core.workspace import open_workspace
from kitty.db import connect
from kitty.db.connection import transaction
from kitty.models import Job, Resume
from kitty.modules import ModuleDisabledError, modules_state
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
    "starbucks", "hiringcafe", "jobspy",
})
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
}


class APIError(Exception):
    def __init__(self, status: int, message: str, *, headers: dict[str, str] | None = None,
                 problems: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.status = status
        #: Extra response headers (WWW-Authenticate, Retry-After) for this error.
        self.headers = headers or {}
        #: Located problems (a build file's line and column) answered beside ``error``.
        self.problems = problems or []


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
        "is_active": job.is_active, "closed_at": job.closed_at, "freshness": job.freshness,
        "verification_status": job.verification_status,
        "verification_reason": job.verification_reason,
        "verification_checked_at": job.verification_checked_at,
    }


def _pdf_bytes(settings: Settings, resume: Resume) -> bytes:
    from kitty.core.apply.filler import resume_file

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


#: API route prefix -> owning module; the longest matching prefix wins. Anything not
#: listed (tasks, ai status/settings/models, atlas home, brain, bootstrap, modules, reload)
#: belongs to the always-on core.
ROUTE_MODULES: tuple[tuple[str, str], ...] = tuple(sorted((
    *((prefix, "applyai") for prefix in (
        "/api/dashboard", "/api/job-options", "/api/jobs", "/api/pipeline", "/api/reminders",
        "/api/job-tools", "/api/automation", "/api/questions", "/api/resumes", "/api/profile",
        "/api/sources", "/api/finder", "/api/reports", "/api/mail", "/api/triage",
        "/api/postings", "/api/schema/jobs", "/api/schema/import-jobs", "/api/atlas/sections/jobs")),
    *((prefix, "workspace") for prefix in (
        "/api/calendar", "/api/opportunities", "/api/study", "/api/flashcards", "/api/notes",
        "/api/documents", "/api/schema", "/api/backlinks", "/api/ai/preview", "/api/ai/mind-map",
        "/api/ai/run", "/api/atlas/sections", "/api/quick-notes", "/api/extensions/learnharness",
        "/api/blocks", "/api/tags", "/api/mirror", "/api/math")),
    ("/api/terminal", "terminal"),
), key=lambda item: -len(item[0])))
#: Extension modules per feature module, in dispatch order; None is the always-on core.
#: The live channel starts first and the build second: other extensions read both.
EXTENSIONS: tuple[tuple[str | None, str], ...] = (
    (None, "web_live"), (None, "web_build"),
    ("applyai", "web_jobs"), ("applyai", "web_automation"), ("applyai", "web_resumes"),
    ("applyai", "web_profile"), ("applyai", "web_insights"),
    ("workspace", "web_calendar"), ("workspace", "web_opportunities"), ("workspace", "web_study"),
    ("workspace", "web_flashcards"), ("workspace", "web_notes"), ("workspace", "web_documents"),
    ("workspace", "web_math"),
    (None, "web_ai"),
    ("workspace", "web_schema"), (None, "web_atlas"), (None, "web_brain"), ("workspace", "web_backlinks"),
    ("workspace", "web_learnharness"), ("workspace", "web_quick_notes"), ("workspace", "web_blocks"),
    ("workspace", "web_tags"), ("workspace", "web_mirror"), (None, "web_desktop"), (None, "web_clients"), (None, "web_agents"),
    (None, "web_keys"),
    (None, "web_devices"), (None, "web_search"),
    (None, "web_account"), (None, "web_rika"), (None, "web_goals"), (None, "web_rika_mail"), (None, "web_rika_memory"),
    ("terminal", "web_agent_skills"), ("terminal", "web_terminal"), ("terminal", "web_tern"),
)


def route_module(path: str) -> str | None:
    """The module that owns an API path, or None for core routes."""
    for prefix, module in ROUTE_MODULES:
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


class WebApplication:
    """Local services and configuration; each HTTP request owns its connection.

    The active module set is frozen when the process starts (``config.modules``).
    Only the extensions, workers and credentials of active modules exist; a pending
    selection saved through ``/api/modules`` takes effect in the next process.
    """

    def __init__(self, settings: Settings, config: Config, static_dir: Path, *, desktop=None, safe: bool = False):
        self.settings = settings
        self.config = config
        self.modules: tuple[str, ...] = tuple(config.modules)
        #: Safe mode (``--safe``): every window starts without the build's custom CSS and
        #: theme token overrides (Q9).
        self.safe = safe
        #: The desktop shell's owner pipe (kitty.web_desktop.DesktopOwner); None for kitty-web.
        self.desktop = desktop
        #: The serving origin (``http://127.0.0.1:<port>``), set by the server that serves this app.
        self.origin: str | None = None
        self.static_dir = static_dir.resolve()
        self.mutation_lock = threading.Lock()
        self.discovery_lock = threading.Lock()
        self.discovery: dict[str, Any] = {"running": False, "error": None, "result": None}
        #: Set when the server closes: long-lived upgraded streams end themselves.
        self.streams_closed = threading.Event()
        self.extensions = tuple(
            importlib.import_module(f"kitty.{name}") for module, name in EXTENSIONS
            if module is None or module in self.modules
        )
        self.tasks = TaskRunner()
        self.automation = None
        if "applyai" in self.modules:
            from kitty import web_automation

            self.automation = web_automation.AutomationController(settings)
        for extension in self.extensions:
            start = getattr(extension, "start", None)
            if start:
                start(self)

    def close(self) -> None:
        """End open streams and stop schedulers, then drain accepted operations and persistence."""
        self.streams_closed.set()
        for extension in reversed(self.extensions):
            stop = getattr(extension, "stop", None) or getattr(extension, "close", None)
            if stop:
                stop(self)
        if self.automation is not None:
            self.automation.close()
        self.tasks.close()

    def mutated(self) -> None:
        """Tell extensions a mutation committed (live push wakes at once instead of polling)."""
        for extension in self.extensions:
            hook = getattr(extension, "mutated", None)
            if hook:
                hook(self)

    def require_module(self, module: str) -> None:
        """403 unless `module` is in this process's active snapshot."""
        if module not in self.modules:
            raise APIError(403, str(ModuleDisabledError(module)))

    def guard(self, path: str) -> None:
        """Reject a request for a disabled module before any handler or worker runs."""
        module = route_module(path)
        if module is not None:
            self.require_module(module)

    def modules_state(self) -> dict[str, Any]:
        return modules_state(self.modules, self.settings.config_dir)

    def reload_config(self) -> None:
        """Refresh validated config after an explicit, serialized settings change.

        The active module snapshot is pinned: a pending module edit in the build is neither
        applied nor validated here.
        """
        with open_workspace(self.settings, modules=self.modules) as workspace:
            self.config = workspace.config

    def job_filter(self, view: str):
        from kitty.core import jobs

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
        self.guard(path)
        for extension in self.extensions:
            result = extension.get(self, conn, path, query)
            if result is not None:
                return result
        if path == "/api/modules":
            self._no_query(query)
            return self.modules_state()
        if path == "/api/bootstrap":
            self._no_query(query)
            return self._bootstrap(conn)
        if "applyai" in self.modules:
            result = self._applyai_get(conn, path, query)
            if result is not None:
                return result
        raise APIError(404, "API route not found")

    def _bootstrap(self, conn: sqlite3.Connection) -> dict:
        """Core shell state; careers figures exist only while applyai is active."""
        result: dict[str, Any] = {"demo": self.settings.demo, "modules": self.modules_state()}
        if "applyai" in self.modules:
            from kitty.core import jobs, today, tracks

            digest = today.build_digest(conn, self.config)
            result.update({
                "tracks": [{"name": name, "label": name.replace("_", " ").title(),
                            "weekly_target": target} for name, target in tracks.weekly_targets(conn).items()],
                "counts": {key: jobs.count_jobs(conn, self.job_filter(view)) for key, view in (
                    ("jobs", "jobs"), ("queued", "queue"),
                    ("applications", "applications"), ("favorites", "favorites"))},
                "weekly_total": digest.weekly_total, "streak_days": digest.streak_days,
                "today": today.digest_text(digest),
            })
        return result

    def _applyai_get(self, conn: sqlite3.Connection, path: str, query: dict[str, str]) -> dict | None:
        from kitty.core import applications, jobs, resumes, resume_versions
        from kitty.core.apply.filler import choose_resume

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
        return None

    @staticmethod
    def _no_query(query: dict) -> None:
        if query:
            raise APIError(400, "This route does not accept query parameters")

    @staticmethod
    def _job(conn: sqlite3.Connection, job_id: int) -> Job:
        from kitty.core import jobs

        job = jobs.get_job(conn, job_id)
        if job is None:
            raise APIError(404, "Job not found")
        return job

    def set_modules(self, body: dict) -> dict:
        """Persist the pending module selection; the active snapshot never changes."""
        enabled = body.get("enabled")
        if set(body) != {"enabled"} or not isinstance(enabled, list):
            raise APIError(400, "Expected {\"enabled\": [module ids]}")
        try:
            configure_modules(self.settings.config_dir, enabled)
        except ValueError as exc:
            raise APIError(400, str(exc)) from None
        return self.modules_state()

    def mutate(self, conn: sqlite3.Connection, path: str, body: dict) -> dict:
        if path == "/api/reload":
            if body:
                raise APIError(400, "Reload expects an empty object")
            self.reload_config()
            return {"ok": True}
        if path == "/api/modules":
            return self.set_modules(body)
        self.guard(path)
        op_log_before = record_events.op_log_mark(conn)
        result = self._dispatch_mutation(conn, path, body)
        record_events.capture(conn, path, body, result, op_log_before=op_log_before)
        return result

    def _dispatch_mutation(self, conn: sqlite3.Connection, path: str, body: dict) -> dict:
        for extension in self.extensions:
            result = extension.post(self, conn, path, body)
            if result is not None:
                return result
        return self._mutate_job(conn, path, body)

    def _mutate_job(self, conn: sqlite3.Connection, path: str, body: dict) -> dict:
        match = re.fullmatch(r"/api/jobs/([1-9][0-9]{0,17})", path)
        if match is None:
            raise APIError(404, "API route not found")
        from kitty.core import applications, favorites, followups, jobs, resumes
        from kitty.core.apply.filler import choose_resume
        from kitty.dates import parse_user_date

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
        elif action == "track":
            if value is not None and (not isinstance(value, str) or value not in self.config.track_names):
                raise APIError(400, "Choose a configured track or null for none")
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
                elif action in {"status", "track"} and job.application_id is None:
                    raise APIError(409, "Record your submission before changing application status")
                elif action == "status":
                    applications.set_status(conn, job.application_id, value, source="manual",
                                            occurred_at=occurred_at, evidence=note)
                elif action == "track":
                    applications.set_track(conn, job.application_id, value)
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
        #: This launch's UI secret, bootstrap code and link tickets (kitty.web_auth).
        self.ui_auth = web_auth.UiAuth()
        #: The address paired programs use (Settings → Connected apps shows it for the browser extension).
        app.origin = self.origin

    def server_close(self) -> None:
        # Upgraded streams outlive requests; end them before joining request threads.
        self.app.streams_closed.set()
        super().server_close()


class RequestHandler(BaseHTTPRequestHandler):
    server: LocalHTTPServer
    server_version = "rika!"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: Any) -> None:
        # URLs/search strings and private job IDs do not belong in access logs.
        pass

    def parse_request(self) -> bool:
        #: Extra headers for this request's response (the client ingress's CORS headers).
        self.response_headers: dict[str, str] = {}
        #: The WebSocket subprotocol a 101 selects, set only once an upgrade is authorised.
        self.websocket_protocol: str | None = None
        return super().parse_request()

    def _security(self, *, mutation: bool = False) -> None:
        if self.headers.get_all("Authorization", []):
            raise APIError(403, "Paired client tokens reach only /api/ingress")
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
        return parse_target(self.path)

    def _authorize(self, path: str, query: dict[str, str], mutation: bool) -> dict[str, str]:
        """Require this launch's UI secret (kitty.web_auth); returns the query without a ticket.

        Upgrades carry it as a subprotocol instead (checked in ``_upgrade``); the browser-mode
        code exchange needs the code instead; a GET may carry a single-use link ticket instead.
        """
        auth = self.server.ui_auth
        if mutation and path == web_auth.SESSION_PATH:
            return query
        presented = self.headers.get_all(web_auth.HEADER, [])
        if not presented and self.command == "GET" and web_auth.TICKET in query:
            rest = {key: value for key, value in query.items() if key != web_auth.TICKET}
            if auth.follow(query[web_auth.TICKET], _canonical(path, rest)):
                return rest
            raise _unauthorized()
        if len(presented) != 1 or not auth.valid(presented[0]):
            raise _unauthorized()
        return query

    def _session(self, path: str, body: dict) -> dict:
        """The UI secret's own two routes: the code exchange and link tickets."""
        auth = self.server.ui_auth
        if path == web_auth.SESSION_PATH:
            if set(body) != {"code"}:
                raise APIError(400, "A session request carries only its code")
            secret = auth.redeem(body["code"])
            if secret is None:
                raise _unauthorized()
            return {"secret": secret}
        target = body.get("path")
        if set(body) != {"path"} or not isinstance(target, str):
            raise APIError(400, "A link request carries only its path")
        link_path, link_query = parse_target(target)
        if (not link_path.startswith("/api/") or web_clients.is_ingress(link_path)
                or link_path == web_auth.SESSION_PATH or link_path.startswith(web_auth.SESSION_PATH + "/")
                or web_auth.TICKET in link_query):
            raise APIError(400, "Links are only for rika!'s own API downloads")
        ticket = auth.issue_link(_canonical(link_path, link_query))
        return {"url": f"{target}{'&' if '?' in target else '?'}{web_auth.TICKET}={ticket}"}

    def _send(self, status: int, data: bytes, content_type: str, *, download: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src http://127.0.0.1:3210; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        for key, value in self.response_headers.items():
            self.send_header(key, value)
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
        if web_clients.is_ingress(self.path):
            self._dispatch(False)
        else:
            self._json(405, {"error": "Cross-origin preflight is not supported"})

    def _dispatch(self, mutation: bool) -> None:
        try:
            if web_clients.is_ingress(self.path):
                web_clients.ingress_request(self)
                return
            self._security(mutation=mutation)
            path, query = self._target()
            upgrade = self.command == "GET" and self.headers.get("Upgrade") is not None
            if (path == "/api" or path.startswith("/api/")) and not upgrade:
                query = self._authorize(path, query, mutation)
            if mutation and (path == web_auth.SESSION_PATH or path == web_auth.LINK_PATH):
                self.server.app._no_query(query)
                self._json(200, self._session(path, self._body()))
                return
            if mutation:
                # Native desktop commands additionally require an explicit page
                # origin; the shared guard above already rejects a wrong one.
                if path == "/api/desktop/command" and not self.headers.get_all("Origin", []):
                    raise APIError(403, "Missing local request origin")
                self.server.app._no_query(query)
                body = self._body()
                with self.server.app.mutation_lock:
                    with closing(connect(self.server.app.settings.db_path)) as conn:
                        result = self.server.app.mutate(conn, path, body)
                self.server.app.mutated()
                self._json(200, result)
            elif upgrade:
                self._upgrade(path, query)
            elif path == "/api" or path.startswith("/api/"):
                self._get_api(path, query)
            else:
                # `?safe` opens one window in safe mode (Q9) and `?code` is the browser-mode
                # one-time code (kitty.web_auth); the page reads both itself.
                self.server.app._no_query({key: value for key, value in query.items() if key not in {"safe", "code"}})
                self._static(path)
        except APIError as exc:
            self.close_connection = True
            self.response_headers.update(exc.headers)
            self._json(exc.status, {"error": str(exc), **({"problems": exc.problems} if exc.problems else {})})
        except BuildStoreError as exc:
            # A settings change refused by the build file (it has an error, or is a link).
            self.close_connection = True
            self._json(exc.status, {"error": str(exc),
                                    **({"problems": [problem.as_dict() for problem in exc.problems]} if exc.problems else {})})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except (TimeoutError, sqlite3.OperationalError):
            self.close_connection = True
            self._json(503, {"error": "Local workspace is busy or unavailable; try again"})
        except Exception:
            self.close_connection = True
            self._json(500, {"error": "Local operation failed; inspect CLI diagnostics"})

    def _upgrade(self, path: str, query: dict[str, str]) -> None:
        """A long-lived same-origin upgrade (the terminal WebSocket).

        Browsers cannot add X-Kitty-Request or the UI secret's header to an upgrade, so the
        exact Origin and the secret as a subprotocol (kitty.web_auth) are required here, and
        the owning extension demands a single-use token that only a header-protected API
        call hands out.
        """
        if self.headers.get_all("Origin", []) != [self.server.origin]:
            raise APIError(403, "Cross-origin requests are not allowed")
        if not self.server.ui_auth.protocol_secret(self.headers.get_all("Sec-WebSocket-Protocol", [])):
            raise _unauthorized()
        #: The subprotocol the 101 response selects (kitty.web_socket.accept).
        self.websocket_protocol = web_auth.PROTOCOL
        app = self.server.app
        app.guard(path)
        for extension in app.extensions:
            upgrade = getattr(extension, "upgrade", None)
            if upgrade and upgrade(app, self, path, query):
                return
        raise APIError(404, "API route not found")

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
        app.guard(path)
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
                from kitty.core import resumes

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


def parse_target(raw: str) -> tuple[str, dict[str, str]]:
    """A request target's decoded path and its query (each key once), or a 400."""
    if len(raw) > 8192 or not raw.startswith("/") or raw.startswith("//"):
        raise APIError(400, "Invalid request target")
    parsed = urlsplit(raw)
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


def _canonical(path: str, query: dict[str, str]) -> str:
    """One GET target as a link ticket is bound to it: the path and its sorted query."""
    return json.dumps([path, sorted(query.items())], ensure_ascii=True)


def _unauthorized() -> APIError:
    return APIError(401, web_auth.REFUSAL, headers={"WWW-Authenticate": 'Kitty realm="rika!"'})


def _interrupt(*_args: object) -> None:
    raise KeyboardInterrupt


def _link(server: LocalHTTPServer) -> str:
    """A browser-mode link: the page trades its one-time code for the UI secret."""
    return f"{server.origin}/?code={server.ui_auth.issue_code()}"


def _announce_links(server: LocalHTTPServer, code_link: str, stop: threading.Event) -> None:
    """Print a fresh link whenever the current one is used or expires (kitty.web_auth)."""
    while True:
        server.ui_auth.wait_code(code_link.rsplit("=", 1)[1], stop)
        if stop.is_set():
            return
        code_link = _link(server)
        print(f"rika! next link (one browser tab each, works once within {int(web_auth.CODE_TTL // 60)} minutes): {code_link}", flush=True)


def _serve(settings: Settings, static_dir: Path, port: int, *, safe: bool = False) -> None:
    # A build file saved with an error starts the server on the last good build (Q9).
    with open_workspace(settings, modules=startup_modules(settings)) as workspace:
        if settings.demo and workspace.config.enabled("applyai"):
            from kitty.core import demo

            demo.seed(workspace.conn, settings, workspace.config)
        if settings.demo:
            from kitty.core import agent_demo
            from kitty.core.rika import demo_seed

            demo_seed.seed(workspace.conn)
            agent_demo.seed_demo(workspace.conn, settings.root)
        app = WebApplication(settings, workspace.config, static_dir, safe=safe)
    # A plain `kill` stops kitty-web like Ctrl+C: workers drain and the endpoint file goes.
    signal.signal(signal.SIGTERM, _interrupt)
    with LocalHTTPServer(app, port) as server:
        stop = threading.Event()
        try:
            # Paired programs find this server through the owner-only endpoint file.
            with endpoint.published(settings.root, server.origin):
                # A stop signal right after the announcement must still end serving cleanly.
                try:
                    link = _link(server)
                    print(f"rika! {'synthetic demo ' if settings.demo else ''}website: {link}", flush=True)
                    threading.Thread(target=_announce_links, args=(server, link, stop),
                                     daemon=True, name="kitty-links").start()
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        finally:
            stop.set()
            app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, help="rika! workspace root")
    parser.add_argument("--static-dir", type=Path, help="Built SPA directory (default: web/dist)")
    parser.add_argument("--demo", action="store_true", help="Disposable synthetic workspace; no live discovery")
    parser.add_argument("--safe", action="store_true",
                        help="Safe mode: open every window without the build's custom CSS and theme overrides")
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
                settings = Settings(root=demo_root, db_path=demo_root / "data" / "demo.db", demo=True)
                _serve(settings, static_dir, args.port, safe=args.safe)
        else:
            _serve(Settings.load(root=root), static_dir, args.port, safe=args.safe)
    except (ConfigError, OSError, ValueError):
        parser.exit(1, "Unable to open the local website. Check the port, workspace configuration and public example files with the CLI.\n")


if __name__ == "__main__":
    main()

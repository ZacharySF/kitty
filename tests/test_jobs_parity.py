"""ApplyAI's local job routes answered by rika-server (server/src/jobs) against the retained
Python core on two copies of one seeded workspace.

The reference owns the deleted handlers' glue (`web_jobs.get/post`'s list, options, pipeline,
reminder, tool-panel and bulk branches, `WebApplication._applyai_get` and `_mutate_job`, and
`web_resumes.get`'s library reads). A file clock makes every stamp, freshness, follow-up,
snooze and posting-age cutoff comparable without masking anything. The worker keeps the
dashboard, public metadata, adding a posting, the profile editors, resume registration,
generation, the coach, decisions and downloads.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import yaml
from terminal_support import RIKA_SERVER, front_site
from test_blocks_parity import base_time

from kitty import dates, models
from kitty.config import load_config
from kitty.core import applications, favorites, followups, jobs, pipeline, record_events, reminders, resume_versions, resumes, sources, today
from kitty.core.apply.filler import choose_resume
from kitty.core.workspace import sync_config_to_db
from kitty.db import connect
from kitty.db.connection import transaction
from kitty.modules import MODULE_IDS, ModuleDisabledError
from kitty.web_jobs import _date, _text
from kitty.web_server import APIError, WebApplication, _integer, _job_json, _pdf_available, _pdf_bytes

QUEUE_STATES = {"new", "queued", "later", "skipped", "not_eligible"}
STATUSES = {"applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn"}
ELIGIBILITY = {"eligible", "boosted", "ambiguous", "unknown", "ineligible"}
LATER_STATUSES = frozenset({"oa", "interview", "offer", "rejected", "ghosted", "withdrawn"})
VERSION = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
NO_NETWORK = "http://127.0.0.1:9/"


class Clock:
    value = None

    @classmethod
    def now(cls):
        return cls.value


def clocked(patch):
    for module in (dates, jobs, reminders, followups, today, models):
        patch.setattr(module, "now", Clock.now)


def _resume_keys(query, allowed):
    if set(query) - set(allowed):
        raise APIError(400, "Unknown resume request fields")


class Reference:
    """The deleted handlers as they were, over the retained core."""

    def __init__(self, db_path, root, modules):
        self.conn, self.modules = connect(db_path), modules
        self.settings = SimpleNamespace(root=root, config_dir=root / "config")
        self.reload_config()

    def reload_config(self):
        self.config = load_config(self.settings.config_dir, modules=self.modules)
        sync_config_to_db(self.conn, self.config)

    @staticmethod
    def _no_query(query):
        if query:
            raise APIError(400, "This route does not accept query parameters")

    @staticmethod
    def _job(conn, job_id):
        job = jobs.get_job(conn, job_id)
        if job is None:
            raise APIError(404, "Job not found")
        return job

    def job_filter(self, view):
        if view not in {"jobs", "inbox", "queue", "applications", "favorites"}:
            raise APIError(400, "Unknown job view")
        return jobs.JobFilter(
            queue_states=["queued"] if view == "queue" else ["new"] if view == "inbox" else None,
            not_applied=view in {"queue", "inbox"}, applied_only=view == "applications",
            favorites_only=view == "favorites", include_closed=view in {"applications", "favorites"},
            order="best", prefer_cities=self.config.prefer_cities,
            prefer_remote=self.config.profile.open_to_remote,
        )

    # --------------------------------------------------------------------- reads

    def get(self, path, query):
        app, conn = self, self.conn
        if path == "/api/job-options":
            app._no_query(query)
            return {"views": [{"key": view.key, "title": view.title} for view in app.config.views.views],
                    "sources": [source.name for source in sources.list_sources(conn)]}
        if path in {"/api/jobs", "/api/jobs/selection"}:
            fields = {"view", "search", "track", "tier", "offset", "limit", "saved", "source",
                      "eligibility", "status", "state", "closed", "order", "company", "age", "favorites"}
            if set(query) - fields:
                raise APIError(400, "Unknown jobs query parameter")
            view = query.get("view", "jobs")
            filt = app.job_filter("queue" if view == "apply" else view)
            if view in {"apply", "inbox"}:
                filt.eligibility = jobs.NOT_FILTERED_OUT
            if view == "apply":
                filt.queue_states = ["new", "queued"]
                filt.tiers = ["A", "B"]
            if view == "inbox":
                filt.posted_within_days = 14
            saved = query.get("saved", "")
            if saved:
                key = _integer(saved, "Saved view", 0, 9)
                configured = next((v for v in app.config.views.views if v.key == key), None)
                if configured is None:
                    raise APIError(400, "Saved view not found")
                selected = jobs.JobFilter.from_view_filters(configured.filters)
                for field in dataclasses.fields(selected):
                    value = getattr(selected, field.name)
                    if field.name not in {"order", "limit", "prefer_cities", "prefer_remote"} and value is not None and value != ():
                        if field.name in {"include_closed", "not_applied", "applied_only", "favorites_only"}:
                            setattr(filt, field.name, getattr(filt, field.name) or value)
                        elif field.name in {"queue_states", "tracks", "tiers", "statuses", "eligibility", "sources"} and getattr(filt, field.name):
                            setattr(filt, field.name, list(set(value) & set(getattr(filt, field.name))) or ["__no_match__"])
                        else:
                            setattr(filt, field.name, value)
            search = _text(query.get("search", ""), "Search", 500)
            company = query.get("company", "0")
            if company not in {"0", "1"}:
                raise APIError(400, "Company filter must be 0 or 1")
            if search:
                if company == "1":
                    filt.company_text = search
                else:
                    filt.text = search
            if "age" in query:
                filt.posted_within_days = _integer(query["age"], "Age", 1, 36500) if query["age"] else None
            if query.get("favorites", "") not in {"", "1"}:
                raise APIError(400, "Favorites must be 1 when enabled")
            if query.get("favorites") == "1":
                filt.favorites_only = True
            filters = [("track", "tracks", set(app.config.track_names)), ("tier", "tiers", {"A", "B", "C"}),
                       ("status", "statuses", STATUSES), ("state", "queue_states", QUEUE_STATES | {"applied"}),
                       ("eligibility", "eligibility", ELIGIBILITY),
                       ("source", "sources", {s.name for s in sources.list_sources(conn)})]
            for parameter, field, allowed in filters:
                value = query.get(parameter, "")
                if value:
                    if value not in allowed:
                        raise APIError(400, f"Unknown {parameter}")
                    existing = getattr(filt, field)
                    setattr(filt, field, [value] if not existing or value in existing else ["__no_match__"])
            closed = query.get("closed", "")
            if closed not in {"", "0", "1"}:
                raise APIError(400, "Closed must be 0 or 1")
            if closed:
                filt.include_closed = closed == "1"
            order = query.get("order", "")
            if order:
                if order not in {"best", "newest", "company", "deadline", "score", "track"}:
                    raise APIError(400, "Unknown ordering")
                filt.order = order
            if view == "apply":
                filt.include_closed = False
            offset = _integer(query.get("offset", "0"), "offset", 0, 100000)
            limit = _integer(query.get("limit", "30"), "limit", 1, 100)
            total = jobs.count_jobs(conn, filt)
            if path == "/api/jobs/selection":
                if total > 10000:
                    raise APIError(400, "Narrow the search to at most 10,000 jobs before selecting all")
                filt.limit = 10000
                return {"ids": [job.id for job in jobs.list_jobs(conn, filt)], "total": total}
            if view == "apply" and (not order or order == "best"):
                filt.limit = None
                ranked = jobs.list_jobs(conn, filt)
                ranked.sort(key=lambda job: (job.fill_state == "ready", job.tier != "A"))
                page = ranked[offset:offset + limit]
            else:
                filt.limit = offset + limit
                page = jobs.list_jobs(conn, filt)[offset:] if offset < total else []
            return {"jobs": [_job_json(job) for job in page], "total": total}
        if path == "/api/pipeline":
            if set(query) - {"track"}:
                raise APIError(400, "Unknown pipeline parameter")
            track = query.get("track") or None
            if track and track not in app.config.track_names:
                raise APIError(400, "Unknown track")
            board = pipeline.board(conn, track=track)
            funnel = pipeline.funnel(conn, track=track)
            return {"columns": {status: [dataclasses.asdict(card) for card in cards] for status, cards in board.items()},
                    "funnel": {**dataclasses.asdict(funnel), "response_rate": funnel.response_rate, "advance_rate": funnel.advance_rate}}
        if path == "/api/reminders":
            app._no_query(query)
            due = today.due_reminders(conn, today.now().date())
            upcoming = []
            for reminder in reminders.upcoming_reminders(conn, limit=100):
                job = jobs.get_job(conn, reminder.job_id) if reminder.job_id else None
                upcoming.append({**dataclasses.asdict(reminder), "label": f"{job.company_name} — {job.title}" if job else reminder.note or "Reminder"})
            return {"due": [dataclasses.asdict(item) for item in due], "upcoming": upcoming}
        match = re.fullmatch(r"/api/job-tools/([1-9][0-9]{0,17})", path)
        if match:
            app._no_query(query)
            job = app._job(conn, int(match[1]))
            application = applications.get_application_for_job(conn, job.id)
            return {"tier": job.tier, "queue_state": job.queue_state, "deadline_at": job.deadline_at,
                    "tracks": job.tracks, "eligibility": job.eligibility, "apply_url": job.apply_url or job.url,
                    "sources": [dataclasses.asdict(source) for source in job.sources],
                    "evidence": {field: getattr(job, field) for field in (
                        "tier_source", "first_seen_at", "last_seen_at", "closed_at", "is_active",
                        "school_selective_note", "verification_status", "verified_at",
                        "verification_checked_at", "verification_reason", "fill_attempted_at",
                        "fill_ready_at", "edit_notes", "like_score", "like_terms")},
                    "freshness": job.freshness,
                    "application": dataclasses.asdict(application) if application else None,
                    "can_undo": bool(application and applications.last_undoable_event(conn, application.id)),
                    "followups": [dataclasses.asdict(r) for r in reminders.followups_for(conn, application.id)] if application else []}
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
                    "history": [dataclasses.asdict(event) for event in applications.history(conn, application.id)],
                } if application else None,
            })
            return result
        if path == "/api/resumes":
            self._no_query(query)
            defaults = {}
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
                "versions": [{key: version[key] for key in ("id", "track", "status", "is_default", "created_at")}
                             for version in resume_versions.list_versions(conn, self.settings)],
            }
        if path == "/api/resumes/workflow":
            _resume_keys(query, ())
            defaults = {name: resume_versions.default_resume(conn, app.config, name) for name in app.config.track_names}
            return {"tracks": app.config.track_names,
                    "defaults": {name: item.label if item else None for name, item in defaults.items()},
                    "resumes": [{"id": item.id, "label": item.label, "description": item.description,
                                 "available": _pdf_available(app.settings, item),
                                 "default_tracks": [name for name, selected in defaults.items()
                                                    if selected and selected.id == item.id]}
                                for item in resumes.list_resumes(conn)],
                    "versions": [dict(row) for row in conn.execute(
                        "SELECT id,track,status,created_at FROM resume_versions ORDER BY created_at DESC,rowid DESC")]}
        match = re.fullmatch(r"/api/resumes/versions/" + VERSION, path)
        if match:
            _resume_keys(query, ())
            if not conn.execute("SELECT 1 FROM resume_versions WHERE id=?", (match[1],)).fetchone():
                raise APIError(404, "Resume version not found")
            try:
                return {"version": resume_versions.get_version(conn, self.settings, match[1])}
            except (ValueError, OSError) as exc:
                raise APIError(409, "Resume audit is unavailable or has changed; restore immutable artifacts") from exc
        raise APIError(404, "API route not found")

    # --------------------------------------------------------------------- writes

    def post(self, path, body):
        app, conn = self, self.conn
        if path == "/api/jobs/bulk":
            if set(body) - {"ids", "action", "value"}:
                raise APIError(400, "Unknown bulk action field")
            ids = body.get("ids")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 10000 or any(type(i) is not int or not 1 <= i <= 2**63 - 1 for i in ids):
                raise APIError(400, "Select between 1 and 10,000 job IDs")
            selected = [app._job(conn, job_id) for job_id in dict.fromkeys(ids)]
            action, value = body.get("action"), body.get("value")
            if action == "tier":
                if value not in ("A", "B", "C", None):
                    raise APIError(400, "Choose tier A, B, C or unranked")
            elif action == "state":
                if not isinstance(value, str) or value not in QUEUE_STATES:
                    raise APIError(400, "Choose a valid queue state")
                if any(job.application_id for job in selected):
                    raise APIError(409, "Applied jobs retain their application state")
            elif action == "not_eligible":
                if any(job.application_id for job in selected):
                    raise APIError(409, "Applied jobs retain their application state")
            elif action == "add_track":
                if not isinstance(value, str) or value not in app.config.track_names:
                    raise APIError(400, "Choose a configured track")
            elif action == "favorite":
                if type(value) is not bool and value != "toggle":
                    raise APIError(400, "Favorite must be a boolean or toggle")
                if value == "toggle":
                    value = not all(job.favorited_at for job in selected)
            elif action == "delete":
                if value is not True:
                    raise APIError(400, "Confirm job deletion first")
                if any(job.application_id for job in selected):
                    raise APIError(409, "Submitted applications and their history are protected from job deletion")
                browser = {"running": False, "job_ids": []}
                if browser["running"] and set(browser["job_ids"]) & {job.id for job in selected}:
                    raise APIError(409, "Stop preparation for these jobs before deleting them; retained Firefox tabs stay open")
            else:
                raise APIError(400, "Unknown bulk action")
            target_ids = [job.id for job in selected]
            if action == "state":
                favorites.set_state(conn, target_ids, value)
            elif action == "not_eligible":
                favorites.mark_not_eligible(conn, target_ids)
            elif action == "favorite":
                (favorites.favorite if value else favorites.unfavorite)(conn, target_ids)
            elif action == "delete":
                with transaction(conn):
                    for job_id in target_ids:
                        if applications.get_application_for_job(conn, job_id) is not None:
                            raise APIError(409, "Submitted applications and their history are protected from job deletion")
                    conn.executemany("UPDATE screening_questions SET first_seen_job_id=NULL WHERE first_seen_job_id=?",
                                     [(job_id,) for job_id in target_ids])
                    conn.executemany("DELETE FROM jobs WHERE id=?", [(job_id,) for job_id in target_ids])
            else:
                with transaction(conn):
                    for job in selected:
                        if action == "add_track":
                            jobs.link_track(conn, job.id, value, reason="Manually added in the website")
                        else:
                            jobs.set_tier(conn, job.id, value, reason="Manually chosen in the website")
            return {"ok": True, "changed": len(selected), **({"favorite": value} if action == "favorite" else {})}
        if path == "/api/reminders":
            if set(body) - {"date", "note", "job_id"}:
                raise APIError(400, "Unknown reminder field")
            job_id = body.get("job_id")
            if job_id is not None:
                if type(job_id) is not int or not 1 <= job_id <= 2**63 - 1:
                    raise APIError(400, "Invalid job ID")
                app._job(conn, job_id)
            reminder = reminders.create_reminder(conn, kind="custom", due_at=_date(body.get("date")),
                                                  note=_text(body.get("note", ""), "Note"), job_id=job_id)
            return {"ok": True, "id": reminder.id}
        if path == "/api/reminders/restore":
            row = body.get("reminder")
            fields = {"id", "job_id", "application_id", "kind", "due_at", "note", "done_at", "created_at"}
            if set(body) != {"reminder"} or not isinstance(row, dict) or set(row) != fields:
                raise APIError(400, "Expected the deleted reminder to restore")
            ids_ok = all(row[key] is None or type(row[key]) is int and 1 <= row[key] <= 2**63 - 1
                         for key in ("job_id", "application_id")) and type(row["id"]) is int and 1 <= row["id"] <= 2**63 - 1
            texts_ok = all(isinstance(row[key], str) and len(row[key]) <= 100 for key in ("due_at", "created_at")) \
                and all(row[key] is None or isinstance(row[key], str) for key in ("note", "done_at"))
            if not ids_ok or not texts_ok or row["kind"] not in {"followup", "deadline", "opens", "custom"}:
                raise APIError(400, "Expected the deleted reminder to restore")
            if row["note"] is not None:
                _text(row["note"], "Note")
            if conn.execute("SELECT id FROM reminders WHERE id=?", (row["id"],)).fetchone():
                raise APIError(409, "That reminder already exists")
            try:
                reminders.restore_reminder(conn, row)
            except sqlite3.IntegrityError:
                raise APIError(409, "Its job or application no longer exists") from None
            return {"ok": True}
        match = re.fullmatch(r"/api/reminders/([1-9][0-9]{0,17})(?:/(complete|snooze|delete))?", path)
        if match:
            ident, action = int(match[1]), match[2]
            if action and body:
                raise APIError(400, "Expected empty object")
            if not action and (not body or set(body) - {"date", "note"}):
                raise APIError(400, "Change the reminder date or note")
            if not conn.execute("SELECT id FROM reminders WHERE id=?", (ident,)).fetchone():
                raise APIError(404, "Reminder not found")
            if action == "complete":
                reminders.complete_reminder(conn, ident)
            elif action == "snooze":
                previous, due_at = reminders.snooze_reminder(conn, ident)
                return {"ok": True, "previous": previous, "due_at": due_at}
            elif action == "delete":
                return {"ok": True, "reminder": reminders.delete_reminder(conn, ident)}
            else:
                reminders.update_reminder(conn, ident, due_at=_date(body["date"]) if "date" in body else None,
                                          note=_text(body["note"], "Note") if "note" in body else None)
            return {"ok": True}
        match = re.fullmatch(r"/api/job-tools/([1-9][0-9]{0,17})", path)
        if match:
            if set(body) - {"action", "value", "note"}:
                raise APIError(400, "Unknown job action field")
            job = app._job(conn, int(match[1]))
            application = applications.get_application_for_job(conn, job.id)
            action, value = body.get("action"), body.get("value")
            with transaction(conn):
                if action == "deadline":
                    jobs.set_deadline(conn, job.id, _date(value, optional=True))
                elif action == "next_action":
                    if application is None:
                        raise APIError(409, "Record your submission first")
                    applications.set_next_action(conn, application.id, due_at=_date(value, optional=True),
                                                 note=_text(body.get("note", ""), "Note") or None)
                elif action == "undo":
                    if application is None or applications.undo_last_status(conn, application.id) is None:
                        raise APIError(409, "No status change can be undone; the original submission stays")
                elif action == "tracks":
                    if not isinstance(value, list) or any(not isinstance(t, str) or t not in app.config.track_names for t in value):
                        raise APIError(400, "Choose configured tracks")
                    for track in set(job.tracks) - set(value):
                        jobs.unlink_track(conn, job.id, track)
                    for track in set(value) - set(job.tracks):
                        jobs.link_track(conn, job.id, track, reason="Manually assigned in the website")
                else:
                    raise APIError(400, "Unknown job action")
            return {"ok": True}
        match = re.fullmatch(r"/api/jobs/([1-9][0-9]{0,17})", path)
        if match is None:
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
            occurred_at = dates.parse_user_date(when) if when.strip() else None
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

    def __call__(self, path, body, now, query):
        Clock.value = now
        try:
            if body is not None and query:
                raise APIError(400, "This route does not accept query parameters")
            if path == "/api/reload":
                self.reload_config()
                return 200, {"ok": True}
            if "applyai" not in self.modules:
                raise APIError(403, str(ModuleDisabledError("applyai")))
            if body is None:
                answer = self.get(path, query)
            else:
                before = record_events.op_log_mark(self.conn)
                answer = self.post(path, body)
                record_events.capture(self.conn, path, body, answer, op_log_before=before)
        except APIError as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            return exc.status, {"error": str(exc)}
        except Exception:  # noqa: BLE001 - the server's own catch-all answer
            if self.conn.in_transaction:
                self.conn.rollback()
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))


class Through:
    def __init__(self, site, clock):
        self.site, self.clock = site, clock

    def __call__(self, path, body, now, query):
        self.clock.write_text(now.isoformat(), encoding="utf-8")
        return self.site.request(path + ("?" + urlencode(query) if query else ""), body)


# ------------------------------------------------------------------------------ the workspace

TRACKS = ("quant", "gpu", "cpu", "embedded", "software", "power", "research")
VIEWS = [
    {"key": 1, "title": "New - last 14 days", "filters": {"posted_within_days": 14, "queue_state": ["new", "queued"],
                                                          "eligibility": ["eligible", "boosted", "ambiguous", "unknown"]}},
    {"key": 2, "title": "A-tier not applied", "filters": {"tier": ["A"], "not_applied": True}},
    {"key": 3, "title": "Follow-ups due", "filters": {"next_action_due": True}},
    {"key": 4, "title": "All jobs", "filters": {}},
    {"key": 5, "title": "Closed too, with text", "filters": {"include_closed": True, "text": "intern", "school_selective": False}},
    {"key": 6, "title": "GPU or CPU, deadlines", "filters": {"track": ["gpu", "cpu"], "has_deadline": True, "status": []}},
    {"key": 7, "title": "Sources and states", "filters": {"source": ["greenhouse"], "queue_state": ["queued", "skipped"],
                                                          "not_applied": False, "tier": ["B", "C"]}},
    {"key": 0, "title": "Selective, never applied", "filters": {"school_selective": True, "eligibility": [], "posted_within_days": 400}},
]


def write_config(root):
    config = root / "config"
    tracks = yaml.safe_load((config / "tracks.yaml").read_text(encoding="utf-8"))
    defaults = {"quant": "Alpha", "gpu": "general", "cpu": "Gamma", "embedded": "Beta", "software": "", "power": "Alpha", "research": None}
    for track in tracks["tracks"]:
        if track["name"] in defaults:
            track["default_resume"] = defaults[track["name"]]
        if track["name"] == "quant":
            track.setdefault("location_rules", {})["prefer_cities"] = ["Austin", "New York"]
        if track["name"] == "gpu":
            track.setdefault("location_rules", {})["prefer_cities"] = ["Hewitt", "Austin"]
    (config / "tracks.yaml").write_text(yaml.safe_dump(tracks, sort_keys=False), encoding="utf-8")
    profile = yaml.safe_load((config / "profile.yaml").read_text(encoding="utf-8"))
    profile["open_to_remote"] = True
    (config / "profile.yaml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    (config / "views.yaml").write_text(yaml.safe_dump({"views": VIEWS}, sort_keys=False), encoding="utf-8")
    (config / "apply.yaml").write_text(yaml.safe_dump({"followup_days": [3, 10, 10]}), encoding="utf-8")


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def pdf(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n" + text.encode() + b"\n%%EOF\n")
    return path


def version(root, conn, number, track, *, status="candidate", resume_id=None, tamper=False, errors=None, created):
    version_id = f"{number:08x}-0000-4000-8000-{number:012x}"
    folder = root / "data" / "resume_versions" / version_id
    folder.mkdir(parents=True)
    (folder / "candidate.pdf").write_bytes(b"%PDF-1.4 candidate " + str(number).encode())
    (folder / "source.html").write_text(f"<html><body>Version {number} ✓</body></html>", encoding="utf-8")
    artifacts = {path.name: resumes.file_hash(path) for path in sorted(folder.iterdir())}
    manifest = {"id": version_id, "track": track, "created_at": created, "status": "ignored", "score": 0.1 * number,
                "patches": [{"before": "a", "after": "b", "evidence": ["Posting 1", "Ünïcode"]}],
                "validation": {"valid": True, "errors": errors if errors is not None else [], "offline": False},
                "artifacts": artifacts, "manifest_path": str(folder / "manifest.json"), "pdf_path": str(folder / "candidate.pdf")}
    manifest_path = folder / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if tamper:
        (folder / "candidate.pdf").write_bytes(b"%PDF-1.4 tampered")
    conn.execute("INSERT INTO resume_versions (id,track,status,source_path,source_hash,pdf_path,manifest_path,manifest_hash,resume_id,created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (version_id, track, status, str(folder / "source.html"), "0" * 64, str(folder / "candidate.pdf"),
                  str(manifest_path), resumes.file_hash(manifest_path), resume_id, created))
    return version_id, manifest_path


def seed(workspace, base, monkeypatch):
    root, conn = workspace.settings.root, workspace.conn
    write_config(root)
    sync_config_to_db(conn, load_config(root / "config"))
    seeded = {}
    with monkeypatch.context() as patch:
        clocked(patch)
        Clock.value = base - timedelta(days=40)

        def post(number, company, title, **fields):
            source = fields.pop("source", "manual")
            job, _ = jobs.upsert_job(conn, company=company, title=title, url=f"https://example.invalid/jobs/{number}",
                                     source=source, source_job_id=f"seed-{number}", **fields)
            seeded[f"j{number}"] = job.id
            return job.id

        listing = json.dumps({"provenance": {"url_is_listing": True}})
        j1 = post(1, "Synthetic 100% Labs", "Controls Intern", location="Austin, TX", tracks=["quant"], eligibility="eligible",
                  posted_at=iso(base - timedelta(days=2)), source="greenhouse", raw_json=listing)
        jobs.set_tier(conn, j1, "A", reason="Rules")
        conn.execute("UPDATE jobs SET verification_status='open', verified_at=?, verification_checked_at=?, apply_url=? WHERE id=?",
                     (iso(base - timedelta(days=1)), iso(base - timedelta(days=1)), "https://apply.example.invalid/1", j1))
        j2 = post(2, "Beta Robotics", "FPGA Intern", location="Remote", is_remote=True, tracks=["gpu"], eligibility="boosted",
                  posted_at=iso(base - timedelta(days=5)))
        jobs.set_tier(conn, j2, "B", reason="Rules")
        jobs.set_queue_state(conn, j2, "queued")
        conn.execute("UPDATE jobs SET fill_state='ready', fill_ready_at=?, fill_attempted_at=? WHERE id=?",
                     (iso(base - timedelta(hours=3)), iso(base - timedelta(hours=4)), j2))
        j3 = post(3, "Gamma Power", "Power Intern", location="Waco, TX", tracks=["quant", "power"], eligibility="ineligible",
                  eligibility_reason="requires PhD", posted_at=iso(base - timedelta(days=9)), source="greenhouse")
        jobs.set_tier(conn, j3, "C", reason="Rules")
        j4 = post(4, "Delta Chips", "GPU Verification Intern", location="Hewitt, TX", tracks=["gpu"], eligibility="ambiguous",
                  posted_at=iso(base - timedelta(days=30)), jd_text="Verify GPU shader pipelines with SystemVerilog and C++.")
        j5 = post(5, "Epsilon Quant", "Quant Research Intern", location="New York, NY", tracks=["quant"],
                  jd_text="Quant research with Python and C++ for trading.")
        jobs.set_tier(conn, j5, "A", reason="Rules")
        j6 = post(6, "Zeta Systems", "Embedded Firmware Intern", tracks=["embedded"], source="lever",
                  deadline_at=iso(base + timedelta(days=3)), eligibility="unknown")
        jobs.set_tier(conn, j6, "B", reason="Rules")
        j7 = post(7, "Eta Labs", "Software Intern", tracks=["software"], eligibility="eligible", source="greenhouse")
        j8 = post(8, "Theta Inc", "Research Intern", tracks=["research"], jd_text="FPGA research with Verilog.")
        j9 = post(9, "Iota Co", "Power Electronics Intern", school_selective=True, school_selective_note="Partner schools only",
                  tracks=["power"], deadline_at=iso(base + timedelta(days=20)))
        jobs.set_edit_notes(conn, j9, ["mention SPICE", "shorten summary"])
        j10 = post(10, "Kappa Remote", "Virtual Intern", location="Virtual")
        conn.execute("UPDATE jobs SET verification_status='closed', verified_at=? WHERE id=?", (iso(base - timedelta(days=2)), j10))
        j11 = post(11, "Lambda", "Withdrawn Intern", tracks=["quant"])
        j12 = post(12, "Mu", "Stale Intern", tracks=["gpu"], location="Austin, TX")
        conn.execute("UPDATE jobs SET verification_status='open', verified_at=? WHERE id=?", (iso(base - timedelta(days=30)), j12))
        j13 = post(13, "Nu", "Deletable Intern", tracks=["cpu"])
        j14 = post(14, "Xi", "Deletable Two", tracks=["cpu", "gpu"])
        for number in range(15, 23):
            post(number, f"Filler {number % 3}", f"Filler Intern {number}", tracks=[TRACKS[number % len(TRACKS)]],
                 posted_at=iso(base - timedelta(days=number)), location="Austin, TX" if number % 2 else None)

        # Resumes: registered, changed after registration, missing, inactive, not a PDF, relative.
        files = root / "resumes"
        alpha = resumes.add_resume(conn, "Alpha", pdf(files / "alpha.pdf", "alpha"), description="Main resume")
        beta = resumes.add_resume(conn, "Beta", pdf(files / "beta.pdf", "beta"), default_tracks=["embedded", "cpu"])
        pdf(files / "beta.pdf", "beta, edited by hand")
        gamma = resumes.add_resume(conn, "Gamma", pdf(files / "gamma.pdf", "gamma"))
        (files / "gamma.pdf").unlink()
        delta = resumes.add_resume(conn, "delta", pdf(files / "delta.pdf", "delta"))
        conn.execute("UPDATE resumes SET active=0 WHERE id=?", (delta.id,))
        text = files / "epsilon.txt"
        text.write_bytes(b"%PDF-1.4 not really")
        epsilon = resumes.add_resume(conn, "epsilon", text)
        zeta = resumes.add_resume(conn, "Zeta", pdf(files / "zeta.pdf", "zeta"))
        conn.execute("UPDATE resumes SET file_path='resumes/zeta.pdf', default_tracks='\"quant\"' WHERE id=?", (zeta.id,))
        conn.execute("INSERT INTO resume_default_overrides VALUES ('gpu', ?, NULL), ('software', NULL, NULL)", (zeta.id,))
        seeded.update(alpha=alpha.id, beta=beta.id, gamma=gamma.id, delta=delta.id, epsilon=epsilon.id, zeta=zeta.id)
        jobs.set_resume(conn, j8, alpha.id)

        # Applications, their histories, proposals, follow-ups and next actions.
        Clock.value = base - timedelta(days=20)
        followups.mark_applied(conn, workspace.config, j5, resume_id=alpha.id, track="quant",
                               applied_at=iso(base - timedelta(days=20)), evidence="Confirmation email")
        Clock.value = base - timedelta(days=12)
        application = applications.get_application_for_job(conn, j5)
        applications.set_status(conn, application.id, "oa", occurred_at=iso(base - timedelta(days=12)))
        applications.set_status(conn, application.id, "interview")
        applications.set_next_action(conn, application.id, due_at=iso(base - timedelta(days=1)), note="Send thank-you")
        jobs.close_job(conn, j5, reason="Posting removed")
        followups.mark_applied(conn, workspace.config, j7, track="software")
        seventh = applications.get_application_for_job(conn, j7)
        applications.set_status(conn, seventh.id, "rejected")
        applications.set_status(conn, seventh.id, "offer", source="gmail", state="proposed", evidence="Offer email")
        followups.mark_applied(conn, workspace.config, j11)
        applications.set_status(conn, applications.get_application_for_job(conn, j11).id, "withdrawn")

        # Favorites and hand judgements: the like scores the routes rescore.
        Clock.value = base - timedelta(days=5)
        favorites.favorite(conn, [j4, j8, j2])
        favorites.set_state(conn, [j3], "skipped")
        conn.execute("UPDATE jobs SET like_terms='not json' WHERE id=?", (j9,))

        # Reminders: due, overdue, done, future, about jobs and about nothing.
        Clock.value = base - timedelta(days=2)
        seeded["r_due"] = reminders.create_reminder(conn, kind="custom", due_at=iso(base - timedelta(hours=5)), note="Call recruiter", job_id=j6).id
        seeded["r_late"] = reminders.create_reminder(conn, kind="deadline", due_at=iso(base - timedelta(days=3)), job_id=j9).id
        seeded["r_free"] = reminders.create_reminder(conn, kind="custom", due_at=iso(base + timedelta(days=2)), note="Update portfolio").id
        seeded["r_blank"] = reminders.create_reminder(conn, kind="opens", due_at=iso(base + timedelta(days=4))).id
        seeded["r_job"] = reminders.create_reminder(conn, kind="custom", due_at=iso(base + timedelta(days=1)), note="", job_id=j13).id
        done = reminders.create_reminder(conn, kind="custom", due_at=iso(base - timedelta(days=1)), note="Done already")
        reminders.complete_reminder(conn, done.id)
        conn.execute("INSERT INTO screening_questions (text, norm_hash, first_seen_job_id, times_seen, answered, created_at, updated_at) "
                     "VALUES ('Are you authorized?', 'h1', ?, 2, 0, ?, ?)", (j13, iso(base), iso(base)))

        # Resume versions: valid, an artifact changed, promoted and the track's default.
        created = iso(base - timedelta(days=3))
        seeded["v1"], seeded["v1_manifest"] = version(root, conn, 1, "quant", created=created)
        seeded["v2"], _ = version(root, conn, 2, "gpu", tamper=True, errors=["render warning"], created=created)
        seeded["v3"], _ = version(root, conn, 3, "gpu", status="promoted", resume_id=zeta.id, errors="ab",
                                  created=iso(base - timedelta(days=4)))
        conn.execute("INSERT INTO resume_version_history (track,action,version_id,from_resume_id,to_resume_id,created_at) "
                     "VALUES ('gpu','promote',?,NULL,?,?)", (seeded["v3"], zeta.id, created))
        conn.commit()
    seeded["j"] = [seeded[f"j{number}"] for number in range(1, 15)]
    return seeded


# ------------------------------------------------------------------------------ the scenario

def scenario(call, seeded, base, root, modules=MODULE_IDS):
    answers = []
    allowed = "applyai" in modules
    now = base

    def do(path, body=None, expect=200, **query):
        nonlocal now
        now += timedelta(seconds=1)
        status, answer = call(path, body, now, query)
        if not allowed and path != "/api/reload" and not (body is not None and query):
            expect = 403
        assert status == expect, (len(answers), path, body, query, status, answer)
        answers.append((path, status, answer))
        return answer

    j = seeded["j"]
    j1, j2, j3, j4, j5, j6, j7, j8, j9, j10, j11, j12, j13, j14 = j
    missing = 999_999

    # Lists, views, filters, orders, pages and their refusals.
    for view in ("jobs", "inbox", "queue", "applications", "favorites", "apply"):
        do("/api/jobs", view=view)
        do("/api/jobs/selection", view=view)
    do("/api/jobs")
    do("/api/jobs", expect=400, view="nope")
    do("/api/jobs", expect=400, other="1")
    for order in ("best", "newest", "company", "deadline", "score", "track"):
        do("/api/jobs", order=order, closed="1", limit="100")
    do("/api/jobs", expect=400, order="random")
    do("/api/jobs", view="apply", order="newest")
    do("/api/jobs", search="intern", limit="3", offset="2")
    do("/api/jobs", search="  INTERN  ", company="0")
    do("/api/jobs", search="100%", company="1")
    do("/api/jobs/selection", search="filler", company="1")
    do("/api/jobs", expect=400, company="2")
    do("/api/jobs", expect=400, search="x" * 501)
    do("/api/jobs", expect=400, search="a\x00b")
    do("/api/jobs", search="é" * 500)
    for age in ("", "7", "36500"):
        do("/api/jobs", age=age, view="inbox")
    for age in ("0", "36501", "x", "07a", "１"):
        do("/api/jobs", expect=400, age=age)
    do("/api/jobs", favorites="1")
    do("/api/jobs", expect=400, favorites="0")
    do("/api/jobs", track="quant")
    do("/api/jobs", track="gpu", view="inbox", state="queued")
    do("/api/jobs", expect=400, track="parttime-nope")
    do("/api/jobs", tier="A", view="apply")
    do("/api/jobs", tier="C", view="apply")
    do("/api/jobs", status="interview", view="applications")
    do("/api/jobs", state="applied", closed="1")
    do("/api/jobs", eligibility="ineligible", view="inbox")
    do("/api/jobs", source="greenhouse")
    do("/api/jobs", source="lever", view="queue")
    for parameter in ("tier", "status", "state", "eligibility", "source"):
        do("/api/jobs", expect=400, **{parameter: "nope"})
    do("/api/jobs", closed="0", view="favorites")
    do("/api/jobs", expect=400, closed="yes")
    do("/api/jobs", offset="100000")
    for offset, limit in (("-1", "30"), ("100001", "30"), ("0", "0"), ("0", "101"), ("", "5"), ("1" * 11, "5")):
        do("/api/jobs", expect=400, offset=offset, limit=limit)
    for key in ("0", "1", "2", "3", "4", "5", "6", "7"):
        do("/api/jobs", saved=key)
        do("/api/jobs", saved=key, view="inbox", tier="B")
        do("/api/jobs/selection", saved=key, view="applications")
    do("/api/jobs", saved="7", view="apply", state="queued")
    do("/api/jobs", expect=400, saved="8")
    do("/api/jobs", expect=400, saved="10")
    do("/api/jobs/selection", expect=400, saved="x")
    do("/api/job-options")
    do("/api/job-options", expect=400, x="1")

    # One job, its tool panel and the pipeline.
    for job_id in j:
        do(f"/api/jobs/{job_id}")
        do(f"/api/job-tools/{job_id}")
    do(f"/api/jobs/{missing}", expect=404)
    do(f"/api/job-tools/{missing}", expect=404)
    do(f"/api/jobs/{j1}", expect=400, x="1")
    do(f"/api/job-tools/{j1}", expect=400, x="1")
    do("/api/pipeline")
    do("/api/pipeline", track="quant")
    do("/api/pipeline", track="")
    do("/api/pipeline", expect=400, track="nope")
    do("/api/pipeline", expect=400, other="1")
    do("/api/reminders")
    do("/api/reminders", expect=400, x="1")

    # The resume library.
    do("/api/resumes")
    do("/api/resumes", expect=400, x="1")
    do("/api/resumes/workflow")
    do("/api/resumes/workflow", expect=400, track="quant")
    for key in ("v1", "v2", "v3"):
        do(f"/api/resumes/versions/{seeded[key]}")
    do("/api/resumes/versions/00000000-0000-4000-8000-000000000999", expect=404)
    do(f"/api/resumes/versions/{seeded['v1']}", expect=400, x="1")
    manifest = seeded["v1_manifest"]
    original = manifest.read_bytes()
    manifest.write_bytes(original + b" ")
    try:
        if allowed:
            do("/api/resumes", expect=500)
            do(f"/api/resumes/versions/{seeded['v1']}", expect=409)
    finally:
        manifest.write_bytes(original)

    # One job's actions.
    do(f"/api/jobs/{j6}", {"action": "favorite", "value": True})
    do(f"/api/jobs/{j6}", {"action": "favorite", "value": True})
    do(f"/api/jobs/{j4}", {"action": "favorite", "value": False})
    do(f"/api/jobs/{j4}", {"action": "favorite", "value": 1}, 400)
    do(f"/api/jobs/{j1}", {"action": "queue"})
    do(f"/api/jobs/{j1}", {"action": "skip"})
    do(f"/api/jobs/{j1}", {"action": "restore"})
    do(f"/api/jobs/{j1}", {"action": "queue", "value": None}, 400)
    do(f"/api/jobs/{j5}", {"action": "skip"}, 409)
    do(f"/api/jobs/{j1}", {"action": "notes", "value": "Strong match ✓\nAsk about housing"})
    do(f"/api/jobs/{j1}", {"action": "notes", "value": "x" * 20001}, 400)
    do(f"/api/jobs/{j1}", {"action": "notes", "value": "a\x00"}, 400)
    do(f"/api/jobs/{j1}", {"action": "notes", "value": 5}, 400)
    do(f"/api/jobs/{j1}", {"action": "status", "value": "oa"}, 409)
    do(f"/api/jobs/{j1}", {"action": "track", "value": "quant"}, 409)
    do(f"/api/jobs/{j5}", {"action": "status", "value": "offer", "when": "2025-03-04", "note": "Offer letter"})
    do(f"/api/jobs/{j5}", {"action": "status", "value": "ghosted", "note": "No reply"})
    do(f"/api/jobs/{j5}", {"action": "status", "value": "ghosted", "when": " "})
    do(f"/api/jobs/{j5}", {"action": "status", "value": "applied"}, 400)
    do(f"/api/jobs/{j5}", {"action": "status", "value": "offer", "when": "not a date"}, 400)
    do(f"/api/jobs/{j5}", {"action": "status", "value": "offer", "when": 5}, 400)
    do(f"/api/jobs/{j5}", {"action": "status", "value": "offer", "note": "x" * 20001}, 400)
    do(f"/api/jobs/{j5}", {"action": "track", "value": "gpu"})
    do(f"/api/jobs/{j5}", {"action": "track", "value": None})
    do(f"/api/jobs/{j5}", {"action": "track", "value": "nope"}, 400)
    do(f"/api/jobs/{j5}", {"action": "resume", "value": seeded["alpha"]}, 409)
    do(f"/api/jobs/{j1}", {"action": "favorite", "value": True, "when": "today"}, 400)
    for value, expect in ((seeded["alpha"], 200), (seeded["beta"], 409), (seeded["gamma"], 404), (seeded["delta"], 400),
                          (seeded["epsilon"], 404), (seeded["zeta"], 200), (missing, 400), (0, 400), (True, 400), ("1", 400),
                          (None, 200)):
        do(f"/api/jobs/{j2}", {"action": "resume", "value": value}, expect)
    do(f"/api/jobs/{j1}", {"action": "applied"}, 400)
    do(f"/api/jobs/{j1}", {"action": "applied", "value": True, "when": "2026-01-15", "note": "Submitted on the portal"})
    do(f"/api/jobs/{j1}", {"action": "applied", "value": True})
    do(f"/api/jobs/{j12}", {"action": "applied", "value": True, "when": "+0"})
    do(f"/api/jobs/{j6}", {"action": "applied", "value": True, "when": "today", "note": ""})
    do(f"/api/jobs/{j10}", {"action": "applied", "value": True, "when": "２"})
    do(f"/api/jobs/{j10}", {"action": "applied", "value": True, "when": "²"}, 500)
    do(f"/api/jobs/{j10}", {"action": "applied", "value": True, "when": "99999999"}, 500)
    do(f"/api/jobs/{missing}", {"action": "notes", "value": "x"}, 404)
    do(f"/api/jobs/{j1}", {"action": "teleport"}, 400)
    do(f"/api/jobs/{j1}", {"value": True}, 400)
    do(f"/api/jobs/{j1}", {"action": "favorite", "value": True, "extra": 1}, 400)
    do(f"/api/jobs/{j1}", {"action": "favorite", "value": True}, 400, x="1")

    # Bulk actions.
    for ids in ([], [0], [True], ["1"], [1.0], [2**63], list(range(1, 10_002))):
        do("/api/jobs/bulk", {"ids": ids, "action": "tier", "value": "A"}, 400)
    do("/api/jobs/bulk", {"ids": [j1, missing], "action": "favorite", "value": True}, 404)
    do("/api/jobs/bulk", {"ids": [j3, j4, j3], "action": "tier", "value": "A"})
    do("/api/jobs/bulk", {"ids": [j3], "action": "tier", "value": None})
    do("/api/jobs/bulk", {"ids": [j3], "action": "tier", "value": "D"}, 400)
    do("/api/jobs/bulk", {"ids": [j3, j4], "action": "state", "value": "later"})
    do("/api/jobs/bulk", {"ids": [j3, j4], "action": "state", "value": "skipped"})
    do("/api/jobs/bulk", {"ids": [j3, j4], "action": "state", "value": "new"})
    do("/api/jobs/bulk", {"ids": [j3, j5], "action": "state", "value": "new"}, 409)
    do("/api/jobs/bulk", {"ids": [j3], "action": "state", "value": "applied"}, 400)
    do("/api/jobs/bulk", {"ids": [j9, j12], "action": "not_eligible"}, 409)
    do("/api/jobs/bulk", {"ids": [j9], "action": "not_eligible"})
    do("/api/jobs/bulk", {"ids": [j9, j10], "action": "add_track", "value": "research"})
    do("/api/jobs/bulk", {"ids": [j9], "action": "add_track", "value": "nope"}, 400)
    do("/api/jobs/bulk", {"ids": [j8, j9], "action": "favorite", "value": "toggle"})
    do("/api/jobs/bulk", {"ids": [j8, j9], "action": "favorite", "value": "toggle"})
    do("/api/jobs/bulk", {"ids": [j2, j7], "action": "favorite", "value": False})
    do("/api/jobs/bulk", {"ids": [j2], "action": "favorite", "value": "yes"}, 400)
    do("/api/jobs/bulk", {"ids": [j13], "action": "delete", "value": "yes"}, 400)
    do("/api/jobs/bulk", {"ids": [j13, j5], "action": "delete", "value": True}, 409)
    do("/api/jobs/bulk", {"ids": [j13, j14], "action": "delete", "value": True})
    do("/api/jobs/bulk", {"ids": [j13], "action": "delete", "value": True}, 404)
    do("/api/jobs/bulk", {"ids": [j2], "action": "explode"}, 400)
    do("/api/jobs/bulk", {"ids": [j2], "action": "tier", "value": "A", "extra": 1}, 400)

    # Reminders.
    for date in ("today", "tomorrow", "+3", "5d", "2026-12-01", "2026-12-01T10:30:00+02:00"):
        do("/api/reminders", {"date": date, "note": f"  Note for {date}  ", "job_id": j1})
    created = do("/api/reminders", {"date": "+1"})
    for body in ({"date": "bad"}, {"date": ""}, {}, {"date": 5}, {"date": "x" * 101}, {"date": "today", "note": "x" * 20001},
                 {"date": "today", "job_id": "1"}, {"date": "today", "job_id": 0}, {"date": "today", "extra": 1}):
        do("/api/reminders", body, 400)
    do("/api/reminders", {"date": "today", "job_id": missing}, 404)
    rid = created["id"] if allowed else 1
    do(f"/api/reminders/{rid}", {"date": "2027-01-02", "note": "Changed"})
    do(f"/api/reminders/{rid}", {"note": ""})
    do(f"/api/reminders/{rid}", {}, 400)
    do(f"/api/reminders/{rid}", {"date": "today", "x": 1}, 400)
    do(f"/api/reminders/{rid}", {"date": "never"}, 400)
    do(f"/api/reminders/{rid}/complete", {})
    do(f"/api/reminders/{rid}/complete", {"x": 1}, 400)
    do(f"/api/reminders/{seeded['r_due']}/snooze", {})
    do(f"/api/reminders/{seeded['r_free']}/snooze", {})
    deleted = do(f"/api/reminders/{seeded['r_late']}/delete", {})
    do(f"/api/reminders/{missing}/complete", {}, 404)
    do(f"/api/reminders/{missing}", {"note": "x"}, 404)
    row = deleted["reminder"] if allowed else {"id": 1}
    do("/api/reminders/restore", {"reminder": row})
    do("/api/reminders/restore", {"reminder": row}, 409)
    for bad in ({**row, "kind": "other"}, {**row, "id": 0}, {**row, "due_at": None}, {**row, "note": 5}, {"id": 1},
                {**row, "id": True}, {**row, "due_at": "x" * 101}):
        do("/api/reminders/restore", {"reminder": bad}, 400)
    do("/api/reminders/restore", {"reminder": {**row, "id": 5000, "kind": ["custom"]}}, 500)
    do("/api/reminders/restore", {"reminder": {**row, "id": 5001, "note": "x\x00"}}, 400)
    do("/api/reminders/restore", {"reminder": {**row, "id": 5002, "job_id": j13}}, 409)
    do("/api/reminders/restore", {"reminder": row, "extra": 1}, 400)
    do("/api/reminders")

    # The tool panel's edits.
    do(f"/api/job-tools/{j2}", {"action": "deadline", "value": "2026-12-24"})
    do(f"/api/job-tools/{j2}", {"action": "deadline", "value": ""})
    do(f"/api/job-tools/{j2}", {"action": "deadline", "value": None}, 400)
    do(f"/api/job-tools/{j2}", {"action": "deadline", "value": "someday"}, 400)
    do(f"/api/job-tools/{j2}", {"action": "next_action", "value": "+2"}, 409)
    do(f"/api/job-tools/{j5}", {"action": "next_action", "value": "+2", "note": "  Follow up  "})
    do(f"/api/job-tools/{j5}", {"action": "next_action", "value": "", "note": ""})
    do(f"/api/job-tools/{j5}", {"action": "undo"})
    do(f"/api/job-tools/{j5}", {"action": "undo"})
    do(f"/api/job-tools/{j7}", {"action": "undo"})
    do(f"/api/job-tools/{j11}", {"action": "undo"})
    do(f"/api/job-tools/{j11}", {"action": "undo"}, 409)
    do(f"/api/job-tools/{j2}", {"action": "undo"}, 409)
    do(f"/api/job-tools/{j3}", {"action": "tracks", "value": ["gpu", "research", "gpu"]})
    do(f"/api/job-tools/{j3}", {"action": "tracks", "value": []})
    do(f"/api/job-tools/{j3}", {"action": "tracks", "value": ["nope"]}, 400)
    do(f"/api/job-tools/{j3}", {"action": "tracks", "value": "quant"}, 400)
    do(f"/api/job-tools/{j3}", {"action": "fly"}, 400)
    do(f"/api/job-tools/{j3}", {"action": "deadline", "value": "today", "extra": 1}, 400)
    do(f"/api/job-tools/{missing}", {"action": "undo"}, 404)

    # The worker's config changes (a view and a track), then everything is read again.
    if call_reload(root):
        do("/api/reload", {})
    do("/api/job-options")
    do("/api/jobs", saved="9")
    do("/api/jobs", view="inbox")
    do("/api/resumes/workflow")
    do(f"/api/jobs/{j8}", {"action": "applied", "value": True})
    for path in ("/api/jobs", "/api/jobs/selection", "/api/pipeline", "/api/reminders", "/api/resumes"):
        do(path)
    for job_id in j:
        do(f"/api/jobs/{job_id}", expect=404 if job_id in (j13, j14) else 200)
        do(f"/api/job-tools/{job_id}", expect=404 if job_id in (j13, j14) else 200)
    return answers


def call_reload(root):
    """Edit the views and a track's preferred cities; the scenario then reloads the worker."""
    config = root / "config"
    views = yaml.safe_load((config / "views.yaml").read_text(encoding="utf-8"))
    if not any(view["key"] == 9 for view in views["views"]):
        views["views"].append({"key": 9, "title": "Added later", "filters": {"tier": ["A"]}})
        (config / "views.yaml").write_text(yaml.safe_dump(views, sort_keys=False), encoding="utf-8")
    profile = yaml.safe_load((config / "profile.yaml").read_text(encoding="utf-8"))
    profile["open_to_remote"] = False
    (config / "profile.yaml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return True


def restore_config(root, snapshot):
    for name, data in snapshot.items():
        (root / "config" / name).write_bytes(data)


STORED = {
    "jobs": "id", "job_tracks": "job_id, track", "applications": "id", "status_events": "id", "reminders": "id",
    "screening_questions": "id", "resumes": "id", "resume_default_overrides": "track", "search_pending": "kind, ref",
}


def stored(db_path):
    with closing(connect(db_path)) as conn:
        found = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")] for table, order in STORED.items()}
        found["record_events"] = [tuple(row) for row in conn.execute(
            "SELECT section, entity, entity_id, action, route FROM record_events ORDER BY seq")]
    return found


def prepare(workspace, tmp_path, monkeypatch, modules):
    base = base_time()
    seeded = seed(workspace, base, monkeypatch)
    if modules != MODULE_IDS:
        (workspace.settings.root / "config" / "build.json").write_text(
            json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    reference_path = tmp_path / "reference.db"
    shutil.copy(workspace.settings.db_path, reference_path)
    snapshot = {name: (workspace.settings.root / "config" / name).read_bytes() for name in ("views.yaml", "profile.yaml")}
    return base, seeded, reference_path, snapshot


def reference_run(workspace, reference_path, seeded, base, monkeypatch, modules, snapshot):
    root = workspace.settings.root
    with monkeypatch.context() as patch:
        clocked(patch)
        reference = Reference(reference_path, root, modules)
        try:
            return scenario(reference, seeded, base, root, modules)
        finally:
            reference.conn.close()
            restore_config(root, snapshot)


CASES = [MODULE_IDS, ("workspace",)]


@pytest.mark.parametrize("modules", CASES)
def test_reference_scenario(workspace, tmp_path, monkeypatch, modules, record_property):
    """Executable oracle sanity check, usable before the Rust implementation exists."""
    base, seeded, path, snapshot = prepare(workspace, tmp_path, monkeypatch, modules)
    answers = reference_run(workspace, path, seeded, base, monkeypatch, modules, snapshot)
    record_property("requests", len(answers))
    statuses = {status for _, status, _ in answers}
    if "applyai" in modules:
        assert len(answers) > 300
        assert statuses == {200, 400, 404, 409, 500}
        lists = [answer for route, status, answer in answers if route == "/api/jobs" and status == 200]
        assert all(page["total"] for page in lists[:6])
        assert any(item["freshness"] == "verified" for page in lists for item in page["jobs"])
    else:
        assert statuses == {200, 400, 403}


@pytest.mark.parametrize("modules", CASES)
def test_the_front_answers_jobs_as_the_python_core_does(workspace, tmp_path, monkeypatch, modules):
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER})")
    base, seeded, reference_path, snapshot = prepare(workspace, tmp_path, monkeypatch, modules)
    expected = reference_run(workspace, reference_path, seeded, base, monkeypatch, modules, snapshot)
    clock = tmp_path / "clock"
    clock.write_text(base.isoformat(), encoding="utf-8")
    root = workspace.settings.root
    try:
        with front_site(workspace.settings, env={"KITTY_TEST_CLOCK": str(clock), "KITTY_PACKS_URL": NO_NETWORK}) as site:
            actual = scenario(Through(site, clock), seeded, base, root, modules)
    finally:
        restore_config(root, snapshot)
    assert len(actual) == len(expected)
    for index, (answer, wanted) in enumerate(zip(actual, expected, strict=True)):
        assert answer == wanted, (index, answer[0])
    left, right = stored(workspace.settings.db_path), stored(reference_path)
    for table in right:
        assert left[table] == right[table], table


def test_the_worker_answers_only_the_python_job_routes(workspace, tmp_path):
    job, _ = jobs.upsert_job(workspace.conn, company="Synthetic", title="Kept Intern", url="https://example.invalid/kept")
    conn = workspace.conn
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for path in ("/api/jobs", "/api/jobs/selection", f"/api/jobs/{job.id}", "/api/job-options", "/api/pipeline",
                     "/api/reminders", f"/api/job-tools/{job.id}", "/api/resumes", "/api/resumes/workflow",
                     "/api/resumes/versions/00000000-0000-4000-8000-000000000001"):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, path, {})
        for path in (f"/api/jobs/{job.id}", "/api/jobs/bulk", "/api/reminders", "/api/reminders/restore", "/api/reminders/1",
                     "/api/reminders/1/snooze", f"/api/job-tools/{job.id}"):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, {"action": "favorite", "value": True})
        projection = app.get_json(conn, "/api/internal/jobs/config", {})
        assert [track["name"] for track in projection["tracks"]] == workspace.config.track_names
        assert app.get_json(conn, "/api/internal/jobs/automation", {}) == {"running": False, "job_ids": []}
    finally:
        app.close()
    conn.close()
    with front_site(workspace.settings, env={"KITTY_PACKS_URL": NO_NETWORK}) as site:
        status, dashboard = site.request("/api/dashboard")
        assert status == 200 and "funnel" in dashboard
        status, added = site.request("/api/jobs/add", {"company": "Front Added", "title": "Intern", "url": "https://example.invalid/front"})
        assert status == 200 and added["created"] is True
        status, listed = site.request("/api/jobs", None)
        assert status == 200 and {item["company"] for item in listed["jobs"]} >= {"Front Added", "Synthetic"}
        assert site.request("/api/resumes/coach")[0] == 200
        assert site.request("/api/internal/jobs/config")[0] == 404
        assert site.request("/api/jobs/metadata", {"url": "https://example.invalid", "confirmed": False}) == (
            400, {"error": "Confirm the public metadata request"})

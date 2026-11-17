"""ApplyAI's local insight routes and the AI settings answered by rika-server
(server/src/insights) against the retained Python core on two copies of one seeded workspace.

The reference owns the deleted handlers' glue (`web_insights.get`'s reports and mail branches,
`download`, the finder reject and mail-proposal decisions, `web_automation.get`'s health and
`post`'s question log, `web_ai.get` and the settings write) over the retained core. A file
clock makes every stamp comparable. Discovery, the finder's run and approval, Gmail's consent
and check, triage, posting refreshes, the browser controller, plans and AI previews and runs
stay on the worker.
"""
from __future__ import annotations

import http.client
import io
import json
import re
import shutil
import zipfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlencode

import pytest
from terminal_support import RIKA_SERVER, front_site

from kitty import web_ai, web_auth
from kitty.core import applications, export, jobs, questions, record_events, sources, stats
from kitty.core import keys as keystore
from kitty.core.apply import health
from kitty.core.builds import project
from kitty.core.builds import store as build_store
from kitty.core.finder import store as finder
from kitty.core.gmail import auth
from kitty.core.gmail.proposals import get_pending, pending_proposals
from kitty.core.rika import router
from kitty.db import connect
from kitty.db.connection import transaction
from kitty.modules import MODULE_IDS, ModuleDisabledError
from kitty.settings import Settings
from kitty.web_server import APIError, WebApplication, route_module

KEY = "sk-or-synthetic-0123456789"
PRIVACY = ("Only text you select and review, and what you ask Rika with the records its tools read for that request, "
           "is sent to OpenRouter. Note text is shared only while Notes allow AI. Providers must decline data collection. "
           "AI output is a draft, not verified fact.")


class Clock:
    value: datetime

    @classmethod
    def now(cls):
        return cls.value

    @classmethod
    def now_iso(cls):
        return cls.value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _keys(values, allowed):
    if set(values) - set(allowed):
        raise APIError(400, "Unknown parameter")


def _confirm(body):
    if body.get("confirm") is not True:
        raise APIError(400, "Explicit confirmation is required")


class Reference:
    """The deleted handlers as they were, over the retained core."""

    def __init__(self, settings, modules):
        self.settings, self.modules = settings, modules
        self.conn = connect(settings.db_path)
        self.app = SimpleNamespace(settings=settings)

    def guard(self, path):
        module = route_module(path)
        if module is not None and module not in self.modules:
            raise APIError(403, str(ModuleDisabledError(module)))

    def status(self):
        conn = self.conn
        settings = web_ai._settings(conn)
        return {
            "configured": bool(web_ai._key(self.app)), "model": settings["model"], "routing": settings["routing"],
            "max_output_tokens": settings["max_output_tokens"], "daily_budget_usd": float(settings["daily_budget_usd"]),
            "spent_today": float(web_ai._spent(conn)), "revision": settings["revision"],
            "rika_tiers": router.configured(conn), "rika_tier_defaults": router.defaults(conn),
            "models": [dict(row) for row in conn.execute("SELECT * FROM ai_models ORDER BY name")],
            "privacy_notice": PRIVACY,
        }

    def get(self, path, query):
        conn = self.conn
        if path == "/api/reports":
            _keys(query, {})
            return {"sections": {name: [rate.__dict__ | {"response_rate": rate.response_rate, "advance_rate": rate.advance_rate}
                                        for rate in rates] for name, rates in stats.response_report(conn).items()},
                    "summary": stats.report_summary(conn), "datasets": list(export.QUERIES)}
        if path == "/api/mail":
            _keys(query, {})
            state = auth.setup_state(self.settings)
            last = conn.execute("SELECT started_at, stats_json, error FROM runs WHERE kind='mail' ORDER BY id DESC LIMIT 1").fetchone()
            last_data = None
            if last:
                stored = json.loads(last["stats_json"] or "{}")
                last_data = {"started_at": last["started_at"], "status": stored.get("status", "unknown"),
                             "fetched": stored.get("fetched", 0), "proposed": stored.get("proposed", 0),
                             "error": "Last check needs attention; review authorization or connection." if last["error"] else None}
            return {"client_present": state.client_present, "token_present": state.token_present,
                    "ready": state.ready, "scope": "gmail.readonly", "last_check": last_data,
                    "proposals": [item.__dict__ for item in pending_proposals(conn)]}
        if path == "/api/apply/health" and "applyai" in self.modules:
            _keys(query, set())
            return health.report(conn)
        if path == "/api/ai/status":
            web_ai._keys(query, {})
            return self.status()
        raise APIError(404, "API route not found")

    def download(self, query):
        conn = self.conn
        _keys(query, {"format", "dataset"})
        fmt, dataset = query.get("format", "json"), query.get("dataset", "all")
        if fmt not in {"json", "csv"} or dataset not in {"all", *export.QUERIES}:
            raise APIError(400, "Unknown export format or dataset")
        tables = list(export.QUERIES) if dataset == "all" else [dataset]
        if fmt == "json":
            document = {"exported_at": Clock.now_iso()}
            for table in tables:
                columns, rows = export.table_rows(conn, table)
                document[table] = [dict(zip(columns, row)) for row in rows]
            return json.dumps(document, ensure_ascii=False, indent=2).encode(), "application/json", f"kitty-{dataset}.json"

        def csv_bytes(table):
            import csv
            columns, rows = export.table_rows(conn, table)
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows([export.escape_cell(value) for value in row] for row in rows)
            return stream.getvalue().encode("utf-8")
        if dataset != "all":
            return csv_bytes(dataset), "text/csv; charset=utf-8", f"{dataset}.csv"
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for table in tables:
                archive.writestr(f"{table}.csv", csv_bytes(table))
        return stream.getvalue(), "application/zip", "kitty-all-csv.zip"

    def settings_write(self, body):
        conn, app = self.conn, self.app
        web_ai._keys(body, {"revision", "model", "routing", "max_output_tokens", "daily_budget_usd", "rika_tiers", "key", "remove_key", "confirmed"})
        if body.get("confirmed") is not True:
            raise APIError(400, "Confirm the AI settings change")
        settings = web_ai._settings(conn)
        if type(body.get("revision")) is not int or body["revision"] != settings["revision"]:
            raise APIError(409, "AI settings changed; reload before saving")
        model = body.get("model", settings["model"])
        if not isinstance(model, str) or model and conn.execute("SELECT 1 FROM ai_models WHERE id=?", (model,)).fetchone() is None:
            raise APIError(400, "Choose a model from the refreshed list")
        routing = body.get("routing", settings["routing"])
        if routing not in {"price", "latency", "throughput"}:
            raise APIError(400, "Choose price, latency, or throughput routing")
        maximum = body.get("max_output_tokens", settings["max_output_tokens"])
        if type(maximum) is not int or not 128 <= maximum <= 8192:
            raise APIError(400, "Output limit must be 128–8192 tokens")
        cap = web_ai._number(body.get("daily_budget_usd", settings["daily_budget_usd"]), "Daily budget", 0, 100)
        value = body.get("rika_tiers", {})
        if not isinstance(value, dict) or set(value) - set(router.TIERS):
            raise APIError(400, "Rika tiers are fast, standard and deep")
        for chosen in value.values():
            if not isinstance(chosen, str) or chosen and conn.execute("SELECT 1 FROM ai_models WHERE id=?", (chosen,)).fetchone() is None:
                raise APIError(400, "Choose each Rika tier model from the refreshed list, or none for the default")
        tiers = {**router.configured(conn), **value}
        if body.get("key") and body.get("remove_key"):
            raise APIError(400, "Cannot add and remove the key together")
        try:
            if body.get("key"):
                if not isinstance(body["key"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,512}", body["key"]):
                    raise APIError(400, "Enter a valid OpenRouter API key")
                keystore.for_settings(app.settings).set("OPENROUTER", body["key"])
            elif body.get("remove_key") is True:
                keystore.for_settings(app.settings).remove("OPENROUTER")
        except keystore.KeysError as exc:
            raise APIError(exc.status, str(exc)) from None
        with project.editing(conn, self.settings.root) as build:
            build_store.section(build, "settings", "ai").update(
                model=model, routing=routing, max_output_tokens=maximum, daily_budget_usd=float(cap),
                rika_fast_model=tiers["fast"], rika_standard_model=tiers["standard"], rika_deep_model=tiers["deep"])
        return self.status()

    def post(self, path, body):
        conn = self.conn
        match = re.fullmatch(r"/api/finder/([1-9][0-9]*)/reject", path)
        if match:
            _keys(body, {"confirm"}); _confirm(body)
            candidate = finder.get_candidate(conn, int(match[1]))
            if candidate is None or candidate.decision != "pending":
                raise APIError(409, "Candidate is missing or already decided")
            finder.reject(conn, int(match[1]))
            return {"ok": True, "decision": "rejected"}
        match = re.fullmatch(r"/api/mail/proposals/([1-9][0-9]*)/(confirm|reject)", path)
        if match:
            _keys(body, {"confirm"}); _confirm(body)
            if get_pending(conn, int(match[1])) is None:
                raise APIError(409, "Proposal is missing or already decided")
            (applications.confirm_proposal if match[2] == "confirm" else applications.reject_proposal)(conn, int(match[1]))
            return {"ok": True}
        if path == "/api/questions":
            _keys(body, {"text", "job_id"})
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 4000 or "\x00" in text:
                raise APIError(400, "Question must contain 1–4000 characters")
            job_id = body.get("job_id")
            if job_id is not None:
                if type(job_id) is not int or not 1 <= job_id <= 2**53 - 1:
                    raise APIError(400, f"job_id must be an integer from 1 to {2**53 - 1}")
                if jobs.get_job(conn, job_id) is None:
                    raise APIError(404, "Job not found")
            with transaction(conn):
                question = questions.log_question(conn, text, job_id=job_id)
            return {"id": question.id}
        if path == "/api/ai/settings":
            return self.settings_write(body)
        raise APIError(404, "API route not found")

    def __call__(self, path, body, now, query, download=False):
        Clock.value = now
        try:
            if body is not None and query:
                raise APIError(400, "This route does not accept query parameters")
            self.guard(path)
            if download:
                data, kind, name = self.download(query)
                return 200, archived(data, kind, f'attachment; filename="{name}"')
            if body is None:
                answer = self.get(path, query)
            else:
                before = record_events.op_log_mark(self.conn)
                answer = self.post(path, body)
                record_events.capture(self.conn, path, body, answer, op_log_before=before)
        except (APIError, build_store.BuildStoreError) as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            return exc.status, {"error": str(exc)}
        except Exception:  # noqa: BLE001 - the server's 500
            if self.conn.in_transaction:
                self.conn.rollback()
            return 500, {"error": "Local operation failed; inspect CLI diagnostics"}
        return 200, json.loads(json.dumps(answer))


def archived(data, kind, disposition):
    """A download as compared: bytes, except a zip's entries (their stamps are the writer's)."""
    if kind == "application/zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [(item.filename, item.compress_type, archive.read(item).hex()) for item in archive.infolist()]
        return {"entries": entries, "type": kind, "disposition": disposition}
    return {"data": data.hex(), "type": kind, "disposition": disposition}


class Through:
    def __init__(self, site, clock, root):
        self.site, self.clock, self.root = site, clock, root

    def __call__(self, path, body, now, query, download=False):
        self.clock.write_text(now.isoformat(), encoding="utf-8")
        target = quote(path, safe="/") + ("?" + urlencode(query) if query else "")
        if not download:
            return self.site.request(target, body)
        client = http.client.HTTPConnection("127.0.0.1", self.site.port, timeout=10)
        try:
            client.request("GET", target, headers={"Origin": self.site.server.origin, web_auth.HEADER: self.site.secret})
            response = client.getresponse()
            data = response.read()
            kind = response.getheader("Content-Type")
            if kind.startswith("application/json") and response.status != 200:
                return response.status, json.loads(data)
            return response.status, archived(data, kind, response.getheader("Content-Disposition"))
        finally:
            client.close()


def seed(workspace, base):
    """Jobs, applications and status history around the clock's week and month, proposals,
    finder candidates, mail runs, apply attempts, screening questions and AI settings."""
    conn, settings = workspace.conn, workspace.settings
    names = workspace.config.track_names
    sources.ensure_source(conn, "greenhouse", "ats")
    conn.execute("INSERT INTO resumes(label,file_path,file_hash,created_at,updated_at) VALUES('General','r.pdf','00','2026-01-01','2026-01-01')")
    conn.execute("INSERT INTO resumes(label,file_path,file_hash,created_at,updated_at) VALUES('Research','s.pdf','01','2026-01-01','2026-01-01')")

    def stamp(days, hours=0, fmt="z"):
        at = base - timedelta(days=days, hours=hours)
        return {"z": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "naive": at.strftime("%Y-%m-%dT%H:%M:%S"),
                "west": (at.astimezone(timezone(timedelta(hours=-11)))).isoformat(), "date": at.strftime("%Y-%m-%d"),
                "micro": at.isoformat(timespec="microseconds")}[fmt]

    created = []
    for index in range(32):
        company = ["Formula =Co", "Plus +Labs", "Δelta Systems", "Comma, Inc", "Quote \"Works\"", "Line\nBreak"][index % 6]
        job, _ = jobs.upsert_job(conn, company=f"{company} {index // 6}", title=["=Intern", "-Analyst", "@Engineer", "Plain"][index % 4],
                                 url=f"https://example.invalid/{index}", source="greenhouse" if index % 3 else "manual",
                                 source_job_id=f"seed-{index}", location="Waco, TX" if index % 2 else None)
        if index % 5 == 0:
            conn.execute("INSERT INTO job_sources(job_id,source,source_job_id,first_seen_at,last_seen_at) VALUES(?,?,?,?,?)",
                         (job.id, "manual" if index % 3 else "greenhouse", f"extra-{index}", "2026-01-01", "2026-01-01"))
        conn.execute("UPDATE jobs SET tier=? WHERE id=?", ([None, "A", "B", "C"][index % 4], job.id))
        if index % 2:
            jobs.link_track(conn, job.id, names[index % len(names)])
        created.append(job.id)
    # Applications on the week's and month's edges, in every stored spelling.
    spellings = [(0, 1, "z"), (0, 2, "naive"), (1, 0, "west"), (2, 0, "date"), (3, 0, "micro"), (6, 0, "z"), (7, 0, "z"),
                 (8, 0, "z"), (13, 0, "z"), (20, 0, "z"), (27, 5, "z"), (31, 0, "z"), (40, 0, "z"), (55, 0, "z"),
                 (63, 0, "z"), (90, 0, "z"), (-2, 0, "z"), (1, 8, "z"), (0, 3, "z")]
    apps = []
    for index, (days, hours, fmt) in enumerate(spellings):
        resume = [None, 1, 2][index % 3]
        track = [None, names[0], names[1 % len(names)]][index % 3]
        apps.append(applications.record_application(conn, created[index], resume_id=resume, track=track,
                                                    applied_at=stamp(days, hours, fmt)).id)
    conn.execute("UPDATE applications SET applied_at='not a date' WHERE id=?", (apps[-1],))
    conn.execute("UPDATE applications SET resume_id=NULL, track=NULL WHERE id=?", (apps[4],))
    history = [(0, "oa", 1), (0, "interview", 0), (1, "rejected", 2), (2, "offer", 3), (3, "oa", 5), (5, "withdrawn", 6),
               (6, "interview", 9), (7, "ghosted", 12), (8, "oa", 40), (9, "offer", 35), (10, "rejected", 60)]
    for app_index, status, days in history:
        applications.set_status(conn, apps[app_index], status, occurred_at=stamp(days, 0, "z"))
    applications.undo_last_status(conn, apps[2])
    proposals = []
    for app_index, status, days, evidence in [(11, "interview", 1, "Synthetic recruiting subject"), (12, "rejected", 2, None),
                                               (13, "oa", 2, "Assessment link"), (14, "offer", 4, "Offer letter")]:
        proposals.append(applications.set_status(conn, apps[app_index], status, occurred_at=stamp(days, 0, "z"),
                                                 state="proposed", source="gmail", evidence=evidence).id)
    # Finder candidates and mail runs.
    candidates = [finder.insert_candidate(conn, name=f"Synthetic Firm {index}", area=names[0], score=index,
                                          reason="Synthetic public evidence", ats_kind=None, ats_slug=None, board_url=None,
                                          website="https://example.invalid", evidence=["Synthetic"], run_id=None) for index in range(3)]
    conn.execute("UPDATE firm_candidates SET decision='approved', decided_at='2026-01-02T00:00:00Z' WHERE id=?", (candidates[2],))
    conn.execute("INSERT INTO runs(kind,started_at,finished_at,stats_json,error) VALUES('mail','2026-01-01T00:00:00Z',NULL,?,NULL)",
                 (json.dumps({"status": "ok", "fetched": 9, "proposed": 2}),))
    conn.execute("INSERT INTO runs(kind,started_at,finished_at,stats_json,error) VALUES('finder','2026-01-01T00:00:00Z',NULL,'{}',NULL)")
    # Apply attempts: a family past the sample floor, small ones, unknown causes and classes.
    attempts = []
    families = ["greenhouse"] * 24 + ["lever"] * 5 + ["workday"] * 3
    for index, family in enumerate(families):
        job_id = created[index]
        plan = [("needs_owner", "missing_answer", 1)]
        if index % 3 == 0:
            plan.append(("ready", "ready", 0))
        elif index % 7 == 1:
            plan = [("closed", "closed", 0)]
        elif index % 5 == 2:
            plan = [("failed", "mystery_cause", 0), ("unsupported", "step_limit", 2)]
        elif index % 4 == 3:
            plan = [("ready", "ready", 0)]
        for outcome, cause, inputs in plan:
            attempts.append(conn.execute(
                "INSERT INTO apply_attempts(job_id,ats_family,started_at,ended_at,duration_ms,stage,outcome,cause,owner_inputs,"
                "hide_automation) VALUES(?,?,?,?,?,?,?,?,?,0)",
                (job_id, family, stamp(index, 1), stamp(index, 0, "micro"), 1000, "page", outcome, cause, inputs)).lastrowid)
    for index, attempt in enumerate(attempts):
        for position, provenance in enumerate(["profile", "saved_answer:visa", "ai_draft", "mystery", "account:x"][: index % 5 + 1]):
            conn.execute("INSERT INTO apply_attempt_fields(attempt_id,position,label,provenance) VALUES(?,?,?,?)",
                         (attempt, position, f"Field {position}", provenance))
            if (index + position) % 2 == 0:
                conn.execute("INSERT INTO apply_field_checks(attempt_id,position,edited,confirmed_at) VALUES(?,?,?,?)",
                             (attempt, position, (index * position) % 3 == 0, "2026-01-01"))
    with transaction(conn):
        questions.log_question(conn, "Are you authorized to work in the U.S.?", job_id=created[0])
    # AI: the catalogue, a saved model, spend today and before, and an OpenRouter key on file.
    for model_id, name in [("anthropic/claude-x", "Claude X"), ("openai/gpt-y", "GPT Y"), ("synthetic/text", "A Synthetic")]:
        conn.execute("INSERT INTO ai_models VALUES(?,?,?,?,?,?)", (model_id, name, "0.000003", "0.000015", 200000, stamp(0, 1)))
    today = base.date().isoformat()
    for index, (created_at, reserved, actual) in enumerate([(f"{today}T01:00:00+00:00", "0.25", None),
                                                           (f"{today}T02:00:00+00:00", "0.5", "0.1234567890123456789"),
                                                           (f"{today}T03:00:00+00:00", "1E-7", None),
                                                           ((base - timedelta(days=1)).isoformat(), "9", "9")]):
        conn.execute("INSERT INTO ai_reviews(id,purpose,plan_json,fingerprint,expires_at,state,reserved_usd,actual_usd,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?)", (f"r{index}", "summarize", "{}", "f", created_at, "completed", reserved, actual, created_at))
    project.sync(conn, settings.root)
    with project.editing(conn, settings.root) as build:
        build_store.section(build, "settings", "ai").update(model="anthropic/claude-x", daily_budget_usd=2.5,
                                                             rika_fast_model="synthetic/text")
    keystore.for_settings(settings).set("OPENROUTER", KEY)
    (settings.config_dir / "gmail_client.json").write_text("synthetic-client-secret")
    conn.commit()
    return {"jobs": created, "apps": apps, "proposals": proposals, "candidates": candidates}


def scenario(call, seeded, base, modules):
    answers = []
    now = base
    applyai = "applyai" in modules

    def do(path, body=None, expect=200, download=False, **query):
        nonlocal now
        now += timedelta(seconds=1)
        # Without ApplyAI its routes are 403 before anything but a mutation's query check.
        wanted = expect if applyai or route_module(path) != "applyai" or (body is not None and query) else 403
        status, answer = call(path, body, now, query, download)
        assert status == wanted, (len(answers), path, body, query, status, answer)
        answers.append((path, status, answer))
        return answer

    # Reports and exports.
    do("/api/reports")
    do("/api/reports", expect=400, x="1")
    for query in ({}, {"format": "json"}, {"format": "csv"}, {"format": "json", "dataset": "jobs"},
                  {"format": "csv", "dataset": "applications"}, {"format": "csv", "dataset": "status_events"},
                  {"dataset": "status_events"}):
        do("/api/reports/export", download=True, **query)
    for query in ({"format": "xml"}, {"dataset": "companies"}, {"format": "csv", "x": "1"}, {"format": ""}):
        do("/api/reports/export", expect=400, download=True, **query)

    # Apply health: a core route (no ApplyAI prefix) that only ApplyAI's extension answers.
    do("/api/apply/health", expect=200 if applyai else 404)
    do("/api/apply/health", expect=400 if applyai else 404, x="1")

    # Mail: setup state, the last check, proposals and their decisions.
    do("/api/mail")
    do("/api/mail", expect=400, x="1")
    (call.root / "data" / "gmail_token.json").write_text("synthetic-token-secret")
    do("/api/mail")
    first, second, third, fourth = seeded["proposals"]
    for bad in ({}, {"confirm": 1}, {"confirm": "true"}, {"confirm": True, "x": 1}, {"x": 1}):
        do(f"/api/mail/proposals/{first}/confirm", bad, 400)
    do(f"/api/mail/proposals/{first}/confirm", {"confirm": True}, 400, x="1")
    do("/api/mail/proposals/999/confirm", {"confirm": True}, 409)
    do("/api/mail/proposals/" + "9" * 30 + "/confirm", {"confirm": True}, 500)
    do("/api/mail/proposals/0/confirm", {"confirm": True}, 404)
    do("/api/mail/proposals/01/confirm", {"confirm": True}, 404)
    do(f"/api/mail/proposals/{first}/accept", {"confirm": True}, 404)
    do(f"/api/mail/proposals/{first}/confirm", {"confirm": True})
    do(f"/api/mail/proposals/{first}/confirm", {"confirm": True}, 409)
    do(f"/api/mail/proposals/{second}/reject", {"confirm": True})
    do(f"/api/mail/proposals/{second}/confirm", {"confirm": True}, 409)
    do(f"/api/mail/proposals/{third}/confirm", {"confirm": True})
    do("/api/mail")
    do("/api/reports")
    (call.root / "config" / "gmail_client.json").unlink()
    do("/api/mail")

    # Finder rejections (approval writes the watchlist and stays on the worker).
    pending, other, decided = seeded["candidates"]
    for bad in ({}, {"confirm": False}, {"confirm": True, "x": 1}):
        do(f"/api/finder/{pending}/reject", bad, 400)
    do(f"/api/finder/{decided}/reject", {"confirm": True}, 409)
    do("/api/finder/999/reject", {"confirm": True}, 409)
    do("/api/finder/" + "9" * 25 + "/reject", {"confirm": True}, 500)
    do(f"/api/finder/{pending}/reject", {"confirm": True})
    do(f"/api/finder/{pending}/reject", {"confirm": True}, 409)

    # Screening questions.
    job = seeded["jobs"][3]
    for bad in ({}, {"text": ""}, {"text": "  \t"}, {"text": 5}, {"text": "x" * 4001}, {"text": "a\x00b"},
                {"text": "Why?", "x": 1}, {"text": "Why?", "job_id": 0}, {"text": "Why?", "job_id": "1"},
                {"text": "Why?", "job_id": True}, {"text": "Why?", "job_id": 1.0}, {"text": "Why?", "job_id": 2**53}):
        do("/api/questions", bad, 400)
    do("/api/questions", {"text": "Why?", "job_id": 999999}, 404)
    do("/api/questions", {"text": "Why?"}, 400, x="1")
    do("/api/questions", {"text": "  Are you authorized to work in the U.S.?  "})
    do("/api/questions", {"text": "ARE YOU AUTHORIZED, to work in the u.s.", "job_id": job})
    do("/api/questions", {"text": "ARE YOU AUTHORIZED, to work in the u.s.", "job_id": job})
    do("/api/questions", {"text": "Describe a project you are proud of 😀 — İstanbul", "job_id": job})
    do("/api/questions", {"text": "x" * 4000, "job_id": seeded["jobs"][4]})
    do("/api/questions", {"text": "Ｗｈｙ　ｕｓ？\u3000"})

    # AI status and settings (the core's, with any module set).
    status = do("/api/ai/status")
    do("/api/ai/status", expect=400, x="1")
    revision = status["revision"]
    base_body = {"revision": revision, "confirmed": True}
    for bad, code in [({"revision": revision}, 400), ({**base_body, "confirmed": 1}, 400), ({**base_body, "x": 1}, 400),
                      ({"revision": revision + 1, "confirmed": True}, 409), ({"revision": True, "confirmed": True}, 409),
                      ({"revision": float(revision), "confirmed": True}, 409), ({"confirmed": True}, 409),
                      ({**base_body, "model": "nope/model"}, 400), ({**base_body, "model": 5}, 400),
                      ({**base_body, "routing": "fastest"}, 400), ({**base_body, "routing": None}, 400),
                      ({**base_body, "routing": []}, 500), ({**base_body, "max_output_tokens": 127}, 400),
                      ({**base_body, "max_output_tokens": 8193}, 400), ({**base_body, "max_output_tokens": 512.0}, 400),
                      ({**base_body, "max_output_tokens": True}, 400), ({**base_body, "daily_budget_usd": True}, 400),
                      ({**base_body, "daily_budget_usd": "abc"}, 400), ({**base_body, "daily_budget_usd": None}, 400),
                      ({**base_body, "daily_budget_usd": [1]}, 400), ({**base_body, "daily_budget_usd": 100.5}, 400),
                      ({**base_body, "daily_budget_usd": -1}, 400), ({**base_body, "daily_budget_usd": "NaN"}, 400),
                      ({**base_body, "daily_budget_usd": "-Infinity"}, 400), ({**base_body, "daily_budget_usd": "1 0"}, 400),
                      ({**base_body, "daily_budget_usd": "0x10"}, 400), ({**base_body, "rika_tiers": []}, 400),
                      ({**base_body, "rika_tiers": {"huge": ""}}, 400), ({**base_body, "rika_tiers": {"fast": "nope"}}, 400),
                      ({**base_body, "rika_tiers": {"deep": 5}}, 400), ({**base_body, "key": KEY, "remove_key": True}, 400),
                      ({**base_body, "key": KEY, "remove_key": 1}, 400), ({**base_body, "key": "short"}, 400),
                      ({**base_body, "key": 1234567890123}, 400), ({**base_body, "key": "bad key with spaces"}, 400)]:
        do("/api/ai/settings", bad, code)
    do("/api/ai/settings", base_body, 400, x="1")
    saved = do("/api/ai/settings", {**base_body, "daily_budget_usd": "1_2.5_0"})
    saved = do("/api/ai/settings", {"revision": saved["revision"], "confirmed": True, "routing": "price", "max_output_tokens": 8192,
                                    "daily_budget_usd": 1e-07, "rika_tiers": {"deep": "openai/gpt-y", "fast": ""}})
    saved = do("/api/ai/settings", {"revision": saved["revision"], "confirmed": True, "model": "openai/gpt-y",
                                    "daily_budget_usd": " ١٢ ", "key": "", "remove_key": False})
    saved = do("/api/ai/settings", {"revision": saved["revision"], "confirmed": True, "remove_key": True})
    saved = do("/api/ai/settings", {"revision": saved["revision"], "confirmed": True, "model": "", "daily_budget_usd": 100,
                                    "key": "new_synthetic-KEY_0123456789"})
    saved = do("/api/ai/settings", {"revision": saved["revision"], "confirmed": True, "model": "synthetic/text",
                                    "daily_budget_usd": "0.005", "max_output_tokens": 128, "routing": "throughput"})
    do("/api/ai/settings", {"revision": saved["revision"], "confirmed": True, "daily_budget_usd": 0})
    do("/api/ai/status")
    return answers


TABLES = {
    "applications": "id", "status_events": "id", "firm_candidates": "id", "screening_questions": "id",
    "question_jobs": "question_id, job_id", "ai_settings": "id", "ai_models": "id", "ai_reviews": "id",
    "record_events": "seq", "build_state": "id",
}


def stored(settings):
    with closing(connect(settings.db_path)) as conn:
        found = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
                 for table, order in TABLES.items()}
    for row in found["record_events"]:
        row.pop("at")
    for row in found["build_state"]:
        row.pop("applied_at")
    found["build.json"] = (settings.config_dir / "build.json").read_text(encoding="utf-8")
    found["key"] = keystore.for_settings(settings).read("OPENROUTER")
    found["files"] = sorted(str(path.relative_to(settings.root)) for path in settings.root.rglob("*")
                            if path.is_file() and path.parts[len(settings.root.parts)] in {".private", "config"})
    return found


def base_time():
    # A Tuesday, so the week's Monday and the month's first day both fall inside the seed.
    today = datetime.now(timezone.utc).date()
    day = date(today.year, today.month, 2) + timedelta(days=(1 - date(today.year, today.month, 2).weekday()) % 7)
    return datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)


def prepare(workspace, tmp_path, modules):
    base = base_time()
    if modules != MODULE_IDS:
        (workspace.settings.config_dir / "build.json").write_text(
            json.dumps({"schema": 1, "modules": list(modules)}), encoding="utf-8")
    seeded = seed(workspace, base)
    workspace.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    workspace.conn.close()
    reference_root = tmp_path / "reference"
    shutil.copytree(workspace.settings.root, reference_root, ignore=shutil.ignore_patterns("reference"))
    db_path = reference_root / workspace.settings.db_path.relative_to(workspace.settings.root)
    return base, seeded, Settings.load(root=reference_root, db_path=db_path)


def reference_run(settings, seeded, base, monkeypatch, modules):
    with monkeypatch.context() as patch:
        for module in (finder, questions, applications):
            patch.setattr(module, "now_iso", Clock.now_iso)
        patch.setattr(stats, "now", Clock.now)
        patch.setattr(web_ai, "_now", Clock.now)
        reference = Reference(settings, modules)
        reference.root = settings.root
        try:
            return scenario(reference, seeded, base, modules)
        finally:
            reference.conn.close()


CASES = [MODULE_IDS, ("workspace",)]


@pytest.fixture(autouse=True)
def no_key_in_environment(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


@pytest.mark.parametrize("modules", CASES)
def test_reference_scenario(workspace, tmp_path, monkeypatch, modules, record_property):
    """Executable oracle sanity check, usable before the Rust implementation exists."""
    base, seeded, settings = prepare(workspace, tmp_path, modules)
    answers = reference_run(settings, seeded, base, monkeypatch, modules)
    record_property("requests", len(answers))
    assert len(answers) > 100
    if "applyai" in modules:
        report = answers[0][2]
        assert report["summary"]["week"]["applied"] == 3 and report["summary"]["streak_days"] >= 2
        assert all(report["sections"][name] for name in ("resume", "track", "source", "tier"))
        health_report = next(answer for path, _, answer in answers if path == "/api/apply/health")
        assert health_report["families"][0]["urr"] is not None and health_report["causes"] and health_report["correctness"]
    assert {status for _, status, _ in answers} == ({200, 400, 404, 409, 500} if "applyai" in modules else {200, 400, 403, 404, 409, 500})


@pytest.mark.parametrize("modules", CASES)
def test_the_front_answers_insights_as_the_python_core_does(workspace, tmp_path, monkeypatch, modules):
    if not RIKA_SERVER.is_file():
        pytest.skip(f"rika-server is not built ({RIKA_SERVER})")
    base, seeded, reference_settings = prepare(workspace, tmp_path, modules)
    expected = reference_run(reference_settings, seeded, base, monkeypatch, modules)
    clock = tmp_path / "clock"
    clock.write_text(base.isoformat(), encoding="utf-8")
    with front_site(workspace.settings, env={"KITTY_TEST_CLOCK": str(clock), "OPENROUTER_API_KEY": ""}) as site:
        actual = scenario(Through(site, clock, workspace.settings.root), seeded, base, modules)
    assert len(actual) == len(expected)
    for index, (answer, wanted) in enumerate(zip(actual, expected, strict=True)):
        assert answer == wanted, (index, answer[0])
    left, right = stored(workspace.settings), stored(reference_settings)
    for table in right:
        assert left[table] == right[table], table


def test_the_worker_answers_only_the_python_insight_routes(workspace, tmp_path):
    from kitty import web_automation, web_insights

    candidate = finder.insert_candidate(workspace.conn, name="Synthetic Firm", area=workspace.config.track_names[0], score=1,
                                        reason="Synthetic", ats_kind=None, ats_slug=None, board_url=None,
                                        website="https://example.invalid", evidence=["Synthetic"], run_id=None)
    workspace.conn.commit()
    conn = workspace.conn
    app = WebApplication(workspace.settings, workspace.config, tmp_path, ui_session="ui-test")
    try:
        for path in ("/api/reports", "/api/mail", "/api/apply/health", "/api/ai/status"):
            with pytest.raises(APIError, match="API route not found"):
                app.get_json(conn, path, {})
        for path in ("/api/questions", "/api/mail/proposals/1/confirm", "/api/mail/proposals/1/reject",
                     f"/api/finder/{candidate}/reject", "/api/ai/settings"):
            with pytest.raises(APIError, match="API route not found"):
                app.mutate(conn, path, {"confirm": True})
        assert not hasattr(web_insights, "download")
        assert web_automation.get(app, conn, "/api/automation/status", {})["phase"] == "idle"
    finally:
        app.close()
    conn.close()
    with front_site(workspace.settings) as site:
        for path, key in (("/api/triage", "pending"), ("/api/finder", "candidates"), ("/api/sources", "sources"),
                          ("/api/automation/status", "phase"), ("/api/questions", "questions")):
            status, answer = site.request(path)
            assert status == 200 and key in answer, (path, answer)
        assert site.request("/api/finder/999/approve", {"confirm": True}) == (
            409, {"error": "Candidate is missing or already decided"})
        assert site.request("/api/ai/preview", {"purpose": "summarize", "text": "x"}) == (
            409, {"error": "Add your OpenRouter API key in AI settings first"})

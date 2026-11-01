"""Website adapters for discovery, reporting, read-only mail and approved triage.

No network operation is triggered by a GET. Paid runs require a fresh local
estimate, an explicit approval and a cap no greater than the configured cap.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
import secrets
import time
import zipfile
from contextlib import closing
from dataclasses import asdict

from kitty.core import applications, export, finder, refresh, sources, stats
from kitty.core.discovery.pipeline import ADAPTERS, run_discover
from kitty.core.gmail import auth
from kitty.core.gmail.check import check_gmail
from kitty.core.gmail.proposals import get_pending, pending_proposals
from kitty.core.triage import run as triage
from kitty.core.triage.rules import apply_rules_tiers
from kitty.dates import now_iso
from kitty.db import connect


def _keys(values, allowed):
    from kitty.web_server import APIError
    if set(values) - set(allowed):
        raise APIError(400, "Unknown parameter")


def _confirm(body):
    from kitty.web_server import APIError
    if body.get("confirm") is not True:
        raise APIError(400, "Explicit confirmation is required")


def _external(app):
    from kitty.web_server import APIError
    if app.settings.demo:
        raise APIError(403, "External operations are disabled in demo mode")


def _number(value, name, minimum, maximum):
    from kitty.web_server import APIError
    if type(value) is not int or not minimum <= value <= maximum:
        raise APIError(400, f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _track(app, value):
    from kitty.web_server import APIError
    if value is not None and (not isinstance(value, str) or value not in app.config.track_names):
        raise APIError(400, "Unknown track")
    return value


def _task(app, kind, work):
    # Do not send arbitrary exception text (OAuth responses and paths can contain secrets).
    def safe_work():
        try:
            return work()
        except Exception as exc:
            raise RuntimeError(f"{kind} failed ({type(exc).__name__}); review local setup and retry explicitly") from None
    return {"task": app.tasks.start(kind, safe_work)}


def _triage_result(result):
    data = asdict(result)
    data["error_count"] = len(result.errors)
    data["errors"] = ["A job could not be processed; review its stored tier and batch state."] if result.errors else []
    data["status"] = "partial" if result.errors else "ok"
    return data


def _fingerprint(plan, config):
    payload = [config.triage.model_dump(mode="json"), plan.hashes,
               [(job.id, answer) for job, answer in plan.cached],
               [(job.id, tier, reason) for job, tier, reason in plan.rules]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def get(app, conn, path, query):
    from kitty.web_server import APIError, FREE_SOURCES
    if path == "/api/sources":
        _keys(query, {})
        return {"sources": [{
                    "name": item.name, "enabled": bool(item.enabled), "kind": item.kind,
                    "fragile": bool(item.fragile), "last_success": item.last_success_at,
                    "status": item.last_status or "never run", "last_run": item.last_run_at,
                    "jobs_seen": item.last_count,
                    "configured": item.name in app.config.sources.sources,
                    "runnable": item.name in FREE_SOURCES and item.name in ADAPTERS,
                    "error": "Source did not complete. " + " ".join(
                        sources.recovery_actions(item.last_status, item.last_error).values())
                        if item.last_error else None,
                } for item in sources.list_sources(conn)],
                "discovery": {"running": bool(app.discovery.get("running")),
                              "error": "Discovery did not complete." if app.discovery.get("error") else None,
                              "result": app.discovery.get("result")}}
    if path == "/api/finder":
        _keys(query, {"decision"})
        decision = query.get("decision", "pending")
        if decision not in {"pending", "approved", "rejected"}:
            raise APIError(400, "Unknown finder decision")
        last = finder.last_run(conn)
        last_data = None
        if last:
            started, stored = last
            last_data = {"started_at": started, "proposed": stored.get("proposed", 0),
                         "sources": [{"name": name, "status": item.get("status", "unknown"),
                                      "found": item.get("found", 0), "failed": bool(item.get("error"))}
                                     for name, item in stored.get("adapters", {}).items()]}
        return {"candidates": [asdict(item) for item in finder.list_candidates(conn, decision)],
                "counts": finder.decision_counts(conn),
                "last_run": last_data,
                "watchlist": [{"name": item.name, "tracks": item.tracks} for item in app.config.watchlist.companies]}
    if path == "/api/reports":
        _keys(query, {})
        return {"sections": {name: [asdict(rate) | {"response_rate": rate.response_rate,
                "advance_rate": rate.advance_rate} for rate in rates]
                for name, rates in stats.response_report(conn).items()},
                "summary": stats.report_summary(conn),
                "datasets": list(export.QUERIES)}
    if path == "/api/mail":
        _keys(query, {})
        state = auth.setup_state(app.settings)
        last = conn.execute("SELECT started_at, stats_json, error FROM runs WHERE kind='mail' ORDER BY id DESC LIMIT 1").fetchone()
        last_data = None
        if last:
            stored = json.loads(last["stats_json"] or "{}")
            last_data = {"started_at": last["started_at"], "status": stored.get("status", "unknown"),
                         "fetched": stored.get("fetched", 0), "proposed": stored.get("proposed", 0),
                         "error": "Last check needs attention; review authorization or connection." if last["error"] else None}
        return {"client_present": state.client_present, "token_present": state.token_present,
                "ready": state.ready, "scope": "gmail.readonly", "last_check": last_data,
                "proposals": [asdict(item) for item in pending_proposals(conn)]}
    if path == "/api/triage":
        _keys(query, {})
        return {"model": app.config.triage.model, "cap_usd": app.config.triage.max_cost_usd_per_run,
                "tracks": app.config.track_names,
                "pending": [{"run_id": run_id, "batch_id": item["batch_id"], "sent": item.get("sent", 0),
                             "estimate_usd": item.get("estimate_usd", 0)}
                            for run_id, item in triage.pending_batches(conn)]}
    return None


def post(app, conn, path, body):
    from kitty.web_server import APIError, FREE_SOURCES
    if path == "/api/finder/run":
        _keys(body, {"confirm", "cap"}); _confirm(body); _external(app)
        cap = _number(body.get("cap", finder.DEFAULT_CAP), "cap", 0, 1000)
        config = copy.deepcopy(app.config)
        def run_finder():
            result = finder.run_finder(app.settings, config, cap=cap)
            return {"run_id": result.run_id, "cap": result.cap, "proposed": len(result.proposed),
                    "skipped": result.skipped, "ats_found": result.ats_found,
                    "sources": {name: {"status": item.status, "found": item.found,
                        "error": "Source could not complete; inspect source setup or restrictions." if item.error else None}
                        for name, item in result.adapters.items()}}
        return _task(app, "finder", run_finder)
    match = re.fullmatch(r"/api/finder/([1-9][0-9]*)/(approve|reject)", path)
    if match:
        _keys(body, {"confirm"}); _confirm(body)
        from kitty.core.finder.store import get_candidate
        candidate_id = int(match[1])
        candidate = get_candidate(conn, candidate_id)
        if candidate is None or candidate.decision != "pending":
            raise APIError(409, "Candidate is missing or already decided")
        if match[2] == "approve":
            finder.approve(conn, app.settings, app.config, candidate_id)
            app.reload_config()
        else:
            finder.reject(conn, candidate_id)
        return {"ok": True, "decision": "approved" if match[2] == "approve" else "rejected"}
    if path in {"/api/mail/auth", "/api/mail/check"}:
        _keys(body, {"confirm", "days"} if path.endswith("check") else {"confirm"})
        _confirm(body); _external(app)
        if path.endswith("auth"):
            def authorize():
                auth.run_consent_flow(app.settings)
                return {"authorized": True, "scope": "gmail.readonly"}
            return _task(app, "mail", authorize)
        days = _number(body.get("days", 30), "days", 1, 3650)
        def check():
            with closing(connect(app.settings.db_path)) as own:
                result = check_gmail(own, app.settings, days=days)
                own.commit()
                return result.stats() | {"error": "Mail check did not complete; review authorization or connection." if result.error else None}
        return _task(app, "mail", check)
    match = re.fullmatch(r"/api/mail/proposals/([1-9][0-9]*)/(confirm|reject)", path)
    if match:
        _keys(body, {"confirm"}); _confirm(body)
        event_id = int(match[1])
        if get_pending(conn, event_id) is None:
            raise APIError(409, "Proposal is missing or already decided")
        (applications.confirm_proposal if match[2] == "confirm" else applications.reject_proposal)(conn, event_id)
        return {"ok": True}
    if path == "/api/triage/estimate":
        _keys(body, {"track", "limit"})
        track = _track(app, body.get("track"))
        limit = _number(body.get("limit", 50), "limit", 1, 1000)
        config = copy.deepcopy(app.config)
        if not hasattr(app, "_insights_plans"):
            app._insights_plans = {}
        def estimate():
            with closing(connect(app.settings.db_path)) as own:
                plan = triage.plan_triage(own, config, app.settings, track=track, limit=limit)
                token = secrets.token_urlsafe(24)
                with app.mutation_lock:
                    # A fresh estimate supersedes earlier estimates in this local workspace.
                    app._insights_plans.clear()
                    app._insights_plans[token] = (_fingerprint(plan, config), track, limit, time.monotonic())
                return {"estimate_id": token, "model": config.triage.model,
                        "cap_usd": config.triage.max_cost_usd_per_run,
                        "to_send": len(plan.to_send), "cached": len(plan.cached), "rules": len(plan.rules),
                        "now": asdict(triage.plan_estimate(plan, config, batch=False)),
                        "batch": asdict(triage.plan_estimate(plan, config, batch=True))}
        return _task(app, "triage", estimate)
    if path == "/api/triage/run":
        _keys(body, {"confirm", "mode", "estimate_id", "cap_usd"}); _confirm(body)
        mode = body.get("mode")
        if not isinstance(mode, str) or mode not in {"now", "batch", "rules", "collect"}:
            raise APIError(400, "Unknown triage mode")
        config = copy.deepcopy(app.config)
        if mode != "rules":
            _external(app)
        estimate_record = None
        if mode in {"now", "batch"}:
            token = body.get("estimate_id")
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
                raise APIError(400, "Review a fresh estimate before approving a paid run")
            estimate_record = getattr(app, "_insights_plans", {}).get(token)
            if estimate_record is None or time.monotonic() - estimate_record[3] > 1800:
                raise APIError(409, "Estimate expired; estimate again")
            cap = body.get("cap_usd")
            if type(cap) not in {int, float} or not math.isfinite(cap) or not 0 < cap <= config.triage.max_cost_usd_per_run:
                raise APIError(400, "Approved cap must be positive and no greater than the configured cap")
        elif "cap_usd" in body or "estimate_id" in body:
            raise APIError(400, "This mode does not accept an estimate or spending cap")
        def run_triage():
            with closing(connect(app.settings.db_path)) as own:
                if mode == "rules":
                    result = {"mode": "rules", "rules_set": apply_rules_tiers(own, config), "cost_usd": 0}
                elif mode == "collect":
                    pending = triage.pending_batches(own)
                    result = {"batches": [_triage_result(item) for item in triage.collect_batches(
                        own, config, app.settings, triage.make_client(app.settings))]} if pending else {"batches": []}
                else:
                    fingerprint, track, limit, _created = estimate_record
                    plan = triage.plan_triage(own, config, app.settings, track=track, limit=limit)
                    if _fingerprint(plan, config) != fingerprint:
                        return {"status": "stale", "error": "Inputs changed after estimation. No paid call was made; estimate again."}
                    with app.mutation_lock:
                        if app._insights_plans.pop(token, None) is None:
                            return {"status": "stale", "error": "Estimate was superseded. No paid call was made; estimate again."}
                    config.triage.max_cost_usd_per_run = cap
                    client = triage.make_client(app.settings) if plan.to_send else None
                    result = _triage_result((triage.run_now if mode == "now" else triage.submit_batch)(own, config, plan, client))
                own.commit()
                return result
        return _task(app, "triage", run_triage)
    match = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]+)/(run|toggle)", path)
    if match or path == "/api/sources/run":
        _keys(body, {"confirm", "enabled"} if match and match[2] == "toggle" else {"confirm"})
        _confirm(body)
        name = match[1] if match else None
        if name is not None and name not in app.config.sources.sources:
            raise APIError(404, "Unknown configured source")
        if match and match[2] == "toggle":
            from kitty.web_profile import set_source_enabled
            return set_source_enabled(app, name, body.get("enabled"))
        _external(app)
        config = copy.deepcopy(app.config)
        selected = [source_name for source_name, item in config.sources.sources.items()
                    if item.enabled and source_name in FREE_SOURCES and source_name in ADAPTERS
                    and (name is None or source_name == name)]
        if not selected:
            raise APIError(400, "Enable a supported free source first")
        def discover():
            # Claim the same running state as the original discovery endpoint.
            with app.discovery_lock:
                if app.discovery["running"]:
                    raise RuntimeError("Discovery already running")
                app.discovery = {"running": True, "error": None, "result": None}
            state = {"running": False, "error": "Discovery failed; inspect source health.", "result": None}
            try:
                result = run_discover(app.settings, config, only=selected)
                unhealthy = sum(item.status != "ok" for item in result.sources.values())
                state = {"running": False, "error": None,
                         "result": f"Checked {len(result.sources)} sources; {unhealthy} incomplete or unhealthy."}
                return {"run_id": result.run_id, "rejudged": result.rejudged,
                        "sources": {source_name: {key: value for key, value in item.as_dict().items()
                             if key in {"status", "fetched", "new", "merged", "seen_again", "closed", "unrouted"}}
                             | {"error": "Source did not complete; inspect its health and recovery guidance." if item.error else None}
                            for source_name, item in result.sources.items()}}
            finally:
                with app.discovery_lock:
                    app.discovery = state
        return _task(app, "discovery", discover)
    if path == "/api/postings/refresh":
        _keys(body, {"confirm", "dry_run", "track", "limit", "job_ids"})
        dry_run = body.get("dry_run", False)
        if type(dry_run) is not bool:
            raise APIError(400, "dry_run must be boolean")
        limit = _number(body.get("limit", 10), "limit", 1, 100)
        track = _track(app, body.get("track"))
        ids = body.get("job_ids")
        if ids is not None:
            if not isinstance(ids, list) or not ids or len(ids) > 100:
                raise APIError(400, "job_ids must contain 1 to 100 job IDs")
            ids = [_number(value, "job ID", 1, 2**63 - 1) for value in ids]
            if any(conn.execute("SELECT 1 FROM jobs WHERE id=?", (value,)).fetchone() is None for value in ids):
                raise APIError(404, "A requested job does not exist")
        try:
            selected_jobs = refresh.select_jobs(conn, limit=limit, track=track, job_ids=ids)
        except ValueError:
            raise APIError(400, "Requested jobs are outside the selected track") from None
        if dry_run:
            return {"jobs": [{"id": item.id, "company": item.company_name, "title": item.title,
                             "freshness": item.freshness} for item in selected_jobs]}
        _confirm(body); _external(app)
        config = copy.deepcopy(app.config)
        def refresh_postings():
            with closing(connect(app.settings.db_path)) as own:
                results = refresh.refresh_jobs(own, config, app.settings, limit=limit, track=track, job_ids=ids)
                own.commit()
                return {"postings": [{"job_id": item.job_id, "status": item.check.status,
                     "reason": sources.safe_error(item.check.reason), "changed_fields": sorted(item.changed_fields)} for item in results]}
        return _task(app, "posting-refresh", refresh_postings)
    return None


def download(app, conn, path, query):
    from kitty.web_server import APIError
    if path != "/api/reports/export":
        return None
    _keys(query, {"format", "dataset"})
    fmt, dataset = query.get("format", "json"), query.get("dataset", "all")
    if fmt not in {"json", "csv"} or dataset not in {"all", *export.QUERIES}:
        raise APIError(400, "Unknown export format or dataset")
    tables = list(export.QUERIES) if dataset == "all" else [dataset]
    if fmt == "json":
        document = {"exported_at": now_iso()}
        for table in tables:
            columns, rows = export.table_rows(conn, table)
            document[table] = [dict(zip(columns, row)) for row in rows]
        return json.dumps(document, ensure_ascii=False, indent=2).encode(), "application/json", f"kitty-{dataset}.json"
    def csv_bytes(table):
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

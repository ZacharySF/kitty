"""Website preparation and observation; final application submission stays human.

One controller owns a browser worker for the server lifetime. Stop releases remote
control, not the retained Firefox tabs. Plans/preflight are local snapshots, not
proof that a live form is ready.

Every plan proposes a ``prepare_applications`` effect (kitty.core.effects, reviewed
inline here). Its payload is the selection (mode, track, cap, requested job IDs,
account mode); its preconditions bind the selected jobs, each job's identity and
chosen resume row (``job:<id>``), resume availability, the account plan, the saved
credential binding and the recovery offer/model (both hashed) and the profile,
answers, apply and tracks configuration (hashed). Start approves and executes exactly
that digest once: anything changed since the review refuses it, and the person reviews
again. The browser run that follows re-checks every job, its approved resume row and
the resume bytes against the stored executed effect (kitty.core.apply.filler).
Only core confirmation rules record submissions::

    POST /api/automation/plan | /preflight  {mode?, track?, limit?, job_ids?, authentication_mode?}
        -> {mode, track, cap, job_ids, jobs, left, authentication, recovery, note,
            effect: {id, digest, expires_at}, demo, watch_hours, firefox_configured, known_answers}
    POST /api/automation/start  {effect_id, digest, confirmed: true, watch?, background?,
                                 authorize_authentication?, authorize_recovery_ai?} -> status
    POST /api/automation/restart-firefox  {confirmed: true} -> status (phase restarting, then
        restarted or failed): a restart_firefox: handoff's button; refused while a worker runs
    GET  /api/apply/health  -> kitty.core.apply.health.report (read-only attempt records)
"""
from __future__ import annotations

import copy
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from types import SimpleNamespace

from kitty import web_effects
from kitty.core import effects, form_questions, jobs, questions
from kitty.config import AuthenticationSettings, _authentication_origin
from kitty.core.apply import autofill, filler
from kitty.core.apply.credentials import CredentialError, credential_binding, load_credentials
from kitty.core.apply.urls import detect_ats
from kitty.core.apply.watch import Watch, WatchedTab
from kitty.db import connect
from kitty.db.connection import transaction

APPROVAL_REQUIRED = "Preparation approval is required; review the plan again before filling"
#: Configuration a reviewed preparation depends on (bound hashed, never stored as is).
CONFIG_SECTIONS = ("profile", "answers", "apply", "tracks")
#: Start's choices, recorded on the approval (not part of the digest).
START_OPTIONS = ("watch", "background", "authorize_authentication", "authorize_recovery_ai")


def _authentication_plan(app, selected: list[dict], mode: str) -> tuple[dict, str | None]:
    """Expose reviewed destinations and Profile Google identity, never private credential values."""
    policy = app.config.apply.authentication
    origins = set(policy.approved_origins if policy.enabled else [])
    origins.update(origin for job in selected
                   if (origin := _authentication_origin(job["apply_url"] or job["url"])))
    # Google is an identity provider, never an employer-password recipient.
    origins.discard("https://accounts.google.com")
    binding = None
    problem = None
    email = app.config.profile.email.strip()
    google_available = bool(email and "@" in email and not any(c.isspace() for c in email))
    if mode != "google" and selected:
        try:
            credentials = load_credentials(app.settings)
        except CredentialError as exc:
            problem = str(exc)
        else:
            binding = credential_binding(credentials)
    verification = policy.email_verification.model_dump(mode="json")
    verification["scopes"] = [scope.model_dump(mode="json")
                              for scope in policy.email_verification.scopes_for(origins)]
    verification["enabled"] = bool(verification["enabled"] and verification["scopes"] and mode != "google")
    return {
        "enabled": policy.enabled,
        "allow_account_creation": mode in ("auto", "create") or (mode == "configured" and policy.allow_account_creation),
        "allow_account_terms": mode in ("auto", "login", "create") or (mode == "configured" and policy.allow_account_terms),
        "approved_origins": sorted(origins),
        "configured_origins": [origin for origin in policy.approved_origins
                               if origin != "https://accounts.google.com"] if policy.enabled else [],
        "credentials_available": binding is not None,
        "credential_problem": problem,
        "google_email": email if mode == "google" else None,
        "google_available": google_available,
        "mode": mode,
        "existing_account_origins": list(policy.existing_account_origins),
        "manual_account_origins": list(policy.manual_account_origins),
        "email_verification": verification,
    }, binding


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def _keys(values: dict, allowed: set[str]) -> None:
    if set(values) - allowed:
        raise _error(400, "Unknown parameter")


def _positive(value: Any, name: str, maximum: int = 1000) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _error(400, f"{name} must be an integer from 1 to {maximum}")
    return value


def _confirm(app, body: dict) -> None:
    if app.settings.demo:
        raise _error(403, "External actions are disabled in demo mode")
    if body.get("confirmed") is not True:
        raise _error(400, "Explicit confirmation is required")


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutomationController:
    """Serialize browser access without capturing an HTTP SQLite connection."""

    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._watch: Watch | None = None
        self._tabs: list[WatchedTab] = []
        self._closed = False
        self._state: dict[str, Any] = {
            "running": False, "phase": "idle", "job_ids": [], "reports": [],
            "observed": [], "error": None, "started_at": None, "finished_at": None,
            "watched_count": 0,
            "activity": None, "last_activity_at": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def start(self, config, approval: effects.Effect, *, watch: bool = True, background: bool = False) -> dict:
        """Run the executed prepare_applications effect's jobs (see `approve`)."""
        if self.settings.demo:
            raise _error(403, "Browser automation is disabled in demo mode")
        with self._lock:
            if self._closed:
                raise _error(409, "Browser controller is shutting down")
            if self._state["running"] and self._state["phase"] != "watching":
                raise _error(409, "A browser worker is active; stop it and wait before starting another")
            job_ids = list(approval.preconditions.get("job_ids") or [])
            if not job_ids:
                raise _error(409, "No eligible jobs in this preparation plan")
            if (approval.kind != filler.PREPARATION_KIND or approval.state != "executed"
                    or any(filler.job_precondition(job_id) not in approval.preconditions for job_id in job_ids)):
                raise _error(409, filler.APPROVAL_CHANGED)
            previous = self._thread if self._state["running"] else None
            if previous is not None:
                self._watch.stop.set()
                self._state["phase"] = "stopping"
        if previous is not None:
            previous.join(timeout=5)
        with self._lock:
            if self._closed:
                raise _error(409, "Browser controller is shutting down")
            if self._state["running"]:
                raise _error(409, "The previous browser worker is stopping; wait for it before preparing again")
            captured = copy.deepcopy(config)
            self._state = {
                "running": True, "phase": "filling", "job_ids": list(job_ids),
                "reports": [], "observed": [], "error": None, "started_at": _stamp(),
                "finished_at": None, "watched_count": 0,
                "activity": None, "last_activity_at": _stamp(),
            }
            observe = watch and captured.apply.watch_hours > 0
            self._watch = Watch(
                hours=captured.apply.watch_hours if observe else 1,
                tabs=self._tabs, on_start=self._watching, on_applied=self._applied,
            )
            self._thread = threading.Thread(
                target=self._run, args=(captured, job_ids, approval, self._watch, background, observe),
                name="kitty-web-browser", daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._state.update(running=False, phase="failed", error="Browser worker could not start",
                                   finished_at=_stamp())
                raise _error(500, "Browser worker could not start") from None
            return copy.deepcopy(self._state)

    def _filled(self, reports) -> None:
        safe = []
        for report in reports:
            row = asdict(report)
            if row["error"]:
                message = row["error"].lower()
                if row["error"] == filler.APPROVAL_CHANGED:
                    row["error"] = filler.APPROVAL_CHANGED
                elif "no such frame" in message or "no such browsing context" in message or "tab closed" in message:
                    row["error"] = "The application tab closed or was replaced. Keep Firefox open and review a new preparation plan."
                elif "disconnect" in message or "connection lost" in message:
                    row["error"] = "The Firefox connection was lost. Keep Firefox open and review a new preparation plan; earlier results are retained."
                elif "timeout" in message or "timed out" in message:
                    row["error"] = "Firefox did not respond in time. Review the application tab before trying again."
                elif row["error"].startswith(("Firefox not found.", "Firefox requires ", "Firefox did not open ", "Firefox could not start ")):
                    pass  # Static startup diagnostics contain no form or credential values.
                elif "remote-control session" in message or "remote-controlling the rika! Firefox" in message:
                    row["error"] = "Another controller holds the rika! Firefox session. Stop its watcher before preparing this run; your tabs stay open."
                else:
                    row["error"] = "Browser preparation failed; review the retained tab or browser setup"
                row["note"] = row["error"]
            safe.append(row)
        with self._lock:
            self._state["reports"] = safe
            if reports:
                self._state["error"] = ("Browser could not prepare any selected jobs"
                                        if all(item.status == "error" for item in reports) else None)

    def _watching(self, count: int) -> None:
        with self._lock:
            if self._state["phase"] != "stopping":
                self._state["phase"] = "watching"
            self._state["watched_count"] = count

    def _applied(self, tab: WatchedTab) -> None:
        with self._lock:
            self._state["observed"].append({"job_id": tab.job_id, "label": tab.label, "observed_at": _stamp()})
            self._state["watched_count"] = len(self._tabs)

    def _progress(self, message: str) -> None:
        import re
        activity = re.fullmatch(r"(preparing|authenticating|verifying|recovering):([0-9]+)(?::(planning|inspect|wait|focus|recommit|retry|reload|handoff))?", message)
        if activity:
            stage, identifier, action = activity.groups()
            with self._lock:
                if self._state["phase"] != "stopping":
                    self._state["phase"] = {"preparing": "filling", "authenticating": "authenticating", "verifying": "verifying", "recovering": "recovering"}[stage]
                self._state["activity"] = {"job_id": int(identifier), "action": action or stage}
                self._state["last_activity_at"] = _stamp()
        # Core catches watcher disconnects so prepared forms stay usable. Surface
        # the failed observation instead of presenting a healthy completion.
        if message.startswith("stopped watching:"):
            with self._lock:
                self._state["error"] = "Browser observation disconnected; review tabs and record submissions manually"

    def _run(self, config, ids: list[int], approval: effects.Effect, watch: Watch, background: bool,
             observe: bool) -> None:
        def filled(reports):
            self._filled(reports)
            # Keep the core's cancellation and retained-tab handling even for
            # no-watch runs; stop before its observation loop starts.
            if not observe:
                watch.stop.set()
        try:
            filler.fill_jobs(self.settings, config, ids, watch=watch, progress=self._progress,
                             on_filled=filled, on_report=self._filled, background=background,
                             pause=config.apply.autofill_pause_seconds, approval=approval)
        except filler.ApprovalError:
            with self._lock:
                self._state["error"] = filler.APPROVAL_CHANGED
        except Exception as exc:
            with self._lock:
                self._state["error"] = f"Browser worker failed ({type(exc).__name__})"
        finally:
            with self._lock:
                phase = "failed" if self._state["error"] else "stopped" if self._state["phase"] == "stopping" else "completed"
                self._state.update(running=False, phase=phase, finished_at=_stamp(), watched_count=0)

    def stop(self) -> dict:
        with self._lock:
            if self._watch is not None and self._state["running"]:
                self._watch.stop.set()
                self._state["phase"] = "stopping"
            return copy.deepcopy(self._state)

    def restart_firefox(self) -> dict:
        """The owner's Restart Firefox for a `restart_firefox:` handoff (browser.restart_own_firefox).

        Only while no browser worker runs; the restart itself runs in the worker
        thread (killing and starting Firefox takes seconds). The handoffs it
        answers then say the job needs a new preparation plan.
        """
        if self.settings.demo:
            raise _error(403, "Browser automation is disabled in demo mode")
        with self._lock:
            if self._closed:
                raise _error(409, "Browser controller is shutting down")
            if self._state["running"]:
                raise _error(409, "A browser worker is active; stop it and wait before restarting Firefox")
            self._state.update(running=True, phase="restarting", error=None, activity=None,
                               started_at=_stamp(), finished_at=None)
            self._thread = threading.Thread(target=self._restart, name="kitty-web-firefox-restart", daemon=True)
            self._thread.start()
            return copy.deepcopy(self._state)

    def _restart(self) -> None:
        from kitty.core.apply.browser import BrowserError, restart_own_firefox

        error = None
        try:
            restart_own_firefox(self.settings)
        except BrowserError as exc:
            error = str(exc).strip().splitlines()[0][:300] if str(exc).strip() else "Firefox could not restart"
        except Exception as exc:  # noqa: BLE001 - the worker reports instead of dying silently
            error = f"Firefox could not restart ({type(exc).__name__})"
        with self._lock:
            if error is None:
                self._tabs.clear()              # every tab went with the old Firefox
                for row in self._state["reports"]:
                    if (row.get("note") or "").startswith("restart_firefox:"):
                        row["note"] = ("Firefox restarted: review a new preparation plan for this job "
                                       "(Continue filling).")
            self._state.update(running=False, phase="failed" if error else "restarted", error=error,
                               finished_at=_stamp())

    def close(self) -> None:
        """Wait for the session to release before deleting a temporary workspace."""
        with self._lock:
            self._closed = True
            if self._watch is not None:
                self._watch.stop.set()
                if self._state["running"]:
                    self._state["phase"] = "stopping"
            thread = self._thread
        if thread is not None:
            thread.join()


def _selection(app, conn, body: dict) -> tuple[str, str | None, int, list[int]]:
    mode = body.get("mode", "selected" if body.get("job_ids") else "queue")
    if mode not in ("selected", "queue", "queued", "top", "favorites"):
        raise _error(400, "mode must be selected, queue, queued, top, or favorites")
    track = body.get("track")
    if track is not None and not isinstance(track, str):
        raise _error(400, "track must be a string")
    track = track or None
    if track is not None and app.config.track(track) is None:
        raise _error(400, "Unknown track")
    limit = _positive(body.get("limit", min(app.config.apply.autofill_cap, 1000)), "limit")
    cap = min(limit, app.config.apply.autofill_cap)
    ids = body.get("job_ids", [])
    if not isinstance(ids, list) or len(ids) > 1000:
        raise _error(400, "job_ids must be a list of at most 1000 IDs")
    for job_id in ids:
        _positive(job_id, "job_id", 2**53 - 1)
    if mode == "selected" and not ids:
        raise _error(400, "Select at least one job")
    if mode != "selected" and ids:
        raise _error(400, "job_ids are only supported for selected mode")
    return mode, track, cap, list(dict.fromkeys(ids))


def _payload(app, conn, body: dict) -> dict:
    """The prepare_applications payload: the requested selection and account mode."""
    mode, track, cap, ids = _selection(app, conn, body)
    auth_mode = body.get("authentication_mode", "google" if
                         app.config.apply.authentication.account_mode == "google" else "auto")
    if auth_mode not in ("auto", "configured", "login", "create", "google"):
        raise _error(400, "authentication_mode must be auto, configured, login, create, or google")
    return {"mode": mode, "track": track, "cap": cap, "job_ids": ids, "authentication_mode": auth_mode}


def _plan(app, conn, payload: dict) -> dict:
    """The plan for a payload, read from current state, with the preconditions it binds.

    Private keys (``_preconditions``, ``_credential_binding``, ``_recovery_model``) never
    leave the server; credential and recovery bindings enter the preconditions hashed.
    """
    mode, track, cap, ids = payload["mode"], payload["track"], payload["cap"], payload["job_ids"]
    left: list[dict] = []
    if mode == "favorites":
        planned = autofill.plan_run(conn, app.config, limit=cap, track=track)
        ids = planned.job_ids
        left = [asdict(item) for item in planned.left]
    elif mode in ("queue", "queued", "top"):
        candidates = jobs.list_jobs(conn, jobs.JobFilter(
            queue_states=["queued"] if mode == "queued" else ["new", "queued"],
            tiers=["A", "B"] if mode == "queue" else None,
            eligibility=jobs.NOT_FILTERED_OUT, not_applied=True,
            tracks=[track] if track else None, order="best",
            prefer_cities=app.config.prefer_cities, prefer_remote=app.config.profile.open_to_remote,
        ))
        if mode == "queue":
            candidates.sort(key=lambda job: (job.fill_state == "ready", job.tier != "A"))
        ids = [job.id for job in candidates]
    selected = []
    bindings = {}
    for job_id in ids:
        job = jobs.get_job(conn, job_id)
        if job is None:
            raise _error(404, "Selected job not found")
        reason = filler.preparation_blocker(job)
        if reason is None:
            if job.application_id is not None:
                reason = "already applied"
            elif job.queue_state in ("skipped", "applied"):
                reason = f"queue state {job.queue_state}"
            elif track and track not in job.tracks:
                reason = "outside selected track"
            elif mode != "selected" and job.fill_state == "ready":
                reason = "already verified ready at last attempt; select individually to recheck retained tab"
            elif len(selected) >= cap:
                reason = f"over this run's cap of {cap}"
        if reason:
            left.append({"job_id": job.id, "company": job.company_name, "title": job.title, "reason": reason})
            continue
        resume = filler.choose_resume(conn, app.config, job)
        from kitty.web_server import _pdf_available
        available = resume is not None and _pdf_available(app.settings, resume)
        bindings[filler.job_precondition(job.id)] = filler.job_binding(job, resume)
        selected.append({
            "id": job.id, "company": job.company_name, "title": job.title,
            "url": job.url, "apply_url": job.apply_url,
            "ats": detect_ats(job.apply_url or job.url), "fill_state": job.fill_state,
            "blocker": job.fill_blocker, "retained_tab": bool(job.fill_context_id),
            "resume": {"id": resume.id, "label": resume.label, "file_hash": resume.file_hash,
                       "available": available} if resume else None,
            "warnings": [] if available else ["No verified registered PDF; upload must be reviewed manually"],
        })
    authentication, credential_binding = _authentication_plan(app, selected, payload["authentication_mode"])
    from kitty import web_ai
    recovery, recovery_model = web_ai.recovery_offer(app, conn)
    preconditions = {
        "job_ids": [job["id"] for job in selected],
        **bindings,
        "available": {str(job["id"]): job["resume"]["available"] if job["resume"] else None for job in selected},
        "authentication": authentication,
        "credentials": effects.hashed(credential_binding) if credential_binding is not None else None,
        "recovery": recovery,
        "recovery_model": effects.hashed(recovery_model) if recovery_model is not None else None,
        **{f"config:{name}": effects.hashed(getattr(app.config, name).model_dump(mode="json"))
           for name in CONFIG_SECTIONS},
    }
    return {"mode": mode, "track": track, "cap": cap, "job_ids": preconditions["job_ids"],
            "jobs": selected, "left": left, "authentication": authentication, "recovery": recovery,
            "_preconditions": preconditions, "_recovery_model": recovery_model,
            "_credential_binding": credential_binding,
            "note": "Historical local snapshot only, not live readiness. Reviewed resume bytes, URLs, answers, account choice and saved credentials are bound to this plan; changes require review again. Unknown answers stay blank. Final application Submit stays manual; separately authorized account requests may be sent."}


def preparation_plan(app, conn, body: dict) -> dict:
    """The plan a request body would propose, read now (no effect is recorded)."""
    return _plan(app, conn, _payload(app, conn, body))


@dataclass
class Review:
    """What prepare_applications reads: a config snapshot and settings (`app`) and the
    connection. `plan` is the plan built by the latest precondition read."""

    app: Any
    conn: sqlite3.Connection
    plan: dict | None = None


def _preconditions(ctx: Review, payload: dict) -> dict:
    ctx.plan = _plan(ctx.app, ctx.conn, payload)
    return ctx.plan["_preconditions"]


def _account_policy(config, plan: dict) -> AuthenticationSettings:
    """The run's account policy when the person authorized account actions for it."""
    auth = plan["authentication"]
    mode = auth["mode"]
    if mode == "google":
        if not auth["google_available"]:
            raise _error(409, "Set your job Google email in Profile and review again")
    elif not auth["credentials_available"]:
        raise _error(409, "Saved credentials are unavailable; fix the private credential file and review again")
    origins = auth["configured_origins"] if mode == "configured" else auth["approved_origins"]
    if not origins or (mode == "configured" and not config.apply.authentication.enabled):
        raise _error(400, "Review the exact HTTPS account destinations before preparing applications")
    policy = AuthenticationSettings(
        enabled=True, approved_origins=origins,
        allow_account_creation=auth["allow_account_creation"],
        allow_account_terms=auth["allow_account_terms"],
        account_mode=config.apply.authentication.account_mode if mode == "configured" else mode,
        existing_account_origins=auth["existing_account_origins"],
        email_verification=auth["email_verification"],
        manual_account_origins=auth["manual_account_origins"],
    )
    policy._credential_binding = plan["_credential_binding"]
    if mode == "google":
        policy._google_email = auth["google_email"]
    return policy


def _run(ctx: Review, effect: effects.Effect) -> dict:
    """Inside the claim: refuse a start this plan cannot run (nothing commits then, so the
    review stays usable) and hand the controller the run's plan and account policy."""
    plan = ctx.plan
    # The executor only uses the plan whose preconditions matched the approved digest.
    if plan is None or effects.digest(effect.kind, effect.payload, plan["_preconditions"]) != effect.digest:
        raise effects.Refused(filler.APPROVAL_CHANGED)
    if not plan["job_ids"]:
        raise _error(409, "No eligible jobs in this preparation plan")
    options = effect.options
    policy = (_account_policy(ctx.app.config, plan) if options.get("authorize_authentication")
              else AuthenticationSettings())
    if any(job["resume"] and not job["resume"]["available"] for job in plan["jobs"]):
        raise _error(409, "A selected resume is unavailable or changed; choose a verified PDF and review the plan again")
    if options.get("authorize_recovery_ai") and plan["_recovery_model"] is None:
        raise _error(409, "Model-assisted recovery is unavailable; review AI settings first")
    return {"plan": plan, "authentication": policy}


PREPARE_APPLICATIONS = effects.REGISTRY.register(effects.Executable(
    name=filler.PREPARATION_KIND, title="Prepare applications in Firefox (you submit)",
    proposers=frozenset({"user"}), validate=lambda ctx, raw: _payload(ctx.app, ctx.conn, raw),
    preconditions=_preconditions, review="inline", lifetime=timedelta(hours=1), run=_run,
))


def propose(app, conn, body: dict) -> dict:
    """Propose the plan for review: the public plan and its effect's id, digest and expiry."""
    ctx = Review(SimpleNamespace(config=copy.deepcopy(app.config), settings=app.settings), conn)
    try:
        effect = effects.propose(conn, PREPARE_APPLICATIONS.name, body, actor=web_effects.user(app), ctx=ctx)
    except effects.EffectError as exc:
        raise _error(exc.status, str(exc)) from None
    plan = {key: value for key, value in ctx.plan.items() if not key.startswith("_")}
    return {**plan, "effect": {"id": effect.id, "digest": effect.digest, "expires_at": effect.expires_at},
            "demo": app.settings.demo, "watch_hours": ctx.app.config.apply.watch_hours,
            "firefox_configured": bool(app.settings.firefox_path),
            "known_answers": len(ctx.app.config.answers.answers)}


@dataclass(frozen=True)
class Approved:
    """An executed preparation: the run's config snapshot (carrying the reviewed account
    policy), the executed effect the filler re-checks against, its plan and Start's options."""

    config: Any
    effect: effects.Effect
    plan: dict
    options: dict


def approve(app, conn, body: dict) -> Approved:
    """Approve and execute the reviewed prepare_applications effect, once.

    A missing or unknown effect is APPROVAL_REQUIRED; a refused (anything it binds
    changed), expired, already used or different digest is APPROVAL_CHANGED, with the
    changed precondition names under ``problems``: review the plan again.
    """
    effect_id, presented = body.get("effect_id"), body.get("digest")
    if not isinstance(effect_id, str) or not effect_id or not isinstance(presented, str) or not presented:
        raise _error(409, APPROVAL_REQUIRED)
    try:
        stored = effects.get(conn, effect_id)
    except effects.UnknownEffect:
        raise _error(409, APPROVAL_REQUIRED) from None
    if stored.kind != PREPARE_APPLICATIONS.name:
        raise _error(409, APPROVAL_REQUIRED)
    options = {"watch": body.get("watch", True),
               "background": body.get("background", stored.payload["mode"] != "selected"),
               "authorize_authentication": body.get("authorize_authentication", False),
               "authorize_recovery_ai": body.get("authorize_recovery_ai", False)}
    ctx = Review(SimpleNamespace(config=copy.deepcopy(app.config), settings=app.settings), conn)
    try:
        effect, result = effects.approve_and_execute(conn, stored.id, presented, kinds={PREPARE_APPLICATIONS.name},
                                                     actor=web_effects.user(app), ctx=ctx, options=options)
    except effects.Refused as exc:
        from kitty.web_server import APIError
        raise APIError(409, filler.APPROVAL_CHANGED,
                       problems=[{"precondition": name} for name in exc.changed]) from None
    except effects.EffectError as exc:
        raise _error(exc.status, str(exc)) from None
    config = ctx.app.config
    config.apply.authentication = result["authentication"]
    return Approved(config, effect, result["plan"], options)


def get(app, conn, path: str, query: dict) -> dict | None:
    if path == "/api/automation/status":
        _keys(query, set())
        return app.automation.snapshot()
    if path == "/api/apply/health":
        _keys(query, set())
        from kitty.core.apply import health
        return health.report(conn)
    if path == "/api/questions":
        _keys(query, {"kind", "job_id", "min_jobs"})
        from kitty.web_server import _integer
        min_jobs = _integer(query.get("min_jobs", "0"), "min_jobs", 0, 100000)
        kind = query.get("kind", "all")
        if kind not in {"all", *form_questions.KINDS}:
            raise _error(400, "Unknown question kind")
        job_id = None
        if "job_id" in query:
            from kitty.web_server import _integer
            job_id = _integer(query["job_id"], "job_id", 1, 2**53 - 1)
        found = form_questions.summarize(conn, app.config)
        return {"questions": [asdict(item) for item in found if (kind == "all" or item.kind == kind)
                              and len(item.job_ids) >= min_jobs
                              and (job_id is None or job_id in item.job_ids)], "kinds": list(form_questions.KINDS)}
    return None


def post(app, conn, path: str, body: dict) -> dict | None:
    if path in {"/api/automation/plan", "/api/automation/preflight"}:
        _keys(body, {"mode", "track", "limit", "job_ids", "authentication_mode"})
        return propose(app, conn, body)
    if path == "/api/automation/start":
        _keys(body, {"effect_id", "digest", "confirmed", *START_OPTIONS})
        _confirm(app, body)
        for key in START_OPTIONS:
            if key in body and type(body[key]) is not bool:
                raise _error(400, f"{key} must be a boolean")
        approved = approve(app, conn, body)
        grant = None
        if approved.options["authorize_recovery_ai"]:
            from kitty import web_ai
            grant = web_ai.reserve_recovery(app, conn, approved.plan["_recovery_model"])
            approved.config.apply.recovery._model_grant = grant
        try:
            return app.automation.start(approved.config, approved.effect, watch=approved.options["watch"],
                                        background=approved.options["background"])
        except Exception:
            if grant is not None:
                web_ai.cancel_recovery_reservation(conn, grant["reservation_id"])
            raise
    if path == "/api/automation/stop":
        _keys(body, set())
        return app.automation.stop()
    if path == "/api/automation/restart-firefox":
        # A restart_firefox: handoff's button: kills the rika! Firefox (requests its lost
        # session still held die unsent) and starts it without restoring tabs.
        _keys(body, {"confirmed"})
        _confirm(app, body)
        return app.automation.restart_firefox()
    if path == "/api/questions/refresh":
        _keys(body, set())
        with transaction(conn):
            count = questions.refresh_answered_flags(conn, app.config)
        return {"answered": count}
    if path == "/api/questions":
        _keys(body, {"text", "job_id"})
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000 or "\x00" in text:
            raise _error(400, "Question must contain 1–4000 characters")
        try:
            text.encode("utf-8")
        except UnicodeError:
            raise _error(400, "Question must be valid Unicode") from None
        job_id = body.get("job_id")
        if job_id is not None:
            _positive(job_id, "job_id", 2**53 - 1)
            if jobs.get_job(conn, job_id) is None:
                raise _error(404, "Job not found")
        with transaction(conn):
            question = questions.log_question(conn, text, job_id=job_id)
        return {"id": question.id}
    if path == "/api/questions/collect":
        _keys(body, {"confirmed", "limit", "states", "tiers"})
        _confirm(app, body)
        limit = _positive(body.get("limit", 100), "limit")
        states = body.get("states", ["new", "queued"])
        tiers = body.get("tiers", ["A", "B"])
        for value, choices, label in ((states, {"new", "queued", "later", "skipped", "not_eligible", "applied"}, "states"),
                                      (tiers, {"A", "B", "C"}, "tiers")):
            if not isinstance(value, list) or not value or any(not isinstance(x, str) or x not in choices for x in value):
                raise _error(400, f"Invalid {label}")
        settings, config = app.settings, copy.deepcopy(app.config)
        def work():
            owned = connect(settings.db_path)
            try:
                result = form_questions.collect(owned, settings, config, states=states, tiers=tiers, limit=limit)
                return {**vars(result), "skipped": dict(result.skipped), "failed": dict(result.failed)}
            finally:
                owned.close()
        return app.tasks.start("questions-collect", work)
    return None

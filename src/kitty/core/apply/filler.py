"""The application form filler: opens each job's form and fills what it knows.

Flow per job: open the application form in a new tab, attach the chosen
resume, fill contact / education fields from profile.yaml and screening
questions from answers.yaml, then stop. I review the tab and click submit.

The filler never submits (see page.py for how that is enforced) and never
invents a value. Required questions it could not answer are logged to the
unanswered-questions queue so I can add them to answers.yaml once. It does not
record an application while filling; with a Watch it then waits for my submit
and records the application when the tab shows the confirmation page (watch.py).
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Sequence
from urllib.parse import urlsplit

from kitty.config import Config
from kitty.core import effects
from kitty.core import jobs as jobs_core
from kitty.core import questions as questions_core
from kitty.core import resumes as resumes_core
from kitty.core.resume_versions import default_resume
from kitty.core.discovery.text import authorization_jurisdiction
from kitty.core.refresh import PostingCheck, persist_check
from kitty.core.apply import accounts as accounts_core
from kitty.core.apply.account_flow import AccountStage
from kitty.core.apply.auth import AuthResult, authenticate, authentication_pending, google_session
from kitty.core.apply.residence_gate import complete_residence_gate, prepare_profile_bootstrap, residence_gate, submit_profile_bootstrap
from kitty.core.apply.bidi import Bidi, BidiError, ConnectionLost, Frame, SessionBusy, SessionEndError, Tab, TabClosed, open_session, open_tab
from kitty.core.apply.browser import (
    BrowserError, busy_message, ensure_firefox, headless_firefox, hide_automation,
)
from kitty.core.apply.credentials import CredentialError, credential_binding, load_credentials
from kitty.core.apply.email_verification import complete_email_verification
from kitty.core.apply.records import fill_required_records
from kitty.core.apply.recovery import Action, Blocker, Result as RecoveryResult
from kitty.core.apply.recovery_runtime import (
    JobRecovery, PreparationStopped, finish_model_reservation, model_planner,
)
from kitty.core.apply.fields import (
    MANUAL_CONSENT, TEXT_KINDS, FormField, Plan, is_affirmative, is_resume_input, lookup_answer, match_option,
    plan_for,
)
from kitty.core.apply.guard import OWNER_HINT, OwnerTookOver, SubmitGuardStop, UnsafeAction, stop_for
from kitty.core.apply.page import (
    ChoiceSelectionError, choose_select, fill_text, form_frames, pick_from_list, pick_from_search,
    read_fields, settle, tick, upload, uploads_pending, wait_for_form, wait_for_uploads,
)
from kitty.core.apply.urls import (
    UNSUPPORTED_FLOW, clean_url, detect_ats, form_url, names_posting, redirect_source, route_apply_link,
    unsupported_flow,
)
from kitty.core.apply import resolve as resolve_core
from kitty.core.apply.preflight import advance, inspect_page, wait_for_page
from kitty.core.apply import attempts as attempts_core
from kitty.core.apply.state import record_attempt
from kitty.core.apply.watch import (
    CONTEXT_MARKER, RULES, Watch, WatchedTab, look, owned_context, reattach, watch_tabs,
)
from kitty.db import connect
from kitty.db.connection import transaction
from kitty.models import Job, Resume
from kitty.normalize import canonical_url
from kitty.settings import Settings

#: Seconds between opening one job's page and the next: polite over fast.
PAUSE_BETWEEN_JOBS = 2.0
_ACCOUNT_FIELD_LABELS = {
    "email": "account email", "emailConfirm": "account email confirmation",
    "password": "account password", "passwordConfirm": "account password confirmation",
}

OutcomeCause = Literal[
    "ready", "human_boundary", "missing_applicant_fact", "unsupported_controls",
    "unavailable", "ineligible", "browser_error",
]


class EligibilityOverride(Enum):
    """Only an explicit CLI choice can authorize ineligible compatibility research."""

    COMPATIBILITY_RESEARCH = "compatibility_research"


class _EntryHandoff(PreparationStopped):
    """The loaded page is an explicitly excluded flow: stop before touching it."""


def preparation_blocker(job: Job, *, eligibility_override: EligibilityOverride | None = None) -> str | None:
    """Local exclusions, before inspecting tabs, credentials, or applicant files.

    Explicitly excluded flows (Indeed Apply, Tesla, employer-own portals, Paradox,
    unresolved Litos; `urls.unsupported_flow`) are typed handoffs, as is a stored
    `unsupported_flow:` handoff for the very URL the job would open.
    """
    if not job.is_active:
        return "closed: Posting unavailable; refresh availability before preparing it."
    if (job.queue_state == "not_eligible" or job.eligibility == "ineligible") and (
        eligibility_override is not EligibilityOverride.COMPATIBILITY_RESEARCH
    ):
        return "ineligible: " + (job.eligibility_reason or "marked not eligible")
    url = job.apply_url or job.url
    if flow := unsupported_flow(url):
        return flow
    if (job.fill_state == "blocked" and (job.fill_blocker or "").startswith(UNSUPPORTED_FLOW)
            and job.fill_url == url):
        return job.fill_blocker
    return None


def _outcome_cause(report: FillReport) -> OutcomeCause:
    """Classify existing statuses and boundary prefixes without changing their evidence."""
    note = (report.note or "").casefold()
    if note.startswith("closed:"):
        return "unavailable"
    if note.startswith("ineligible:"):
        return "ineligible"
    if report.status == "ready":
        return "ready"
    if report.error == APPROVAL_CHANGED:
        return "human_boundary"
    if report.error or report.status == "error":
        return "browser_error"
    if note.startswith(("login:", "captcha:", "verification:", "consent:", UNSUPPORTED_FLOW)):
        return "human_boundary"
    if note.startswith("owner:"):
        return "human_boundary"         # the owner acted in the tab while kitty filled it
    return report.cause


@dataclass
class FillReport:
    """What happened on one job's application form."""

    job_id: int
    company: str
    title: str
    url: str                  # the page that was opened
    ats: str                  # greenhouse | lever | ashby | smartrecruiters | workday | other
    resume_label: str | None  # resume uploaded (None if none / no file input)
    filled: list[str]         # human-readable field names filled, e.g. ["first name", "resume"]
    unanswered: list[str]     # required questions left blank (logged to the questions queue)
    status: str               # attempted | partial | blocked | ready | error
    error: str | None = None
    note: str | None = None
    resumed: bool = False
    pages: int = 0
    resume_id: int | None = None  # actual uploaded or previously pinned retained attachment
    cause: OutcomeCause = "unsupported_controls"
    recovery: list[dict] = field(default_factory=list)
    attempt_id: int | None = None  # the apply_attempts row this report was recorded as
    guard_released: bool = False   # the submit guard was released before the tab was handed over

    def __post_init__(self) -> None:
        self.cause = _outcome_cause(self)
        # In-memory only (never a dataclass field, so never serialized): the salt and
        # sandbox keys the watcher needs to re-read a ready tab (attempts.Capture).
        self.capture: attempts_core.Capture | None = None


@dataclass
class FormResult:
    """What fill_form did on one page (no database involved)."""

    filled: list[str] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    to_log: list[str] = field(default_factory=list)       # unanswered with no answers.yaml entry
    interaction_errors: list[str] = field(default_factory=list)
    resume_uploaded: bool = False
    fields_found: int = 0
    resume_verified: bool = False
    blocker: str | None = None
    interrupted: bool = False
    written: list[attempts_core.Written] = field(default_factory=list)  # fields set, with provenance
    failed_labels: list[str] = field(default_factory=list)  # fields whose control interaction failed
    prefilled: list[str] = field(default_factory=list)      # labels already non-blank at the first read


def choose_resume(conn: sqlite3.Connection, config: Config, job: Job) -> Resume | None:
    """The job's chosen resume, else the default resume of its first track."""
    if job.resume_id is not None:
        chosen = resumes_core.get_resume(conn, job.resume_id)
        if chosen is not None:
            return chosen
    return default_resume(conn, config, job.tracks[0]) if job.tracks else None


def resume_file(settings: Settings, resume: Resume) -> Path:
    """The resume's file on disk; stored paths are relative to the project root."""
    path = Path(resume.file_path)
    return path if path.is_absolute() else settings.root / path


class ApprovalError(UnsafeAction):
    """A reviewed web preparation no longer matches its inputs."""


APPROVAL_CHANGED = "Preparation approval changed or is missing; review the plan again before filling."


#: The effect kind of a reviewed web preparation run (kitty.core.effects); kitty.web_automation
#: registers it. Its executed effect is the run's approval.
PREPARATION_KIND = "prepare_applications"


def job_precondition(job_id: int) -> str:
    """The precondition of a prepare_applications effect binding one job."""
    return f"job:{job_id}"


def job_binding(job: Job, resume: Resume | None) -> dict:
    """A job's reviewed identity and its chosen resume's registered row, as reviewed."""
    return {
        "id": job.id, "url": job.url, "apply_url": job.apply_url, "company": job.company_name,
        "tracks": list(job.tracks),
        "resume": None if resume is None else {
            "id": resume.id, "file_hash": resume.file_hash, "file_path": resume.file_path,
        },
    }


def approved_resume(
    approval: effects.Effect, conn: sqlite3.Connection, settings: Settings, job: Job,
) -> Resume | None:
    """Re-check a job against the executed approval and return its approved resume.

    The stored (append-only) effect is the authority, not the caller's copy: it must be
    the executed prepare_applications effect with the same digest. The job's identity
    and the approved resume row must still equal the reviewed binding, and the file on
    disk must still hash to the reviewed bytes. A new default is never resolved.
    """
    try:
        stored = effects.get(conn, approval.id)
    except effects.EffectError:
        raise ApprovalError(APPROVAL_CHANGED) from None
    if stored.kind != PREPARATION_KIND or stored.state != "executed" or stored.digest != approval.digest:
        raise ApprovalError(APPROVAL_CHANGED)
    name = job_precondition(job.id)
    reviewed = stored.preconditions.get(name)
    if not isinstance(reviewed, dict):
        raise ApprovalError(APPROVAL_CHANGED)
    resume = resumes_core.get_resume(conn, reviewed["resume"]["id"]) if reviewed.get("resume") else None
    try:
        stored.require(name, job_binding(job, resume))
    except effects.Refused:
        raise ApprovalError(APPROVAL_CHANGED) from None
    if resume is None:
        return None
    try:
        path = resume_file(settings, resume)
        if not path.is_file() or resumes_core.file_hash(path) != resume.file_hash:
            raise ApprovalError(APPROVAL_CHANGED)
    except OSError:
        raise ApprovalError(APPROVAL_CHANGED) from None
    return resume


def _upload_approved(
    frame: Frame, key: str, path: Path, approval: effects.Effect, job_id: int,
    conn: sqlite3.Connection, settings: Settings,
) -> None:
    job = jobs_core.get_job(conn, job_id)
    resume = approved_resume(approval, conn, settings, job) if job is not None else None
    if resume is None:
        raise ApprovalError(APPROVAL_CHANGED)
    try:
        content = path.read_bytes()
    except OSError:
        raise ApprovalError(APPROVAL_CHANGED) from None
    if hashlib.sha256(content).hexdigest() != resume.file_hash:
        raise ApprovalError(APPROVAL_CHANGED)
    # Firefox can read its File object at the later human submit, after the worker
    # exits. Keep a private, content-addressed artifact instead of a temporary or
    # mutable library path. Never overwrite an existing approved artifact.
    folder = settings.data_dir / "approved_resumes"
    frozen = folder / f"{resume.file_hash}.pdf"
    try:
        if folder.is_symlink():
            raise ApprovalError(APPROVAL_CHANGED)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not frozen.exists() and not frozen.is_symlink():
            with tempfile.NamedTemporaryFile(dir=folder) as staged:
                staged.write(content)
                staged.flush()
                os.fchmod(staged.fileno(), 0o400)
                try:
                    os.link(staged.name, frozen)
                except FileExistsError:
                    pass
        if frozen.is_symlink() or not frozen.is_file():
            raise ApprovalError(APPROVAL_CHANGED)
        with os.fdopen(os.open(frozen, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != resume.file_hash:
            raise ApprovalError(APPROVAL_CHANGED)
    except OSError:
        raise ApprovalError(APPROVAL_CHANGED) from None
    upload(frame, key, frozen)


# ------------------------------------------------------------- the form


def _apply_plan(page_frames: list[Frame], form_field: FormField, plan: Plan) -> bool:
    """Put a planned value into one field; return True when it was set."""
    frame = page_frames[form_field.frame]
    if (MANUAL_CONSENT.search(form_field.label) or
            any(MANUAL_CONSENT.search(option) for option in form_field.options)):
        return False
    if form_field.kind in TEXT_KINDS:
        return fill_text(frame, form_field.key, plan.candidates[0])
    if form_field.kind == "combobox":
        # Prefer the actual choices before filtering them by a spelling. Async or
        # virtualized lists may need a query before they expose the desired option.
        if pick_from_list(frame, form_field.key, plan.candidates, location=plan.location,
                          graduation=plan.graduation):
            return True
        searches = [plan.search_text] if plan.search_text else list(dict.fromkeys(plan.candidates))[:4]
        return any(pick_from_search(frame, form_field.key, text, plan.candidates,
                                    location=plan.location, prefix=not plan.exact_choices,
                                    graduation=plan.graduation) for text in searches)
    if (form_field.kind == "checkbox" and len(form_field.options) == 1 and plan.from_answers
            and is_affirmative(plan.candidates[0])):
        return tick(frame, form_field.option_keys[0])          # "I agree to ..." with answer Yes
    index = match_option(plan.candidates, form_field.options, location=plan.location,
                         prefix=not plan.exact_choices, graduation=plan.graduation)
    if index is None:
        return False
    if form_field.kind == "select":
        return choose_select(frame, form_field.key, form_field.options[index])
    if form_field.kind in ("radio", "checkbox"):
        return tick(frame, form_field.option_keys[index])
    return False


def fill_form(
    page: Tab, config: Config, resume_path: Path | None, *, company: str | None = None,
    tracks: Sequence[str] = (), jurisdiction: str | None = None, preserve_existing: bool = False,
    upload_resume: Callable[[Frame, str, Path], None] | None = None,
    checkpoint: Callable[[FormResult], bool] | None = None,
) -> FormResult:
    """Fill every field on the open page that profile.yaml / answers.yaml covers.

    Nonblank fields and existing attachments are retained, including manual
    selections from an earlier attempt. Only blank fields are written.
    """
    result = FormResult()
    if checkpoint is not None and not checkpoint(result):
        result.interrupted = True
        return result
    frames, fields = wait_for_form(page)
    result.prefilled = [f.label for f in fields if f.kind not in ("file", "unsupported") and not f.blank and f.label]
    if uploads_pending(page):
        if not wait_for_uploads(page):
            result.fields_found = len(fields)
            result.blocker = "Resume upload or parsing is still pending; wait for completion before retrying."
            return result
        frames = form_frames(page)
        fields = read_fields(frames)

    target = next((f for f in fields if is_resume_input(f)), None)
    if resume_path is not None and target is not None and target.blank:
        populated = any(f.kind != "file" and not f.blank for f in fields)
        if preserve_existing and populated and not target.attachment_only:
            result.blocker = ("Resume upload deferred to protect retained answers from resume parsing; "
                              "attach it manually and review your values before retrying.")
        else:
            (upload_resume or upload)(frames[target.frame], target.key, resume_path)
            result.filled.append("resume")
            if not wait_for_uploads(page):
                result.fields_found = len(fields)
                result.blocker = "Resume upload or parsing is still pending; wait for completion before retrying."
                return result
            result.resume_uploaded = True
            frames = form_frames(page)
            fields = read_fields(frames)

    planned = []
    for form_field in fields:
        if form_field.blank:
            plan = plan_for(form_field, config, company=company, tracks=tracks,
                            jurisdiction=jurisdiction)
            if plan is not None:
                planned.append((form_field, plan))
    # Address lookups can appear before the country/state controls they depend on.
    # Fill those parents first; keep the remaining plans in their original order.
    contact_order = {"country": 0, "state": 1}
    planned.sort(key=lambda item: contact_order.get(item[1].field_name.casefold(), 2))
    failed_choices: set[tuple[int, str]] = set()
    for form_field, plan in planned:
        if checkpoint is not None and not checkpoint(result):
            result.interrupted = True
            break
        try:
            if _apply_plan(frames, form_field, plan):
                if plan.field_name not in result.filled:
                    result.filled.append(plan.field_name)
                result.written.append(attempts_core.Written(
                    form_field.label or form_field.name or plan.field_name, plan.source,
                    frame=form_field.frame, keys=form_field.option_keys or (form_field.key,)))
        except ChoiceSelectionError as exc:
            label = form_field.label or form_field.name or plan.field_name
            result.interaction_errors.append(f"{label}: {exc}")
            result.failed_labels.append(label)
            failed_choices.add((form_field.frame, form_field.key))
        except (BidiError, UnsafeAction):
            continue                      # left blank; reported below if it is required

    # Invalid saved values are diagnosed by the recovery agent, not silently retyped here.
    if checkpoint is not None and not checkpoint(result):
        result.interrupted = True
    settle(page, timeout_ms=5000)          # let uploads and dropdown lookups finish
    final = read_fields(form_frames(page))
    result.fields_found = len(final)
    for form_field in final:
        if not form_field.required or (not form_field.blank and not form_field.invalid):
            continue
        if (form_field.frame, form_field.key) in failed_choices:
            continue                      # saved answer exists; interacting with its control failed
        label = form_field.label or form_field.name or "unlabelled field"
        if form_field.kind == "file":
            result.unanswered.append(f"{label} (file upload)")
            continue
        if label not in result.unanswered:
            result.unanswered.append(label)
        if form_field.label and lookup_answer(
            config, form_field.label, company=company, tracks=tracks, jurisdiction=jurisdiction,
            options=form_field.options,
        ) is None and plan_for(
            form_field, config, company=company, tracks=tracks, jurisdiction=jurisdiction,
        ) is None:
            result.to_log.append(form_field.label)
    if uploads_pending(page):
        result.blocker = "Resume upload or parsing is still pending; wait for completion before retrying."
    result.resume_verified = not result.blocker and any(
        is_resume_input(f) and not f.blank and not f.invalid for f in final
    )
    return result


# ------------------------------------------------------------- one job


def _record(
    conn: sqlite3.Connection, job: Job, resume: Resume | None, result: FormResult
) -> None:
    """Record chosen resume and missing answers, never an application or readiness."""
    with transaction(conn):
        if result.resume_uploaded and resume is not None and job.resume_id != resume.id:
            jobs_core.set_resume(conn, job.id, resume.id)
        if job.queue_state == "new":
            jobs_core.set_queue_state(conn, job.id, "queued")
        for question in dict.fromkeys(result.to_log):
            questions_core.log_question(conn, question, job_id=job.id)


def _same_posting_page(expected_url: str, actual_url: str) -> bool:
    expected, actual = urlsplit(expected_url), urlsplit(actual_url)
    if (expected.scheme, expected.netloc.lower()) != (actual.scheme, actual.netloc.lower()):
        return False
    def detail_url(parts):
        path = re.sub(r"/apply(?:/applyManually)?/?$", "", parts.path)
        return canonical_url(parts._replace(path=path, fragment="").geturl())
    return detail_url(expected) == detail_url(actual)


def _recover_page(recovery, before, result, *, retry, absorb, verify) -> bool:
    """Diagnose, execute one guarded strategy, observe its outcome, then replan."""
    if not recovery.config.apply.recovery.enabled or recovery.handed_off:
        return False
    observation = recovery.observe(before, result)
    while True:
        verify()
        if recovery.progress:
            recovery.progress(f'recovering:{recovery.job.id}:planning')
        decision = recovery.agent.decide(observation)
        recovery.active_tool = decision.action.value
        recovery.journal.record(recovery.agent.event(decision))
        if decision.action == Action.HANDOFF:
            recovery.handed_off = True
            recovery.record(decision, RecoveryResult.HANDED_OFF)
            return False
        prior, prior_snapshot = observation, before
        try:
            recovery.guard()
            current = recovery.observe(inspect_page(recovery.page), result)
            capability = {
                Action.FOCUS: current.can_focus, Action.RECOMMIT: current.can_recommit,
                Action.RETRY: current.can_retry, Action.RELOAD: current.can_reload,
            }.get(decision.action, True)
            if (not capability or current.blocker not in (
                    Blocker.NONE, Blocker.UNKNOWN, Blocker.LOADING, Blocker.TRANSIENT
                ) or (current.busy and decision.action in (Action.FOCUS, Action.RECOMMIT, Action.RETRY))):
                recovery.handed_off = True
                recovery.record(decision, RecoveryResult.DENIED)
                return False
            if decision.action == Action.WAIT:
                if recovery.stop is not None:
                    recovery.stop.wait(decision.wait_seconds)
                else:
                    time.sleep(decision.wait_seconds)
            elif decision.action == Action.FOCUS:
                recovery.focus()
            elif decision.action == Action.RECOMMIT:
                names = recovery.recommit()
                if result is not None:
                    result.filled.extend(name for name in names if name not in result.filled)
                    absorb(result)
            elif decision.action == Action.RETRY:
                result = retry()
                absorb(result)
            elif decision.action == Action.RELOAD:
                recovery.page.goto(recovery.page.main.url)
            before = inspect_page(recovery.page)
            observation = recovery.observe(before, result)
            # API success, DOM churn and a nonempty write log are not proof that
            # the form accepted anything. Require fresh state improvement.
            improved = (
                observation.required_count < prior.required_count or
                observation.invalid_count < prior.invalid_count or
                (prior.busy and not observation.busy) or
                (bool(before.step) and before.step != prior_snapshot.step) or
                (bool(before.entry) and before.entry != prior_snapshot.entry)
            )
            focus_restored = decision.action == Action.FOCUS and prior.can_focus and not observation.can_focus
            outcome = (RecoveryResult.OBSERVED if decision.action == Action.INSPECT else
                       RecoveryResult.COMPLETED if improved or focus_restored else RecoveryResult.NO_EFFECT)
            recovery.record(decision, outcome)
            if improved:
                recovery.refresh_progress()
                return True
        except PreparationStopped:
            recovery.record(decision, RecoveryResult.CANCELLED)
            raise
        except UnsafeAction:
            recovery.record(decision, RecoveryResult.DENIED)
            raise
        except BidiError as exc:
            recovery.record(decision, RecoveryResult.FAILED)
            if isinstance(exc, (ConnectionLost, TabClosed)):
                raise
            # Reobserve a stale/covered control; never replay it blindly.
            before = inspect_page(recovery.page)
            observation = recovery.observe(before, result)


def fill_job(
    page: Tab, conn: sqlite3.Connection, settings: Settings, config: Config, job: Job,
    *, resume_tab: bool = False, approval: effects.Effect | None = None,
    eligibility_override: EligibilityOverride | None = None,
    recovery_planner=None, progress=None, stop=None, run: attempts_core.Run | None = None,
) -> FillReport:
    """Prepare through review, never submit; excluded jobs leave the browser untouched.

    Every browser attempt (not compatibility research) appends an apply_attempts row
    with its stage, typed cause and written fields' provenance; a ready one also gets
    its value hashes and the watcher's Submit capture (kitty.core.apply.attempts).
    """
    if eligibility_override is not None and (
        eligibility_override is not EligibilityOverride.COMPATIBILITY_RESEARCH or approval is not None
    ):
        raise ApprovalError(APPROVAL_CHANGED)
    if blocker := preparation_blocker(job, eligibility_override=eligibility_override):
        return _excluded_report(conn, job, blocker, run)
    research = job.queue_state == "not_eligible" or job.eligibility == "ineligible"
    jurisdiction = authorization_jurisdiction(job.location, job.country)
    ats = detect_ats(job.apply_url or job.url)
    url = form_url(job, ats)
    report = FillReport(job_id=job.id, company=job.company_name, title=job.title, url=url,
                        ats=ats, resume_label=None, filled=[], unanswered=[], status="attempted",
                        resumed=resume_tab, resume_id=job.resume_id if resume_tab else None)
    try:
        resume = (approved_resume(approval, conn, settings, job) if approval is not None
                  else choose_resume(conn, config, job))
    except ApprovalError as exc:
        return _error_report(job.id, job, str(exc))
    resume_path = resume_file(settings, resume) if resume is not None else None
    if resume_path is not None and not resume_path.is_file():
        if approval is not None:
            return _error_report(job.id, job, APPROVAL_CHANGED)
        resume_path = None
    record_attempt(conn, job.id, "attempted", context_id=page.context, url=page.bidi.redact(url))
    attempt = attempts_core.Progress()
    written: list[attempts_core.Written] = []
    failing: list[str] = []
    earlier = attempts_core.written_labels(conn, job.id) if resume_tab else set()
    owner_inputs: int | None = None
    resume_verified = False
    visited: set[str] = set()
    previous: str | None = None
    authentication_attempted = False
    verification_attempts = 0
    credentials = None
    closed_url: str | None = None
    policy = config.apply.authentication
    # A granted ATS-family tenant (accounts.tenant_for): its own credentials and policy.
    tenant: accounts_core.Tenant | None = None
    foreground: Tab | None = None
    recovery = None

    def routed(destination: str) -> None:
        """The form now lives at `destination` (a resolved redirect or a routed Apply link)."""
        nonlocal url, ats
        url, ats = destination, detect_ats(destination)
        report.url, report.ats = destination, ats
        # A reviewed plan's identity includes the job's URL; research changes nothing.
        if approval is None and not research:
            resolve_core.store_url(conn, job.id, destination, source_url=job.apply_url or job.url)

    def absorb(result):
        nonlocal resume_verified, owner_inputs
        # Account values can be echoed in field labels/errors; never persist them.
        result.filled = [page.bidi.redact(value) for value in result.filled]
        result.unanswered = [page.bidi.redact(value) for value in result.unanswered]
        result.to_log = [value for value in result.to_log if page.bidi.redact(value) == value]
        result.interaction_errors = [page.bidi.redact(value) for value in result.interaction_errors]
        _record(conn, job, resume, result)
        if owner_inputs is None:
            # Values already on a resumed tab that no earlier attempt wrote were the owner's.
            owner_inputs = len({page.bidi.redact(label) for label in result.prefilled} - earlier
                               - {item.label for item in written}) if resume_tab else 0
        for item in result.written:
            item = attempts_core.Written(page.bidi.redact(item.label), item.provenance, item.frame, item.keys)
            if not any(old.frame == item.frame and old.keys == item.keys and
                       (item.keys or old.label == item.label) for old in written):
                written.append(item)
        failing.extend(page.bidi.redact(label) for label in result.failed_labels)
        report.filled.extend(name for name in result.filled if name not in report.filled)
        report.unanswered = result.unanswered
        resume_verified = resume_verified or result.resume_verified
        if result.resume_uploaded and resume:
            report.resume_label, report.resume_id = resume.label, resume.id
        elif result.resume_verified and report.resume_label is None:
            report.resume_label = "existing attachment (retained)"

    def verify():
        # The per-step checkpoint reads the submit guard's veto log first: the
        # owner acting in this tab, or a blocked submit, stops the fill.
        stop_for(page.new_vetoes())
        if approval is not None:
            current = jobs_core.get_job(conn, job.id)
            if current is None:
                raise ApprovalError(APPROVAL_CHANGED)
            approved_resume(approval, conn, settings, current)
        recovery.checkpoint()

    def granted() -> str | None:
        """Adopt the granted tenant at the current page (policy and credentials), or a handoff."""
        nonlocal tenant, policy
        if research:
            return None
        from kitty.config import _authentication_origin
        if tenant is not None and tenant.origin == _authentication_origin(page.main.url):
            return None
        try:
            found = accounts_core.tenant_for(conn, settings, config, page.main.url)
        except accounts_core.AccountError as exc:
            return str(exc)
        if found is not None:
            tenant, policy = found, found.policy
        return None

    def verify_email(*, started=None, sent=False):
        nonlocal credentials, authentication_attempted, verification_attempts
        if verification_attempts >= 2:
            report.status, report.note = "blocked", "verification: repeated email challenges need your review."
            return False
        verification_attempts += 1
        attempt.reach("verify")
        verify()
        if tenant is not None:
            credentials = tenant.credentials
        else:
            try:
                credentials = load_credentials(settings)
            except CredentialError as exc:
                report.status, report.note = "blocked", str(exc)
                return False
            if policy._credential_binding is not None and credential_binding(credentials) != policy._credential_binding:
                raise ApprovalError(APPROVAL_CHANGED)
        if progress:
            progress(f'verifying:{job.id}')
        result = complete_email_verification(
            page, conn, job.id, policy, credentials, requested_at=started,
            request_sent=sent, checkpoint=verify,
            accepted_navigation=recovery.accepted_navigation,
        )
        recovery.mutated = recovery.mutated or result.acted
        if not result.verified and result.blocker is None and not result.acted:
            # The challenge was no longer present (transition snapshot); re-inspect.
            verification_attempts -= 1
            time.sleep(0.5)
            return True
        if result.verified:
            if "employer email verification" not in report.filled:
                report.filled.append("employer email verification")
                written.append(attempts_core.Written("employer email verification", "account"))
            if tenant is not None and tenant.account.state in ("unknown", "created"):
                accounts_core.set_state(conn, tenant.origin, "verified", event="verified")
            authentication_attempted = False
            recovery.accepted_navigation()
            return True
        report.status, report.note = "blocked", result.blocker
        return False

    def rescue(snapshot, result=None):
        try:
            return _recover_page(
                recovery, snapshot, result, absorb=absorb, verify=verify,
                retry=lambda: fill_form(
                    page, config, None, company=job.company_name, tracks=job.tracks,
                    jurisdiction=jurisdiction, preserve_existing=True, checkpoint=recovery.checkpoint,
                ),
            )
        finally:
            recovery.active_tool = None

    try:
        # Firefox can silently drop dropdown pointer/key actions in a background
        # tab even though script-based contact writes work. Focus the target for
        # the entire production flow, not just a standalone choice helper.
        contexts = page.bidi.command("browsingContext.getTree", {"maxDepth": 0})["contexts"]
        window = next((node.get("clientWindow") for node in contexts
                       if node["context"] == page.context), None)
        for node in contexts:
            if window is None or node["context"] == page.context or node.get("clientWindow") != window:
                continue
            candidate = Tab(page.bidi, node["context"])
            try:
                if candidate.main.call("() => document.visibilityState === 'visible'"):
                    foreground = candidate
                    break
            except BidiError:
                continue
        page.activate()
        # From here until the finally below, the submit guard's in-page veto is
        # armed in this tab (new documents too): nothing kitty or the page does
        # can activate a submit without kitty's one-shot token (guard.py).
        page.arm()
        if not resume_tab:
            page.goto(url)
        settle(page)
        if not resume_tab and redirect_source(url):
            # A JavaScript redirect (appcast) can leave after the load settles.
            deadline = time.monotonic() + 5
            while redirect_source(page.main.url) and time.monotonic() < deadline:
                time.sleep(0.25)
            landed = page.main.url
            if (redirect_source(landed) is None and urlsplit(landed).scheme == "https"
                    and (unsupported_flow(landed) or names_posting(landed))):
                # An opaque redirect resolved by loading it (appcast's JavaScript redirect too).
                routed(clean_url(landed))
                if flow := unsupported_flow(url):
                    raise _EntryHandoff(flow)
        recovery = JobRecovery(conn, settings, config, job, page, jurisdiction=jurisdiction,
                               planner=recovery_planner, progress=progress, stop=stop)
        for _ in range(12):
            verify()
            before = wait_for_page(page, previous=previous)
            if authentication_attempted and before.blocker and before.blocker.startswith("login:"):
                # Account redirects can render their verification notice after
                # the sign-in controls. Observe it without repeating credentials.
                before = wait_for_page(page, previous=before.signature, seconds=2)
            if authentication_attempted and not policy.approves(page.main.url):
                report.status, report.note = "blocked", "login: redirected to an unapproved origin; continue manually."
                break
            if not (before.blocker or "").startswith("closed:"):
                attempt.reach("entry")
            if (before.blocker or "").startswith("verification:"):
                attempt.reach("verify")
            family = None if research else accounts_core.granted_family(conn, page.main.url)
            if family is not None and (
                    (before.blocker or "").startswith(("verification:", "login:"))
                    or authentication_pending(page) or (family == "eightfold" and residence_gate(page))):
                # The owner's family grant covers this tenant: its own account policy.
                if handoff := granted():
                    report.status, report.note = "blocked", handoff
                    break
            if (not research and policy.enabled and policy.email_verification.enabled
                    and before.blocker and before.blocker.startswith("verification:")):
                if verify_email():
                    visited.discard(before.signature)
                    continue
                break
            if not research and policy.enabled and residence_gate(page):
                # Eightfold's pre-account country/privacy gate precedes any
                # sign-in form; its consent check is dialog-shaped, so handle
                # it before the generic consent/unsupported classification.
                if complete_residence_gate(page, config, allow_terms=policy.allow_account_creation):
                    report.filled.append("country of residence")
                    written.append(attempts_core.Written("country of residence", "profile"))
                    recovery.mutated = True
                    visited.discard(before.signature)
                    settle(page)
                    continue
                report.status, report.note = "blocked", (
                    "consent: the Eightfold country/privacy step needs your review before the account form")
                report.cause = "human_boundary"
                break
            account_stage = bool(before.blocker and before.blocker.startswith("login:")) or authentication_pending(page)
            if account_stage:
                attempt.reach("account")
            if account_stage and (not policy.enabled or research):
                report.status, report.note = "blocked", "login: account access is not authorized for this run."
                break
            if account_stage and policy.enabled and not research:
                if authentication_attempted:
                    report.status, report.note = "blocked", "login: authentication did not advance; continue manually (no automatic retry)."
                    break
                if not policy.approves(page.main.url) or not policy.approves(before.url):
                    report.status, report.note = "blocked", "login: this origin is not approved for credentials."
                    break
                from kitty.config import _authentication_origin
                if _authentication_origin(page.main.url) in policy.manual_account_origins:
                    report.status, report.note = "blocked", "login: this saved account requires manual sign-in; existing sessions can be reused."
                    break
                if progress:
                    progress(f'authenticating:{job.id}')
                authentication_started = datetime.now(timezone.utc).replace(microsecond=0)
                authentication_attempted = True
                if policy.account_mode == "google":
                    authentication = google_session(page, policy, config.profile.email.strip())
                elif tenant is not None:
                    def account_checkpoint():
                        # Account pages are guarded auth transactions: a navigation that
                        # stays inside the tenant's approval becomes the owned page.
                        if policy.approves(page.main.url):
                            recovery.accepted_navigation()
                        verify()

                    stage = AccountStage(page, conn, settings, tenant, job_id=job.id,
                                         application_url=job.apply_url or job.url, checkpoint=account_checkpoint)
                    try:
                        authentication = stage.run()
                    except accounts_core.AccountError as exc:
                        authentication = AuthResult(blocker=str(exc))
                    tenant = stage.tenant
                    credentials = tenant.credentials
                else:
                    try:
                        credentials = load_credentials(settings)
                    except CredentialError as exc:
                        report.status, report.note = "blocked", str(exc)
                        break
                    page.bidi.protect_secrets(credentials.email, credentials.password)
                    authentication = authenticate(page, policy, credentials,
                                                  application_url=job.apply_url or job.url,
                                                  memory=accounts_core.Memory(conn, settings))
                report.filled.extend(_ACCOUNT_FIELD_LABELS[role] for role in authentication.filled)
                written.extend(attempts_core.Written(_ACCOUNT_FIELD_LABELS[role], "account")
                               for role in authentication.filled)
                recovery.mutated = recovery.mutated or bool(authentication.filled) or authentication.acted
                if (policy.email_verification.enabled and authentication.blocker
                        and authentication.blocker.startswith("verification:")):
                    if verify_email(started=authentication_started,
                                    sent=authentication.acted and "email" in authentication.filled):
                        visited.discard(before.signature)
                        continue
                    break
                if authentication.blocker and residence_gate(page):
                    # Eightfold asks for the country of residence before its
                    # account form. No password has been submitted yet, so the
                    # account stage may run once more after this single fact.
                    if complete_residence_gate(page, config, allow_terms=policy.allow_account_creation):
                        report.filled.append("country of residence")
                        written.append(attempts_core.Written("country of residence", "profile"))
                        recovery.mutated = True
                        if "password" not in authentication.filled:
                            authentication_attempted = False
                        visited.discard(before.signature)
                        settle(page)
                        continue
                if authentication.blocker or not authentication.acted:
                    report.status = "partial" if authentication.filled and not authentication.acted else "blocked"
                    report.note = authentication.blocker or before.blocker
                    break
                previous = before.signature
                settle(page)
                recovery.accepted_navigation()
                continue
            if before.blocker:
                if before.blocker.startswith("loading:") and rescue(before):
                    visited.discard(before.signature)
                    continue
                if before.blocker.startswith("closed:"):
                    closed_url = before.url
                report.status, report.note = "blocked", before.blocker
                break
            if before.signature in visited:
                if rescue(before):
                    visited.discard(before.signature)
                    continue
                report.status, report.note = "blocked", "Application step did not advance or repeated; continue manually."
                break
            visited.add(before.signature)
            previous = before.signature
            if before.entry == "apply-link":
                # A custom site's Apply link, routed by its destination (inspect_page
                # keeps only recognized families and excluded flows). A GET navigation.
                destination, flow = route_apply_link(before.url, before.entry_url)
                if flow:
                    report.status, report.note = "blocked", flow
                    break
                page.goto(destination)
                routed(destination)
                settle(page)
                recovery.accepted_navigation()
                continue
            if before.entry:
                advance(before, ats)
                settle(page)
                recovery.accepted_navigation()
                continue
            if not research and (config.profile.education_records or config.profile.employment_records):
                record = fill_required_records(page, config, company=job.company_name,
                                               tracks=job.tracks, checkpoint=verify)
                report.filled.extend(name for name in record.filled if name not in report.filled)
                written.extend(attempts_core.Written(name, "profile") for name in record.filled)
                if record.changed:
                    recovery.mutated = True
                    recovery.refresh_progress()
                    visited.discard(before.signature)
                    continue
                if record.handled and record.blocker:
                    report.status, report.note = "blocked", record.blocker
                    report.cause = record.cause
                    break
            if not (before.step or before.boundary):
                if rescue(before):
                    visited.discard(before.signature)
                    continue
                report.status, report.note = "blocked", "Unsupported application page: no recognized form or safe entry; continue manually."
                break
            result = fill_form(
                page, config, resume_path, company=job.company_name,
                tracks=job.tracks, jurisdiction=jurisdiction, preserve_existing=resume_tab,
                upload_resume=(lambda frame, key, path: _upload_approved(
                    frame, key, path, approval, job.id, conn, settings
                )) if approval is not None else None,
                checkpoint=recovery.checkpoint,
            )
            absorb(result)
            attempt.page(page.bidi.redact(before.step) if before.step else None)
            report.pages += 1
            if result.interrupted and rescue(inspect_page(page), result):
                visited.discard(before.signature)
                continue
            if result.interaction_errors:
                if rescue(inspect_page(page), result):
                    visited.discard(before.signature)
                    continue
                report.status = "partial"
                report.note = "Selection interaction failed: " + "; ".join(result.interaction_errors)
                report.cause = "unsupported_controls"
                if result.blocker:
                    report.note += "\n" + result.blocker
                break
            if result.blocker:
                if rescue(inspect_page(page), result):
                    visited.discard(before.signature)
                    continue
                report.status, report.note = "blocked", result.blocker
                break
            after = inspect_page(page)
            if after.blocker:
                if after.blocker.startswith("loading:") and rescue(after, result):
                    visited.discard(before.signature)
                    continue
                if after.blocker.startswith("closed:"):
                    closed_url = after.url
                report.status, report.note = "blocked", after.blocker
                break
            if before.step == "Create profile" and not research:
                # Eightfold's candidate profile (not the job application): set its
                # unlabelled residence chooser and required privacy acknowledgment
                # last, since list interactions above can reset them, then create it.
                if prepare_profile_bootstrap(page, config, allow_terms=policy.allow_account_creation):
                    if "country of residence" not in report.filled:
                        report.filled.append("country of residence")
                        written.append(attempts_core.Written("country of residence", "profile"))
                if submit_profile_bootstrap(page, config, allow_terms=policy.allow_account_creation):
                    report.pages += 1
                    recovery.mutated = True
                    settle(page)
                    recovery.accepted_navigation()
                    visited.discard(before.signature)
                    continue
                report.status, report.note = "partial", (
                    "Eightfold profile step still needs required answers: " + "; ".join(result.unanswered)
                    if result.unanswered else "Eightfold profile step could not be completed; review it.")
                report.cause = "missing_applicant_fact" if result.to_log else "unsupported_controls"
                break
            if after.unsupported_required:
                if rescue(after, result):
                    visited.discard(before.signature)
                    continue
                report.status = "blocked"
                report.cause = "unsupported_controls"
                if after.missing_profile_sections:
                    report.note = (
                        "unsupported: required profile records need manual completion: " +
                        ", ".join(after.missing_profile_sections) +
                        ". Open each section's Add editor, enter and save your records, then resume. "
                        "Review any other required controls and fields already filled."
                    )
                else:
                    report.note = "unsupported: required custom controls need manual completion. Review any fields already filled."
                break
            if result.unanswered or after.errors:
                if rescue(after, result):
                    visited.discard(before.signature)
                    continue
                report.status = "partial" if result.fields_found else "blocked"
                report.note = ("Required answers or uploads are missing or invalid: " +
                               "; ".join(result.unanswered)) if result.unanswered else "Page validation errors need your review."
                report.cause = "missing_applicant_fact" if result.to_log else "unsupported_controls"
                break
            if after.review or after.boundary:
                attempt.reach("review")
                if any(f.invalid or (f.required and f.blank) for f in read_fields(form_frames(page))):
                    if rescue(after, result):
                        visited.discard(before.signature)
                        continue
                    report.status, report.note = "blocked", "Required application fields still need review."
                    break
                # A fresh review-only page cannot prove earlier required steps.
                if not resume_verified:
                    report.status, report.note = "blocked", "Resume upload is not verified; review or attach your resume manually."
                elif not result.fields_found and report.pages == 1:
                    report.status, report.note = "blocked", "Earlier required steps cannot be verified from this review-only page."
                else:
                    report.status = "ready"
                break
            if ats in ("workday", "smartrecruiters") and after.safe_next:
                if not result.fields_found:
                    report.status, report.note = "blocked", "Unsupported application step: no verifiable fields; continue manually."
                    break
                advance(after, ats)
                settle(page)
                recovery.accepted_navigation()
                continue
            if after.step and after.signature != before.signature:
                # Filling moved to another recognized step (e.g. a resume import
                # opening its profile form); inspect it rather than give up.
                previous = before.signature
                continue
            report.status = "partial" if report.filled else "blocked"
            report.note = "Unsupported application page: no recognized review boundary or safe next step; continue manually."
            break
        else:
            report.status, report.note = "blocked", "Application step limit reached; continue manually."
    except SubmitGuardStop as exc:
        report.status, report.note = "blocked", str(exc)
    except PreparationStopped as exc:
        report.status, report.note = "blocked", str(exc)
    except OSError:
        report.status = "blocked"
        report.note = "Recovery evidence could not be saved; review local storage before resuming. The application tab is retained."
    except TabClosed:
        report.status = "blocked"
        report.error = report.note = (
            "The application tab closed or was replaced. Continue filling to reopen this job; "
            "unsaved answers may need re-entry."
        )
    except (BidiError, UnsafeAction) as exc:
        report.status = "blocked"
        report.error = page.bidi.redact(str(exc)).strip().splitlines()[0][:300]
        report.note = report.error
    finally:
        # Disarm the submit guard before anything can call this tab ready or
        # hand it over (N3 §7); a veto it logged after the last checkpoint
        # (the owner pressing Submit at the end) still decides the outcome.
        page.release()
        report.guard_released = True
        try:
            stop_for(page.new_vetoes())
        except SubmitGuardStop as exc:
            # A stop already recorded keeps its own cause; a late veto only
            # overrides an outcome that would otherwise hand the tab over as fine.
            # The owner whose own Submit was blocked is still told to press it again.
            if report.status != "blocked":
                report.status, report.note = "blocked", str(exc)
            elif isinstance(exc, OwnerTookOver) and OWNER_HINT not in (report.note or ""):
                report.note = f"{report.note} {OWNER_HINT}" if report.note else str(exc)
        if foreground is not None:
            try:
                contexts = page.bidi.command("browsingContext.getTree", {"maxDepth": 0})["contexts"]
                same_window = {node["context"] for node in contexts
                               if node.get("clientWindow") == window}
                if ({page.context, foreground.context} <= same_window and
                        page.main.call("() => document.visibilityState === 'visible'")):
                    foreground.activate()
            except BidiError:
                pass                    # closed/moved tabs or a new user selection win
    try:
        current_url = page.main.call("""(key, context) => {
          try { sessionStorage.setItem(key, context); } catch {}
          return location.href;
        }""", CONTEXT_MARKER, page.context)
    except BidiError:
        current_url = url
    report.url = page.bidi.redact(report.url)
    report.note = page.bidi.redact(report.note) if report.note else None
    if (closed_url and _same_posting_page(url, closed_url)
            and _same_posting_page(url, current_url)):
        with transaction(conn):
            persist_check(conn, job.id, PostingCheck(
                "closed", "Employer posting page explicitly reports this posting unavailable",
                closed_url,
            ))
    if research and report.status != "error" and not (report.note or "").startswith("closed:"):
        detail = report.note
        report.status = "blocked"
        report.note = ("ineligible: Compatibility research only; " +
                       (job.eligibility_reason or "marked not eligible") +
                       (f"\n{detail}" if detail else ""))
    report.cause = _outcome_cause(report)
    if recovery is not None:
        try:
            recovery.journal.outcome(report.cause, report.status == "ready")
        except OSError:
            report.status, report.cause = "blocked", "unsupported_controls"
            report.note = "Recovery evidence could not be saved; review local storage before resuming."
        report.recovery = recovery.journal.events
    record_attempt(conn, job.id, report.status, blocker=report.note if report.status != "ready" else None,
                   context_id=page.context, url=page.bidi.redact(current_url))
    if not research:
        capture, hashes = None, []
        if report.status == "ready":
            try:
                capture, hashes = attempts_core.release(page, written)
            except BidiError:
                capture, hashes = None, []
        report.attempt_id = attempts_core.record(
            conn, job_id=job.id, ats=ats, url=url, progress=attempt, status=report.status,
            note=report.note, error=report.error, outcome_cause=report.cause, run=run,
            failing=[*report.unanswered, *failing], owner_inputs=owner_inputs or 0,
            written=written, hashes=hashes,
        )
        report.capture = capture
    with transaction(conn):
        questions_core.refresh_answered_flags(conn, config)
    if job.fill_context_id and not resume_tab:
        restart = "Previous tab unavailable; restarted known fields. Manual values are not restored."
        report.note = f"{report.note} {restart}" if report.note else restart
    return report


# ------------------------------------------------------------ many jobs


def _error_report(job_id: int, job: Job | None, message: str) -> FillReport:
    """A report for a job the filler could not even open."""
    return FillReport(
        job_id=job_id, company=job.company_name if job else "?", title=job.title if job else "?",
        url=(job.apply_url or job.url) if job else "", ats=detect_ats(job.url) if job else "other",
        resume_label=None, filled=[], unanswered=[], status="error", error=message,
    )


def _blocked_report(job: Job, message: str) -> FillReport:
    return FillReport(
        job_id=job.id, company=job.company_name, title=job.title,
        url=job.apply_url or job.url, ats=detect_ats(job.apply_url or job.url),
        resume_label=None, filled=[], unanswered=[], status="blocked", note=message,
    )


def _excluded_report(conn: sqlite3.Connection, job: Job, message: str, run: attempts_core.Run | None) -> FillReport:
    """A local exclusion before any browser access.

    A closed posting, and an explicitly excluded flow (it stays in the URR
    denominator with zero credit), are also live-check attempts.
    """
    report = _blocked_report(job, message)
    if report.cause == "unavailable" or message.startswith(UNSUPPORTED_FLOW):
        report.attempt_id = attempts_core.record(
            conn, job_id=job.id, ats=report.ats, url=report.url, progress=attempts_core.Progress(),
            status=report.status, note=report.note, error=None, outcome_cause=report.cause, run=run,
        )
    return report


def _watched_tab(job: Job, tab: Tab, report: FillReport) -> WatchedTab:
    """Keep the attachment identity; a later default change cannot rewrite history."""
    return WatchedTab(
        job_id=job.id, context=tab.context, ats=report.ats, form_url=report.url,
        label=f"{job.company_name} - {job.title}", resume_id=report.resume_id,
        track=job.tracks[0] if job.tracks else None,
        marker=tab.context, attempt_id=report.attempt_id, capture=report.capture,
    )


def _pause(seconds: float, watch: Watch | None) -> None:
    """Wait between two jobs; a set stop flag ends the wait at once."""
    if watch is not None:
        watch.stop.wait(seconds)
    else:
        time.sleep(seconds)


def _retained_tab(bidi: Bidi, job: Job) -> Tab | None:
    """Match the owned tab across BiDi sessions, then verify its application URL."""
    from urllib.parse import parse_qs, unquote, urljoin, urlsplit
    from kitty.config import _authentication_origin
    from kitty.core.apply.watch import posting_key

    if not job.fill_context_id or not job.fill_url:
        return None
    contexts = bidi.command("browsingContext.getTree", {}).get("contexts", [])
    origins = {_authentication_origin(job.fill_url), _authentication_origin(job.apply_url or job.url)}
    origins.discard(None)
    context = owned_context(bidi, contexts, job.fill_context_id, origins)
    if context is None:
        return None
    old, current = urlsplit(job.fill_url), urlsplit(context.get("url", ""))
    if (old.scheme, old.netloc, old.path, old.query) == (current.scheme, current.netloc, current.path, current.query):
        return Tab(bidi, context["context"])
    # SSO may change a path, but a retained tab can also have been used to
    # browse another posting. Require the original posting's identity.
    key = posting_key(job.url) or posting_key(job.apply_url or "")
    if (job.fill_blocker and job.fill_blocker.startswith(("login:", "consent:", "verification:", "captcha:")) and
            key is not None and posting_key(context.get("url", "")) == key and
            _authentication_origin(context.get("url", "")) ==
            _authentication_origin(job.apply_url or job.url) and
            _authentication_origin(context.get("url", "")) is not None):
        return Tab(bidi, context["context"])
    # Oracle keeps the job in its path after email verification and section changes.
    oracle_url = job.apply_url or job.url
    oracle_source = urlsplit(oracle_url)
    oracle_posting = re.fullmatch(
        r"(/hcmUI/CandidateExperience/[^/]+/sites/[^/]+/job/\d+)"
        r"(?:/apply(?:/(?:email|section/\d+))?)?/?",
        unquote(oracle_source.path),
    )
    if (detect_ats(oracle_url) == "oracle" and oracle_posting and
            (old.scheme, old.netloc) == (current.scheme, current.netloc) ==
            (oracle_source.scheme, oracle_source.netloc) and
            re.fullmatch(re.escape(oracle_posting[1]) + r"/apply/(?:email|section/\d+)/?",
                         unquote(current.path))):
        return Tab(bidi, context["context"])
    # Workday steps can change path without changing this retained application.
    source = urlsplit(job.url)
    source_path = unquote(source.path).split("/")
    if "apply" in source_path:
        source_path = source_path[:source_path.index("apply")]
    posting = source_path[-1] if source_path else ""
    if (detect_ats(job.url) == "workday" and "job" in source_path and posting and
            (old.scheme, old.netloc) == (current.scheme, current.netloc) ==
            (source.scheme, source.netloc) and
            posting in unquote(current.path).split("/")):
        return Tab(bidi, context["context"])
    if (detect_ats(job.url) == "workday" and current.path.rstrip("/").endswith("/login") and
            (current.scheme, current.netloc) == (source.scheme, source.netloc) and
            job.fill_blocker and job.fill_blocker.startswith(("login:", "verification:", "consent:"))):
        redirects = parse_qs(current.query, keep_blank_values=True).get("redirect", [])
        if len(redirects) == 1:
            destination = urlsplit(urljoin(context["url"], redirects[0]))
            parts = unquote(destination.path).split("/")
            if "apply" in parts:
                parts = parts[:parts.index("apply")]
            if ((destination.scheme, destination.netloc) == (source.scheme, source.netloc) and
                    "job" in parts and parts[-1] == posting):
                return Tab(bidi, context["context"])
    return None


def _fill_all(
    bidi: Bidi, conn: sqlite3.Connection, settings: Settings, config: Config,
    job_ids: list[int], say: Callable[[str], None], *, keep_open: bool,
    watch: Watch | None = None, background: bool = False, pause: float = PAUSE_BETWEEN_JOBS,
    approval: effects.Effect | None = None,
    eligibility_override: EligibilityOverride | None = None,
    recovery_planner=None, on_report=None, stop=None, run: attempts_core.Run | None = None,
) -> list[FillReport]:
    """Fill each job in its own new tab, pausing `pause` seconds between jobs.

    Kept-open tabs on a board the watcher knows are added to `watch.tabs`; a
    set stop flag ends the run before the next job. `background` tabs open
    behind the current one and the first is not brought forward afterwards.
    """
    if eligibility_override is not None and (
        eligibility_override is not EligibilityOverride.COMPATIBILITY_RESEARCH or approval is not None
    ):
        raise ApprovalError(APPROVAL_CHANGED)
    reports: list[FillReport] = []
    first_tab: Tab | None = None
    def publish():
        if on_report is not None:
            on_report(reports)

    for position, job_id in enumerate(job_ids):
        if position:
            _pause(pause, watch)
        if (stop is not None and stop.is_set()) or (watch is not None and watch.stop.is_set()):
            break
        job = jobs_core.get_job(conn, job_id)
        if job is None:
            reports.append(_error_report(job_id, None, f"no job with id {job_id}"))
            publish()
            continue
        if blocker := preparation_blocker(job, eligibility_override=eligibility_override):
            reports.append(_excluded_report(conn, job, blocker, run))
            publish()
            continue
        if approval is not None:
            try:
                approved_resume(approval, conn, settings, job)
            except ApprovalError as exc:
                reports.append(_error_report(job_id, job, str(exc)))
                publish()
                continue
        say(f"[{position + 1}/{len(job_ids)}] {job.company_name} - {job.title}")
        opened = attempts_core.Progress()
        try:
            retained = _retained_tab(bidi, job)
            tab = retained or open_tab(bidi, background=background)
            first_tab = first_tab or tab
            report = fill_job(tab, conn, settings, config, job, resume_tab=retained is not None,
                              approval=approval, eligibility_override=eligibility_override,
                              recovery_planner=recovery_planner, progress=say,
                              stop=stop if stop is not None else watch.stop if watch is not None else None,
                              run=run)
            reports.append(report)
            if watch is not None:
                watch.tabs[:] = [old for old in watch.tabs if old.context != tab.context and old.job_id != job.id]
            if not keep_open:
                tab.close()
            elif (watch is not None and report.ats in RULES
                  and report.cause not in ("unavailable", "ineligible")):
                watch.tabs.append(_watched_tab(job, tab, report))
        except BidiError as exc:
            message = bidi.redact(str(exc)).strip().splitlines()[0][:300]
            # An unavailable later tab must not erase reports already prepared.
            # If cleanup failed after reporting this job, keep its actual result.
            if not reports or reports[-1].job_id != job_id:
                report = _error_report(job_id, job, message)
                report.attempt_id = attempts_core.record(
                    conn, job_id=job.id, ats=report.ats, url=report.url, progress=opened,
                    status=report.status, note=None, error=message, outcome_cause=report.cause, run=run,
                )
                reports.append(report)
            say(f"Browser stopped this job: {message}")
            if isinstance(exc, ConnectionLost):
                for remaining_id in job_ids[position + 1:]:
                    remaining = jobs_core.get_job(conn, remaining_id)
                    blocker = preparation_blocker(remaining, eligibility_override=eligibility_override) if remaining else None
                    reports.append(_excluded_report(conn, remaining, blocker, run) if blocker else _error_report(
                        remaining_id, remaining,
                        "Browser disconnected before this job could be prepared; reconnect Firefox and review a new plan.",
                    ))
                publish()
                break
        publish()
    if first_tab is not None and keep_open and not background:
        try:
            first_tab.activate()
        except BidiError as exc:
            say(f"Could not focus the prepared tab: {bidi.redact(str(exc))[:300]}")
    return reports


def _fill_and_watch(
    bidi: Bidi, conn: sqlite3.Connection, settings: Settings, config: Config,
    job_ids: list[int], say: Callable[[str], None], *, keep_open: bool,
    on_filled: Callable[[list[FillReport]], None], watch: Watch | None,
    background: bool = False, pause: float = PAUSE_BETWEEN_JOBS,
    approval: effects.Effect | None = None,
    eligibility_override: EligibilityOverride | None = None,
    recovery_planner=None, on_report=None, stop=None, run: attempts_core.Run | None = None,
) -> list[FillReport]:
    """Fill, hand the reports over, then (with a Watch) watch the tabs in the same session.

    Tabs carried over in `watch.tabs` are found again first (their ids are per session).
    """
    emit = say
    say = lambda message: emit(bidi.redact(message))
    if watch is not None and watch.tabs:
        watch.tabs[:] = reattach(bidi, watch.tabs)
    reports = _fill_all(bidi, conn, settings, config, job_ids, say, keep_open=keep_open,
                        watch=watch, background=background, pause=pause, approval=approval,
                        eligibility_override=eligibility_override, recovery_planner=recovery_planner,
                        on_report=on_report, stop=stop, run=run)
    on_filled(reports)
    if watch is None or not watch.tabs or watch.stop.is_set():
        return reports
    watch.on_start(len(watch.tabs))
    try:
        watch_tabs(lambda tab: look(bidi, tab), conn, config, watch, say,
                   observe=lambda tab, confirmed: attempts_core.observe(conn, bidi, tab, confirmed=confirmed))
    except BidiError as exc:              # Firefox quit or the connection broke
        say(f"stopped watching: {str(exc).strip().splitlines()[0][:200]}")
    return reports


def fill_jobs(
    settings: Settings,
    config: Config,
    job_ids: list[int],
    *,
    progress: Callable[[str], None] | None = None,
    headless: bool = False,
    on_filled: Callable[[list[FillReport]], None] | None = None,
    on_report: Callable[[list[FillReport]], None] | None = None,
    watch: Watch | None = None,
    background: bool = False,
    pause: float = PAUSE_BETWEEN_JOBS,
    approval: effects.Effect | None = None,
    eligibility_override: EligibilityOverride | None = None,
) -> list[FillReport]:
    """Open each job's application form in the kitty Firefox window and fill it.

    Opens its own DB connection (called from a TUI worker thread, like
    kitty.core.discovery.pipeline.run_discover). The browser window and its
    tabs stay open for me to review and submit. `on_filled` gets the reports as
    soon as filling is done. Unconfirmed session cleanup preserves those outcomes
    and adds a warning to their notes and progress. With a `watch` whose hours
    are positive, it then keeps the session and watches the tabs until each is
    submitted or closed, the stop flag is set or time is up, and records each
    submitted application.
    `headless=True` uses a throwaway invisible browser instead (tests, smoke
    runs); its tabs stay open only while a watch runs. `background` opens
    the tabs behind the one in front (autofill), and `pause` is the wait in
    seconds between two jobs. Web callers supply the executed `approval` effect
    (kitty.core.effects, kind prepare_applications): every job must be bound in it,
    and each job's identity, its approved resume row and the resume bytes are
    re-checked against the stored effect before opening Firefox, before each job and
    again at each upload. CLI/TUI callers without an approval retain their normal
    pinned-or-default selection.
    Closed and ineligible jobs return blocked reports before browser access.
    The typed CLI-only compatibility override can inspect ineligible forms,
    but cannot authenticate, report readiness, or add them to submission watching.
    It cannot be combined with a reviewed web approval.
    """
    say = progress or (lambda message: None)
    stop = watch.stop if watch is not None else None
    watch = watch if watch is not None and watch.hours > 0 else None
    notify_filled = on_filled or (lambda reports: None)
    completed: list[FillReport] | None = None
    recovery_ai = recovery_budget = None
    run: attempts_core.Run | None = None
    run_open = False

    def close_run(error: str | None = None) -> None:
        nonlocal run_open
        if run_open:
            run_open = False
            attempts_core.finish_run(conn, run, error=error)

    def report_filled(reports: list[FillReport]) -> None:
        nonlocal completed
        completed = reports
        finish_model_reservation(conn, config, recovery_budget)
        close_run()
        notify_filled(reports)

    conn = connect(settings.db_path)
    try:
        if eligibility_override is not None and (
            eligibility_override is not EligibilityOverride.COMPATIBILITY_RESEARCH or approval is not None
        ):
            raise ApprovalError(APPROVAL_CHANGED)
        run = attempts_core.start_run(conn)
        run_open = True
        # The live check right before preparing (proposal-05 Step 3): closed postings
        # leave the queue, opaque redirects resolve to the employer URL (not under a
        # reviewed plan, whose identity includes that URL) and excluded flows become
        # handoffs. Public GETs only, robots-checked, one request/second/host.
        resolve_core.before_attempt(conn, settings, job_ids, write_url=approval is None, say=say)
        # Entirely excluded runs require no browser session. Mixed runs retain
        # their original ordering and recheck each exclusion immediately before use.
        local_reports = []
        for job_id in job_ids:
            job = jobs_core.get_job(conn, job_id)
            if job is None:
                local_reports.append(_error_report(job_id, None, f"no job with id {job_id}"))
            elif blocker := preparation_blocker(job, eligibility_override=eligibility_override):
                local_reports.append(_blocked_report(job, blocker))
        if len(local_reports) == len(job_ids):
            local_reports = [_excluded_report(conn, jobs_core.get_job(conn, report.job_id), report.note, run)
                             if report.status == "blocked" else report for report in local_reports]
        if len(local_reports) == len(job_ids):
            report_filled(local_reports)
            return local_reports
        if locked := accounts_core.keyring_blocker(conn, settings):
            # Employer account passwords live in a keyring that is locked: a typed
            # handoff before any browser opens, never an unlock prompt nobody sees.
            say(locked)
            reports = []
            for job_id in job_ids:
                job = jobs_core.get_job(conn, job_id)
                if job is None or preparation_blocker(job, eligibility_override=eligibility_override):
                    reports.append(next(r for r in local_reports if r.job_id == job_id))
                    continue
                report = _blocked_report(job, locked)
                report.attempt_id = attempts_core.record(
                    conn, job_id=job.id, ats=report.ats, url=report.url, progress=attempts_core.Progress(),
                    status=report.status, note=report.note, error=None, outcome_cause=report.cause, run=run,
                )
                reports.append(report)
            report_filled(reports)
            return reports
        if approval is not None:
            for job_id in job_ids:
                job = jobs_core.get_job(conn, job_id)
                if job is not None and preparation_blocker(job, eligibility_override=eligibility_override):
                    continue
                if job is None:
                    raise ApprovalError(APPROVAL_CHANGED)
                approved_resume(approval, conn, settings, job)
        recovery_ai, recovery_budget = model_planner(config)
        if headless:
            with headless_firefox(settings) as bidi:
                return _fill_and_watch(bidi, conn, settings, config, job_ids, say,
                                       keep_open=watch is not None, on_filled=report_filled,
                                       watch=watch, background=background, pause=pause, approval=approval,
                                       eligibility_override=eligibility_override, recovery_planner=recovery_ai,
                                       on_report=on_report, stop=stop, run=run)
        say("opening the rika! Firefox window")
        port = ensure_firefox(settings)
        try:
            run.hide_automation = hide_automation(port)
            if not run.hide_automation:
                say("warning: pages can see this Firefox is remote-controlled (navigator.webdriver)")
            bidi = open_session(port)
        except SessionBusy as exc:
            raise SessionBusy(busy_message(port)) from exc
        try:
            return _fill_and_watch(bidi, conn, settings, config, job_ids, say, keep_open=True,
                                   on_filled=report_filled, watch=watch, background=background,
                                   pause=pause, approval=approval, eligibility_override=eligibility_override,
                                   recovery_planner=recovery_ai, on_report=on_report, stop=stop, run=run)
        finally:
            bidi.close()                  # ends remote control only: the window and tabs stay
    except (BrowserError, BidiError) as exc:
        if isinstance(exc, SessionEndError) and completed is not None:
            # Filling and its callback already finished. Teardown is a separate
            # failure, not evidence that the prepared applications failed.
            message = f"Browser cleanup unconfirmed: {bidi.redact(str(exc))}"
            for report in completed:
                report.note = f"{report.note}\n{message}" if report.note else message
            say(message)
            return completed
        message = str(exc).strip().splitlines()[0][:300]
        reports = []
        for job_id in job_ids:
            job = jobs_core.get_job(conn, job_id)
            blocker = preparation_blocker(job, eligibility_override=eligibility_override) if job else None
            reports.append(_blocked_report(job, blocker) if blocker else _error_report(job_id, job, message))
        report_filled(reports)
        return reports
    finally:
        try:
            finish_model_reservation(conn, config, recovery_budget)
            close_run()
        finally:
            conn.close()

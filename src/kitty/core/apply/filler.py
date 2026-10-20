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
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

from kitty.config import Config
from kitty.core import jobs as jobs_core
from kitty.core import questions as questions_core
from kitty.core import resumes as resumes_core
from kitty.core.resume_versions import default_resume
from kitty.core.refresh import PostingCheck, persist_check
from kitty.core.apply.auth import authenticate, google_session
from kitty.core.apply.bidi import Bidi, BidiError, ConnectionLost, Frame, SessionBusy, Tab, TabClosed, open_session, open_tab
from kitty.core.apply.browser import (
    BrowserError, busy_message, ensure_firefox, headless_firefox, hide_automation,
)
from kitty.core.apply.credentials import CredentialError, load_credentials
from kitty.core.apply.fields import (
    TEXT_KINDS, FormField, Plan, is_affirmative, is_resume_input, lookup_answer, match_option,
    plan_for,
)
from kitty.core.apply.page import (
    UnsafeAction, choose_select, fill_text, form_frames, pick_from_list, pick_from_search,
    read_fields, settle, tick, upload, uploads_pending, wait_for_form, wait_for_uploads,
)
from kitty.core.apply.urls import detect_ats, form_url
from kitty.core.apply.preflight import advance, inspect_page, wait_for_page
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
_MANUAL_CONSENT = re.compile(
    r"\b(?:agree|consent|certify|attest|signature|terms(?: and conditions| of use| of service)?|"
    r"privacy (?:policy|notice)|accept.{0,80}(?:privacy|data processing)|"
    r"einverstanden|einwilligung|datenschutzerklärung)\b", re.I,
)


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


@dataclass
class FormResult:
    """What fill_form did on one page (no database involved)."""

    filled: list[str] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    to_log: list[str] = field(default_factory=list)       # unanswered with no answers.yaml entry
    resume_uploaded: bool = False
    fields_found: int = 0
    resume_verified: bool = False
    blocker: str | None = None


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


@dataclass(frozen=True)
class FillApproval:
    """Immutable reviewed job and resume identity; never resolve a new default."""

    job_id: int
    url: str
    apply_url: str | None
    company: str
    tracks: tuple[str, ...]
    resume_id: int | None
    file_hash: str | None
    file_path: str | None

    def resume(self, conn: sqlite3.Connection, settings: Settings, job: Job) -> Resume | None:
        if (job.id, job.url, job.apply_url, job.company_name, tuple(job.tracks)) != (
            self.job_id, self.url, self.apply_url, self.company, self.tracks
        ):
            raise ApprovalError(APPROVAL_CHANGED)
        if self.resume_id is None:
            return None
        resume = resumes_core.get_resume(conn, self.resume_id)
        if resume is None or (resume.file_hash, resume.file_path) != (self.file_hash, self.file_path):
            raise ApprovalError(APPROVAL_CHANGED)
        try:
            path = resume_file(settings, resume)
            if not path.is_file() or resumes_core.file_hash(path) != self.file_hash:
                raise ApprovalError(APPROVAL_CHANGED)
        except OSError:
            raise ApprovalError(APPROVAL_CHANGED) from None
        return resume


def _upload_approved(
    frame: Frame, key: str, path: Path, approval: FillApproval,
    conn: sqlite3.Connection, settings: Settings,
) -> None:
    job = jobs_core.get_job(conn, approval.job_id)
    if job is None or approval.resume(conn, settings, job) is None:
        raise ApprovalError(APPROVAL_CHANGED)
    try:
        content = path.read_bytes()
    except OSError:
        raise ApprovalError(APPROVAL_CHANGED) from None
    if hashlib.sha256(content).hexdigest() != approval.file_hash:
        raise ApprovalError(APPROVAL_CHANGED)
    # Firefox can read its File object at the later human submit, after the worker
    # exits. Keep a private, content-addressed artifact instead of a temporary or
    # mutable library path. Never overwrite an existing approved artifact.
    folder = settings.data_dir / "approved_resumes"
    frozen = folder / f"{approval.file_hash}.pdf"
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
        if digest != approval.file_hash:
            raise ApprovalError(APPROVAL_CHANGED)
    except OSError:
        raise ApprovalError(APPROVAL_CHANGED) from None
    upload(frame, key, frozen)


# ------------------------------------------------------------- the form


def _apply_plan(page_frames: list[Frame], form_field: FormField, plan: Plan) -> bool:
    """Put a planned value into one field; return True when it was set."""
    frame = page_frames[form_field.frame]
    if (_MANUAL_CONSENT.search(form_field.label) or
            any(_MANUAL_CONSENT.search(option) for option in form_field.options)):
        return False
    if form_field.kind in TEXT_KINDS:
        fill_text(frame, form_field.key, plan.candidates[0])
        return True
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
    tracks: Sequence[str] = (), preserve_existing: bool = False,
    upload_resume: Callable[[Frame, str, Path], None] | None = None,
) -> FormResult:
    """Fill every field on the open page that profile.yaml / answers.yaml covers.

    Nonblank fields and existing attachments are retained, including manual
    selections from an earlier attempt. Only blank fields are written.
    """
    result = FormResult()
    frames, fields = wait_for_form(page)
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
            plan = plan_for(form_field, config, company=company, tracks=tracks)
            if plan is not None:
                planned.append((form_field, plan))
    # Address lookups can appear before the country/state controls they depend on.
    # Fill those parents first; keep the remaining plans in their original order.
    contact_order = {"country": 0, "state": 1}
    planned.sort(key=lambda item: contact_order.get(item[1].field_name.casefold(), 2))
    for form_field, plan in planned:
        try:
            if _apply_plan(frames, form_field, plan) and plan.field_name not in result.filled:
                result.filled.append(plan.field_name)
        except (BidiError, UnsafeAction):
            continue                      # left blank; reported below if it is required

    settle(page, timeout_ms=5000)          # let uploads and dropdown lookups finish
    final = read_fields(form_frames(page))
    result.fields_found = len(final)
    for form_field in final:
        if not form_field.required or (not form_field.blank and not form_field.invalid):
            continue
        label = form_field.label or form_field.name or "unlabelled field"
        if form_field.kind == "file":
            result.unanswered.append(f"{label} (file upload)")
            continue
        if label not in result.unanswered:
            result.unanswered.append(label)
        if form_field.label and lookup_answer(
            config, form_field.label, company=company, tracks=tracks,
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


def fill_job(
    page: Tab, conn: sqlite3.Connection, settings: Settings, config: Config, job: Job,
    *, resume_tab: bool = False, approval: FillApproval | None = None,
) -> FillReport:
    """Prepare a retained/new page with real input, then restore its prior foreground tab."""
    ats = detect_ats(job.apply_url or job.url)
    url = form_url(job, ats)
    report = FillReport(job_id=job.id, company=job.company_name, title=job.title, url=url,
                        ats=ats, resume_label=None, filled=[], unanswered=[], status="attempted",
                        resumed=resume_tab, resume_id=job.resume_id if resume_tab else None)
    try:
        resume = approval.resume(conn, settings, job) if approval is not None else choose_resume(conn, config, job)
    except ApprovalError as exc:
        return _error_report(job.id, job, str(exc))
    resume_path = resume_file(settings, resume) if resume is not None else None
    if resume_path is not None and not resume_path.is_file():
        if approval is not None:
            return _error_report(job.id, job, APPROVAL_CHANGED)
        resume_path = None
    record_attempt(conn, job.id, "attempted", context_id=page.context, url=page.bidi.redact(url))
    resume_verified = False
    visited: set[str] = set()
    previous: str | None = None
    authentication_attempted = False
    closed_url: str | None = None
    policy = config.apply.authentication
    foreground: Tab | None = None
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
        if not resume_tab:
            page.goto(url)
        settle(page)
        for _ in range(12):
            before = wait_for_page(page, previous=previous)
            if authentication_attempted and before.blocker and before.blocker.startswith("login:"):
                # Account redirects can render their verification notice after
                # the sign-in controls. Observe it without repeating credentials.
                before = wait_for_page(page, previous=before.signature, seconds=2)
            if authentication_attempted and not policy.approves(page.main.url):
                report.status, report.note = "blocked", "login: redirected to an unapproved origin; continue manually."
                break
            if before.blocker and before.blocker.startswith("login:") and policy.enabled:
                if authentication_attempted:
                    report.status, report.note = "blocked", "login: authentication did not advance; continue manually (no automatic retry)."
                    break
                if not policy.approves(page.main.url) or not policy.approves(before.url):
                    report.status, report.note = "blocked", "login: this origin is not approved for credentials."
                    break
                authentication_attempted = True
                if policy.account_mode == "google":
                    authentication = google_session(page, policy, config.profile.email.strip())
                else:
                    try:
                        credentials = load_credentials(settings)
                    except CredentialError as exc:
                        report.status, report.note = "blocked", str(exc)
                        break
                    page.bidi.protect_secrets(credentials.email, credentials.password)
                    authentication = authenticate(page, policy, credentials)
                report.filled.extend(_ACCOUNT_FIELD_LABELS[role] for role in authentication.filled)
                if authentication.blocker or not authentication.acted:
                    report.status = "partial" if authentication.filled and not authentication.acted else "blocked"
                    report.note = authentication.blocker or before.blocker
                    break
                previous = before.signature
                settle(page)
                continue
            if before.blocker:
                if before.blocker.startswith("closed:"):
                    closed_url = before.url
                report.status, report.note = "blocked", before.blocker
                break
            if before.signature in visited:
                report.status, report.note = "blocked", "Application step did not advance or repeated; continue manually."
                break
            visited.add(before.signature)
            previous = before.signature
            if before.entry:
                advance(before, ats)
                settle(page)
                continue
            if not (before.step or before.boundary):
                report.status, report.note = "blocked", "Unsupported application page: no recognized form or safe entry; continue manually."
                break
            result = fill_form(
                page, config, resume_path, company=job.company_name,
                tracks=job.tracks, preserve_existing=resume_tab,
                upload_resume=(lambda frame, key, path: _upload_approved(
                    frame, key, path, approval, conn, settings
                )) if approval is not None else None,
            )
            # A site can echo account values in field labels or errors. Never persist
            # those echoes in preparation evidence or the unanswered-questions queue.
            result.filled = [page.bidi.redact(value) for value in result.filled]
            result.unanswered = [page.bidi.redact(value) for value in result.unanswered]
            result.to_log = [value for value in result.to_log if page.bidi.redact(value) == value]
            report.pages += 1
            _record(conn, job, resume, result)
            report.filled.extend(name for name in result.filled if name not in report.filled)
            report.unanswered = result.unanswered
            resume_verified = resume_verified or result.resume_verified
            if result.resume_uploaded and resume:
                report.resume_label = resume.label
                report.resume_id = resume.id
            elif result.resume_verified and report.resume_label is None:
                report.resume_label = "existing attachment (retained)"
            if result.blocker:
                report.status, report.note = "blocked", result.blocker
                break
            after = inspect_page(page)
            if after.blocker:
                if after.blocker.startswith("closed:"):
                    closed_url = after.url
                report.status, report.note = "blocked", after.blocker
                break
            if after.unsupported_required:
                report.status = "blocked"
                report.note = "unsupported: required custom controls need manual completion. Review any fields already filled."
                break
            if result.unanswered or after.errors:
                report.status = "partial" if result.fields_found else "blocked"
                report.note = ("Required answers or uploads are missing or invalid: " +
                               "; ".join(result.unanswered)) if result.unanswered else "Page validation errors need your review."
                break
            if after.review or after.boundary:
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
                continue
            report.status = "partial" if report.filled else "blocked"
            report.note = "Unsupported application page: no recognized review boundary or safe next step; continue manually."
            break
        else:
            report.status, report.note = "blocked", "Application step limit reached; continue manually."
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
    record_attempt(conn, job.id, report.status, blocker=report.note if report.status != "ready" else None,
                   context_id=page.context, url=page.bidi.redact(current_url))
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


def _watched_tab(job: Job, tab: Tab, report: FillReport) -> WatchedTab:
    """Keep the attachment identity; a later default change cannot rewrite history."""
    return WatchedTab(
        job_id=job.id, context=tab.context, ats=report.ats, form_url=report.url,
        label=f"{job.company_name} - {job.title}", resume_id=report.resume_id,
        track=job.tracks[0] if job.tracks else None,
        marker=tab.context,
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
    approvals: Mapping[int, FillApproval] | None = None,
) -> list[FillReport]:
    """Fill each job in its own new tab, pausing `pause` seconds between jobs.

    Kept-open tabs on a board the watcher knows are added to `watch.tabs`; a
    set stop flag ends the run before the next job. `background` tabs open
    behind the current one and the first is not brought forward afterwards.
    """
    reports: list[FillReport] = []
    first_tab: Tab | None = None
    for position, job_id in enumerate(job_ids):
        if position:
            _pause(pause, watch)
        if watch is not None and watch.stop.is_set():
            break
        job = jobs_core.get_job(conn, job_id)
        if job is None:
            reports.append(_error_report(job_id, None, f"no job with id {job_id}"))
            continue
        if not job.is_active:
            reports.append(_error_report(job_id, job, "Posting unavailable; refresh availability before preparing it."))
            continue
        approval = approvals.get(job_id) if approvals is not None else None
        if approvals is not None:
            try:
                if approval is None:
                    raise ApprovalError(APPROVAL_CHANGED)
                approval.resume(conn, settings, job)
            except ApprovalError as exc:
                reports.append(_error_report(job_id, job, str(exc)))
                continue
        say(f"[{position + 1}/{len(job_ids)}] {job.company_name} - {job.title}")
        try:
            retained = _retained_tab(bidi, job)
            tab = retained or open_tab(bidi, background=background)
            first_tab = first_tab or tab
            report = fill_job(tab, conn, settings, config, job, resume_tab=retained is not None,
                              approval=approval)
            reports.append(report)
            if watch is not None:
                watch.tabs[:] = [old for old in watch.tabs if old.context != tab.context and old.job_id != job.id]
            if not keep_open:
                tab.close()
            elif watch is not None and report.ats in RULES and not (report.note or "").startswith("closed:"):
                watch.tabs.append(_watched_tab(job, tab, report))
        except BidiError as exc:
            message = bidi.redact(str(exc)).strip().splitlines()[0][:300]
            # An unavailable later tab must not erase reports already prepared.
            # If cleanup failed after reporting this job, keep its actual result.
            if not reports or reports[-1].job_id != job_id:
                reports.append(_error_report(job_id, job, message))
            say(f"Browser stopped this job: {message}")
            if isinstance(exc, ConnectionLost):
                for remaining_id in job_ids[position + 1:]:
                    reports.append(_error_report(
                        remaining_id, jobs_core.get_job(conn, remaining_id),
                        "Browser disconnected before this job could be prepared; reconnect Firefox and review a new plan.",
                    ))
                break
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
    approvals: Mapping[int, FillApproval] | None = None,
) -> list[FillReport]:
    """Fill, hand the reports over, then (with a Watch) watch the tabs in the same session.

    Tabs carried over in `watch.tabs` are found again first (their ids are per session).
    """
    emit = say
    say = lambda message: emit(bidi.redact(message))
    if watch is not None and watch.tabs:
        watch.tabs[:] = reattach(bidi, watch.tabs)
    reports = _fill_all(bidi, conn, settings, config, job_ids, say, keep_open=keep_open,
                        watch=watch, background=background, pause=pause, approvals=approvals)
    on_filled(reports)
    if watch is None or not watch.tabs or watch.stop.is_set():
        return reports
    watch.on_start(len(watch.tabs))
    try:
        watch_tabs(lambda tab: look(bidi, tab), conn, config, watch, say)
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
    watch: Watch | None = None,
    background: bool = False,
    pause: float = PAUSE_BETWEEN_JOBS,
    approvals: Mapping[int, FillApproval] | None = None,
) -> list[FillReport]:
    """Open each job's application form in the kitty Firefox window and fill it.

    Opens its own DB connection (called from a TUI worker thread, like
    kitty.core.discovery.pipeline.run_discover). The browser window and its
    tabs stay open for me to review and submit. `on_filled` gets the reports as
    soon as filling is done. With a `watch` (and watch.hours > 0) it then keeps
    the session and watches the tabs until each is submitted or closed, the
    stop flag is set or time is up, and records each submitted application.
    `headless=True` uses a throwaway invisible browser instead (tests, smoke
    runs); its tabs stay open only while a watch runs. `background` opens
    the tabs behind the one in front (autofill), and `pause` is the wait in
    seconds between two jobs. Web callers supply immutable `approvals`; all are
    checked before opening Firefox and again before each upload. CLI/TUI callers
    without approvals retain their normal pinned-or-default selection.
    """
    say = progress or (lambda message: None)
    watch = watch if watch is not None and watch.hours > 0 else None
    on_filled = on_filled or (lambda reports: None)
    conn = connect(settings.db_path)
    try:
        if approvals is not None:
            for job_id in job_ids:
                job = jobs_core.get_job(conn, job_id)
                approval = approvals.get(job_id)
                if job is None or approval is None:
                    raise ApprovalError(APPROVAL_CHANGED)
                approval.resume(conn, settings, job)
        if headless:
            with headless_firefox(settings) as bidi:
                return _fill_and_watch(bidi, conn, settings, config, job_ids, say,
                                       keep_open=watch is not None, on_filled=on_filled,
                                       watch=watch, background=background, pause=pause, approvals=approvals)
        say("opening the kitty Firefox window")
        port = ensure_firefox(settings)
        try:
            if not hide_automation(port):
                say("warning: pages can see this Firefox is remote-controlled (navigator.webdriver)")
            bidi = open_session(port)
        except SessionBusy as exc:
            raise SessionBusy(busy_message(port)) from exc
        try:
            return _fill_and_watch(bidi, conn, settings, config, job_ids, say, keep_open=True,
                                   on_filled=on_filled, watch=watch, background=background,
                                   pause=pause, approvals=approvals)
        finally:
            bidi.close()                  # ends remote control only: the window and tabs stay
    except (BrowserError, BidiError) as exc:
        message = str(exc).strip().splitlines()[0][:300]
        reports = [_error_report(job_id, jobs_core.get_job(conn, job_id), message)
                   for job_id in job_ids]
        on_filled(reports)
        return reports
    finally:
        conn.close()

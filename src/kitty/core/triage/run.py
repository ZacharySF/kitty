"""`kitty triage`: tier jobs, pick a resume, and note what to check before applying.

Which jobs: active, new or queued, not applied to, routed to a track. A job I
tiered by hand is never touched. Then:
  * ineligible jobs get tier C from the rules (the filter's reason is the reason);
  * jobs whose tracks all have triage_depth 'rules' (part-time) get a rules tier,
    and never reach the model;
  * everything else goes to the model, unless llm_cache already holds an answer
    for exactly this input (job, model, prompt) - then that answer is reused free.

Two ways to call the model: `now` (one request at a time, cap checked before
each) and `batch` (Message Batches API, half price, results within 24 h; the
batch is trimmed so its estimate fits the cap, and `collect` applies it later).
Every run is a row in `runs` with its counts, cost and whether the cap stopped it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import anthropic

from kitty.config import Config
from kitty.core import jobs as jobs_core
from kitty.core import resumes as resumes_core
from kitty.core import sources as sources_core
from kitty.core.resume_coach import read_resume_lines
from kitty.core.resume_versions import default_resume
from kitty.core.triage.cost import Estimate, Usage, cost_usd, estimate, request_estimate
from kitty.core.triage.prompt import (
    PROMPT_VERSION,
    AnswerError,
    ResumeText,
    Verdict,
    answer_schema,
    parse_answer,
    system_prompt,
    user_prompt,
)
from kitty.core.triage.rules import rules_changes
from kitty.dates import now_iso
from kitty.db.connection import transaction
from kitty.models import Job
from kitty.settings import Settings

KIND = "triage"
#: API errors after which sending more requests in this run is pointless.
_STOP_ERRORS = (anthropic.RateLimitError, anthropic.AuthenticationError, anthropic.PermissionDeniedError)


class TriageError(RuntimeError):
    """Triage cannot start (no key, no readable resume)."""


class Client(Protocol):
    """The slice of anthropic.Anthropic used here (tests pass a fake)."""

    messages: object


# ------------------------------------------------------------------- the plan


@dataclass
class TriagePlan:
    """Everything a run needs, built without calling the model."""

    system: str
    schema: dict
    resume_lines: dict[str, list[str]]                             # label -> lines (as stored)
    resume_ids: dict[str, int]                                     # label -> resumes.id
    to_send: list[Job] = field(default_factory=list)
    users: dict[int, str] = field(default_factory=dict)            # job id -> user prompt
    hashes: dict[int, str] = field(default_factory=dict)           # job id -> input hash
    cached: list[tuple[Job, str]] = field(default_factory=list)    # (job, cached answer json)
    rules: list[tuple[Job, str, str]] = field(default_factory=list)  # (job, tier, reason)


def _resumes(conn: sqlite3.Connection, settings: Settings, config: Config) -> list[tuple[int, ResumeText]]:
    """Every registered resume whose file can be read, with the tracks that send it."""
    users: dict[str, list[str]] = {}
    for track in config.tracks.tracks:
        selected = default_resume(conn, config, track.name)
        if selected:
            users.setdefault(selected.label, []).append(track.name)
    found = []
    managed = {row["resume_id"] for row in conn.execute(
        "SELECT resume_id FROM resume_versions WHERE resume_id IS NOT NULL"
    )}
    for resume in resumes_core.list_resumes(conn):
        if resume.id in managed and resume.label not in users:
            continue                 # keep historical versions out of future model choices
        path = Path(resume.file_path)
        path = path if path.is_absolute() else settings.root / path
        try:
            lines = read_resume_lines(path)
        except (OSError, ValueError):
            continue
        if lines:
            found.append((resume.id, ResumeText(resume.label, resume.description,
                                                users.get(resume.label, []), lines)))
    return found


def input_hash(config: Config, system: str, user: str) -> str:
    """Identifies one exact request: same hash, same answer, no need to ask again."""
    payload = json.dumps([PROMPT_VERSION, config.triage.model, config.triage.effort, system, user])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cached_answer(conn: sqlite3.Connection, job_id: int, model: str, digest: str) -> str | None:
    row = conn.execute(
        "SELECT response_json FROM llm_cache WHERE job_id = ? AND kind = ? AND model = ? AND input_hash = ?",
        (job_id, KIND, model, digest),
    ).fetchone()
    return row["response_json"] if row else None


def base_plan(conn: sqlite3.Connection, config: Config, settings: Settings) -> TriagePlan:
    """The parts of a plan that do not depend on the jobs: prompt, schema, resumes."""
    resumes = _resumes(conn, settings, config)
    if not resumes:
        raise TriageError("no readable resume is registered: kitty resumes add <file> --label <label>")
    texts = [text for _, text in resumes]
    return TriagePlan(
        system=system_prompt(config, texts),
        schema=answer_schema([text.label for text in texts]),
        resume_lines={text.label: text.lines for text in texts},
        resume_ids={text.label: resume_id for resume_id, text in resumes},
    )


def plan_triage(
    conn: sqlite3.Connection, config: Config, settings: Settings, *,
    track: str | None = None, limit: int | None = None,
) -> TriagePlan:
    """Decide what each candidate job needs: a rules tier, a cached answer, or a request."""
    plan = base_plan(conn, config, settings)
    plan.rules = rules_changes(conn, config)
    depth = {t.name: t.triage_depth for t in config.tracks.tracks}
    companies = {row["id"]: (row["priority"], row["notes"])
                 for row in conn.execute("SELECT id, priority, notes FROM companies")}
    jobs = jobs_core.list_jobs(conn, jobs_core.JobFilter(
        queue_states=["new", "queued"], tracks=[track] if track else None, not_applied=True,
        order="best", prefer_cities=config.prefer_cities, prefer_remote=config.profile.open_to_remote,
    ))
    for job in jobs:
        if job.tier_source == "manual" or not job.tracks:
            continue
        if all(depth.get(name) == "none" for name in job.tracks):
            continue
        uses_model = any(depth.get(name) == "llm" for name in job.tracks)
        if job.eligibility == "ineligible" or not uses_model:
            continue                                   # rules tier only (in plan.rules)
        priority, notes = companies.get(job.company_id, (0, None))
        user = user_prompt(job, config, company_priority=priority, company_notes=notes)
        digest = input_hash(config, plan.system, user)
        answer = _cached_answer(conn, job.id, config.triage.model, digest)
        if answer is not None:
            if job.tier_source != "llm":
                plan.cached.append((job, answer))
            continue
        if limit is not None and len(plan.to_send) >= limit:
            continue
        plan.to_send.append(job)
        plan.users[job.id] = user
        plan.hashes[job.id] = digest
    return plan


def plan_estimate(plan: TriagePlan, config: Config, *, batch: bool) -> Estimate:
    """The estimate shown before a run (see cost.py for why it runs high)."""
    users = [plan.users[job.id] for job in plan.to_send]
    return estimate(plan.system, users, config.triage.prices, config.triage.est_output_tokens, batch=batch)


# -------------------------------------------------------------- writing results


@dataclass
class RunResult:
    """What one `kitty triage` invocation did."""

    mode: str                       # now | batch | collect
    run_id: int | None = None
    rules_set: int = 0
    cached_applied: int = 0
    sent: int = 0
    applied: int = 0
    errors: list[str] = field(default_factory=list)
    notes_dropped: int = 0
    cost_usd: float = 0.0
    cap_hit: bool = False
    skipped_for_cap: int = 0
    batch_id: str | None = None
    pending: bool = False

    def stats(self) -> dict:
        return {key: value for key, value in asdict(self).items() if key != "run_id"}


def apply_verdict(conn: sqlite3.Connection, job: Job, verdict: Verdict, resume_ids: dict[str, int]) -> None:
    """Store a checked answer on the job. A resume I (or the filler) already chose stays."""
    reason = f"{verdict.tier_reason} Resume: {verdict.resume_label} - {verdict.resume_reason}"
    jobs_core.set_tier(conn, job.id, verdict.tier, reason=reason, tier_source="llm")
    if job.resume_id is None and verdict.resume_label in resume_ids:
        jobs_core.set_resume(conn, job.id, resume_ids[verdict.resume_label])
    jobs_core.set_edit_notes(conn, job.id, [note.as_text() for note in verdict.edit_notes])
    jobs_core.mark_triaged(conn, job.id)


def _apply_rules(conn: sqlite3.Connection, plan: TriagePlan) -> int:
    with transaction(conn):
        for job, tier, reason in plan.rules:
            jobs_core.set_tier(conn, job.id, tier, reason=reason, tier_source="rules")
    return len(plan.rules)


def _cache(conn: sqlite3.Connection, job_id: int, model: str, digest: str, answer: str,
           usage: Usage, cost: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO llm_cache (job_id, kind, model, input_hash, response_json, "
        "input_tokens, output_tokens, cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, KIND, model, digest, answer,
         usage.input_tokens + usage.cache_write_tokens + usage.cache_read_tokens,
         usage.output_tokens, round(cost, 6), now_iso()),
    )


def _answer_text(message) -> str:
    """The JSON text of a response (thinking blocks, if any, are skipped)."""
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise AnswerError("answer was cut off at max_output_tokens")
    parts = [block.text for block in message.content if getattr(block, "type", "") == "text"]
    if not parts:
        raise AnswerError(f"no text in the answer (stop_reason {getattr(message, 'stop_reason', '?')})")
    return "".join(parts)


def _use_answer(conn, config: Config, plan: TriagePlan, job: Job, answer: str, result: RunResult) -> bool:
    """Check one answer and store it; a bad answer is recorded as an error, not raised."""
    try:
        verdict = parse_answer(answer, job.jd_text, plan.resume_lines, config.profile)
    except AnswerError as exc:
        result.errors.append(f"{job.id} {job.company_name}: {exc}")
        return False
    apply_verdict(conn, job, verdict, plan.resume_ids)
    result.notes_dropped += verdict.dropped_notes
    return True


def _apply_cached(conn, config: Config, plan: TriagePlan, result: RunResult) -> None:
    """Reuse answers already paid for (same job, same model, same prompt)."""
    with transaction(conn):
        for job, answer in plan.cached:
            result.cached_applied += _use_answer(conn, config, plan, job, answer, result)


def request_params(config: Config, plan: TriagePlan, user: str) -> dict:
    """One Messages API request: cached system block, the job, the answer schema."""
    output_config: dict = {"format": {"type": "json_schema", "schema": plan.schema}}
    if config.triage.effort:
        output_config["effort"] = config.triage.effort
    return {
        "model": config.triage.model,
        "max_tokens": config.triage.max_output_tokens,
        "system": [{"type": "text", "text": plan.system,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": output_config,
    }


def _guess(config: Config, plan: TriagePlan, job: Job, *, batch: bool, first: bool) -> float:
    """High-side dollars for one request, checked against the cap before sending."""
    prices = config.triage.prices
    usage = request_estimate(plan.system, plan.users[job.id], prices, config.triage.est_output_tokens,
                             batch=batch, first=first)
    return cost_usd(usage, prices, batch=batch)


# ---------------------------------------------------------------------- modes


def run_now(conn: sqlite3.Connection, config: Config, plan: TriagePlan, client: Client, *,
            progress: Callable[[str], None] | None = None) -> RunResult:
    """Send each job now, one at a time; stop before the cap could be passed."""
    say = progress or (lambda message: None)
    result = RunResult(mode="now")
    result.run_id = sources_core.start_run(conn, KIND)
    result.rules_set = _apply_rules(conn, plan)
    _apply_cached(conn, config, plan, result)
    cap = config.triage.max_cost_usd_per_run
    for index, job in enumerate(plan.to_send):
        if result.cost_usd + _guess(config, plan, job, batch=False, first=result.sent == 0) > cap:
            result.cap_hit = True
            result.skipped_for_cap = len(plan.to_send) - index
            say(f"cost cap ${cap:.2f} reached; {result.skipped_for_cap} job(s) left for the next run")
            break
        say(f"[{index + 1}/{len(plan.to_send)}] {job.company_name} - {job.title}")
        try:
            message = client.messages.create(**request_params(config, plan, plan.users[job.id]))
        except anthropic.APIError as exc:                          # recorded; the run goes on
            result.errors.append(f"{job.id} {job.company_name}: {type(exc).__name__}: {exc}")
            if isinstance(exc, _STOP_ERRORS):
                break                                              # no point sending more
            continue
        result.sent += 1
        usage = Usage.from_api(message.usage)
        cost = cost_usd(usage, config.triage.prices, batch=False)
        result.cost_usd += cost
        try:
            answer = _answer_text(message)
        except AnswerError as exc:
            result.errors.append(f"{job.id} {job.company_name}: {exc}")
            continue
        with transaction(conn):
            _cache(conn, job.id, config.triage.model, plan.hashes[job.id], answer, usage, cost)
            result.applied += _use_answer(conn, config, plan, job, answer, result)
    sources_core.finish_run(conn, result.run_id, stats=result.stats())
    return result


def submit_batch(conn: sqlite3.Connection, config: Config, plan: TriagePlan, client: Client) -> RunResult:
    """Send the jobs that fit under the cap as one Message Batch; `collect` applies it."""
    result = RunResult(mode="batch")
    result.run_id = sources_core.start_run(conn, KIND)
    result.rules_set = _apply_rules(conn, plan)
    _apply_cached(conn, config, plan, result)
    cap = config.triage.max_cost_usd_per_run
    requests, jobs, planned = [], {}, 0.0
    for job in plan.to_send:
        guess = _guess(config, plan, job, batch=True, first=False)
        if planned + guess > cap:
            result.cap_hit = True
            result.skipped_for_cap = len(plan.to_send) - len(requests)
            break
        planned += guess
        custom_id = f"job-{job.id}"
        requests.append({"custom_id": custom_id, "params": request_params(config, plan, plan.users[job.id])})
        jobs[custom_id] = {"job_id": job.id, "input_hash": plan.hashes[job.id]}
    if not requests:
        sources_core.finish_run(conn, result.run_id, stats=result.stats())
        return result
    batch = client.messages.batches.create(requests=requests)
    result.batch_id, result.sent, result.pending = batch.id, len(requests), True
    stats = result.stats() | {"jobs": jobs, "estimate_usd": round(planned, 4), "model": config.triage.model}
    conn.execute("UPDATE runs SET stats_json = ? WHERE id = ?", (json.dumps(stats), result.run_id))
    conn.commit()
    return result


def pending_batches(conn: sqlite3.Connection) -> list[tuple[int, dict]]:
    """(run id, stats) of submitted batches that have not been collected yet."""
    rows = conn.execute(
        "SELECT id, stats_json FROM runs WHERE kind = ? AND finished_at IS NULL AND stats_json IS NOT NULL",
        (KIND,),
    ).fetchall()
    found = []
    for row in rows:
        stats = json.loads(row["stats_json"])
        if stats.get("mode") == "batch" and stats.get("batch_id"):
            found.append((row["id"], stats))
    return found


def collect_batches(conn: sqlite3.Connection, config: Config, settings: Settings,
                    client: Client) -> list[RunResult]:
    """Apply every finished batch; report the ones still processing."""
    collected = []
    for run_id, stats in pending_batches(conn):
        result = RunResult(mode="collect", run_id=run_id, batch_id=stats["batch_id"])
        batch = client.messages.batches.retrieve(stats["batch_id"])
        if batch.processing_status != "ended":
            result.pending = True
            collected.append(result)
            continue
        plan = base_plan(conn, config, settings)          # current resumes, for checking notes
        model = stats.get("model", config.triage.model)
        with transaction(conn):
            for entry in client.messages.batches.results(stats["batch_id"]):
                meta = stats["jobs"].get(entry.custom_id)
                job = jobs_core.get_job(conn, meta["job_id"]) if meta else None
                if job is None:
                    continue
                if entry.result.type != "succeeded":
                    result.errors.append(f"{job.id} {job.company_name}: batch result {entry.result.type}")
                    continue
                message = entry.result.message
                usage = Usage.from_api(message.usage)
                cost = cost_usd(usage, config.triage.prices, batch=True)
                result.cost_usd += cost
                result.sent += 1
                try:
                    answer = _answer_text(message)
                except AnswerError as exc:
                    result.errors.append(f"{job.id} {job.company_name}: {exc}")
                    continue
                _cache(conn, job.id, model, meta["input_hash"], answer, usage, cost)
                if job.tier_source == "manual":              # tiered by hand while waiting
                    continue
                company = conn.execute(
                    "SELECT priority, notes FROM companies WHERE id = ?", (job.company_id,)
                ).fetchone()
                user = user_prompt(
                    job, config, company_priority=company["priority"], company_notes=company["notes"]
                )
                if input_hash(config, plan.system, user) != meta["input_hash"]:
                    result.errors.append(
                        f"{job.id} {job.company_name}: inputs changed while batch was pending; "
                        "cached the old answer without applying it"
                    )
                    continue
                result.applied += _use_answer(conn, config, plan, job, answer, result)
        sources_core.finish_run(conn, run_id, stats=stats | result.stats() | {"mode": "batch"})
        collected.append(result)
    return collected


def make_client(settings: Settings) -> Client:
    """The real Anthropic client; the key comes from .env (ANTHROPIC_API_KEY)."""
    if not settings.anthropic_key_set:
        raise TriageError("ANTHROPIC_API_KEY is not set in .env (copy .env.example)")
    return anthropic.Anthropic()

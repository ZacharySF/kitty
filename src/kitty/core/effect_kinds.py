"""The effect kinds an agent proposes and a person reviews in rika!'s pending-approvals list.

``delete_block``
    Move a note block and its children to Trash. The preconditions are the target
    block (page, parent, type, text hash) and its whole subtree, so an edit to the
    block or anything under it after the proposal refuses the deletion. The executor
    writes the delete as the proposing actor, inside the kernel's claim transaction, so
    History and "Undo this agent session" show it as that agent's (approved by you).

``submit_application``
    Proposal-only (kitty.core.effects.ProposalOnly): it shows exactly what would be
    submitted (posting, application URL, the resume that would be attached and the
    preparation state) and has no executor. kitty never submits an application:
    approving one records your decision and tells you to submit it yourself in the
    browser; the application is recorded only when you mark it applied afterwards.

Kinds whose approval lives in their own view (Google Calendar pushes, the AI request,
application preparation) register themselves in the modules that own them.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from kitty.config import Config
from kitty.core import effects
from kitty.core.blocks import BlockError
from kitty.core.blocks.actor import Actor, acting
from kitty.core.blocks.store import transact

MAX_REASON = 500
SUBMIT_HANDOFF = ("rika! never submits applications. Your approval is recorded; open the application in your "
                  "browser, check it, and press Submit yourself. Then mark the job applied.")


@dataclass(frozen=True)
class Context:
    """What the pending-approvals kinds read: the connection and, for applications, the config."""

    conn: sqlite3.Connection
    config: Config | None = None


def _sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _reason(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > MAX_REASON or "\x00" in value:
        raise effects.EffectError(f"The reason is text of at most {MAX_REASON} characters")
    return value.strip()


# --------------------------------------------------------------------- delete_block


def _validate_delete(ctx: Context, raw: Any) -> dict:
    from kitty.core import block_api

    if not isinstance(raw, dict) or set(raw) - {"block", "reason"}:
        raise effects.EffectError("A block deletion names the block and, optionally, why")
    try:
        row = block_api._editable(ctx.conn, raw.get("block"))
    except BlockError as exc:
        raise effects.EffectError(str(exc), exc.status) from None
    if row["parent_id"] is None:
        raise effects.EffectError("Delete pages in rika!, not through an effect")
    return {"block": row["id"], "reason": _reason(raw.get("reason"))}


def _subtree(conn: sqlite3.Connection, block: str) -> list[sqlite3.Row]:
    return conn.execute(
        "WITH RECURSIVE tree(id) AS (SELECT id FROM blocks WHERE parent_id=? AND deleted_at IS NULL"
        " UNION ALL SELECT b.id FROM blocks b JOIN tree t ON b.parent_id=t.id WHERE b.deleted_at IS NULL)"
        " SELECT b.id, b.type, b.text FROM blocks b JOIN tree USING(id) ORDER BY b.id", (block,)).fetchall()


def _delete_preconditions(ctx: Context, payload: dict) -> dict:
    row = ctx.conn.execute(
        "SELECT b.*, r.deleted_at AS root_deleted FROM blocks b JOIN blocks r ON r.id=b.page_id WHERE b.id=?",
        (payload["block"],)).fetchone()
    if row is None or row["deleted_at"] is not None or row["root_deleted"] is not None:
        return {"target": None, "subtree": None}
    children = _subtree(ctx.conn, row["id"])
    return {
        "target": {"page_id": row["page_id"], "parent_id": row["parent_id"], "type": row["type"],
                   "text_sha256": _sha(row["text"])},
        "subtree": {"blocks": len(children),
                    "sha256": _sha("\n".join(f"{child['id']}\t{child['type']}\t{_sha(child['text'])}" for child in children))},
    }


def _delete_preview(ctx: Context, payload: dict, expected: dict) -> dict:
    row = ctx.conn.execute("SELECT b.type, b.text, r.text AS page_title FROM blocks b JOIN blocks r ON r.id=b.page_id"
                           " WHERE b.id=?", (payload["block"],)).fetchone()
    children = _subtree(ctx.conn, payload["block"])
    return {"action": "Move this block and its children to Trash (restorable from Trash or History)",
            "page": row["page_title"], "type": row["type"], "text": row["text"][:4000],
            "children": [child["text"][:200] for child in children[:20]], "child_count": len(children),
            "reason": payload["reason"]}


def _delete_run(ctx: Context, effect: effects.Effect) -> dict:
    with acting(effect.actor if isinstance(effect.actor, Actor) else Actor()):
        result = transact(ctx.conn, [{"op": "delete", "id": effect.payload["block"]}])
    page_id = effect.preconditions["target"]["page_id"]
    return {"txn_id": result["txn_id"], "page_id": page_id, "revision": result["revisions"].get(page_id)}


DELETE_BLOCK = effects.REGISTRY.register(effects.Executable(
    name="delete_block", title="Delete a note block", proposers=frozenset({"agent", "client", "user"}),
    validate=_validate_delete, preconditions=_delete_preconditions, preview=_delete_preview,
    review="prompt", lifetime=timedelta(hours=1), run=_delete_run,
))


# --------------------------------------------------------------------- submit_application


def _job(ctx: Context, job_id: Any):
    from kitty.core import jobs as jobs_core

    if type(job_id) is not int or not 1 <= job_id <= 2**53 - 1:
        raise effects.EffectError("job_id is a job's number")
    job = jobs_core.get_job(ctx.conn, job_id)
    if job is None:
        raise effects.UnknownEffect("Job not found")
    return job


def _validate_submit(ctx: Context, raw: Any) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"job_id", "reason"}:
        raise effects.EffectError("An application submission names the job and, optionally, why")
    job = _job(ctx, raw.get("job_id"))
    if job.application_id is not None:
        raise effects.Refused("This application is already recorded as submitted")
    return {"job_id": job.id, "reason": _reason(raw.get("reason"))}


def _resume(ctx: Context, job) -> dict | None:
    from kitty.core.apply.filler import choose_resume

    if ctx.config is None:
        raise effects.EffectError("The application's resume needs the workspace configuration")
    resume = choose_resume(ctx.conn, ctx.config, job)
    return {"id": resume.id, "label": resume.label, "file_hash": resume.file_hash} if resume else None


def _submit_preconditions(ctx: Context, payload: dict) -> dict:
    from kitty.core import jobs as jobs_core

    job = jobs_core.get_job(ctx.conn, payload["job_id"])
    if job is None:
        return {"job": None}
    return {
        "job": {"id": job.id, "company": job.company_name, "title": job.title, "url": job.url,
                "apply_url": job.apply_url, "active": job.is_active},
        "application_id": job.application_id,
        "resume": _resume(ctx, job),
        "preparation": {"state": job.fill_state, "blocker": job.fill_blocker, "ready_at": job.fill_ready_at},
    }


def _submit_preview(ctx: Context, payload: dict, expected: dict) -> dict:
    job, resume = expected["job"], expected["resume"]
    return {"action": "Submit this application (you do it, in the browser)",
            "company": job["company"], "title": job["title"], "apply_url": job["apply_url"] or job["url"],
            "resume": resume, "preparation": expected["preparation"], "reason": payload["reason"],
            "notice": SUBMIT_HANDOFF}


SUBMIT_APPLICATION = effects.REGISTRY.register(effects.ProposalOnly(
    name="submit_application", title="Submit an application", proposers=frozenset({"agent", "client", "user"}),
    validate=_validate_submit, preconditions=_submit_preconditions, preview=_submit_preview,
    review="prompt", lifetime=timedelta(days=1), handoff=SUBMIT_HANDOFF,
))

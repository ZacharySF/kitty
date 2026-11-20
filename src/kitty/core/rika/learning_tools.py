"""Rika's career memory tool (kitty.core.rika.learnings): career_memory, a read."""
from __future__ import annotations

from kitty.core import jobs
from kitty.core.rika import learnings
from kitty.core.rika.tools import Context, Result, Tool, ToolError, integer, obj

LISTED = 20


def _career_memory(ctx: Context, args: dict) -> Result:
    job_id = args.get("job_id")
    if job_id is not None and jobs.get_job(ctx.conn, job_id) is None:
        raise ToolError(f"No job {job_id}")
    vocabulary = ctx.app.config.resume_terms
    learnings.refresh(ctx.conn, vocabulary, modules=ctx.app.modules)
    items = learnings.learnings(ctx.conn)
    kinds: dict[str, list[dict]] = {kind: [] for kind in learnings.KINDS}
    for item in items:
        if len(kinds[item["kind"]]) < LISTED:
            kinds[item["kind"]].append({"id": item["id"], "text": item["text"], "pinned": item["pinned"],
                                        **item["detail"]})
    summary = learnings.summary_for_prompt(ctx.conn, job_id=job_id, vocabulary=vocabulary)
    return Result(f"Read Rika's career memory: {len(items)} learning{'' if len(items) == 1 else 's'}",
                  {"summary": summary, "posting_skills": kinds["posting_skill"], "patterns": kinds["posting_pattern"],
                   "resume_edits": kinds["resume_edit"], "notes": kinds["note"],
                   "note": "Observed from the user's own job search and reviews: patterns, never applicant facts. "
                           "The user pins or forgets these on Rika's page, Memory tab."})


TOOLS = (
    Tool("career_memory", "Read what Rika has learned from the job postings the user saved, queued, applied to or "
         "skipped and from their resume edit reviews: skills employers they target keep asking for (and whether a "
         "confirmed Resume Workshop fact backs each), the kinds of jobs they keep choosing, edits they accept or "
         "reject, and notes they added. Give job_id to put what that posting asks for first. Use it before "
         "recommending jobs or resume edits.",
         obj(job_id=integer("ApplyAI job id to focus on")), "read", _career_memory, module="applyai"),
)

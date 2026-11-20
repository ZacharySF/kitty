"""Snapshotted, budget-bounded tailoring queues; never application submission.

A force-killed in-flight call is ambiguous, not retryable. Recovery keeps its
maximum reservation and marks that item failed while leaving unstarted work queued.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from kitty.dates import now_iso
from kitty.db.connection import transaction
from kitty.core.workshop import store

MAX_JOBS = 50


def _amount(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise store.WorkshopError("Choose a finite positive batch cost limit")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise store.WorkshopError("Invalid batch cost limit") from None
    if not amount.is_finite() or amount <= 0 or amount > 100:
        raise store.WorkshopError("Batch cost limit must be above zero and at most $100")
    return amount


def _row(conn, batch_id):
    row = conn.execute("SELECT * FROM workshop_batches WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise store.WorkshopError("Tailoring batch not found", 404)
    return row


def get(conn, batch_id):
    row = dict(_row(conn, batch_id))
    row.pop("master_snapshot")
    row["research"] = bool(row["research"])
    items = []
    for item in conn.execute("SELECT * FROM workshop_batch_items WHERE batch_id=? ORDER BY position", (batch_id,)):
        item = dict(item)
        item.pop("job_snapshot")
        items.append(item)
    row["items"] = items
    return row


def listing(conn):
    return [get(conn, r["id"]) for r in conn.execute(
        "SELECT id FROM workshop_batches ORDER BY created_at DESC,rowid DESC LIMIT 100")]


def job_choices(conn):
    return [dict(row) for row in conn.execute(
        "SELECT j.id,j.title,c.display_name AS company FROM jobs j "
        "JOIN companies c ON c.id=j.company_id WHERE j.is_active=1 "
        "ORDER BY j.updated_at DESC,j.id DESC LIMIT 500")]


def _job(conn, job_id):
    row = conn.execute(
        "SELECT j.id,j.title,j.jd_text,j.jd_hash,j.url,j.is_active,c.display_name AS company,c.website "
        "FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=?", (job_id,)).fetchone()
    if row is None:
        raise store.WorkshopError("Selected job no longer exists", 404)
    return dict(row)


def create(conn, *, master_id, base_revision, job_ids, research=False, max_cost_usd="1.00"):
    # Check source authority before resolving any job or doing external work.
    with transaction(conn, immediate=True):
        doc = store.get_doc(conn, master_id)
        if type(base_revision) is not int or doc["revision"] != base_revision:
            raise store.WorkshopError("Master changed; reload before creating a batch", 409)
        if doc["archived"]:
            raise store.WorkshopError("Choose an active master resume", 409)
        if type(research) is not bool:
            raise store.WorkshopError("Research must be true or false")
        if not isinstance(job_ids, list) or not 1 <= len(job_ids) <= MAX_JOBS:
            raise store.WorkshopError(f"Choose between 1 and {MAX_JOBS} jobs")
        if any(type(job) is not int or job <= 0 for job in job_ids):
            raise store.WorkshopError("Invalid job selection")
        amount = _amount(max_cost_usd)
        jobs = [_job(conn, job) for job in dict.fromkeys(job_ids)]
        if any(not j["is_active"] for j in jobs):
            raise store.WorkshopError("A selected posting is inactive; review the job list", 409)
        if any(not (j["jd_text"] or "").strip() for j in jobs):
            raise store.WorkshopError("Every selected job needs a saved description before tailoring", 409)
        identifier, stamp = str(uuid4()), now_iso()
        conn.execute("INSERT INTO workshop_batches "
                     "(id,master_id,master_revision,master_snapshot,research,max_cost_usd,status,created_at) "
                     "VALUES(?,?,?,?,?,?,'queued',?)",
                     (identifier, master_id, base_revision, json.dumps(doc), int(research), str(amount), stamp))
        conn.executemany("INSERT INTO workshop_batch_items "
                         "(batch_id,position,job_id,job_snapshot,company,role,status,updated_at) "
                         "VALUES(?,?,?,?,?,?,'queued',?)",
                         [(identifier, i, j["id"], json.dumps(j), j["company"], j["title"], stamp)
                          for i, j in enumerate(jobs)])
    return get(conn, identifier)


def cancel(conn, batch_id):
    with transaction(conn, immediate=True):
        row = _row(conn, batch_id)
        if row["status"] != "done":
            conn.execute("UPDATE workshop_batches SET status='cancelled',finished_at=? WHERE id=?",
                         (now_iso(), batch_id))
            conn.execute("UPDATE workshop_batch_items SET status='cancelled',updated_at=? "
                         "WHERE batch_id=? AND status IN ('queued','researching','tailoring')", (now_iso(), batch_id))
    return get(conn, batch_id)


def recover(conn):
    """On process startup, never retry a call that might already have been billed."""
    with transaction(conn, immediate=True):
        conn.execute("UPDATE workshop_batch_items SET status='failed',error=?,updated_at=? "
                     "WHERE status IN ('researching','tailoring') AND batch_id IN "
                     "(SELECT id FROM workshop_batches WHERE status='running')",
                     ("Interrupted by process restart. The request may have been billed; it was not retried.", now_iso()))
        conn.execute("UPDATE workshop_batches SET status='queued' WHERE status='running'")


def _update_item(conn, batch_id, position, **fields):
    allowed = {"status", "doc_id", "proposal_id", "research_id", "error"}
    if not fields or set(fields) - allowed:
        raise ValueError("Invalid batch item update")
    fields["updated_at"] = now_iso()
    # A cancelled queue cannot be resurrected by a late callback.
    conn.execute("UPDATE workshop_batch_items SET " + ",".join(f"{key}=?" for key in fields) +
                 " WHERE batch_id=? AND position=? AND status!='cancelled'",
                 (*fields.values(), batch_id, position))


def run(app, conn, batch_id):
    from kitty.core.workshop import ai, research

    if not ai.get_settings(conn)["enabled"]:
        raise store.WorkshopError("Enable hosted resume editing and review its privacy notice first", 403)
    with transaction(conn, immediate=True):
        batch = dict(_row(conn, batch_id))
        if batch["status"] != "queued":
            raise store.WorkshopError("This batch is already running or finished", 409)
        conn.execute("UPDATE workshop_batches SET status='running' WHERE id=?", (batch_id,))
    master = json.loads(batch["master_snapshot"])

    def cancelled():
        stopping = getattr(app, "streams_closed", None)
        return bool(stopping and stopping.is_set()) or _row(conn, batch_id)["status"] == "cancelled"

    try:
        items = conn.execute("SELECT * FROM workshop_batch_items WHERE batch_id=? AND status='queued' "
                             "ORDER BY position", (batch_id,)).fetchall()
        for row in items:
            if cancelled():
                break
            position, reserved = row["position"], False
            _update_item(conn, batch_id, position, status="researching" if batch["research"] else "tailoring")
            try:
                snapshot = json.loads(row["job_snapshot"])
                if _job(conn, row["job_id"]) != snapshot:
                    raise store.WorkshopError("Posting changed since this batch was reviewed; create a new batch", 409)
                doc = store.create_doc(
                    conn, title=(row["role"] + " @ " + row["company"])[:200], format=master["format"],
                    source=master["source"], assets=master["assets"], entrypoint=master["entrypoint"],
                    engine=master["engine"], kind="tailored", base_doc_id=batch["master_id"],
                    base_revision=batch["master_revision"], job_id=row["job_id"],
                    company=row["company"], role=row["role"], author="user")
                _update_item(conn, batch_id, position, doc_id=doc["id"])
                research_id = None
                if batch["research"]:
                    result = research.research_company(app, conn, company=row["company"], role=row["role"],
                                                      job_id=row["job_id"], urls=None)
                    research_id = result["id"]
                    _update_item(conn, batch_id, position, research_id=research_id)
                    if result["status"] == "failed":
                        raise store.WorkshopError("Company research failed; inspect its sources before retrying", 409)
                if cancelled():
                    break
                _update_item(conn, batch_id, position, status="tailoring")
                preview = ai.preview_edit(
                    app, conn, doc_id=doc["id"], instruction="Tailor this resume for the selected role. "
                    "Use only confirmed personal evidence; show gaps instead of inventing qualifications.",
                    job_id=row["job_id"], company=row["company"], role=row["role"], research_id=research_id)
                maximum = _amount(preview["maximum_cost_usd"]) if Decimal(preview["maximum_cost_usd"]) else Decimal(0)
                with transaction(conn, immediate=True):
                    current = _row(conn, batch_id)
                    if current["status"] == "cancelled":
                        break
                    used = Decimal(current["reserved_usd"])
                    if used + maximum > Decimal(current["max_cost_usd"]):
                        raise store.WorkshopError("Batch cost ceiling reached before this request was sent", 409)
                    conn.execute("UPDATE workshop_batches SET reserved_usd=? WHERE id=?", (str(used + maximum), batch_id))
                    conn.execute("UPDATE workshop_batch_items SET reserved_usd=? WHERE batch_id=? AND position=?",
                                 (str(maximum), batch_id, position))
                reserved = True
                proposal = ai.run_edit(app, conn, preview_id=preview["id"], digest=preview["digest"],
                                       confirmed=True, cancel_check=cancelled)
                if cancelled():
                    break
                _update_item(conn, batch_id, position, status="review", proposal_id=proposal["id"])
            except store.WorkshopError as exc:
                _update_item(conn, batch_id, position, status="failed", error=str(exc))
            except Exception:
                # Never persist provider bodies or key-bearing exception messages.
                note = "Tailoring failed. " + ("A request may have been billed; it was not retried." if reserved
                                               else "Check the workshop configuration and prerequisites.")
                _update_item(conn, batch_id, position, status="failed", error=note)
    finally:
        with transaction(conn, immediate=True):
            if cancelled():
                conn.execute("UPDATE workshop_batches SET status='cancelled',finished_at=? WHERE id=?", (now_iso(), batch_id))
                conn.execute("UPDATE workshop_batch_items SET status='cancelled',updated_at=? WHERE batch_id=? "
                             "AND status IN ('queued','researching','tailoring')", (now_iso(), batch_id))
            else:
                conn.execute("UPDATE workshop_batches SET status='done',finished_at=? WHERE id=?", (now_iso(), batch_id))
    return get(conn, batch_id)


def insights(conn):
    """Observed current outcomes only. Pending and ghosted are not rejections."""
    rows = conn.execute(
        "SELECT e.doc_id,e.revision,e.resume_id,d.title,r.label,a.status,COUNT(a.id) AS n "
        "FROM workshop_exports e JOIN workshop_docs d ON d.id=e.doc_id "
        "JOIN resumes r ON r.id=e.resume_id LEFT JOIN applications a ON a.resume_id=e.resume_id "
        "GROUP BY e.doc_id,e.revision,e.resume_id,a.status ORDER BY d.title,e.revision").fetchall()
    grouped = {}
    for row in rows:
        key = (row["doc_id"], row["revision"])
        entry = grouped.setdefault(key, {k: row[k] for k in ("doc_id", "revision", "resume_id", "title", "label")})
        counts = entry.setdefault("outcomes", {s: 0 for s in ("applied", "oa", "interview", "offer", "rejected", "ghosted", "withdrawn")})
        if row["status"] in counts:
            counts[row["status"]] += row["n"]
    for entry in grouped.values():
        counts = entry["outcomes"]
        entry["applications"] = sum(counts.values())
        entry["responses"] = sum(counts[s] for s in ("oa", "interview", "offer", "rejected"))
        entry["advanced"] = sum(counts[s] for s in ("oa", "interview", "offer"))
    return {"resumes": list(grouped.values()),
            "notice": "Observed outcomes, not proof that a resume caused a response. Role, company, timing and referrals differ. "
                      "Pending, ghosted and upload failures are not confirmed rejections; counts describe current stages."}

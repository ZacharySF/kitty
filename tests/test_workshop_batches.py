"""Batch creation cannot accept a stale master revision or submit an application."""
import pytest
from kitty.core.workshop import batches, store

def test_batch_rejects_stale_master_before_work(workspace):
    c = workspace.conn
    doc = store.create_doc(c, title="Synthetic", format="html", source="<p>one</p>")
    store.save_source(c, doc['id'], source="<p>two</p>", base_revision=1)
    with pytest.raises(store.WorkshopError) as caught:
        batches.create(c, master_id=doc['id'], base_revision=1, job_ids=[1], research=False, max_cost_usd="1.00")
    assert caught.value.status == 409
    assert c.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0

def test_creation_snapshots_inputs_and_deduplicates_jobs(workspace):
    from kitty.core import jobs
    c = workspace.conn
    job, _ = jobs.upsert_job(c, company="Example Robotics", title="Controls Engineer",
                            url="https://example.invalid/job", source="manual",
                            source_job_id="workshop-one", jd_text="Build test rigs.")
    doc = store.create_doc(c, title="Synthetic", format="html", source="<p>Built test rigs.</p>")
    batch = batches.create(c, master_id=doc['id'], base_revision=1,
                           job_ids=[job.id, job.id], research=False, max_cost_usd="1.00")
    store.save_source(c, doc['id'], source="<p>Later source.</p>", base_revision=1)
    assert len(batch['items']) == 1
    assert batch['master_revision'] == 1
    assert batch['items'][0]['status'] == 'queued'
    assert c.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0
    assert batches.get(c, batch['id'])['status'] == 'queued'


def test_cancelling_queue_and_recovering_interrupted_work(workspace):
    from kitty.core import jobs
    c = workspace.conn
    job, _ = jobs.upsert_job(c, company="Example Robotics", title="Engineer",
                            url="https://example.invalid/job", source="manual",
                            source_job_id="workshop-two", jd_text="Test equipment.")
    doc = store.create_doc(c, title="Synthetic", format="html", source="<p>Equipment.</p>")
    b = batches.create(c, master_id=doc['id'], base_revision=1,
                       job_ids=[job.id], research=False, max_cost_usd="1")
    cancelled = batches.cancel(c, b['id'])
    assert cancelled['status'] == 'cancelled'
    assert cancelled['items'][0]['status'] == 'cancelled'
    b = batches.create(c, master_id=doc['id'], base_revision=1,
                       job_ids=[job.id], research=False, max_cost_usd="1")
    c.execute("UPDATE workshop_batches SET status='running',reserved_usd='0.25' WHERE id=?", (b['id'],))
    c.execute("UPDATE workshop_batch_items SET status='tailoring' WHERE batch_id=?", (b['id'],))
    batches.recover(c)
    recovered = batches.get(c, b['id'])
    assert recovered['status'] == 'queued'
    assert recovered['items'][0]['status'] == 'failed'
    assert recovered['reserved_usd'] == '0.25'
    assert 'interrupted' in recovered['items'][0]['error'].lower()


@pytest.mark.parametrize("cap", ["NaN", "Infinity", "-1", "0", True])
def test_invalid_batch_cost_caps(workspace, cap):
    c = workspace.conn
    doc = store.create_doc(c, title="Synthetic", format="html", source="<p>one</p>")
    with pytest.raises(store.WorkshopError):
        batches.create(c, master_id=doc['id'], base_revision=1,
                       job_ids=[1], research=False, max_cost_usd=cap)


def _tailoring_setup(workspace, monkeypatch):
    import json
    from test_workshop_ai import _enabled, _provider
    from kitty.core import jobs
    from kitty.core.workshop import ai
    app = _enabled(workspace)
    c = workspace.conn
    fact = ai.save_fact(c, text="Built a prototype.", source="Synthetic records", confirmed=True)
    master = store.create_doc(c, title="Synthetic", format="html", source="Built a prototype.")
    selected = []
    for index in range(2):
        job, _ = jobs.upsert_job(c, company="Example Robotics", title=f"Engineer {index}",
                                url=f"https://example.invalid/job/{index}", source="manual",
                                source_job_id=f"budget-{index}", jd_text="Build prototypes.")
        selected.append(job.id)
    sent = _provider(monkeypatch, json.dumps({"summary": "Clarify", "hunks": [{
        "find": "Built a prototype.", "replace": "Built the prototype.",
        "reason": "Use precise wording", "fact_ids": [fact["id"]]}]}))
    return app, master, selected, sent


def test_batch_ceiling_and_original_master_snapshot(workspace, monkeypatch):
    app, master, selected, sent = _tailoring_setup(workspace, monkeypatch)
    c = workspace.conn
    batch = batches.create(c, master_id=master["id"], base_revision=1,
                           job_ids=selected, max_cost_usd="0.015")
    store.save_source(c, master["id"], source="Later master content.", base_revision=1)
    result = batches.run(app, c, batch["id"])
    assert [item["status"] for item in result["items"]] == ["review", "failed"]
    assert len(sent) == 1
    assert result["reserved_usd"] == "0.01"
    assert "ceiling" in result["items"][1]["error"]
    draft = store.get_doc(c, result["items"][0]["doc_id"])
    assert draft["source"] == "Built a prototype."
    assert draft["base_revision"] == 1
    assert c.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


def test_cancel_during_provider_call_never_publishes_proposal(workspace, monkeypatch):
    from kitty.core.rika import llm
    app, master, selected, _ = _tailoring_setup(workspace, monkeypatch)
    c = workspace.conn
    batch = batches.create(c, master_id=master["id"], base_revision=1,
                           job_ids=selected, max_cost_usd="1")
    original_stream = llm.stream
    def cancel_during_call(call, on_token):
        batches.cancel(c, batch["id"])
        return original_stream(call, on_token)
    monkeypatch.setattr(llm, "stream", cancel_during_call)
    result = batches.run(app, c, batch["id"])
    assert result["status"] == "cancelled"
    assert all(item["status"] == "cancelled" for item in result["items"])
    assert result["reserved_usd"] == "0.01"
    assert c.execute("SELECT COUNT(*) FROM workshop_proposals").fetchone()[0] == 0

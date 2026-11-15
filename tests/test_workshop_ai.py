"""Cloud resume editing requires explicit opt-in before any provider access."""
from types import SimpleNamespace
import pytest
from kitty.core.workshop import ai, store

def test_hosted_edit_disabled_before_model_access(workspace):
    c = workspace.conn
    doc = store.create_doc(c, title="Synthetic", format="html", source="<p>Built a prototype.</p>")
    app = SimpleNamespace(settings=workspace.settings, config=workspace.config)
    with pytest.raises(store.WorkshopError) as caught:
        ai.preview_edit(app, c, doc_id=doc['id'], instruction="Make this clearer")
    assert caught.value.status == 403
    assert store.get_doc(c, doc['id'])['revision'] == 1


def _enabled(workspace):
    app = SimpleNamespace(settings=workspace.settings, config=workspace.config)
    ai.update_settings(workspace.conn, enabled=True, model_id="synthetic/model", max_cost_usd="1")
    return app


def _provider(monkeypatch, text):
    from decimal import Decimal
    from kitty.core.rika import llm
    sent = []
    monkeypatch.setattr(llm, "prepare", lambda app, conn, model, messages, tools:
                        llm.Call(model, {"model": model, "messages": messages}, "synthetic", Decimal("0.01"), "binding"))
    monkeypatch.setattr(llm, "reserve", lambda conn, call: "reservation")
    monkeypatch.setattr(llm, "settle", lambda *args: None)
    def stream(call, on_token):
        sent.append(call.body)
        return llm.Reply(text=text, cost=Decimal("0.001"))
    monkeypatch.setattr(llm, "stream", stream)
    return sent


def test_fact_update_requires_revision(workspace):
    fact = ai.save_fact(workspace.conn, text="Built a prototype.", source="My records", confirmed=True)
    with pytest.raises(store.WorkshopError) as caught:
        ai.save_fact(workspace.conn, id=fact["id"], revision=0, text="Built two prototypes.",
                     source="My records", confirmed=True)
    assert caught.value.status == 409


def test_preview_is_exact_redacted_payload_and_one_use(workspace, monkeypatch):
    import json
    app = _enabled(workspace)
    fact = ai.save_fact(workspace.conn, text="Built a prototype.", source="Records", confirmed=True)
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html",
                           source="<p>Built a prototype.</p><p>person@example.test</p>")
    response = json.dumps({"summary": "Clarify", "hunks": [{"find": "Built a prototype.",
        "replace": "Built a prototype.", "reason": "Retain evidence", "fact_ids": [fact["id"]]}]})
    sent = _provider(monkeypatch, response)
    preview = ai.preview_edit(app, workspace.conn, doc_id=doc["id"], instruction="Clarify")
    assert "person@example.test" not in json.dumps(preview["redacted_payload"])
    proposal = ai.run_edit(app, workspace.conn, preview_id=preview["id"], digest=preview["digest"])
    assert sent == [preview["redacted_payload"]]
    assert proposal["status"] == "pending"
    with pytest.raises(store.WorkshopError):
        ai.run_edit(app, workspace.conn, preview_id=preview["id"], digest=preview["digest"])


def test_stale_fact_prevents_provider_call(workspace, monkeypatch):
    app = _enabled(workspace)
    fact = ai.save_fact(workspace.conn, text="Built a prototype.", source="Records", confirmed=True)
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="Built a prototype.")
    sent = _provider(monkeypatch, "{}")
    preview = ai.preview_edit(app, workspace.conn, doc_id=doc["id"], instruction="Clarify")
    ai.save_fact(workspace.conn, id=fact["id"], revision=1, text=fact["text"], source="Records", confirmed=False)
    with pytest.raises(store.WorkshopError) as caught:
        ai.run_edit(app, workspace.conn, preview_id=preview["id"], digest=preview["digest"])
    assert caught.value.status == 409
    assert not sent


@pytest.mark.parametrize("source,hunks", [
    ("Built. Built.", [{"find": "Built.", "replace": "Built.", "fact_ids": ["f"]}]),
    ("Built a prototype.", [{"find": "Built a prototype.", "replace": "Built 100 prototypes.", "fact_ids": ["f"]}]),
    ("Assisted with a prototype.", [{"find": "Assisted with a prototype.", "replace": "Led a prototype.", "fact_ids": ["f"]}]),
    ("Built a prototype.", [{"find": "Built a prototype.", "replace": "Built a Kubernetes prototype.", "fact_ids": ["f"]}]),
])
def test_unsafe_hunks_rejected(source, hunks):
    with pytest.raises(store.WorkshopError):
        ai.validate_hunks(source, hunks, [{"id": "f", "text": source, "confirmed": True, "revision": 1}])


def test_overlapping_hunks_rejected():
    with pytest.raises(store.WorkshopError):
        ai.validate_hunks("Built a prototype.", [
            {"find": "Built a prototype.", "replace": "Built a prototype.", "fact_ids": ["f"]},
            {"find": "a prototype", "replace": "a prototype", "fact_ids": ["f"]},
        ], [{"id": "f", "text": "Built a prototype.", "confirmed": True, "revision": 1}])


def test_review_checks_revision_and_records_preferences(workspace, monkeypatch):
    import json
    app = _enabled(workspace)
    fact = ai.save_fact(workspace.conn, text="Built a prototype.", source="Records", confirmed=True)
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="Built a prototype.")
    _provider(monkeypatch, json.dumps({"summary": "Concise", "hunks": [{
        "find": "Built a prototype.", "replace": "Built the prototype.", "reason": "Article",
        "fact_ids": [fact["id"]]}]}))
    preview = ai.preview_edit(app, workspace.conn, doc_id=doc["id"], instruction="Clarify")
    proposal = ai.run_edit(app, workspace.conn, preview_id=preview["id"], digest=preview["digest"])
    result = ai.review_proposal(workspace.conn, proposal["id"], base_revision=1,
                                accept_ids=[proposal["hunks"][0]["id"]], confirmed=True)
    assert result["document"]["revision"] == 2
    assert result["proposal"]["status"] == "applied"
    assert ai.preferences(workspace.conn)[0]["accepted"] == 1


def test_redaction_preserves_markup_and_protects_name():
    mapping = {}
    redacted = ai._redact("<h1>Synthetic Person</h1>", mapping)
    assert "Synthetic Person" not in redacted
    assert redacted.count("<h1>") == 1
    assert list(mapping.values()) == ["Synthetic Person"]


@pytest.mark.parametrize("source,find,replacement", [
    ("aaaa", "aaa", "a"),
    ("<h1>Synthetic Person</h1>", "Synthetic", "Person"),
    ("Supported a prototype.", "Supported", "Led"),
    ("Built in January.", "January", "February"),
])
def test_partial_anchors_cannot_bypass_protected_fields(source, find, replacement):
    with pytest.raises(store.WorkshopError):
        ai.validate_hunks(source, [{"find": find, "replace": replacement, "fact_ids": ["f"]}],
                          [{"id": "f", "text": source + " " + replacement, "confirmed": True, "revision": 1}])


def test_cancel_after_stream_does_not_publish(workspace, monkeypatch):
    from kitty.core.rika import llm
    app = _enabled(workspace)
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="Built a prototype.")
    _provider(monkeypatch, '{"summary":"No changes","hunks":[]}')
    preview = ai.preview_edit(app, workspace.conn, doc_id=doc["id"], instruction="Clarify")
    cancelled = False
    def stream(call, on_token):
        nonlocal cancelled
        cancelled = True
        return llm.Reply(text='{"summary":"No changes","hunks":[]}')
    monkeypatch.setattr(llm, "stream", stream)
    with pytest.raises(store.WorkshopError, match="cancelled"):
        ai.run_edit(app, workspace.conn, preview_id=preview["id"], digest=preview["digest"],
                    cancel_check=lambda: cancelled)
    assert ai.list_proposals(workspace.conn, doc["id"]) == []


def test_expired_preview_never_sends(workspace, monkeypatch):
    from datetime import timedelta
    app = _enabled(workspace)
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source="Built a prototype.")
    sent = _provider(monkeypatch, "{}")
    preview = ai.preview_edit(app, workspace.conn, doc_id=doc["id"], instruction="Clarify")
    future = ai.now() + timedelta(minutes=11)
    monkeypatch.setattr(ai, "now", lambda: future)
    with pytest.raises(store.WorkshopError, match="expired"):
        ai.run_edit(app, workspace.conn, preview_id=preview["id"], digest=preview["digest"])
    assert not sent


def test_fact_change_marks_review_stale_without_source_write(workspace, monkeypatch):
    app = _enabled(workspace)
    fact = ai.save_fact(workspace.conn, text="Built a prototype.", source="Records", confirmed=True)
    doc = store.create_doc(workspace.conn, title="Synthetic", format="html", source=fact["text"])
    _provider(monkeypatch, '{"summary":"No changes","hunks":[]}')
    preview = ai.preview_edit(app, workspace.conn, doc_id=doc["id"], instruction="Clarify")
    proposal = ai.run_edit(app, workspace.conn, preview_id=preview["id"], digest=preview["digest"])
    ai.save_fact(workspace.conn, id=fact["id"], revision=1, text=fact["text"], source="Records", confirmed=False)
    with pytest.raises(store.WorkshopError, match="stale"):
        ai.review_proposal(workspace.conn, proposal["id"], base_revision=1, accept_ids=[])
    assert store.get_doc(workspace.conn, doc["id"])["revision"] == 1
    assert ai.list_proposals(workspace.conn, doc["id"])[0]["status"] == "stale"


def test_chat_tools_do_not_disclose_disabled_documents(workspace):
    from kitty.core.rika import resume_tools
    from kitty.core.rika.tools import Context, ToolError
    doc = store.create_doc(workspace.conn, title="Private title", format="html", source="Private source")
    ctx = Context(SimpleNamespace(settings=workspace.settings, config=workspace.config), workspace.conn)
    tools = {tool.name: tool for tool in resume_tools.TOOLS}
    for name, args in [("list_resume_documents", {}), ("read_resume_document", {"doc_id": doc["id"]})]:
        with pytest.raises(ToolError, match="Enable"):
            tools[name].run(ctx, args)
    assert tools["research_resume_company"].effect == "outward"
    assert not any("run" in name or "export" in name or "apply" in name for name in tools)


def test_http_ai_routes_require_explicit_confirmation(workspace):
    from kitty import web_workshop_ai
    from kitty.web_server import APIError
    app = SimpleNamespace(settings=workspace.settings, config=workspace.config)
    assert web_workshop_ai.get(app, workspace.conn, "/api/workshop-ai", {})["settings"]["enabled"] is False
    with pytest.raises(APIError):
        web_workshop_ai.post(app, workspace.conn, "/api/workshop-ai/settings", {"enabled": True})
    with pytest.raises(APIError):
        web_workshop_ai.post(app, workspace.conn, "/api/workshop-ai/run", {"preview_id": "x", "digest": "x"})
    with pytest.raises(APIError):
        web_workshop_ai.post(app, workspace.conn, "/api/workshop-ai/research", {"company": "Synthetic"})
    result = web_workshop_ai.post(app, workspace.conn, "/api/workshop-ai/settings",
                                 {"enabled": True, "confirm": True, "max_cost_usd": "0.05"})
    assert result["settings"]["max_cost_usd"] == "0.05"
    assert web_workshop_ai.get(app, workspace.conn, "/api/unrelated", {}) is None


def test_job_preview_uses_public_job_fields_not_private_notes(workspace, monkeypatch):
    app = _enabled(workspace)
    c = workspace.conn
    c.execute("INSERT INTO companies(norm_name,display_name,created_at,updated_at) VALUES('synthetic','Synthetic','now','now')")
    company_id = c.execute("SELECT id FROM companies WHERE norm_name='synthetic'").fetchone()[0]
    c.execute("INSERT INTO jobs(company_id,title,url,dedupe_key,jd_text,my_notes,first_seen_at,last_seen_at,created_at,updated_at)"
              " VALUES(?,'Engineer','https://example.test/job','workshop-synthetic','Kubernetes required','PRIVATE NOTE','now','now','now','now')",
              (company_id,))
    job_id = c.execute("SELECT id FROM jobs WHERE dedupe_key='workshop-synthetic'").fetchone()[0]
    doc = store.create_doc(c, title="Synthetic", format="html", source="Built a prototype.")
    _provider(monkeypatch, "{}")
    preview = ai.preview_edit(app, c, doc_id=doc["id"], instruction="Clarify", job_id=job_id)
    import json
    body = json.dumps(preview["redacted_payload"])
    assert "Kubernetes required" in body
    assert "PRIVATE NOTE" not in body

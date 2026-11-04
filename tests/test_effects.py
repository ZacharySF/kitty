"""The effects kernel: digest binding, single use, expiry, who approves, and a kind with no executor."""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest

from kitty import web_effects
from kitty.core import block_api, effect_kinds, effects
from kitty.core.blocks.actor import Actor, acting
from kitty.web_server import APIError, WebApplication

USER = Actor("user", None, "ui-window")
AGENT = Actor("agent", "omp", "agent-session-1")
#: The kinds the synthetic route below owns (effects.approve/execute/deny refuse any other).
OWN = frozenset({"set_value"})


def _kind(registry: effects.Registry, *, run=None, **extra) -> effects.Kind:
    """A synthetic kind whose preconditions read a mutable dict standing in for state."""
    fields = dict(name="set_value", title="Set a value", proposers=frozenset({"agent", "user"}),
                  preconditions=lambda state, payload: {"revision": state["revision"], "target": state["target"]},
                  **extra)
    kind = effects.Executable(**fields, run=run or (lambda state, effect: state.setdefault("runs", []).append(effect.payload)))
    return registry.register(kind)


@pytest.fixture
def kernel(workspace):
    registry = effects.Registry()
    _kind(registry)
    state = {"revision": 1, "target": "alpha"}
    return SimpleNamespace(conn=workspace.conn, registry=registry, state=state)


def _propose(kernel, payload=None, actor=AGENT, **extra):
    return effects.propose(kernel.conn, "set_value", payload or {"value": 1}, actor=actor, ctx=kernel.state,
                           registry=kernel.registry, **extra)


def _approve(kernel, effect, digest=None, actor=USER, kinds=OWN, **extra):
    return effects.approve(kernel.conn, effect.id, digest or effect.digest, kinds=kinds, actor=actor, ctx=kernel.state,
                           registry=kernel.registry, **extra)


def _execute(kernel, effect, digest=None, actor=USER, kinds=OWN, **extra):
    return effects.execute(kernel.conn, effect.id, digest or effect.digest, kinds=kinds, actor=actor, ctx=kernel.state,
                           registry=kernel.registry, **extra)


def _states(conn, effect):
    return [event["state"] for event in effects.events(conn, effect.id)]


def test_the_digest_is_the_canonical_kind_payload_and_expected_preconditions():
    first = effects.digest("k", {"b": 1, "a": [1, "é"]}, {"rev": 3})
    assert first == effects.digest("k", {"a": [1, "é"], "b": 1}, {"rev": 3})
    assert len({first, effects.digest("k", {"a": [1, "é"], "b": 2}, {"rev": 3}),
                effects.digest("k", {"a": [1, "é"], "b": 1}, {"rev": 4}),
                effects.digest("other", {"a": [1, "é"], "b": 1}, {"rev": 3})}) == 4
    with pytest.raises(effects.EffectError):
        effects.canonical(float("nan"))


def test_propose_approve_execute_runs_once_and_records_every_transition(kernel):
    effect = _propose(kernel)
    assert effect.state == "proposed" and effect.actor == AGENT
    assert effect.preconditions == {"revision": 1, "target": "alpha"}
    assert effect.digest == effects.digest("set_value", {"value": 1}, {"revision": 1, "target": "alpha"})
    approved = _approve(kernel, effect)
    assert approved.state == "approved" and approved.approved_session == USER.session
    done, _ = _execute(kernel, effect)
    assert done.state == "executed" and kernel.state["runs"] == [{"value": 1}]
    assert _states(kernel.conn, effect) == ["proposed", "approved", "executed"]
    with pytest.raises(effects.Refused, match="already executed"):
        _execute(kernel, effect)
    assert kernel.state["runs"] == [{"value": 1}]


@pytest.mark.parametrize("stage", ["approve", "execute"])
def test_any_precondition_change_refuses_for_good(kernel, stage):
    effect = _propose(kernel)
    if stage == "execute":
        _approve(kernel, effect)
    kernel.state["target"] = "beta"
    with pytest.raises(effects.Refused) as refused:
        (_approve if stage == "approve" else _execute)(kernel, effect)
    assert refused.value.changed == ("target",)
    kernel.state["target"] = "alpha"  # changing it back does not revive the effect
    with pytest.raises(effects.Refused, match="already refused"):
        (_approve if stage == "approve" else _execute)(kernel, effect)
    assert "runs" not in kernel.state
    assert _states(kernel.conn, effect)[-1] == "refused"
    detail = effects.events(kernel.conn, effect.id)[-1]["detail"]
    assert detail["changed"] == ["target"]


def test_approving_a_different_payload_or_precondition_digest_is_refused(kernel):
    effect = _propose(kernel)
    other = effects.digest("set_value", {"value": 2}, effect.preconditions)
    with pytest.raises(effects.Refused, match="differs"):
        _approve(kernel, effect, other)
    second = _propose(kernel)
    with pytest.raises(effects.Refused, match="differs"):
        _approve(kernel, second, effects.digest("set_value", second.payload, {"revision": 2, "target": "alpha"}))
    third = _propose(kernel)
    _approve(kernel, third)
    with pytest.raises(effects.Refused, match="differs"):
        _execute(kernel, third, other)
    assert "runs" not in kernel.state


def test_proposals_and_approvals_expire(kernel):
    effect = _propose(kernel)
    late = effects.now() + timedelta(hours=2)
    with pytest.raises(effects.Refused, match="expired"):
        _approve(kernel, effect, at=late)
    assert _states(kernel.conn, effect) == ["proposed", "expired"]
    fresh = _propose(kernel)
    _approve(kernel, fresh)
    with pytest.raises(effects.Refused, match="approval expired"):
        _execute(kernel, fresh, at=effects.now() + effects.DEFAULT_APPROVAL_LIFETIME + timedelta(seconds=1))
    assert "runs" not in kernel.state
    assert effects.pending(kernel.conn, review=None, registry=kernel.registry, at=late) == []


@pytest.mark.parametrize("actor", [AGENT, Actor("client", "Tern", "client-1"), Actor("system", "compaction"),
                                   Actor("peer", "laptop"), Actor("user")])
def test_only_a_user_window_session_approves_or_denies(kernel, actor):
    effect = _propose(kernel)
    with pytest.raises(effects.Forbidden):
        _approve(kernel, effect, actor=actor)
    with pytest.raises(effects.Forbidden):
        effects.deny(kernel.conn, effect.id, kinds=OWN, actor=actor)
    assert _states(kernel.conn, effect) == ["proposed"]


def test_an_approval_belongs_to_the_session_that_granted_it(kernel):
    effect = _propose(kernel)
    _approve(kernel, effect)
    for other in (Actor("user", None, "ui-another-launch"), AGENT):
        with pytest.raises(effects.Forbidden):
            _execute(kernel, effect, actor=other)
    _execute(kernel, effect)
    assert kernel.state["runs"] == [{"value": 1}]


def test_a_kind_lists_who_may_propose_it(kernel):
    with pytest.raises(effects.Forbidden):
        _propose(kernel, actor=Actor("peer", "laptop"))


def test_a_route_approves_executes_or_denies_only_the_kinds_it_owns(kernel):
    registry = kernel.registry
    registry.register(effects.Executable(name="other_value", title="Other", proposers=frozenset({"agent"}),
                                         preconditions=lambda state, payload: {}, run=lambda state, effect: "ran other"))
    effect = _propose(kernel)
    elsewhere = frozenset({"other_value"})
    attempts = (
        lambda: _approve(kernel, effect, kinds=elsewhere),
        lambda: effects.approve_and_execute(kernel.conn, effect.id, effect.digest, kinds=elsewhere, actor=USER,
                                            ctx=kernel.state, registry=registry),
        lambda: effects.deny(kernel.conn, effect.id, kinds=elsewhere, actor=USER),
    )
    for attempt in attempts:
        with pytest.raises(effects.Forbidden, match="own view"):
            attempt()
    assert _states(kernel.conn, effect) == ["proposed"]
    _approve(kernel, effect)
    with pytest.raises(effects.Forbidden, match="own view"):
        _execute(kernel, effect, kinds=elsewhere)
    assert _states(kernel.conn, effect) == ["proposed", "approved"] and "runs" not in kernel.state
    # A bare kind name is not a set of kinds ("set_value" would contain "value"); nor is an empty set.
    for wrong in ("set_value", frozenset(), None):
        with pytest.raises(TypeError):
            _execute(kernel, effect, kinds=wrong)
    _execute(kernel, effect)
    assert kernel.state["runs"] == [{"value": 1}]


def test_an_executor_error_rolls_back_and_an_inline_approval_leaves_the_proposal_pending(kernel):
    registry = effects.Registry()

    def refuse(state, effect):
        raise effects.Refused("Over budget")
    _kind(registry, run=refuse)
    effect = effects.propose(kernel.conn, "set_value", {"value": 1}, actor=USER, ctx=kernel.state, registry=registry)
    with pytest.raises(effects.Refused, match="Over budget"):
        effects.approve_and_execute(kernel.conn, effect.id, effect.digest, kinds=OWN, actor=USER, ctx=kernel.state,
                                    registry=registry)
    assert _states(kernel.conn, effect) == ["proposed"]


def test_inline_approve_and_execute_is_single_use(kernel):
    effect = _propose(kernel, actor=USER)
    done, _ = effects.approve_and_execute(kernel.conn, effect.id, effect.digest, kinds=OWN, actor=USER, ctx=kernel.state,
                                          registry=kernel.registry, options={"budget_usd": "0.02"})
    assert done.state == "executed" and _states(kernel.conn, effect) == ["proposed", "approved", "executed"]
    assert effects.events(kernel.conn, effect.id)[1]["detail"] == {"options": {"budget_usd": "0.02"}}
    with pytest.raises(effects.Refused):
        effects.approve_and_execute(kernel.conn, effect.id, effect.digest, kinds=OWN, actor=USER, ctx=kernel.state,
                                    registry=kernel.registry)
    assert kernel.state["runs"] == [{"value": 1}]


def test_the_tables_are_append_only_and_allow_one_end(kernel):
    effect = _propose(kernel)
    _approve(kernel, effect)
    _execute(kernel, effect)
    for sql in ("UPDATE effects SET digest=digest", "DELETE FROM effects", "UPDATE effect_events SET state='proposed'",
                "DELETE FROM effect_events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            kernel.conn.execute(sql)
    with pytest.raises(sqlite3.DatabaseError):
        kernel.conn.execute("INSERT INTO effect_events(effect_id,state,actor,at) VALUES(?,'executed','user','t')", (effect.id,))
    unapproved = _propose(kernel)
    with pytest.raises(sqlite3.DatabaseError, match="invalid effect transition"):
        kernel.conn.execute("INSERT INTO effect_events(effect_id,state,actor,at) VALUES(?,'executed','user','t')", (unapproved.id,))


def test_policy_grants_are_single_use_and_bound_to_the_digest():
    kind = effects.Kind(name="step", title="Step", proposers=frozenset({"system"}), preconditions=lambda ctx, p: {},
                        approver="policy", policy=lambda payload, pre: payload["action"] in pre["allowed"])
    allowed = effects.Proposal("step", {"action": "wait"}, {"allowed": ["wait", "handoff"]})
    grant = effects.policy_approve(kind, allowed)
    with pytest.raises(effects.Refused):
        grant.consume(effects.Proposal("step", {"action": "handoff"}, {"allowed": ["wait", "handoff"]}))
    grant.consume(allowed)
    with pytest.raises(effects.Refused, match="already used"):
        grant.consume(allowed)
    with pytest.raises(effects.Refused):
        effects.policy_approve(kind, effects.Proposal("step", {"action": "reload"}, {"allowed": ["wait"]}))


# --------------------------------------------------------------------- submit_application


def test_submit_application_has_no_executor_and_none_can_be_registered():
    kind = effects.REGISTRY.get("submit_application")
    assert isinstance(kind, effects.ProposalOnly) and not isinstance(kind, effects.Executable)
    assert not hasattr(kind, "run")
    with pytest.raises(TypeError):
        effects.Executable(name="submit_application", title="Submit", proposers=frozenset({"user"}),
                           preconditions=lambda ctx, payload: {}, run=lambda ctx, effect: None)
    with pytest.raises((TypeError, AttributeError)):
        kind.run = lambda ctx, effect: None  # type: ignore[attr-defined]
    fresh = effects.Registry()
    with pytest.raises(TypeError):
        fresh.register(effects.Kind(name="submit_application", title="Submit", proposers=frozenset({"user"}),
                                    preconditions=lambda ctx, payload: {}))
    with pytest.raises(ValueError):
        effects.REGISTRY.register(kind)
    with pytest.raises(effects.NotExecutable) as error:
        effects.REGISTRY.executable("submit_application")
    assert isinstance(error.value, TypeError)


# --------------------------------------------------------------------- the review list


@pytest.fixture
def app(workspace):
    return SimpleNamespace(ui_session="ui-test-window", config=workspace.config, settings=workspace.settings,
                           _no_query=WebApplication._no_query)


def _page(conn):
    made = block_api.create_page(conn, "Plans", blocks=["keep this", {"text": "drop this", "children": ["and its child"]}])
    return made["page_id"], [made["blocks"][0]["id"], made["blocks"][1]["id"], made["blocks"][2]["id"]]


def _agent_proposes(conn, kind, payload, config=None):
    return effects.propose(conn, kind, payload, actor=AGENT, ctx=effect_kinds.Context(conn, config))


def test_an_agent_proposed_block_delete_is_approved_in_the_window_and_executes(workspace, app):
    conn = workspace.conn
    page, (keep, drop, child) = _page(conn)
    effect = _agent_proposes(conn, "delete_block", {"block": drop, "reason": "duplicate"})
    listed = web_effects.get(app, conn, "/api/effects", {})["pending"]
    assert [item["id"] for item in listed] == [effect.id]
    assert listed[0]["preview"]["text"] == "drop this" and listed[0]["preview"]["child_count"] == 1
    assert listed[0]["actor"]["kind"] == "agent" and listed[0]["executable"] is True
    approved = web_effects.post(app, conn, "/api/effects/approve", {"id": effect.id, "digest": effect.digest})
    assert approved["effect"]["state"] == "approved" and approved["pending"] == []
    done = web_effects.post(app, conn, "/api/effects/execute", {"id": effect.id, "digest": effect.digest})
    assert done["effect"]["state"] == "executed"
    assert [block["text"] for block in block_api.get_page(conn, page)["blocks"]] == ["keep this"]
    actor = conn.execute("SELECT actor, actor_session FROM op_log ORDER BY seq DESC LIMIT 1").fetchone()
    assert tuple(actor) == ("agent", AGENT.session)


@pytest.mark.parametrize("change", ["text", "child"])
def test_a_block_delete_whose_target_changed_after_approval_is_refused(workspace, app, change):
    conn = workspace.conn
    page, (keep, drop, child) = _page(conn)
    effect = _agent_proposes(conn, "delete_block", {"block": drop})
    web_effects.post(app, conn, "/api/effects/approve", {"id": effect.id, "digest": effect.digest})
    with acting(Actor("user")):
        if change == "text":
            block_api.update_block(conn, drop, text="drop this? no, keep it")
        else:
            block_api.append_blocks(conn, page, ["new child"], parent=drop)
    with pytest.raises(APIError) as refused:
        web_effects.post(app, conn, "/api/effects/execute", {"id": effect.id, "digest": effect.digest})
    assert refused.value.status == 409
    assert refused.value.problems == [{"precondition": "subtree" if change == "child" else "target"}]
    assert len(block_api.get_page(conn, page)["blocks"]) == 2
    assert effects.get(conn, effect.id).state == "refused"


def test_agents_cannot_approve_and_inline_kinds_stay_in_their_view(workspace, app):
    conn = workspace.conn
    page, (keep, drop, child) = _page(conn)
    effect = _agent_proposes(conn, "delete_block", {"block": drop})
    with pytest.raises(effects.Forbidden):
        effects.approve(conn, effect.id, effect.digest, kinds={"delete_block"}, actor=AGENT, ctx=effect_kinds.Context(conn))
    denied = web_effects.post(app, conn, "/api/effects/deny", {"id": effect.id})
    assert denied["effect"]["state"] == "denied" and denied["recent"][0]["id"] == effect.id
    with pytest.raises(APIError) as unknown:
        web_effects.post(app, conn, "/api/effects/approve", {"id": "0" * 32, "digest": "x"})
    assert unknown.value.status == 404
    # An effect reviewed in its own view (here, a paid AI request) is refused by the list's routes.
    from kitty import web_ai

    assert effects.REGISTRY.get(web_ai.REQUEST.name).review == "inline"
    inline = effects.Registry()
    inline.register(effects.Executable(name="ai_request", title="Paid AI request", proposers=frozenset({"user"}),
                                       preconditions=lambda ctx, payload: {}, run=lambda ctx, effect: None))
    request = effects.propose(conn, "ai_request", {"prompt": "synthetic"}, actor=USER, registry=inline)
    for path in ("/api/effects/approve", "/api/effects/execute", "/api/effects/deny"):
        with pytest.raises(APIError) as elsewhere:
            web_effects.post(app, conn, path, {"id": request.id, **({} if path.endswith("deny") else
                                                                    {"digest": request.digest})})
        assert elsewhere.value.status == 403 and "own view" in str(elsewhere.value)
    assert effects.get(conn, request.id).state == "proposed"


def _job(workspace):
    from kitty.core import jobs as jobs_core

    job, _ = jobs_core.upsert_job(
        workspace.conn, company="Synthetic Labs", title="Intern", url="https://example.com/jobs/1",
        apply_url="https://example.com/jobs/1/apply", source="manual", source_job_id="synthetic-effects")
    return job.id


def test_submit_application_shows_what_would_be_submitted_and_approving_only_records_the_decision(workspace, app):
    conn = workspace.conn
    job_id = _job(workspace)
    effect = _agent_proposes(conn, "submit_application", {"job_id": job_id}, workspace.config)
    listed = web_effects.get(app, conn, "/api/effects", {})["pending"][0]
    assert listed["executable"] is False and "never submits" in listed["handoff"]
    assert listed["preview"]["company"] == "Synthetic Labs"
    assert listed["preview"]["apply_url"] == "https://example.com/jobs/1/apply"
    approved = web_effects.post(app, conn, "/api/effects/approve", {"id": effect.id, "digest": effect.digest})
    assert approved["effect"]["state"] == "approved" and "press submit yourself" in approved["notice"].lower()
    with pytest.raises(APIError) as typed:
        web_effects.post(app, conn, "/api/effects/execute", {"id": effect.id, "digest": effect.digest})
    assert typed.value.status == 400 and "no executor" in str(typed.value)
    assert effects.get(conn, effect.id).state == "approved"
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


# --------------------------------------------------------------------- agent surfaces


def test_an_mcp_agent_proposes_but_has_no_way_to_approve(workspace, app):
    import asyncio
    import json

    from mcp import Client

    from kitty.mcp_server import build_server

    page, (keep, drop, child) = _page(workspace.conn)

    async def run():
        async with Client(build_server(workspace.settings, ("applyai", "workspace"))) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            result = await client.call_tool("propose_delete_block", {"block": drop, "reason": "duplicate"})
            proposed = json.loads(result.content[0].text)
            state = json.loads((await client.call_tool("get_effect", {"effect_id": proposed["id"]})).content[0].text)
            return names, proposed, state

    names, proposed, state = asyncio.run(run())
    assert {"propose_delete_block", "propose_submit_application", "get_effect", "get_application_proposal"} <= names
    assert not {name for name in names if "approve" in name or "execute" in name}
    assert proposed["state"] == state["state"] == "proposed" and proposed["actor"]["kind"] == "agent"
    assert [block["text"] for block in block_api.get_page(workspace.conn, page)["blocks"]] == ["keep this", "drop this"]
    effect = effects.get(workspace.conn, proposed["id"])
    web_effects.post(app, workspace.conn, "/api/effects/approve", {"id": effect.id, "digest": effect.digest})
    web_effects.post(app, workspace.conn, "/api/effects/execute", {"id": effect.id, "digest": effect.digest})
    assert [block["text"] for block in block_api.get_page(workspace.conn, page)["blocks"]] == ["keep this"]


def test_kitty_effects_propose_records_an_agent_session_proposal(workspace, monkeypatch):
    from typer.testing import CliRunner

    from kitty import cli

    page, (keep, drop, child) = _page(workspace.conn)
    job_id = _job(workspace)
    monkeypatch.setattr(cli, "_open", lambda: SimpleNamespace(
        settings=workspace.settings, config=workspace.config, conn=workspace.conn, close=lambda: None))
    monkeypatch.setenv("KITTY_AGENT_NAME", "omp")
    deleted = CliRunner().invoke(cli.app, ["effects", "propose-delete", drop, "--reason", "duplicate"])
    submitted = CliRunner().invoke(cli.app, ["effects", "propose-submit", str(job_id)])
    assert deleted.exit_code == 0 and submitted.exit_code == 0, deleted.output + submitted.output
    listed = CliRunner().invoke(cli.app, ["effects", "list", "--json"])
    pending = __import__("json").loads(listed.output)["pending"]
    assert [(item["kind"], item["actor"]["kind"], item["actor"]["name"]) for item in pending] == [
        ("delete_block", "agent", "omp"), ("submit_application", "agent", "omp")]
    refused = CliRunner().invoke(cli.app, ["effects", "propose-delete", page])
    assert refused.exit_code == 1


# --------------------------------------------------------------------- the mediated agent surface


def test_an_agent_token_proposes_through_the_mediator_journaled_and_reads_only_its_own(workspace):
    from test_agent_mediation import _journal, _session

    from kitty.core import agent_access

    conn = workspace.conn
    page, (keep, drop, _child) = _page(conn)
    mine, other = _session(conn, kind="omp", name="omp"), _session(conn, kind="codex", name="codex")
    proposed = agent_access.call(conn, mine, "propose_delete_block", {"block": drop, "reason": "duplicate"})
    assert proposed["state"] == "proposed" and proposed["kind"] == "delete_block"
    assert proposed["actor"] == {"kind": "agent", "name": "omp", "session": mine["id"]}
    assert "cannot approve" in proposed["notice"]
    assert [block["text"] for block in block_api.get_page(conn, page)["blocks"]] == ["keep this", "drop this"]
    assert [(row["tool"], row["op"], row["block_id"], row["decision"]) for row in _journal(conn, mine["id"])] == [
        ("propose_delete_block", "write", drop, "allow")]
    assert agent_access.call(conn, mine, "get_effect", {"effect_id": proposed["id"]})["events"][0]["state"] == "proposed"
    with pytest.raises(agent_access.AccessError) as hidden:
        agent_access.call(conn, other, "get_effect", {"effect_id": proposed["id"]})
    assert hidden.value.status == 404
    # No mediated tool approves, executes or denies, and the kernel refuses the agent's session.
    assert not {name for name in agent_access.TOOLS if {"approve", "execute", "deny"} & set(name.split("_"))}
    with pytest.raises(effects.Forbidden):
        effects.approve(conn, proposed["id"], proposed["digest"], kinds={"delete_block"},
                        actor=Actor("agent", "omp", mine["id"]), ctx=effect_kinds.Context(conn))
    reader = _session(conn, scopes=("notes.read",))
    with pytest.raises(agent_access.AccessError) as scoped:
        agent_access.call(conn, reader, "propose_delete_block", {"block": keep})
    assert scoped.value.status == 403
    assert [effect.id for effect in effects.pending(conn)] == [proposed["id"]]


def test_a_policy_that_denies_the_write_refuses_the_proposal_and_stores_nothing(workspace):
    from test_agent_mediation import _journal, _session

    from kitty.core import agent_access, agent_policy

    conn = workspace.conn
    _, (_keep, drop, _child) = _page(conn)
    agent_policy.apply(conn, "default: allow\nrules:\n  - {decision: deny, purposes: [notes], ops: [write]}\n")
    session = _session(conn)
    with pytest.raises(agent_access.AccessError) as refused:
        agent_access.call(conn, session, "propose_delete_block", {"block": drop})
    assert refused.value.status == 403
    assert conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0
    assert [(row["tool"], row["decision"]) for row in _journal(conn, session["id"])] == [("propose_delete_block", "deny")]


def test_an_agent_tab_proposes_over_http_mcp_and_cli_and_only_the_window_approves(tmp_path, monkeypatch):
    import asyncio
    import json
    from contextlib import closing

    from mcp import Client
    from terminal_support import make_root, site
    from test_agent_mediation import _agent_token
    from test_web_clients import call as ingress_call
    from typer.testing import CliRunner

    from kitty import cli
    from kitty.agent_client import AgentClient
    from kitty.db import connect
    from kitty.mcp_server import build_server

    settings = make_root(tmp_path, ("workspace",))
    with site(settings) as running:
        with closing(connect(settings.db_path)) as conn:
            with acting(Actor("user")):
                page, (keep, drop, _child) = _page(conn)
            session, token = _agent_token(running, conn)
        status, info, _ = ingress_call(running, "GET", "/api/ingress/agent/session", token=token)
        assert status == 200 and {"propose_delete_block", "get_effect"} <= set(info["tools"])
        status, proposed, _ = ingress_call(running, "POST", "/api/ingress/agent/call",
                                           {"tool": "propose_delete_block", "arguments": {"block": drop}}, token=token)
        assert status == 200 and proposed["state"] == "proposed" and proposed["actor"]["session"] == session["id"]

        # The agent tab's MCP server and `kitty effects` propose through the same mediated tools.
        async def run():
            remote = AgentClient(f"http://127.0.0.1:{running.port}", token)
            async with Client(build_server(None, remote=remote)) as client:
                names = {tool.name for tool in (await client.list_tools()).tools}
                result = await client.call_tool("get_effect", {"effect_id": proposed["id"]})
                return names, json.loads(result.content[0].text)

        names, seen = asyncio.run(run())
        assert {"propose_delete_block", "get_effect"} <= names and seen["state"] == "proposed"
        assert not {name for name in names if "approve" in name or "execute" in name or "submit" in name}
        monkeypatch.setenv("KITTY_AGENT_TOKEN", token)
        monkeypatch.setenv("KITTY_AGENT_ORIGIN", f"http://127.0.0.1:{running.port}")
        tab = CliRunner().invoke(cli.app, ["effects", "propose-delete", keep, "--json"])
        assert tab.exit_code == 0, tab.output
        assert json.loads(tab.output)["actor"]["session"] == session["id"]
        shown = CliRunner().invoke(cli.app, ["effects", "show", proposed["id"], "--json"])
        assert shown.exit_code == 0 and json.loads(shown.output)["state"] == "proposed"
        submit = CliRunner().invoke(cli.app, ["effects", "propose-submit", "1"])
        assert submit.exit_code == 1 and "Agent tab" in submit.output

        # The token is not the UI secret: no approval route takes it, with or without a page's headers.
        body = {"id": proposed["id"], "digest": proposed["digest"]}
        for path in ("/api/effects/approve", "/api/effects/execute", "/api/effects/deny"):
            assert ingress_call(running, "POST", path, body, token=token)[0] in {401, 403}
            assert running.request(path, body, headers={"X-Kitty-Secret": None,
                                                        "Authorization": f"Bearer {token}"})[0] in {401, 403}
            assert running.request(path, body, headers={"Authorization": f"Bearer {token}"})[0] == 403
        assert ingress_call(running, "POST", "/api/ingress/agent/call",
                            {"tool": "approve_effect", "arguments": body}, token=token)[0] == 404

        # rika!'s own window approves; the deletion runs as the agent's session.
        assert running.request("/api/effects/approve", body)[0] == 200
        status, done = running.request("/api/effects/execute", body)
        assert status == 200 and done["effect"]["state"] == "executed"
        with closing(connect(settings.db_path)) as conn:
            assert [block["text"] for block in block_api.get_page(conn, page)["blocks"]] == ["keep this"]
            actor = conn.execute("SELECT actor, actor_session FROM op_log ORDER BY seq DESC LIMIT 1").fetchone()
            assert tuple(actor) == ("agent", session["id"])
        status, final, _ = ingress_call(running, "POST", "/api/ingress/agent/call",
                                        {"tool": "get_effect", "arguments": {"effect_id": proposed["id"]}}, token=token)
        assert status == 200 and final["state"] == "executed"

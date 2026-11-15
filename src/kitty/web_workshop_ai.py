"""Local Workshop privacy controls and explicitly confirmed remote operations."""
from __future__ import annotations

from contextlib import closing
import re

from kitty.core.workshop import ai, research, store
from kitty.db import connect

PREFIX = "/api/workshop-ai"


def _error(exc):
    from kitty.web_server import APIError
    return APIError(exc.status, str(exc))


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise store.WorkshopError("Invalid Workshop AI request fields")


def _confirm(body):
    if body.get("confirm") is not True:
        raise store.WorkshopError("Explicit confirmation is required", 403)


def _task(app, name, work):
    def run():
        try:
            with closing(connect(app.settings.db_path)) as conn:
                return work(conn)
        except store.WorkshopError as exc:
            raise _error(exc) from None
        finally:
            app.mutated()
    return {"task": app.tasks.start(name, run)}


def get(app, conn, path, query):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    try:
        if path == PREFIX:
            _keys(query, ())
            return {"settings": ai.get_settings(conn), "facts": ai.list_facts(conn),
                    "preferences": ai.preferences(conn)}
        if path == PREFIX + "/proposals":
            _keys(query, ("doc_id",), ("doc_id",))
            return {"proposals": ai.list_proposals(conn, ai.text(query["doc_id"], "doc_id", 100))}
        if path == PREFIX + "/research":
            _keys(query, ("company",))
            return {"research": research.list_research(conn, company=query.get("company", ""))}
        return None
    except store.WorkshopError as exc:
        raise _error(exc) from None


def post(app, conn, path, body):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    try:
        if path == PREFIX + "/settings":
            _keys(body, ("enabled", "model_id", "max_cost_usd", "confirm"), ("enabled", "confirm"))
            _confirm(body)
            return {"settings": ai.update_settings(conn, enabled=body["enabled"],
                    model_id=body.get("model_id"), max_cost_usd=body.get("max_cost_usd"))}
        if path == PREFIX + "/facts":
            _keys(body, ("text", "source", "confirmed", "id", "revision"), ("text", "source", "confirmed"))
            return {"fact": ai.save_fact(conn, **body)}
        if path == PREFIX + "/preview":
            _keys(body, ("doc_id", "instruction", "job_id", "company", "role", "research_id"), ("doc_id", "instruction"))
            return {"preview": ai.preview_edit(app, conn, **body)}
        if path == PREFIX + "/run":
            _keys(body, ("preview_id", "digest", "confirm"), ("preview_id", "digest", "confirm"))
            _confirm(body)
            ai.require_enabled(app, conn)
            identifier = ai.text(body["preview_id"], "preview_id", 100)
            digest = ai.text(body["digest"], "digest", 100)
            return _task(app, "workshop-ai-" + identifier,
                         lambda own: {"proposal": ai.run_edit(app, own, preview_id=identifier, digest=digest, confirmed=True)})
        if path == PREFIX + "/research":
            _keys(body, ("company", "role", "job_id", "urls", "confirm"), ("company", "confirm"))
            _confirm(body)
            ai.require_enabled(app, conn)
            args = {key: value for key, value in body.items() if key != "confirm"}
            research._public_label(args["company"], "company")
            return _task(app, "workshop-research", lambda own: {"research": research.research_company(app, own, **args)})
        match = re.fullmatch(PREFIX + r"/proposals/([0-9a-f-]{36})/review", path)
        if match:
            _keys(body, ("base_revision", "accept_ids", "confirm"), ("base_revision", "accept_ids", "confirm"))
            _confirm(body)
            return ai.review_proposal(conn, match[1], base_revision=body["base_revision"],
                                      accept_ids=body["accept_ids"], confirmed=True)
        return None
    except store.WorkshopError as exc:
        raise _error(exc) from None

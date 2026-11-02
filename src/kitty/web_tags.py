"""Workspace module: tags and auto-tagging (platform design Q13), kitty.core.blocks.tags.

GET  /api/tags                          {mode, revision, tags: [{id, name, keywords, aliases, mode,
                                         short, effective_mode, count, revision}]}
GET  /api/tags/page?id=<tag>[&offset=n]  {tag, blocks, count, next_offset, unlinked, unlinked_count}
GET  /api/tags/find?name=<name>          {id}: the tag a #name or alias names (null when none)
POST /api/tags/settings  {mode, revision}           the global mode: suggest or always
POST /api/tags/create    {name, keywords?, aliases?, mode?, short?}
POST /api/tags/update    {id, name?, keywords?, aliases?, mode?, short?}   (mode null: follow the setting)
POST /api/tags/delete    {id}                       to Trash
POST /api/tags/link-all  {id}                       attach every unlinked mention
POST /api/tags/attach    {block, tag}               by hand
POST /api/tags/remove    {block, tag}               detach and remember (never re-attached or suggested)

Every write is a block transaction, so it is logged, undoable from History and pushed
live to every window (the editor's own Tab/Esc go over the blocks socket instead).
"""
from __future__ import annotations

import re

from kitty.core import blocks
from kitty.core.blocks import tags

PREFIX = "/api/tags"


def _error(status: int, message: str):
    from kitty.web_server import APIError
    return APIError(status, message)


def _offset(value) -> int:
    if value is None:
        return 0
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,8}", value):
        raise blocks.BlockError("Offset must be a whole number")
    return int(value)


def _expect(body: dict, keys: set[str]) -> None:
    if set(body) != keys:
        raise blocks.BlockError("Expected {" + ", ".join(f'"{key}"' for key in sorted(keys)) + "}")


def get(app, conn, path, query):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    try:
        if path == PREFIX:
            app._no_query(query)
            return tags.list_tags(conn)
        if path == PREFIX + "/page":
            if set(query) - {"id", "offset"} or "id" not in query:
                raise blocks.BlockError("Name the tag page with id")
            return tags.page(conn, query["id"], _offset(query.get("offset")))
        if path == PREFIX + "/find":
            if set(query) != {"name"}:
                raise blocks.BlockError("Name the tag with name")
            return {"id": tags.find(conn, query["name"])}
    except blocks.BlockError as exc:
        raise _error(exc.status, str(exc)) from None
    return None


def post(app, conn, path, body):
    if not path.startswith(PREFIX + "/"):
        return None
    action = path.removeprefix(PREFIX + "/")
    try:
        if action == "settings":
            _expect(body, {"mode", "revision"})
            return tags.set_mode(conn, app.settings.root, body["mode"], body["revision"])
        if action == "create":
            return tags.create(conn, body)
        if action == "update":
            if "id" not in body:
                raise blocks.BlockError("Name the tag with id")
            fields = dict(body)
            return tags.update(conn, fields.pop("id"), fields)
        if action in {"delete", "link-all"}:
            _expect(body, {"id"})
            return (tags.delete if action == "delete" else tags.link_all)(conn, body["id"])
        if action in {"attach", "remove"}:
            _expect(body, {"block", "tag"})
            return (tags.attach if action == "attach" else tags.remove)(conn, body["block"], body["tag"])
    except blocks.BlockError as exc:
        raise _error(exc.status, str(exc)) from None
    return None

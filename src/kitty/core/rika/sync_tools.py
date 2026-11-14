"""Rika's GitHub backup tools: sync_status and sync_now.

Rika reports on the backup and can start a run. It never connects a repository and
never turns the backup on or off: those are the owner's clicks in Settings → Devices.
"""
from __future__ import annotations

from kitty.core import reposync
from kitty.core.rika.tools import Context, Result, Tool, ToolError, obj


def _backup(ctx: Context) -> reposync.RepoSync:
    backup = getattr(ctx.app, "reposync", None)
    if backup is None:
        raise ToolError("GitHub backup is off in this workspace")
    return backup


def _summary(state: dict) -> str:
    if not state["repo"]:
        return "GitHub backup is not set up; you can connect a private repository in Settings → Devices"
    if state["problem"]:
        return state["problem"]
    if state["paused"]:
        return f"The GitHub backup to {state['repo']} is paused"
    if state["synced_at"]:
        pages = state["pages"]
        return f"Backed up to {state['repo']} at {state['synced_at']}: {pages} page{'s' if pages != 1 else ''}"
    return f"Nothing has been backed up to {state['repo']} yet"


def _sync_status(ctx: Context, args: dict) -> Result:
    state = _backup(ctx).state(ctx.conn)
    return Result(_summary(state), state)


def _sync_now(ctx: Context, args: dict) -> Result:
    backup = _backup(ctx)
    try:
        backup.sync_now(ctx.conn)
    except reposync.RepoSyncError as error:
        raise ToolError(str(error)) from None
    return Result("Started a GitHub backup", {"repo": backup.state(ctx.conn)["repo"]})


TOOLS = (
    Tool("sync_status", "How the GitHub backup of the notes is doing: the repository, the last run, the next run "
         "and any problem. The owner sets the backup up in Settings → Devices; you cannot.",
         obj(), "read", _sync_status),
    Tool("sync_now", "Start a GitHub backup run now. Works only when the owner has connected a repository and "
         "the backup is not paused.", obj(), "change", _sync_now),
)

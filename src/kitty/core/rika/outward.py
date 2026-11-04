"""Rika's outward and irreversible tools (design Q9): each waits for one tap in the
history drawer before it runs, and none can be undone. Also the worker-agent and
terminal tools of the Terminal module (design Q11)."""
from __future__ import annotations

from pathlib import Path

from kitty.core import notes
from kitty.core.rika.agents import AgentError, brief
from kitty.core.rika.tools import Context, Result, Tool, ToolError, integer, obj, string
from kitty.core.terminal import CODING_AGENTS
from kitty.modules import MODULE_IDS


def _directory(ctx: Context, value: str | None) -> Path:
    path = Path(value).expanduser() if value else ctx.app.settings.root
    if not path.is_absolute() or not path.is_dir():
        raise ToolError("The working directory must be an existing absolute folder")
    return path.resolve()


def _delete_page_card(ctx: Context, args: dict) -> tuple[str, str]:
    page = notes.page(ctx.conn, args["page_id"])
    blocks = ctx.conn.execute("SELECT count(*) FROM note_outline WHERE page_id=?", (page["id"],)).fetchone()[0]
    return (f"Delete the page {page['title']}",
            f"{blocks} outline blocks and {len(page['document']['elements'])} canvas elements move to Trash. "
            "Rika cannot undo this; restore it from Notes → ⋯ → Trash… while history keeps it.")


def _delete_page(ctx: Context, args: dict) -> Result:
    page = notes.page(ctx.conn, args["page_id"])
    notes.delete_page(ctx.conn, page["id"], page["revision"])
    return Result(f"Deleted the page {page['title']}", {"deleted": page["id"]})


def _modules_card(ctx: Context, args: dict) -> tuple[str, str]:
    return (f"Turn on {', '.join(args['enabled']) or 'only the core'}",
            "Changes which feature modules rika! loads. It takes effect after rika! restarts; "
            "a disabled module's data is kept.")


def _set_modules(ctx: Context, args: dict) -> Result:
    from kitty.core.bootstrap import configure_modules

    if ctx.demo:
        raise ToolError("Module changes are unavailable in the demo")
    saved = configure_modules(ctx.app.settings.config_dir, args["enabled"])
    return Result(f"Saved modules {', '.join(saved) or 'core only'}; restart rika! to apply", {"pending": list(saved)})


def _worker_card(ctx: Context, args: dict) -> tuple[str, str]:
    cwd = _directory(ctx, args.get("cwd"))
    off = args.get("skills_off") or []
    skills = f"\n\nAgent skills switched off for this launch: {', '.join(off)}." if off else ""
    return f"Start {args['kind']} on: {args['task'][:120]}", brief(args["task"], cwd) + skills


def _public(agent: dict) -> dict:
    """An agent as Rika's results carry it: its now state stays in memory, on the page only
    (docs/agent-skills-design.md Q4), never in the action log or a model request."""
    return {key: value for key, value in agent.items() if key != "now"}


def _start_worker(ctx: Context, args: dict) -> Result:
    try:
        agent = ctx.app.rika.agents.start(args["kind"], args["task"], _directory(ctx, args.get("cwd")),
                                          args.get("skills_off") or [])
    except AgentError as exc:
        raise ToolError(str(exc)) from None
    return Result(f"Started {agent['name']}", {"agent": _public(agent)})


def _command_card(ctx: Context, args: dict) -> tuple[str, str]:
    cwd = _directory(ctx, args.get("cwd"))
    return f"Run a shell command in {cwd.name or cwd}", f"$ {args['command']}\n\nRuns in a new Tern tab in {cwd}."


def _run_command(ctx: Context, args: dict) -> Result:
    if ctx.demo:
        raise ToolError("Shell commands are unavailable in the demo")
    try:
        block = ctx.app.rika.agents.run_command(args["command"], _directory(ctx, args.get("cwd")))
    except AgentError as exc:
        raise ToolError(str(exc)) from None
    return Result("Started the command in a Tern tab", {"tern_pane": block})


def _open_terminal(ctx: Context, args: dict) -> Result:
    if ctx.demo:
        return Result("Opened a terminal (the demo opens no window)", {"window": None})
    try:
        ctx.app.rika.agents.open_terminal()
    except AgentError as exc:
        raise ToolError(str(exc)) from None
    return Result("Opened a Tern window", {"window": "tern"})


def _list_agents(ctx: Context, args: dict) -> Result:
    agents = [_public(agent) for agent in ctx.app.rika.agents.list()]
    return Result(f"{len(agents)} worker agent{'s' if len(agents) != 1 else ''} running", {"agents": agents})


TOOLS = (
    Tool("delete_page", "Delete a note page permanently (notes have no Trash).",
         obj(("page_id",), page_id=integer("Note page id")), "outward", _delete_page,
         module="workspace", describe=_delete_page_card),
    Tool("set_modules", "Choose which feature modules rika! loads after its next restart.",
         obj(("enabled",), enabled={"type": "array", "maxItems": len(MODULE_IDS),
                                    "items": string("Module id", enum=MODULE_IDS)}),
         "outward", _set_modules, describe=_modules_card),
    Tool("list_agents", "List the running worker agents with their status, task and last output line.",
         obj(), "read", _list_agents, module="terminal"),
    Tool("start_worker", "Start a worker coding agent (omp, Claude Code or Codex) in a herdr pane and hand it a task.",
         obj(("kind", "task"), kind=string("Agent", enum=CODING_AGENTS), task=string("The task brief", maximum=4000),
             cwd=string("Absolute working directory; defaults to the workspace", maximum=4096),
             skills_off={"type": "array", "maxItems": 200, "description": "Agent skills to switch off for this launch "
                         "(names from the agent's skill list; needs its agent skills turned on)",
                         "items": string("Agent skill name", maximum=80)}),
         "outward", _start_worker, module="terminal", describe=_worker_card),
    Tool("run_command", "Run a shell command in a new Tern tab.",
         obj(("command",), command=string("Shell command", maximum=4000),
             cwd=string("Absolute working directory; defaults to the workspace", maximum=4096)),
         "outward", _run_command, module="terminal", describe=_command_card),
    Tool("open_terminal", "Open a Tern terminal window on rika!'s session.", obj(), "change", _open_terminal,
         module="terminal"),
)

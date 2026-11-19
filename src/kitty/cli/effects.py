"""`kitty effects`: propose and inspect mediated effects."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Optional

import typer
from rich.markup import escape

from kitty.cli.common import _JSON_OPTION, _agent_tab, _open, error_console
from kitty.cli.mediated import _emit, _note_call
from kitty.cli.root import app

# ------------------------------------------------------------------ effects (proposals)

effects_app = typer.Typer(no_args_is_help=True, help=(
    "Propose effects for the owner to approve in rika!'s pending approvals (kitty.core.effects). "
    "Proposals are made as an agent session (one per command, or KITTY_AGENT_SESSION); "
    "nothing here approves or executes one, and submit_application never executes. "
    "In a rika! Agent tab (KITTY_AGENT_TOKEN) propose-delete and show go through rika!'s mediated API."))
app.add_typer(effects_app, name="effects")


@contextmanager
def _effects() -> Iterator:
    """The workspace and this command's agent actor; refusals print and exit 1."""
    from kitty.core import effects
    from kitty.core.blocks.actor import cli_agent

    try:
        agent = cli_agent()
    except ValueError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    workspace = _open()
    try:
        yield workspace, agent
    except effects.EffectError as exc:
        error_console.print(f"[bold red]{escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None
    finally:
        workspace.close()


def _no_tab_route(command: str) -> None:
    """Refuse a command that has no mediated route inside a rika! Agent tab."""
    if _agent_tab():
        error_console.print(f"[bold red]kitty effects {command} does not run in a rika! Agent tab[/bold red] "
                            "(only propose-delete and show reach rika! through the tab's token)")
        raise typer.Exit(code=1)


def _effect_line(effect: dict) -> str:
    actor = effect["actor"]
    who = actor["name"] or actor["kind"]
    return f"{effect['state']:<9} {escape(effect['title'])}  by {escape(who)}  [dim]{effect['id']}[/dim]"


def _proposed(result: dict, as_json: bool) -> None:
    _emit(result, as_json, _effect_line(result) + "\nWaiting for approval in rika!'s pending approvals.")


@effects_app.command("propose-delete")
def effects_propose_delete(
    block: Annotated[str, typer.Argument(help="The block's ID (a text block of a note page).")],
    reason: Annotated[Optional[str], typer.Option("--reason", help="Why, shown to the owner.")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Propose moving a block and its children to Trash; it runs only once the owner approves it."""
    from kitty.core import agent_access

    _proposed(_note_call("propose_delete_block", lambda conn: agent_access.propose_delete_block(conn, block, reason),
                         block=block, reason=reason), as_json)


@effects_app.command("propose-submit")
def effects_propose_submit(
    job_id: Annotated[int, typer.Argument(help="The job's number.")],
    reason: Annotated[Optional[str], typer.Option("--reason", help="Why, shown to the owner.")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Ask the owner to submit an application himself: it shows what would be submitted and never executes."""
    from kitty.core import agent_access, effect_kinds, effects

    _no_tab_route("propose-submit")
    with _effects() as (workspace, agent):
        effect = effects.propose(workspace.conn, "submit_application", {"job_id": job_id, "reason": reason},
                                 actor=agent, ctx=effect_kinds.Context(workspace.conn, workspace.config))
        result = agent_access.effect_state(workspace.conn, effect)
    _proposed(result, as_json)


@effects_app.command("list")
def effects_list(as_json: Annotated[bool, _JSON_OPTION] = False) -> None:
    """Effects waiting in the pending approvals, oldest first, and the latest decided ones."""
    from kitty.core import (  # noqa: F401 (registers the kinds listed here)
        effect_kinds,
        effects,
    )

    _no_tab_route("list")
    with _effects() as (workspace, _agent):
        pending = [effect.as_dict() for effect in effects.pending(workspace.conn)]
        recent = [effect.as_dict() for effect in effects.recent(workspace.conn)]
    lines = [_effect_line(effect) for effect in pending + recent]
    _emit({"pending": pending, "recent": recent}, as_json, "\n".join(lines) if lines else "No effects.")


@effects_app.command("show")
def effects_show(
    effect_id: Annotated[str, typer.Argument(help="The effect's ID.")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """One effect: what it does, its preconditions and every state it went through (in an
    Agent tab, only one this tab proposed)."""
    import json

    from kitty.core import (  # noqa: F401 (registers the kinds shown here)
        agent_access,
        effect_kinds,
        effects,
    )

    if _agent_tab():
        result = _note_call("get_effect", None, effect_id=effect_id)
    else:
        with _effects() as (workspace, _agent):
            result = agent_access.effect_state(workspace.conn, effects.get(workspace.conn, effect_id))
    lines = [_effect_line(result), escape(json.dumps(result["preview"], indent=2, ensure_ascii=False))]
    lines += [f"  {event['at']}  {event['state']}  {escape(event['actor']['name'] or event['actor']['kind'])}"
              for event in result["events"]]
    _emit(result, as_json, "\n".join(lines))

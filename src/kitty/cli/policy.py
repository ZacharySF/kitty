"""`kitty policy`: simulate, show and apply the agent-mediation policy."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.markup import escape

from kitty.cli.common import _JSON_OPTION, _open, console, error_console
from kitty.cli.mediated import _emit
from kitty.cli.root import app

# ------------------------------------------------------------------ policy (agent mediation)

policy_app = typer.Typer(no_args_is_help=True, help=(
    "Agent policies over purpose labels (kitty.core.agent_policy): what agent tabs' tokens may read "
    "and write, enforced or observed by rika!'s mediator, and replayed over the access journal."))
app.add_typer(policy_app, name="policy")


def _policy_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        error_console.print(f"[bold red]Cannot read {escape(str(path))}: {escape(str(exc))}[/bold red]")
        raise typer.Exit(code=1) from None


def _policy_problems(exc) -> typer.Exit:
    error_console.print("[bold red]The policy has problems:[/bold red]")
    for problem in exc.problems:
        error_console.print(f"  {escape(problem)}")
    return typer.Exit(code=1)


@policy_app.command("simulate")
def policy_simulate(
    draft: Annotated[Path, typer.Argument(help="The draft policy (YAML).", exists=True, dir_okay=False)],
    session: Annotated[Optional[str], typer.Option("--session", help="Only this agent session's journal.")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Replay the agent access journal under a draft policy and list every decision that would flip."""
    from kitty.core import agent_policy

    source = _policy_source(draft)
    workspace = _open()
    try:
        result = agent_policy.simulate(workspace.conn, source, session=session)
    except agent_policy.PolicyError as exc:
        raise _policy_problems(exc) from None
    finally:
        workspace.close()
    lines = [f"Replayed {result['replayed']} journal row(s): {result['total']} decision(s) would flip "
             f"({result['counts']['allow_to_deny']} allow→deny, {result['counts']['deny_to_allow']} deny→allow)"
             + ("" if result["enforced"] else "; the draft only observes, so none would be enforced") + "."]
    lines += [f"  #{flip['id']} {flip['at'][:19]} {escape(flip['name'])} {flip['tool']} {flip['op']} {flip['purpose']} "
              f"{flip['block_id'] or '-'}@{flip['seq']}: {flip['recorded']} → [bold]{flip['draft']}[/bold]"
              for flip in result["flips"]]
    if result["truncated"]:
        lines.append(f"[dim]{result['total'] - len(result['flips'])} more not listed; use --json or --session.[/dim]")
    _emit(result, as_json, "\n".join(lines))


@policy_app.command("show")
def policy_show(
    version: Annotated[Optional[int], typer.Option("--version", help="An earlier version (0 is the built-in one).")] = None,
) -> None:
    """Print the policy in force (or an earlier version) as YAML."""
    from kitty.core import agent_policy

    workspace = _open()
    try:
        if version is None:
            active, _ = agent_policy.active(workspace.conn)
            error_console.print(f"[dim]version {active}[/dim]")
        try:
            typer.echo(agent_policy.source(workspace.conn, version), nl=False)
        except agent_policy.PolicyError as exc:
            raise _policy_problems(exc) from None
    finally:
        workspace.close()


@policy_app.command("apply")
def policy_apply(
    policy: Annotated[Path, typer.Argument(help="The policy to put in force (YAML).", exists=True, dir_okay=False)],
) -> None:
    """Validate a policy and put it in force at the mediator as the next version."""
    from kitty.core import agent_policy
    from kitty.db.connection import transaction

    source = _policy_source(policy)
    workspace = _open()
    try:
        with transaction(workspace.conn, immediate=True):
            result = agent_policy.apply(workspace.conn, source)
    except agent_policy.PolicyError as exc:
        raise _policy_problems(exc) from None
    finally:
        workspace.close()
    console.print(f"Policy version {result['version']} is in force ({result['policy']['mode']}).")

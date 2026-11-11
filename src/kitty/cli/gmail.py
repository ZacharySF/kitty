"""`kitty gmail` and `kitty proposals`: read-only Gmail status proposals."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from kitty.cli.common import _open, _settings, console, error_console
from kitty.cli.root import app
from kitty.core import applications as applications_core

# ---- gmail (Stage 6: read-only Gmail proposes status changes; I confirm them)

gmail_app = typer.Typer(no_args_is_help=True, help="Read-only Gmail: propose status changes from recruiting mail.")
app.add_typer(gmail_app, name="gmail")
proposals_app = typer.Typer(
    invoke_without_command=True, help="Status changes waiting for your OK (list, confirm, reject).",
)
app.add_typer(proposals_app, name="proposals")


@gmail_app.command("auth")
def gmail_auth(
    compose: Annotated[bool, typer.Option(
        "--compose", help="Authorise Rika's separate drafting token (read + drafts and sending) instead.")] = False,
) -> None:
    """Open the browser consent page (read-only scope) and save the token to data/.

    With --compose, save Rika's separate opt-in token (gmail.readonly + gmail.compose) to
    data/gmail_compose_token.json; the job-status check keeps using only the read-only token.
    """
    from kitty.core.gmail import auth as gmail_auth_core

    try:
        path = gmail_auth_core.run_consent_flow(_settings(), compose=compose)
    except gmail_auth_core.GmailSetupError as exc:
        error_console.print(escape(str(exc)), highlight=False, soft_wrap=True)
        raise typer.Exit(code=1) from exc
    if compose:
        console.print(f"[green]Gmail connected for Rika's drafts (read + compose).[/green] Token saved to {path}.")
        console.print("Rika saves replies as Gmail drafts; auto-send stays off per kind until you turn it on.")
        return
    console.print(f"[green]Gmail authorised (read-only).[/green] Token saved to {path}.")
    console.print("Next: [bold]kitty gmail check[/bold]")


@gmail_app.command("status")
def gmail_status() -> None:
    """Say whether Gmail is set up, and what the last check found."""
    import json

    from kitty.core.gmail import auth as gmail_auth_core
    from kitty.core.gmail.proposals import pending_proposals

    state = gmail_auth_core.setup_state(_settings())
    if not state.client_present:
        error_console.print(escape(f"{gmail_auth_core.SETUP_STEPS}\n\nMissing: {state.client_path}"),
                            highlight=False, soft_wrap=True)
        raise typer.Exit(code=1)
    if not state.token_present:
        error_console.print(f"Client file found ({state.client_path}), but not authorised yet: "
                            "run [bold]kitty gmail auth[/bold].")
        raise typer.Exit(code=1)
    console.print(f"[green]Gmail set up[/green] (read-only). Token: {state.token_path}")
    workspace = _open()
    try:
        last = workspace.conn.execute(
            "SELECT started_at, stats_json, error FROM runs WHERE kind = 'mail' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is None:
            console.print("Never checked: run [bold]kitty gmail check[/bold].")
        else:
            stats = json.loads(last["stats_json"] or "{}")
            console.print(f"Last check {last['started_at']}: {stats.get('fetched', 0)} new messages, "
                          f"{stats.get('proposed', 0)} proposed, status {stats.get('status', '?')}.")
            if last["error"]:
                error_console.print(f"[yellow]{escape(last['error'])}[/yellow]")
        console.print(f"{len(pending_proposals(workspace.conn))} proposal(s) waiting: "
                      "[bold]kitty proposals[/bold]")
    finally:
        workspace.close()


@gmail_app.command("check")
def gmail_check(
    days: Annotated[int, typer.Option("--days", min=1, help="How far back to look.")] = 30,
) -> None:
    """Read new recruiting mail and propose status changes (nothing moves until you confirm)."""
    from kitty.core.gmail import auth as gmail_auth_core
    from kitty.core.gmail.check import check_gmail

    workspace = _open()
    try:
        try:
            result = check_gmail(workspace.conn, workspace.settings, days=days)
        except gmail_auth_core.GmailSetupError as exc:
            error_console.print(escape(str(exc)), highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc
        for proposal in result.proposals:
            console.print(
                f"  #{proposal.event_id}  {escape(proposal.company)} - {escape(proposal.title)}: "
                f"{proposal.from_status} -> [bold]{proposal.to_status}[/bold]  "
                f"[dim]{escape(proposal.subject)} | \"{escape(proposal.phrase)}\"[/dim]"
            )
        skipped = ", ".join(f"{reason} {count}" for reason, count in sorted(result.skipped_by_reason.items()))
        console.print(
            f"{result.listed} listed, {result.already_seen} seen before, {result.fetched} new, "
            f"{result.classified} status mail, {result.matched} matched, "
            f"[bold]{result.proposed} proposed[/bold]" + (f"  [dim]skipped: {skipped}[/dim]" if skipped else "")
        )
        if result.error:
            error_console.print(f"[red]{result.status}: {escape(result.error)}[/red]")
            raise typer.Exit(code=1)
        if result.proposed:
            console.print("Review with [bold]kitty proposals[/bold] or [bold]v[/bold] on the Pipeline tab.")
    finally:
        workspace.close()


@proposals_app.callback()
def proposals_list(ctx: typer.Context) -> None:
    """List status changes waiting for your OK."""
    if ctx.invoked_subcommand is not None:
        return
    from kitty.core.gmail.proposals import pending_proposals

    workspace = _open()
    try:
        pending = pending_proposals(workspace.conn)
        table = Table(box=None, header_style="bold")
        for column in ("event", "app", "company", "title", "change", "when", "evidence"):
            table.add_column(column, overflow="fold")
        for proposal in pending:
            table.add_row(
                str(proposal.event_id), str(proposal.application_id), escape(proposal.company),
                escape(proposal.title), f"{proposal.current_status} -> {proposal.to_status}",
                proposal.occurred_at[:10], escape(proposal.evidence or ""),
            )
        console.print(table)
        console.print(f"\n{len(pending)} waiting. [bold]kitty proposals confirm <event>[/bold] "
                      "or [bold]kitty proposals reject <event>[/bold].")
    finally:
        workspace.close()


@proposals_app.command("confirm")
def proposals_confirm(
    event_id: Annotated[int, typer.Argument(help="Event id from `kitty proposals`.")],
) -> None:
    """Accept a proposal: the application moves to the proposed status."""
    from kitty.core.gmail.proposals import get_pending

    workspace = _open()
    try:
        proposal = get_pending(workspace.conn, event_id)
        if proposal is None:
            error_console.print(f"[red]No pending proposal with id {event_id}.[/red]")
            raise typer.Exit(code=1)
        applications_core.confirm_proposal(workspace.conn, event_id)
        console.print(f"[green]Confirmed[/green]: {escape(proposal.company)} - {escape(proposal.title)} "
                      f"is now [bold]{proposal.to_status}[/bold].")
    finally:
        workspace.close()


@proposals_app.command("reject")
def proposals_reject(
    event_id: Annotated[int, typer.Argument(help="Event id from `kitty proposals`.")],
) -> None:
    """Decline a proposal: nothing changes; it stays in the history as declined."""
    from kitty.core.gmail.proposals import get_pending

    workspace = _open()
    try:
        proposal = get_pending(workspace.conn, event_id)
        if proposal is None:
            error_console.print(f"[red]No pending proposal with id {event_id}.[/red]")
            raise typer.Exit(code=1)
        applications_core.reject_proposal(workspace.conn, event_id)
        console.print(f"Declined: {escape(proposal.company)} - {escape(proposal.title)} "
                      f"stays [bold]{proposal.current_status}[/bold].")
    finally:
        workspace.close()

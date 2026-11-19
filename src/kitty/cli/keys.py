"""`kitty keys`: list, set, remove and inject API keys."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from kitty.cli.common import _settings, console, error_console
from kitty.cli.root import app

# ---- keys (platform design Q10: one Keys service; values are never printed)

keys_app = typer.Typer(no_args_is_help=True, help="API keys: list, set, remove, and inject into one command.")
app.add_typer(keys_app, name="keys")


def _keystore():
    from kitty.core import keys as keys_core

    return keys_core.for_settings(_settings())


def _keys_exit(exc: Exception) -> typer.Exit:
    error_console.print(f"[bold red]{escape(str(exc))}[/bold red]", highlight=False)
    return typer.Exit(code=1)


@keys_app.command("list")
def keys_list() -> None:
    """Each key: set or unset and where it is stored (never its value)."""
    status = _keystore().status()
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("name", "state", "stored in", "variable"):
        table.add_column(column)
    for row in status["keys"]:
        state = "[red]error[/red]" if row["error"] else "[green]set[/green]" if row["set"] else "[dim]unset[/dim]"
        table.add_row(row["name"], state, row["backend"] or "-", row["env"])
    console.print(table)
    for row in status["keys"]:
        if row["error"]:
            console.print(f"[red]{row['name']}: {escape(row['error'])}[/red]")
    if not status["keyring"]:
        console.print("[yellow]No OS keyring is available: new keys are stored in an owner-only file "
                      "under .private/.[/yellow]")
    if status["account_passwords"]["count"]:
        console.print(f"{status['account_passwords']['count']} employer account password(s) (APPLY_PW_*) are "
                      "listed per employer by `kitty apply accounts`.")
    for warning in status["warnings"]:
        console.print(f"[yellow]{escape(warning)}[/yellow]")


@keys_app.command("set")
def keys_set(
    name: Annotated[str, typer.Argument(help="OPENROUTER, ANTHROPIC, USAJOBS, BRAVE or your own NAME (A-Z, 0-9, _).")],
) -> None:
    """Store a key: typed without echo, or read from standard input (one line)."""
    import getpass
    import sys

    from kitty.core import keys as keys_core

    store = _keystore()
    try:
        name = keys_core.canonical(name)
        value = getpass.getpass(f"{name}: ") if sys.stdin.isatty() else sys.stdin.readline()
        where = store.set(name, value)
    except (keys_core.KeysError, EOFError) as exc:
        raise _keys_exit(exc) from None
    console.print(f"Stored {name} in " + ("the OS keyring." if where == "keyring"
                  else "an owner-only file under .private/ (no OS keyring available)."))


@keys_app.command("remove")
def keys_remove(name: Annotated[str, typer.Argument(help="The key's NAME.")]) -> None:
    """Remove a key from the keyring and the owner-only file (.env is yours to edit)."""
    from kitty.core import keys as keys_core

    store = _keystore()
    try:
        name = keys_core.canonical(name)
        removed = store.remove(name)
        left = store.lookup(name)
    except keys_core.KeysError as exc:
        raise _keys_exit(exc) from None
    console.print(f"Removed {name}." if removed else f"{name} was not stored by kitty.")
    if left:
        console.print(f"[yellow]{keys_core.env_name(name)} is still set in the environment (.env).[/yellow]")


@keys_app.command("move")
def keys_move(name: Annotated[str, typer.Argument(help="The key's NAME.")]) -> None:
    """Move a key from its owner-only file into the OS keyring."""
    from kitty.core import keys as keys_core

    try:
        _keystore().move_to_keyring(name)
    except keys_core.KeysError as exc:
        raise _keys_exit(exc) from None
    console.print(f"Moved {keys_core.canonical(name)} to the OS keyring.")


@keys_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def keys_run(
    args: Annotated[list[str], typer.Argument(metavar="+NAME... -- COMMAND [ARGS]...",
                                              help="Keys to inject, then the command.")],
) -> None:
    """Run COMMAND with the named keys as environment variables, in that process only.

    Example: kitty keys run +OPENROUTER -- apply-ai. OPENROUTER, ANTHROPIC, USAJOBS and
    BRAVE arrive as OPENROUTER_API_KEY, ANTHROPIC_API_KEY, USAJOBS_API_KEY and
    BRAVE_SEARCH_API_KEY; your own keys under their NAME. Other keys kitty knows are
    removed from the command's environment. kitty is replaced by the command, so its
    exit code is the command's.
    """
    import os

    from kitty.core import keys as keys_core

    names = []
    while args and args[0].startswith("+"):
        names.append(args.pop(0)[1:])
    if args and args[0] == "--":
        args.pop(0)
    if not names or not args:
        raise _keys_exit(ValueError("Usage: kitty keys run +NAME [+NAME...] -- COMMAND [ARGS]..."))
    store = _keystore()
    try:
        injected = store.environment(names)
        known = {keys_core.env_name(name) for name in store.names()}
    except keys_core.KeysError as exc:
        raise _keys_exit(exc) from None
    env = {key: value for key, value in os.environ.items() if key not in known}
    env.update(injected)
    try:
        os.execvpe(args[0], args, env)
    except OSError as exc:
        error_console.print(f"[bold red]Cannot run {escape(args[0])}: {escape(exc.strerror or 'error')}[/bold red]",
                            highlight=False)
        raise typer.Exit(code=127) from None

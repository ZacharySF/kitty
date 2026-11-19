"""`kitty notes`: block notes, locally or through rika!'s mediated API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Optional

import typer
from rich.markup import escape

from kitty.cli.common import _JSON_OPTION, error_console
from kitty.cli.mediated import _agent_client, _emit, _note_call
from kitty.cli.root import app

# ------------------------------------------------------------------ notes (blocks)

notes_app = typer.Typer(no_args_is_help=True, help=(
    "Read, search and write notes as blocks (kitty.core.block_api). Writes are recorded as an agent "
    "session in History: one per command, or KITTY_AGENT_SESSION (named by KITTY_AGENT_NAME). "
    "In a rika! Agent tab (KITTY_AGENT_TOKEN) every command goes through rika!'s mediated API."))
app.add_typer(notes_app, name="notes")

_MARKERS = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ", "bulleted_list": "• ", "numbered_list": "1. ",
            "quote": "> ", "callout": "! ", "toggle": "▸ ", "code": "``` ", "divider": "---"}


def _texts(values: list[str]) -> list[str]:
    """Block texts from arguments; ``-`` reads one block from stdin."""
    import sys

    return [sys.stdin.read().rstrip("\n") if value == "-" else value for value in values]


def _written(result: dict) -> str:
    return (f"seq {result['seq']} · revision {result['revision']} · "
            + ", ".join(escape(item["cite"]) for item in result["blocks"]))


@notes_app.command("ls")
def notes_ls(
    query: Annotated[str, typer.Argument(help="Only titles containing this.")] = "",
    kind: Annotated[Optional[str], typer.Option("--kind", help="page, document or quick_note.")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """List note pages, documents and quick notes, most recently edited first."""
    from kitty.core import block_api

    result = _note_call("list_pages", lambda conn: block_api.list_pages(conn, query, kind=kind, limit=limit),
                        query=query, kind=kind, limit=limit)
    lines = [f"{page['type']:<10} {page['number'] or '':>4}  {escape(page['title'])}  [dim]{page['id']}[/dim]"
             for page in result["pages"]]
    more = f"\n[dim]{result['total'] - len(result['pages'])} more; use --limit or a title filter.[/dim]" if result["truncated"] else ""
    _emit(result, as_json, "\n".join(lines) + more if lines else "No pages.")


def _outline(blocks: list[dict], depth: int = 0) -> Iterator[str]:
    for block in blocks:
        marker = ("[x] " if block["props"].get("checked") else "[ ] ") if block["type"] == "to_do" else _MARKERS.get(block["type"], "")
        tags = "".join(f" #{name}" for name in block["tags"])
        yield f"{'  ' * depth}{escape(marker + block['text'])}[cyan]{escape(tags)}[/cyan]  [dim]{block['id']}[/dim]"
        yield from _outline(block["children"], depth + 1)


@notes_app.command("show")
def notes_show(
    page: Annotated[str, typer.Argument(help="A page's block ID or a note page number.")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Print one page's blocks as an outline (block IDs dimmed)."""
    from kitty.core import block_api

    result = _note_call("get_page", lambda conn: block_api.get_page(conn, page), page=page)
    head = result["page"]
    lines = [f"[bold]{escape(head['title'])}[/bold]  [dim]{head['type']} {head['number'] or ''} · revision "
             f"{result['revision']} · seq {result['seq']} · {head['id']}[/dim]", *_outline(result["blocks"])]
    if head["canvas_elements"]:
        lines.append(f"[dim](and {head['canvas_elements']} canvas element(s), not listed)[/dim]")
    _emit(result, as_json, "\n".join(lines))


@notes_app.command("search")
def notes_search(
    query: Annotated[str, typer.Argument(help="Words (prefixes) or, from 3 characters, any substring.")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 20,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Search the blocks of note pages, documents and quick notes (the full-text index)."""
    from kitty.core import block_api

    result = _note_call("search_blocks", lambda conn: block_api.search_blocks(conn, query, limit=limit),
                        query=query, limit=limit)
    lines = [f"{escape(hit['page_title'])}: {escape(hit['snippet']).replace('**', '')}  [dim]{escape(hit['cite'])}[/dim]"
             for hit in result["hits"]]
    more = f"\n[dim]{result['total'] - len(result['hits'])} more; use --limit.[/dim]" if result["truncated"] else ""
    _emit(result, as_json, "\n".join(lines) + more if lines else "No matching blocks.")


@notes_app.command("new")
def notes_new(
    title: Annotated[str, typer.Argument(help="The page title.")],
    texts: Annotated[Optional[list[str]], typer.Argument(metavar="[TEXT]...", help="Text blocks to start with ('-' reads stdin).")] = None,
    notebook: Annotated[Optional[str], typer.Option("--notebook", help="Notebook title (created when missing).")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Create a note page, optionally with text blocks."""
    from kitty.core import block_api

    blocks = _texts(texts or []) or None
    result = _note_call("create_page", lambda conn: block_api.create_page(conn, title, notebook=notebook, blocks=blocks),
                        title=title, notebook=notebook, blocks=blocks)
    _emit(result, as_json, f"Created page {result['number']} {escape(result['cite'])} (seq {result['seq']}).")


@notes_app.command("append")
def notes_append(
    page: Annotated[str, typer.Argument(help="A page's block ID or a note page number.")],
    texts: Annotated[list[str], typer.Argument(metavar="TEXT...", help="One block each ('-' reads stdin).")],
    kind: Annotated[str, typer.Option("--type", help="Block type for every block (text, to_do, bulleted_list, ...).")] = "text",
    parent: Annotated[Optional[str], typer.Option("--parent", help="Append under this block instead.")] = None,
    revision: Annotated[Optional[int], typer.Option("--revision", help="Refuse if the page changed since this revision.")] = None,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Append blocks at the end of a note page."""
    from kitty.core import block_api

    blocks = [{"type": kind, "text": text} for text in _texts(texts)]
    result = _note_call("append_blocks", lambda conn: block_api.append_blocks(conn, page, blocks, parent=parent,
                                                                              revision=revision),
                        page=page, blocks=blocks, parent=parent, revision=revision)
    _emit(result, as_json, f"Appended: {_written(result)}")


@notes_app.command("inbox")
def notes_inbox(
    texts: Annotated[list[str], typer.Argument(metavar="TEXT...", help="One block each ('-' reads stdin).")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Add text blocks to the end of the Inbox page."""
    from kitty.core import block_api

    blocks = _texts(texts)
    result = _note_call("add_to_inbox", lambda conn: block_api.add_to_inbox(conn, blocks), blocks=blocks)
    _emit(result, as_json, f"Added to the Inbox (page {result['number']}): {_written(result)}")


@notes_app.command("quick")
def notes_quick(
    text: Annotated[str, typer.Argument(help="The quick note ('-' reads stdin).")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Create a quick note."""
    from kitty.core import block_api

    body = _texts([text])[0]
    result = _note_call("quick_note", lambda conn: block_api.quick_note(conn, body), text=body)
    _emit(result, as_json, f"Quick note {result['number']}: {_written(result)}")


@notes_app.command("sessions")
def notes_sessions(
    kind: Annotated[str, typer.Option("--kind", help="agent (default), client, user, system, peer or all.")] = "agent",
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 20,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Agent sessions in the kept history, most recent first (undo one with undo-session)."""
    from kitty.core.blocks import sessions

    if kind != "agent" and _agent_client() is not None:
        error_console.print("[bold red]In a rika! Agent tab only agent sessions are listed (--kind agent)[/bold red]")
        raise typer.Exit(code=1)
    result = _note_call("list_agent_sessions",
                        lambda conn: sessions.list_sessions(conn, kind=None if kind == "all" else kind, limit=limit),
                        limit=limit)
    lines = [f"{escape(item['session'])}  {escape(item['actor']['name'] or item['actor']['kind'])}"
             f"{' via ' + escape(item['device']) if item['device'] else ''}  {item['start'][:16]} – {item['end'][:16]}  "
             f"{item['transactions']} transaction(s) on {item['page_count']} page(s)" for item in result["sessions"]]
    _emit(result, as_json, "\n".join(lines) or "No sessions in the kept history.")


@notes_app.command("undo-session")
def notes_undo_session(
    session: Annotated[str, typer.Argument(help="The session ID (from `kitty notes sessions` or History).")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only report what would be reverted.")] = False,
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Revert everything one agent session wrote, as one new transaction; later edits by others are kept and listed."""
    from kitty.core.blocks import sessions

    result = _note_call("undo_agent_session", lambda conn: sessions.undo_session(conn, session, dry_run=dry_run),
                        session=session, dry_run=dry_run)
    verb = "Would revert" if dry_run else "Reverted" if result["applied"] else "Nothing to revert:"
    lines = [f"{verb} {result['reverted_count']} block(s) from {result['transactions']} transaction(s)"
             + (f" (seq {', '.join(str(value) for value in result['seq'].values())})" if result.get("seq") else "")]
    lines += [f"  reverted {escape(item['text'][:60]) or item['id']}: {', '.join(item['fields'])}" for item in result["reverted"]]
    lines += [f"  kept {escape((item.get('text') or '')[:60]) or item.get('id') or 'seq ' + str(item.get('seq'))}: "
              f"{escape('; '.join(item['reasons']))}" for item in result["conflicts"]]
    _emit(result, as_json, "\n".join(lines))


@notes_app.command("citations")
def notes_citations(
    cites: Annotated[list[str], typer.Argument(help="Pinned citations, ((id@seq)) or id@seq.")],
    as_json: Annotated[bool, _JSON_OPTION] = False,
) -> None:
    """Whether each cited block changed since the cited seq, and how (word diff: {-old-}{+new+})."""
    from kitty.core.blocks import history

    result = _note_call("check_citations", lambda conn: history.citations(conn, cites), cites=cites)
    lines = []
    for item in result["citations"]:
        lines.append(f"{escape(item['cite'])}  {item['status']}" + (f" (changed at seq {item['seq']})" if item["changed"] else ""))
        if item["diff"]:
            lines.append("  " + "".join(escape(part["text"]) if part["op"] == "equal" else
                                       f"[red]{{-{escape(part['text'])}-}}[/red]" if part["op"] == "delete" else
                                       f"[green]{{+{escape(part['text'])}+}}[/green]" for part in item["diff"]))
    _emit(result, as_json, "\n".join(lines))

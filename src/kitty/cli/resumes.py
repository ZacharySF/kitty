"""`kitty resumes`: register, improve locally, review and version resumes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.table import Table

from kitty.cli.common import _open, console, error_console
from kitty.cli.root import app
from kitty.core import resumes as resumes_core
from kitty.core.workspace import Workspace

resumes_app = typer.Typer(no_args_is_help=True, help="Register, improve locally, review and version resumes.")
app.add_typer(resumes_app, name="resumes")


# ----------------------------------------------------------------- resumes


@resumes_app.command("add")
def resumes_add(
    file: Annotated[Path, typer.Argument(help="Path to the resume file.")],
    label: Annotated[Optional[str], typer.Option("--label", help="Short name, e.g. quant.")] = None,
    description: Annotated[Optional[str], typer.Option("--description", help="What this version emphasises.")] = None,
    tracks: Annotated[Optional[str], typer.Option("--tracks", help="Comma-separated default tracks.")] = None,
) -> None:
    """Register a resume file: its path, its hash, and a description you write."""
    if not file.exists():
        error_console.print(f"[red]No such file: {file}[/red]")
        raise typer.Exit(code=1)

    workspace = _open()
    try:
        resolved_label = label or typer.prompt("Label (short, e.g. quant)", default=file.stem)
        resolved_description = description or typer.prompt(
            "One line: what does this version emphasise?", default="", show_default=False
        )
        track_names = (
            [t.strip() for t in tracks.split(",") if t.strip()]
            if tracks
            else [
                t.strip()
                for t in typer.prompt(
                    f"Default tracks ({', '.join(workspace.config.track_names)})",
                    default="", show_default=False,
                ).split(",")
                if t.strip()
            ]
        )
        unknown = [t for t in track_names if t not in workspace.config.track_names]
        if unknown:
            error_console.print(f"[red]Unknown track(s): {', '.join(unknown)}[/red]")
            raise typer.Exit(code=1)

        resume = resumes_core.add_resume(
            workspace.conn, resolved_label, file,
            description=resolved_description or None, default_tracks=track_names,
        )
        console.print(
            f"[green]Registered[/green] {resume.label} -> {resume.file_path} "
            f"(sha256 {resume.file_hash[:12]})"
        )
    finally:
        workspace.close()


@resumes_app.command("list")
def resumes_list() -> None:
    """List registered resumes and whether their files still match."""
    workspace = _open()
    try:
        problems = {
            resume.label: problem
            for resume, problem in resumes_core.check_files(workspace.conn)
        }
        table = Table(box=None, header_style="bold")
        for column in ("label", "file", "tracks", "description", "state"):
            table.add_column(column, overflow="fold")
        for resume in resumes_core.list_resumes(workspace.conn):
            table.add_row(
                resume.label, resume.file_path, ",".join(resume.default_tracks) or "-",
                resume.description or "-",
                f"[yellow]{problems[resume.label]}[/yellow]" if resume.label in problems else "ok",
            )
        console.print(table)
    finally:
        workspace.close()


@contextmanager
def _resume_workspace() -> Iterator[Workspace]:
    """Translate local artifact/validation errors without concealing a failed action."""
    workspace = _open()
    try:
        yield workspace
    except (ValueError, OSError, RuntimeError) as exc:
        error_console.print(str(exc), markup=False)
        raise typer.Exit(code=1) from exc
    finally:
        workspace.close()


@resumes_app.command("improve")
def resumes_improve(
    track: Annotated[str, typer.Option("--track", help="Track whose posting evidence to use.")],
    source: Annotated[Optional[Path], typer.Option("--source", help="Local HTML resume source; otherwise infer only an unambiguous match.")] = None,
    min_postings: Annotated[int, typer.Option("--min-postings", min=1, help="Minimum eligible posting sample.")] = 5,
) -> None:
    """Create an evidence-backed HTML/PDF candidate locally. Never promotes it."""
    from kitty.core import resume_versions

    console.print("Local processing only: no upload, no paid AI calls ($0). "
                  "Only supported facts may be edited; missing skills stay review items.")
    with _resume_workspace() as workspace:
        candidate = resume_versions.create_candidate(
            workspace.conn, workspace.config, workspace.settings, track,
            source.expanduser() if source else None, min_postings=min_postings,
        )
        console.print(f"Created candidate {candidate['id']} for {candidate['track']}.", markup=False)
        console.print(f"PDF: {candidate.get('pdf_path') or '(render failed)'}", markup=False)
        validation = candidate.get("validation", {})
        console.print("Validation: " + ("passed" if validation.get("valid") else "FAILED; promotion blocked"))
        for error in validation.get("errors", []):
            console.print(f"  {error}", markup=False)
        for warning in candidate.get("warnings", []):
            console.print(f"Warning: {warning}", markup=False)
        review_items = candidate.get("review_items", [])
        for item in review_items[:10]:
            note = f"{item.get('term', 'Review')}: {item.get('reason', '')}" if isinstance(item, dict) else str(item)
            console.print(f"Factual review (not inserted): {note}", markup=False)
        if len(review_items) > 10:
            console.print(f"{len(review_items) - 10} more factual review items retained with full evidence in the review.")
        console.print(f"Default unchanged. Review exact edits and evidence:\n"
                      f"  kitty resumes review {candidate['id']} --open", markup=False)


@resumes_app.command("review")
def resumes_review(
    version: Annotated[str, typer.Argument(help="Immutable version id from resumes versions.")],
    open_preview: Annotated[bool, typer.Option("--open", help="Open the local candidate PDF after printing the full review.")] = False,
) -> None:
    """Print the exact diff, anchored facts, posting evidence, measurements and validation."""
    from kitty.core import resume_versions
    from kitty.tui.screens.resume_review import open_pdf, review_text

    with _resume_workspace() as workspace:
        candidate = resume_versions.get_version(workspace.conn, workspace.settings, version)
        console.print(review_text(candidate), markup=False, highlight=False, emoji=False, soft_wrap=True)
        if open_preview:
            open_pdf(candidate)


@resumes_app.command("versions")
def resumes_versions(
    track: Annotated[Optional[str], typer.Option("--track", help="Only this track's version history.")] = None,
) -> None:
    """List retained candidates, decisions and promotion/rollback history."""
    import json

    from kitty.core import resume_versions

    with _resume_workspace() as workspace:
        versions = resume_versions.list_versions(workspace.conn, workspace.settings, track)
        if not versions:
            console.print("No resume versions yet. Use kitty resumes improve --track TRACK.")
        for version in versions:
            state = "current default" if version.get("is_default") else "not default"
            console.print(f"{version['id']}  {version['track']}  {version['status']}  {state}  "
                          f"{version.get('created_at', '')}", markup=False)
            console.print(f"  PDF: {version.get('pdf_path') or '(unavailable)'}", markup=False)
            console.print(f"  Validation: {version.get('validation', {})}", markup=False)
            if version.get("history"):
                console.print(json.dumps(version["history"], ensure_ascii=False, indent=2), markup=False)


@resumes_app.command("promote")
def resumes_promote(
    version: Annotated[str, typer.Argument(help="Candidate id to explicitly approve.")],
) -> None:
    """Review and confirm a new default for future unchosen jobs only."""
    from kitty.core import resume_versions
    from kitty.tui.screens.resume_review import review_text

    with _resume_workspace() as workspace:
        candidate = resume_versions.get_version(workspace.conn, workspace.settings, version)
        console.print(review_text(candidate), markup=False, highlight=False, emoji=False, soft_wrap=True)
        if candidate["status"] != "candidate" or not candidate.get("validation", {}).get("valid"):
            raise ValueError("Only an undecided candidate with passing validation can be promoted.")
        if not typer.confirm("Have you reviewed the exact edits, evidence and PDF, and approve this "
                             "candidate as the default for future unchosen jobs?", default=False):
            console.print("Cancelled; default unchanged.")
            return
        result = resume_versions.promote(workspace.conn, workspace.config, workspace.settings, version)
        console.print(f"Promoted {result.get('id', version)}. Existing jobs, applications and "
                      "original source files are unchanged.", markup=False)


@resumes_app.command("reject")
def resumes_reject(
    version: Annotated[str, typer.Argument(help="Candidate id to reject; artifacts are retained.")],
) -> None:
    """Reject a candidate without deleting its audit artifacts or changing the default."""
    from kitty.core import resume_versions

    with _resume_workspace() as workspace:
        resume_versions.reject(workspace.conn, workspace.settings, version)
        console.print(f"Rejected {version}. Audit artifacts retained; default unchanged.", markup=False)


@resumes_app.command("rollback")
def resumes_rollback(
    track: Annotated[str, typer.Option("--track", help="Track whose default should return to its prior version.")],
) -> None:
    """Confirm restoring the prior default without rewriting any application or artifact."""
    from kitty.core import resume_versions

    with _resume_workspace() as workspace:
        current = resume_versions.default_resume(workspace.conn, workspace.config, track)
        console.print(f"Current default for {track}: {current.label if current else '(none)'}", markup=False)
        if not typer.confirm("Restore the prior default? Existing choices and applications stay unchanged.",
                             default=False):
            console.print("Cancelled; default unchanged.")
            return
        result = resume_versions.rollback(workspace.conn, workspace.config, workspace.settings, track)
        console.print(f"Restored default: {result.get('label')} (resume {result.get('id')}). "
                      "History and artifacts retained.", markup=False)

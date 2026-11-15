"""`kitty questions`: every form question in the queue."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.markup import escape

from kitty.cli.common import _open, console
from kitty.cli.root import app

# ---- questions: every form question in the queue, answered once in answers.yaml

questions_app = typer.Typer(no_args_is_help=True,
                            help="Form questions across the queue (read without a browser).")
app.add_typer(questions_app, name="questions")

#: How `questions list` titles each kind of question.
_QUESTION_KIND_TITLES = {
    "choice": "Choice questions", "factual": "Factual questions (typed)", "essay": "Essays",
    "demographic": "Demographic (EEO, voluntary)", "answered": "Already in answers.yaml",
    "profile": "Covered by profile.yaml",
}


def _csv_option(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


@questions_app.command("collect")
def questions_collect(
    states: Annotated[str, typer.Option("--states", help="Queue states, comma-separated.")] = "new,queued",
    tiers: Annotated[str, typer.Option("--tiers", help="Tiers, comma-separated.")] = "A,B",
    limit: Annotated[Optional[int], typer.Option("--limit", min=1, help="At most N jobs.")] = None,
) -> None:
    """Read the application-form questions of the queue's Greenhouse and Lever jobs."""
    from kitty.core import form_questions

    workspace = _open()
    try:
        result = form_questions.collect(
            workspace.conn, workspace.settings, workspace.config,
            states=_csv_option(states), tiers=[t.upper() for t in _csv_option(tiers)], limit=limit,
            progress=lambda message: console.print(f"[dim]{escape(message)}[/dim]"),
        )
        console.print(f"\n{result.considered} job(s) considered, [bold]{result.fetched} form(s) read[/bold] "
                      f"({result.from_cache} from cache), {sum(result.failed.values())} failed, "
                      f"{sum(result.skipped.values())} skipped.")
        for title, counts in (("Failed", result.failed), ("Skipped", result.skipped)):
            for reason, count in counts.most_common():
                console.print(f"  {title.lower()}: {escape(reason)}  {count}")
        console.print(f"{result.questions_seen} questions kept (profile fields and uploads left out), "
                      f"[bold]{result.distinct_questions} distinct[/bold]; "
                      f"{result.new_questions} new question(s), {result.new_links} new job link(s).")
        console.print("Next: [bold]kitty questions list[/bold]")
    finally:
        workspace.close()


def _question_json(summary) -> dict:
    """One question for `questions list --json` (long choice lists capped at 100)."""
    return {
        "id": summary.id, "text": summary.text, "kind": summary.kind, "field_kind": summary.field_kind,
        "job_count": summary.job_count, "required_jobs": summary.required_jobs,
        "companies": list(summary.companies), "job_ids": list(summary.job_ids),
        "choices": list(summary.choices[:100]), "choice_count": len(summary.choices),
        "other_choice_sets": [
            {"jobs": count, "choices": list(choices[:100])}
            for choices, count in summary.choice_sets[1:]
        ],
    }


def _print_question(summary) -> None:
    """One question: job count, text, example companies, choices."""
    required = f"  [dim]required on {summary.required_jobs}[/dim]" if summary.required_jobs else ""
    console.print(f"  [bold]{summary.job_count:>3}[/bold]  {escape(summary.text)}{required}")
    if summary.companies:
        more = len(summary.companies) - 4
        console.print(f"       [dim]{escape(', '.join(summary.companies[:4]))}"
                      + (f" (+{more} more)" if more > 0 else "") + "[/dim]")
    if summary.choices:
        shown = " | ".join(summary.choices[:12])
        more = len(summary.choices) - 12
        console.print(f"       choices: {escape(shown)}" + (f" [dim](+{more} more)[/dim]" if more > 0 else ""))
    if len(summary.choice_sets) > 1:
        console.print(f"       [dim](+{len(summary.choice_sets) - 1} other choice set(s) on other forms)[/dim]")


@questions_app.command("list")
def questions_list(
    show_all: Annotated[bool, typer.Option("--all", help="Everything, including answered and profile questions.")] = False,
    min_jobs: Annotated[int, typer.Option("--min-jobs", min=0, help="Only questions asked by at least N jobs.")] = 1,
    include_essays: Annotated[bool, typer.Option("--include-essays")] = False,
    include_demographic: Annotated[bool, typer.Option("--include-demographic")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Questions still to answer in answers.yaml, most-shared first, grouped by kind."""
    import json

    from kitty.core import form_questions

    wanted = {"choice", "factual"}
    wanted |= {"essay"} if include_essays or show_all else set()
    wanted |= {"demographic"} if include_demographic or show_all else set()
    wanted |= {"answered", "profile"} if show_all else set()
    workspace = _open()
    try:
        found = [s for s in form_questions.summarize(workspace.conn, workspace.config)
                 if s.kind in wanted and s.job_count >= min_jobs]
    finally:
        workspace.close()
    if as_json:
        typer.echo(json.dumps({"questions": [_question_json(s) for s in found]}, indent=2, ensure_ascii=False))
        return
    for kind in form_questions.KINDS:
        group = [s for s in found if s.kind == kind]
        if group:
            console.print(f"\n[bold]{_QUESTION_KIND_TITLES[kind]} ({len(group)})[/bold]  [dim]jobs  question[/dim]")
            for summary in group:
                _print_question(summary)
    console.print(f"\n{len(found)} question(s). Answer them in config/answers.yaml; a `match:` phrase "
                  f"covers differently worded ones ({form_questions.COMPANY} stands for the company's name).")

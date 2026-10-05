"""Profile: who I am to an application form, and how the tool searches for me.

Everything here is read from the yaml config and the resumes table; nothing is
invented. `e` opens a config file in $EDITOR (then reloads it), `n` copies a
resume into resumes/ and registers it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from applyai.core import answers_file
from applyai.core import questions as questions_core
from applyai.core import resumes as resumes_core
from applyai.core.resume_versions import default_resume
from applyai.tui.helpers import muted, panel
from applyai.tui.navigation import ScreenHeader, key_of
from applyai.tui.screens.modals import PickModal, TextPromptModal
from applyai.tui.theme import CRIMSON, EMBER, GOLD, MOSS

#: Config files the `e` key can open, in the order they are offered.
EDITABLE = ("profile.yaml", "answers.yaml", "tracks.yaml", "watchlist.yaml", "sources.yaml")


def _editor() -> str:
    """$VISUAL, then $EDITOR, then nano, then vi."""
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("nano") or "vi"


class ProfileScreen(Screen):
    """Facts, resumes, track preferences and screening answers, in one place."""

    BINDINGS = [
        ("w", "answer_question", "Answer a question"),
        ("e", "edit_file", "Edit a config file"),
        ("n", "add_resume", "Add resume"),
        ("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("profile")
        with Horizontal(id="profile-body"):
            with VerticalScroll(classes="dash-column"):
                with panel("You (profile.yaml)"):
                    yield Static(id="facts")
                with panel("Resumes"):
                    yield Static(id="resumes")
            with VerticalScroll(classes="dash-column"):
                with panel("Tracks and preferences (tracks.yaml)"):
                    yield Static(id="tracks")
                with panel("Screening answers (answers.yaml)"):
                    yield Static(id="answers")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    # ---------------------------------------------------------------- render

    def refresh_data(self) -> None:
        config = self.app.config                                              # type: ignore[attr-defined]
        conn = self.app.conn                                                  # type: ignore[attr-defined]
        root = self.app.workspace.settings.root                               # type: ignore[attr-defined]
        self.query_one("#facts", Static).update(self._facts(config.profile))
        self.query_one("#resumes", Static).update(self._resumes(conn, config, root))
        self.query_one("#tracks", Static).update(self._tracks(config))
        self.query_one("#answers", Static).update(self._answers(conn, config))

    def _facts(self, profile) -> str:
        """What the form filler will type, exactly as written in profile.yaml."""
        rows = [
            ("name", f"{profile.name}  [dim](first {profile.given_name}, last {profile.family_name})[/dim]"),
            ("email", profile.email), ("phone", profile.phone), ("linkedin", profile.linkedin),
            ("github", profile.github), ("website", profile.website), ("school", profile.school),
            ("degree", profile.degree), ("major", profile.major), ("standing", profile.standing),
            ("graduation", profile.graduation),
            ("home", f"{profile.home_city}, {profile.home_state}"),
            ("remote", "open to remote" if profile.open_to_remote else "on-site only"),
            ("work auth", profile.work_authorization),
        ]
        lines = [f"[bold]{label:<11}[/bold] {escape(value) if value else muted('not set - not filled')}"
                 for label, value in rows]
        lines.append("")
        lines.append("[dim]e edits this file; the filler copies these values as written.[/dim]")
        return "\n".join(lines)

    def _resumes(self, conn, config, root: Path) -> str:
        """Each registered resume, the tracks that send it, and whether its file is fine."""
        users: dict[str, list[str]] = {}
        for track in config.tracks.tracks:
            selected = default_resume(conn, config, track.name)
            label = selected.label if selected else track.default_resume
            if label:
                users.setdefault(label, []).append(track.name)
        registered = resumes_core.list_resumes(conn)
        lines = []
        for resume in registered:
            path = root / resume.file_path
            state = (f"[{MOSS}]file ok[/]" if path.exists() else f"[{CRIMSON}]file missing[/]")
            tracks = ", ".join(users.get(resume.label, [])) or muted("no track uses it")
            lines.append(f"[bold {EMBER}]{escape(resume.label)}[/]  {state}  [dim]used by[/dim] {tracks}")
            lines.append(f"  {escape(Path(resume.file_path).name)}")
            if resume.description:
                lines.append(f"  [dim]{escape(resume.description)}[/dim]")
        known = {resume.label for resume in registered}
        for label, tracks in users.items():
            if label not in known:
                lines.append(f"[{CRIMSON}]{escape(label)} is not registered[/] - used by {', '.join(tracks)}; "
                             "press n to add it")
        lines.append("")
        lines.append("[dim]n registers a resume. Review evidence-backed candidate edits and explicitly "
                     f"promote a version in the Resume tab ({key_of('resume')}).[/dim]")
        return "\n".join(lines)

    def _tracks(self, config) -> str:
        """Per track: resume, weekly target, where, and how much it searches."""
        lines = []
        for track in config.tracks.tracks:
            rules = track.location_rules
            if rules.near:
                where = f"within {rules.near.radius_miles} mi of {rules.near.city}, {rules.near.state}"
            else:
                where = ", ".join(rules.prefer_cities) or "anywhere in " + ", ".join(rules.countries)
            remote = "remote ok" if rules.allow_remote else "no remote"
            selected = default_resume(self.app.conn, config, track.name)     # type: ignore[attr-defined]
            label = selected.label if selected else track.default_resume
            lines.append(f"[bold {EMBER}]{track.name}[/]  resume [bold]{escape(label or '-')}[/]  "
                         f"{track.weekly_target}/week  [dim]{remote}[/dim]")
            lines.append(f"  [dim]prefers[/dim] {escape(where)}")
            lines.append(f"  [dim]{len(track.search_terms)} search terms, sources:[/dim] "
                         f"{', '.join(track.sources)}")
        return "\n".join(lines)

    def _answers(self, conn, config) -> str:
        """The answers the filler may use, and the questions still waiting for one."""
        settings = self.app.workspace.settings                                # type: ignore[attr-defined]
        mine, example = settings.config_dir / "answers.yaml", settings.config_dir / "answers.yaml.example"
        lines = []
        if mine.exists() and example.exists() and mine.read_bytes() == example.read_bytes():
            lines.append(f"[bold {GOLD}]answers.yaml is still the example file.[/] Check every answer "
                         "below is true for you before the filler uses it (e to edit).")
            lines.append("")
        for answer in config.answers.answers:
            lines.append(f"[dim]Q[/dim] {escape(answer.question)}")
            lines.append(f"  [{MOSS}]{escape(answer.answer)}[/]")
        if not config.answers.answers:
            lines.append(muted("no answers yet"))
        waiting = questions_core.list_questions(conn)
        lines.append("")
        lines.append(f"[bold]{len(waiting)} question(s) seen on forms with no answer yet[/bold]"
                     + (" - w answers one (saved to answers.yaml)" if waiting else ""))
        lines.extend(f"  {escape(q.text)} [dim]x{q.times_seen}[/dim]" for q in waiting[:8])
        return "\n".join(lines)

    # --------------------------------------------------------------- actions

    def action_answer_question(self) -> None:
        """Pick a waiting question, type my answer, save it to answers.yaml.

        The answer is exactly what I type; the tool never proposes one.
        """
        conn = self.app.conn                                                  # type: ignore[attr-defined]
        waiting = {str(q.id): q for q in questions_core.list_questions(conn)}
        if not waiting:
            self.notify("No questions are waiting for an answer.")
            return

        def _answer(question_id: str | None) -> None:
            if question_id is None:
                return
            question = waiting[question_id]
            self.app.push_screen(
                TextPromptModal("Your answer", label=question.text),
                lambda text: self._save_answer(question.text, text),
            )

        options = [(f"{q.text}  (x{q.times_seen})", key) for key, q in waiting.items()]
        self.app.push_screen(PickModal("Answer which question?", options), _answer)

    def _save_answer(self, question: str, answer: str | None) -> None:
        if not answer or not answer.strip():
            return
        settings = self.app.workspace.settings                               # type: ignore[attr-defined]
        try:
            answers_file.add_answer(settings.config_dir, question, answer.strip())
        except Exception as exc:                                              # shown, not swallowed
            self.notify(f"Not saved: {exc}", severity="error")
            return
        self.app.action_reload_config()                                       # type: ignore[attr-defined]
        questions_core.refresh_answered_flags(self.app.conn, self.app.config)  # type: ignore[attr-defined]
        self.notify("Saved to answers.yaml. The filler uses it from now on.")
        self.refresh_data()

    def action_edit_file(self) -> None:
        def _open(name: str | None) -> None:
            if name is None:
                return
            path = self.app.workspace.settings.config_dir / name               # type: ignore[attr-defined]
            with self.app.suspend():
                subprocess.run([_editor(), str(path)], check=False)
            self.app.action_reload_config()                                    # type: ignore[attr-defined]
            self.refresh_data()

        self.app.push_screen(PickModal("Edit which file?", [(name, name) for name in EDITABLE]), _open)

    def action_add_resume(self) -> None:
        """Ask for a file, then a label; copy it into resumes/ and register it."""
        labels = sorted({t.default_resume for t in self.app.config.tracks.tracks if t.default_resume})  # type: ignore[attr-defined]

        def _got_path(path: str | None) -> None:
            if not path:
                return
            self.app.push_screen(
                TextPromptModal("Label for this resume", label=f"Tracks use: {', '.join(labels)}"),
                lambda label: self._register(path, label),
            )

        self.app.push_screen(
            TextPromptModal("Add a resume", label="Path to the PDF", placeholder="~/Downloads/resume.pdf"),
            _got_path,
        )

    def _register(self, path: str, label: str | None) -> None:
        if not label:
            return
        settings = self.app.workspace.settings                                # type: ignore[attr-defined]
        try:
            resume = resumes_core.import_resume(self.app.conn, settings.root, Path(path), label.strip())  # type: ignore[attr-defined]
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Registered {resume.label}: {Path(resume.file_path).name}")
        self.refresh_data()

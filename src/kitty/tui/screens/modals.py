"""Modal dialogs: prompts, the add-a-job form, status changes and pickers.

Modals gather input and return it. The only one that writes to the database is
AddJobModal, because the form and the write belong together.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option

from kitty.core import jobs as jobs_core
from kitty.core import urlmeta
from kitty.dates import parse_user_date
from kitty.models import PIPELINE_STATUSES


class TextPromptModal(ModalScreen[str | None]):
    """Ask for one line of text. Returns the text, or None when cancelled."""

    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    def __init__(
        self, title: str, *, label: str = "", initial: str = "", placeholder: str = ""
    ) -> None:
        super().__init__()
        self.title_text = title
        self.label_text = label
        self.initial = initial
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(self.title_text, classes="modal-title")
            if self.label_text:
                yield Label(self.label_text, classes="field-label")
            yield Input(value=self.initial, placeholder=self.placeholder, id="value")
            yield Static("enter save   esc cancel", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    @on(Input.Submitted, "#value")
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class PickModal(ModalScreen[str | None]):
    """Pick one option from a list. Returns the option's value, or None."""

    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    def __init__(self, title: str, options: list[tuple[str, str]], *, hint: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.options = options              # (label, value)
        self.hint = hint or "enter choose   esc cancel"

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(self.title_text, classes="modal-title")
            yield OptionList(
                *[Option(label, id=value) for label, value in self.options], id="options"
            )
            yield Static(self.hint, classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#options", OptionList).focus()

    @on(OptionList.OptionSelected, "#options")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


@dataclass(frozen=True)
class StatusChoice:
    """A status change chosen in StatusModal."""

    status: str
    occurred_at: str | None
    note: str | None


class StatusModal(ModalScreen[StatusChoice | None]):
    """Choose a new application status, optionally dated and annotated."""

    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    STATUSES = (*PIPELINE_STATUSES, "withdrawn")

    def __init__(self, subject: str, current: str | None = None) -> None:
        super().__init__()
        self.subject = subject
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Static(f"Status: {self.subject}", classes="modal-title")
            with RadioSet(id="status"):
                for status in self.STATUSES:
                    yield RadioButton(status, value=status == self.current, id=f"st-{status}")
            yield Label("When (blank = now; 'today', '+3', 2026-10-01)", classes="field-label")
            yield Input(placeholder="now", id="when")
            yield Label("Note (optional, stored as evidence)", classes="field-label")
            yield Input(placeholder="", id="note")
            with Horizontal():
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")
            yield Static("up/down pick   enter save   esc cancel", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#status", RadioSet).focus()

    def _chosen(self) -> StatusChoice | None:
        """Read the form, validating the date."""
        radio_set = self.query_one("#status", RadioSet)
        pressed = radio_set.pressed_button
        if pressed is None:
            self.notify("Pick a status first.", severity="warning")
            return None

        when_text = self.query_one("#when", Input).value.strip()
        occurred_at = parse_user_date(when_text) if when_text else None
        if when_text and occurred_at is None:
            self.notify(f"Could not read the date '{when_text}'.", severity="error")
            return None

        note = self.query_one("#note", Input).value.strip() or None
        return StatusChoice(status=str(pressed.label), occurred_at=occurred_at, note=note)

    @on(Button.Pressed, "#save")
    @on(Input.Submitted)
    def _save(self) -> None:
        choice = self._chosen()
        if choice is not None:
            self.dismiss(choice)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class AddJobModal(ModalScreen[int | None]):
    """Add a job by pasting a URL.

    Pressing ctrl+f fetches the page title and fills only the fields that are
    still empty; nothing is saved until I press Save, so a wrong guess is always
    visible and editable first.
    """

    BINDINGS = [
        ("escape", "dismiss_none", "Cancel"),
        ("ctrl+f", "fetch", "Fetch page title"),
    ]

    def __init__(self, track_names: list[str], *, url: str = "") -> None:
        super().__init__()
        self.track_names = track_names
        self.initial_url = url

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal"):
            yield Static("Add a job", classes="modal-title")
            yield Label("URL", classes="field-label")
            yield Input(value=self.initial_url, placeholder="https://...", id="url")
            yield Label("Company", classes="field-label")
            yield Input(id="company")
            yield Label("Title", classes="field-label")
            yield Input(id="title")
            yield Label("Location", classes="field-label")
            yield Input(placeholder="Springfield, IL / Remote", id="location")
            yield Label(
                f"Track (blank for none: {', '.join(self.track_names)})", classes="field-label"
            )
            yield Input(id="track")
            yield Label("Deadline (optional: 2026-10-01, '+14')", classes="field-label")
            yield Input(id="deadline")
            with Horizontal():
                yield Button("Save", variant="primary", id="save")
                yield Button("Fetch title", id="fetch")
                yield Button("Cancel", id="cancel")
            yield Static(
                "ctrl+f fetch page title (fills empty fields only)   esc cancel",
                classes="modal-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#url", Input).focus()

    def _value(self, field_id: str) -> str:
        return self.query_one(f"#{field_id}", Input).value.strip()

    def action_fetch(self) -> None:
        """Start the page fetch in a thread so the UI stays responsive."""
        url = self._value("url")
        if not url:
            self.notify("Paste a URL first.", severity="warning")
            return
        self.notify("Fetching the page title...")
        self._fetch_worker(url)

    @work(thread=True, exclusive=True)
    def _fetch_worker(self, url: str) -> None:
        """Fetch page metadata off the UI thread and fill the empty fields."""
        meta = urlmeta.fetch_page_meta(url)
        self.app.call_from_thread(self._apply_meta, meta)

    def _apply_meta(self, meta: urlmeta.PageMeta) -> None:
        """Fill blank fields from a fetch. Never overwrites what I typed."""
        if meta.error:
            self.notify(f"Could not read the page: {meta.error}", severity="warning")
            return
        for field_id, guess in (
            ("company", meta.company_guess),
            ("title", meta.title_guess),
        ):
            widget = self.query_one(f"#{field_id}", Input)
            if guess and not widget.value.strip():
                widget.value = guess
        self.notify(f"Page title: {meta.page_title or '(none)'} - check the fields.")

    @on(Button.Pressed, "#fetch")
    def _fetch_button(self) -> None:
        self.action_fetch()

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        url, company, title = self._value("url"), self._value("company"), self._value("title")
        if not url:
            self.notify("A URL is required.", severity="error")
            return
        if not company or not title:
            self.notify("Company and title are required - type them in.", severity="error")
            return

        track = self._value("track")
        if track and track not in self.track_names:
            self.notify(f"Unknown track '{track}'.", severity="error")
            return

        deadline_text = self._value("deadline")
        deadline = parse_user_date(deadline_text) if deadline_text else None
        if deadline_text and deadline is None:
            self.notify(f"Could not read the deadline '{deadline_text}'.", severity="error")
            return

        conn = self.app.conn                        # type: ignore[attr-defined]
        existing = jobs_core.find_by_url(conn, url)
        if existing is not None:
            self.notify(f"Already tracked as job {existing.id}.", severity="warning")
            self.dismiss(existing.id)
            return

        job, _created = jobs_core.upsert_job(
            conn,
            company=company,
            title=title,
            url=url,
            location=self._value("location") or None,
            source="manual",
            tracks=[track] if track else (),
            match_reason="added by hand",
            deadline_at=deadline,
        )
        self.notify(f"Added job {job.id}: {company} - {title}")
        self.dismiss(job.id)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

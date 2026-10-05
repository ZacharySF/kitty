"""Jobs: the list screen, with saved views and a filter bar. * stars a favorite (★), v shows only
favorites, c narrows any view to one track."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static

from applyai.core import jobs as jobs_core
from applyai.tui.actions import JobActions
from applyai.tui.helpers import (
    due_label, eligibility_markup, favorite_mark, short_date, status_markup, tier_markup,
)
from applyai.tui.navigation import ScreenHeader

_COLUMNS = (
    ("★", 1), ("id", 5), ("company", 24), ("title", 34), ("location", 18), ("tracks", 12),
    ("freshness", 10), ("tier", 4), ("eligibility", 11), ("state", 10), ("deadline", 10), ("next", 10),
)


class JobsScreen(JobActions, Screen):
    """Every tracked job, filtered by a saved view or a search string."""

    BINDINGS = [
        ("enter", "open_detail", "Open"),
        ("a", "add_job", "Add job"),
        ("f", "fill_applications", "Fill & open"),
        ("s", "change_status", "Status"),
        ("u", "undo_status", "Undo status"),
        ("n", "set_next_action", "Next action"),
        ("d", "set_deadline", "Deadline"),
        ("k", "set_queue_state", "Queue state"),
        ("o", "open_url", "Open in browser"),
        ("slash", "focus_filter", "Filter"),
        ("escape", "clear_filter", "Clear filter"),
        ("r", "refresh", "Refresh"),
        Binding("asterisk", "toggle_favorite", "Favorite", key_display="*"),
        ("v", "toggle_favorites_only", "Favorites only"),
        ("c", "pick_track", "Track filter"),
        # Number keys pick a saved view. Hidden from the footer because the
        # views line already shows each key next to its title.
        *[Binding(str(digit), f"select_view('{digit}')", "Saved view", show=False)
          for digit in range(1, 10)],
    ]

    #: Extra lines for the ? overlay that no single binding can describe.
    EXTRA_HELP = (
        ("1-9", "Switch to the saved view with that key (see the views line)"),
        ("f", "Fill & open: with ★ favorites waiting, fills all of them; otherwise the selected job"),
        ("*", "Favorite: similar jobs rank higher everywhere; f or F (Apply tab) fills favorites"),
    )
    f_fills_favorites = True

    def __init__(self) -> None:
        super().__init__()
        self.view_index = 0
        self.filter_text = ""
        self.favorites_only = False

    def compose(self) -> ComposeResult:
        yield ScreenHeader("jobs")
        yield Static(id="views-line")
        with Vertical(id="filter-bar"):
            yield Input(placeholder="filter company / title / location, enter to apply", id="filter")
        yield DataTable(id="jobs-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#jobs-table", DataTable)
        for name, width in _COLUMNS:
            table.add_column(name, width=width, key=name)
        self.refresh_data()
        table.focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    # -------------------------------------------------------------- views

    @property
    def views(self):
        return self.app.views                       # type: ignore[attr-defined]

    def action_select_view(self, key: str) -> None:
        """Switch to the saved view bound to a number key."""
        for index, view in enumerate(self.views):
            if str(view.key) == str(key):
                self.view_index = index
                self.favorites_only = False
                self.refresh_data()
                self.notify(f"View: {view.title}")
                return
        self.notify(f"No view on key {key}.", severity="warning")

    def current_filter(self) -> jobs_core.JobFilter:
        """The active view, narrowed by the track pick (c) and the filter bar text.

        With v, every favorite instead of the view.
        """
        views = self.views
        if self.favorites_only:
            job_filter = jobs_core.JobFilter(favorites_only=True, include_closed=True)
        elif not views:
            job_filter = jobs_core.JobFilter(include_closed=True)
        else:
            view = views[min(self.view_index, len(views) - 1)]
            job_filter = jobs_core.JobFilter.from_view_filters(view.filters)
        job_filter.order = "best"
        job_filter.prefer_cities = self.app.config.prefer_cities             # type: ignore[attr-defined]
        job_filter.prefer_remote = self.app.config.profile.open_to_remote    # type: ignore[attr-defined]
        if self.track_filter:
            job_filter.tracks = [self.track_filter]
        if self.filter_text:
            job_filter.text = self.filter_text
        return job_filter

    # ------------------------------------------------------------- render

    def refresh_data(self) -> None:
        """Re-query the jobs and repaint the table, keeping the cursor row."""
        table = self.query_one("#jobs-table", DataTable)
        previous = self.current_job_id()
        jobs = jobs_core.list_jobs(self.app.conn, self.current_filter())  # type: ignore[attr-defined]

        table.clear()
        for job in jobs:
            table.add_row(
                favorite_mark(job),
                str(job.id),
                job.company_name,
                job.title,
                job.location or "-",
                ",".join(job.tracks) or "-",
                job.freshness,
                tier_markup(job.tier),
                eligibility_markup(job.eligibility),
                status_markup(job.status) if job.status else job.queue_state,
                short_date(job.deadline_at),
                due_label(job.next_action_at),
                key=str(job.id),
            )

        views = self.views
        titles = "  ".join(
            f"[{'bold $foreground on $primary' if index == self.view_index else 'dim'}] {view.key} {view.title} [/]"
            for index, view in enumerate(views)
        )
        suffix = f"   track: {self.track_filter} (c changes)" if self.track_filter else ""
        suffix += f"   filter: '{self.filter_text}'" if self.filter_text else ""
        if self.favorites_only:
            titles = "[bold $foreground on $primary] ★ favorites [/] [dim](v: back to the views)[/]"
        self.query_one("#views-line", Static).update(f"{titles}{suffix}   ({len(jobs)} jobs)")

        if previous is not None and str(previous) in [str(job.id) for job in jobs]:
            table.move_cursor(row=[job.id for job in jobs].index(previous))

    def current_job_id(self) -> int | None:
        """The job id under the cursor."""
        table = self.query_one("#jobs-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        return int(row_key.value) if row_key.value is not None else None

    # ------------------------------------------------------------ actions

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_toggle_favorites_only(self) -> None:
        """Show every favorite (open or closed), or go back to the saved view."""
        self.favorites_only = not self.favorites_only
        self.refresh_data()

    def action_add_job(self) -> None:
        self.app.action_add_job()                   # type: ignore[attr-defined]

    def action_open_detail(self) -> None:
        job_id = self.current_job_id()
        if job_id is not None:
            self.app.open_job(job_id)               # type: ignore[attr-defined]

    def action_focus_filter(self) -> None:
        """Show the filter bar and put the cursor in it."""
        self.query_one("#filter-bar").add_class("visible")
        field = self.query_one("#filter", Input)
        field.value = self.filter_text
        field.focus()

    def action_clear_filter(self) -> None:
        """Hide the filter bar and drop the filter text."""
        self.filter_text = ""
        self.query_one("#filter", Input).value = ""
        self.query_one("#filter-bar").remove_class("visible")
        self.refresh_data()
        self.query_one("#jobs-table", DataTable).focus()

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        self.filter_text = event.value.strip()
        self.refresh_data()
        self.query_one("#jobs-table", DataTable).focus()

    @on(DataTable.RowSelected, "#jobs-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self.app.open_job(int(event.row_key.value))   # type: ignore[attr-defined]

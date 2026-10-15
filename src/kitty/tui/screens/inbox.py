"""Inbox: new postings from discovery, triaged from the keyboard.

space marks rows; t / g / x / * then act on every marked row (or the cursor row
when nothing is marked). Skipping sets queue_state only - it never creates an
application row. * stars favorites (★), v shows only them, c shows one track
(quant, parttime, ...). Opens on the last 14 days; w switches to every new job.
Ctrl+F searches company names within those filters; Escape clears the search.
A line under the counts lists watchlist companies expected to open soon.
"""

from __future__ import annotations

from dataclasses import replace

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static

from kitty.core import favorites as favorites_core
from kitty.core import jobs as jobs_core
from kitty.core.discovery.openings import SOON_DAYS, opening_soon
from kitty.tui.actions import JobActions
from kitty.tui.helpers import eligibility_markup, favorite_mark, short_date, tier_markup
from kitty.tui.navigation import BY_SCREEN, ScreenHeader
from kitty.tui.screens.modals import PickModal

_COLUMNS = (
    ("", 1), ("★", 1), ("id", 5), ("company", 22), ("title", 38), ("location", 18), ("tracks", 14),
    ("freshness", 10), ("eligibility", 11), ("tier", 4), ("source", 14), ("posted", 10),
)
#: Column keys; a column's label is its key except for the two one-character columns.
_KEYS = {"": "mark", "★": "fav"}
RECENT_DAYS = 14


class InboxScreen(JobActions, Screen):
    """New, not-filtered jobs, best first."""

    BINDINGS = [
        ("space", "toggle_mark", "Mark"),
        ("f", "fill_applications", "Fill & open"),
        ("t", "set_tier", "Tier"),
        ("g", "add_track", "Track"),
        ("x", "skip", "Skip"),
        Binding("asterisk", "toggle_favorite", "Favorite", key_display="*"),
        ("v", "toggle_favorites_only", "Favorites only"),
        ("c", "pick_track", "Track filter"),
        ("enter", "open_detail", "Open"),
        ("o", "open_url", "Open in browser"),
        ("w", "toggle_window", "14 days / all"),
        ("r", "refresh", "Refresh"),
        Binding("ctrl+f", "focus_company_search", "Find company", priority=True),
        ("escape", "clear_company_search", "Clear search"),
    ]
    EXTRA_HELP = (
        ("f t g x *", "Act on every marked row, or the cursor row when none are marked"),
        ("f", "Fill & open: fills each application in the kitty browser; you click submit. "
              "With nothing marked and ★ favorites waiting, fills all of them"),
        ("*", "Favorite: similar jobs rank higher everywhere; f (nothing marked) or F fills favorites"),
    )
    f_fills_favorites = True

    def __init__(self) -> None:
        super().__init__()
        self.marked: set[int] = set()
        self.recent_only = True
        self.favorites_only = False
        self.company_search = ""
        self.counts = (0, 0)            # (shown, all new jobs)

    def compose(self) -> ComposeResult:
        yield ScreenHeader("inbox")
        yield Static(id="inbox-line")
        yield Static(id="inbox-opens")
        with Vertical(id="filter-bar"):
            yield Input(placeholder="Company name — Enter to search, Escape to clear", id="inbox-company")
        yield DataTable(id="inbox-table", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#inbox-table", DataTable)
        for name, width in _COLUMNS:
            table.add_column(name, width=width, key=_KEYS.get(name, name))
        self.refresh_data()
        table.focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    # ------------------------------------------------------------- data

    def current_filter(self) -> jobs_core.JobFilter:
        return jobs_core.JobFilter(
            queue_states=["new"],
            eligibility=jobs_core.NOT_FILTERED_OUT,
            posted_within_days=RECENT_DAYS if self.recent_only else None,
            order="best",
            prefer_cities=self.app.config.prefer_cities,                     # type: ignore[attr-defined]
            prefer_remote=self.app.config.profile.open_to_remote,           # type: ignore[attr-defined]
            favorites_only=self.favorites_only or None,
            tracks=[self.track_filter] if self.track_filter else None,
            company_text=self.company_search or None,
        )

    def fill_targets(self) -> list[int]:
        """Marked rows, or the cursor row; marks clear once the fill starts."""
        ids = self.targets()
        self.marked.clear()
        return ids

    def favorite_targets(self) -> list[int]:
        """Marked rows, or the cursor row; marks clear once they are starred."""
        return self.fill_targets()

    def refresh_data(self) -> None:
        table = self.query_one("#inbox-table", DataTable)
        previous = self.current_job_id()
        jobs = jobs_core.list_jobs(self.app.conn, self.current_filter())   # type: ignore[attr-defined]
        self.marked &= {job.id for job in jobs}

        table.clear()
        for job in jobs:
            table.add_row(
                "●" if job.id in self.marked else " ", favorite_mark(job),
                str(job.id), job.company_name, job.title, job.location or "-",
                ",".join(job.tracks) or "-", job.freshness, eligibility_markup(job.eligibility),
                tier_markup(job.tier), ",".join(s.source for s in job.sources) or "-",
                short_date(job.posted_at or job.first_seen_at),
                key=str(job.id),
            )
        all_new = len(jobs) if not self.recent_only else jobs_core.count_jobs(
            self.app.conn, replace(self.current_filter(), posted_within_days=None))   # type: ignore[attr-defined]
        self.counts = (len(jobs), all_new)
        self._update_line()
        self._update_opens()
        ids = [job.id for job in jobs]
        if previous in ids:
            table.move_cursor(row=ids.index(previous))

    def _update_line(self) -> None:
        """How many are shown, out of how many new jobs, and how to see the rest."""
        shown, all_new = self.counts
        sources_key = BY_SCREEN["sources"].key.upper()
        if self.recent_only:
            line = (f"[bold]{shown}[/bold] posted in the last {RECENT_DAYS} days, of [bold]{all_new}[/bold] new "
                    f"[$accent](w shows all {all_new})[/]")
        else:
            line = f"all [bold]{shown}[/bold] new jobs [$accent](w: only the last {RECENT_DAYS} days)[/]"
        line += f"   {len(self.marked)} marked"
        line += (f"   [$accent]track: {self.track_filter} (c changes)[/]" if self.track_filter
                 else "   [$accent](c: one track)[/]")
        line += ("   [$accent]★ favorites only (v shows all)[/]" if self.favorites_only
                 else "   [$accent](* favorite, v favorites only)[/]")
        if self.company_search:
            line += f"   company: {escape(self.company_search)} (Ctrl+F changes, Esc clears)"
        if shown == 0 and not self.favorites_only:
            line += (f"   [$accent]nothing here: {sources_key} runs a source[/]")
        self.query_one("#inbox-line", Static).update(line)

    def _update_opens(self) -> None:
        soon = opening_soon(self.app.conn)                                    # type: ignore[attr-defined]
        text = ", ".join(f"{escape(o.company)} ~{o.opens_on:%b %d} ({o.basis})" for o in soon)
        self.query_one("#inbox-opens", Static).update(
            f"opens soon: {text}" if soon else f"opens soon: nothing expected in the next {SOON_DAYS} days"
        )

    def current_job_id(self) -> int | None:
        table = self.query_one("#inbox-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        return int(row_key.value) if row_key.value is not None else None

    def targets(self) -> list[int]:
        """Marked rows, or the cursor row when nothing is marked."""
        if self.marked:
            return sorted(self.marked)
        current = self.current_job_id()
        return [current] if current is not None else []

    def _done(self, message: str) -> None:
        self.marked.clear()
        self.refresh_data()
        self.notify(message)

    # ---------------------------------------------------------- actions
    def action_focus_company_search(self) -> None:
        """Focus the company search without changing track, age, or favorite filters."""
        self.query_one("#filter-bar").add_class("visible")
        field = self.query_one("#inbox-company", Input)
        field.value = self.company_search
        field.focus()

    def action_clear_company_search(self) -> None:
        """Clear only the company search and return keyboard actions to the table."""
        self.company_search = ""
        self.query_one("#inbox-company", Input).value = ""
        self.query_one("#filter-bar").remove_class("visible")
        self.refresh_data()
        self.query_one("#inbox-table", DataTable).focus()

    @on(Input.Submitted, "#inbox-company")
    def _company_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.company_search = event.value.strip()
        self.refresh_data()
        self.query_one("#inbox-table", DataTable).focus()


    def action_toggle_mark(self) -> None:
        job_id = self.current_job_id()
        if job_id is None:
            return
        self.marked ^= {job_id}
        table = self.query_one("#inbox-table", DataTable)
        table.update_cell(str(job_id), "mark", "●" if job_id in self.marked else " ")
        table.action_cursor_down()
        self._update_line()

    def action_set_tier(self) -> None:
        ids = self.targets()
        if not ids:
            return

        def _apply(tier: str | None) -> None:
            if tier is None:
                return
            for job_id in ids:
                jobs_core.set_tier(self.app.conn, job_id, None if tier == "none" else tier,   # type: ignore[attr-defined]
                                   reason="set by hand in the Inbox", tier_source="manual")
            self._done(f"Tier {tier} on {len(ids)} job(s).")

        options = [("A - careful review", "A"), ("B - fast queue", "B"), ("C - keep, low priority", "C"),
                   ("clear tier", "none")]
        self.app.push_screen(PickModal(f"Tier for {len(ids)} job(s)", options), _apply)

    def action_add_track(self) -> None:
        ids = self.targets()
        if not ids:
            return

        def _apply(track: str | None) -> None:
            if track is None:
                return
            for job_id in ids:
                jobs_core.link_track(self.app.conn, job_id, track, "added by hand in the Inbox")  # type: ignore[attr-defined]
            self._done(f"Added track {track} to {len(ids)} job(s).")

        names = self.app.config.track_names                                  # type: ignore[attr-defined]
        self.app.push_screen(PickModal(f"Add a track to {len(ids)} job(s)", [(n, n) for n in names]), _apply)

    def action_skip(self) -> None:
        ids = self.targets()
        if ids:
            favorites_core.skip(self.app.conn, ids)                          # type: ignore[attr-defined]
            self._done(f"Skipped {len(ids)} job(s).")

    def action_toggle_window(self) -> None:
        self.recent_only = not self.recent_only
        self.refresh_data()

    def action_toggle_favorites_only(self) -> None:
        self.favorites_only = not self.favorites_only
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_open_detail(self) -> None:
        job_id = self.current_job_id()
        if job_id is not None:
            self.app.open_job(job_id)                                          # type: ignore[attr-defined]

    @on(DataTable.RowSelected, "#inbox-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self.app.open_job(int(event.row_key.value))                        # type: ignore[attr-defined]

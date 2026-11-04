"""Today: the daily digest (what is due, what is new), weekly targets, the funnel.

The landing screen. Every line that needs action is a link to the screen where
it gets handled. The digest is the same one `kitty today` prints.
"""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Static

from kitty.core import applications as applications_core
from kitty.core import jobs as jobs_core
from kitty.core import pipeline as pipeline_core
from kitty.core import questions as questions_core
from kitty.core import sources as sources_core
from kitty.core import stats as stats_core
from kitty.core import today as today_core
from kitty.core.triage import run as triage_run
from kitty.tui.helpers import bar, panel, short_date, status_markup
from kitty.tui.navigation import BY_SCREEN, ScreenHeader
from kitty.tui.theme import ASH, CRIMSON, EMBER, GOLD, MOSS, NIGHT

#: The keep under a red sky with two dragons, after the painting the theme comes
#: from. Sprites are pasted onto a sky of the banner's width: the keep at the
#: left edge, the dragons at fixed fractions of the width.
_KEEP = (
    "              ▗▖               ",
    "          ▲   ▐▌  ▲    ▲       ",
    "      ▲  ▐▌ ▗▟██▙▖ ▐▌  ▐▌      ",
    "   ▲ ▐▌ ▗▟█▙██████▙▟█▙▐█▌  ▲   ",
    "  ▐▌▗▟███████▛▀▀▜██████████▙▖  ",
    "▄▟████████████▌  ▐████████████▙▄▄▃▂",
)
_DRAGON = (
    "▚▄        ▄▞     ",
    " ▝▜▙▄▄▄▄▟▛▘▀▀▄▄▖ ",
    "     ▝▀▘       ▀▘",
)
_WYRMLING = (
    "▚▖  ▗▞   ",
    " ▜▙▟▛▀▀▄▖",
)
_HORIZON = "▁▁▂▃▂▁▁▁▁▁▂▄▃▁▁▁▁▁▁▂▁▁▁▁▁▃▅▃▂▁▁▁▁▁▁▁▁▂▃▂▁▁▁▁"
#: Sky colour per row, darkest at the top and brightest at the horizon.
_SKY = ("#260705", "#3a0b08", "#520f0b", "#6c160f", "#882015", "#a42a1c")
#: The banner only shows when the terminal has room for it and the panels.
BANNER_MIN_HEIGHT = 36


def _paste(rows: list[list[str]], sprite: tuple[str, ...], top: int, left: int) -> None:
    """Draw a sprite's non-space characters onto the rows, clipped at the edges."""
    for down, line in enumerate(sprite):
        for across, char in enumerate(line):
            column = left + across
            if char != " " and 0 <= column < len(rows[top + down]):
                rows[top + down][column] = char


class CastleBanner(Widget):
    """Pure decoration: a black keep and two dragons against a red sky."""

    DEFAULT_CSS = "CastleBanner { height: 6; }"

    def render(self) -> Text:
        width = self.size.width
        rows = [[" "] * width for _ in _SKY]
        rows[-1] = list((_HORIZON * (width // len(_HORIZON) + 1))[:width])
        _paste(rows, _KEEP, 0, 1)
        _paste(rows, _DRAGON, 1, width * 11 // 20)
        _paste(rows, _WYRMLING, 0, width * 3 // 4)
        text = Text(no_wrap=True, overflow="crop")
        for index, (row, sky) in enumerate(zip(rows, _SKY)):
            text.append("".join(row), style=f"{NIGHT} on {sky}")
            if index < len(rows) - 1:
                text.append("\n")
        return text


def _link(screen: str, text: str) -> str:
    """A line that jumps to a screen when clicked, then that screen's name and key.

    The hint sits outside the link: Textual's link colour would repaint it.
    """
    place = BY_SCREEN[screen]
    return f"[@click=app.goto('{screen}')]{text}[/]  [{EMBER}]→ {place.label} {place.key.upper()}[/]"


class DashboardScreen(Screen):
    """The landing screen: am I on pace, and what is waiting for me."""

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("a", "add_job", "Add job"),
        ("i", "app.goto('inbox')", "Inbox"),
        ("s", "app.goto('apply')", "Start applying"),
        ("j", "app.goto('jobs')", "Jobs"),
        ("p", "app.goto('pipeline')", "Pipeline"),
    ]

    def compose(self) -> ComposeResult:
        yield CastleBanner()
        yield ScreenHeader("dashboard")
        with Horizontal(id="dashboard-body"):
            with VerticalScroll(classes="dash-column"):
                with panel("Today"):
                    yield Static(id="attention")
                with panel("Weekly targets"):
                    yield Static(id="targets")
            with VerticalScroll(classes="dash-column"):
                with panel("Pipeline funnel"):
                    yield Static(id="funnel")
                with panel("Jobs by tier (not applied)"):
                    yield Static(id="tiers")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()

    def on_resize(self, event: events.Resize) -> None:
        self.query_one(CastleBanner).display = event.size.height >= BANNER_MIN_HEIGHT

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()
        self.notify("Refreshed.")

    def action_add_job(self) -> None:
        self.app.action_add_job()           # type: ignore[attr-defined]

    # ---------------------------------------------------------------- render

    def refresh_data(self) -> None:
        """Re-read every panel from the database (the digest is computed once)."""
        conn = self.app.conn                # type: ignore[attr-defined]
        digest = today_core.build_digest(conn, self.app.config)              # type: ignore[attr-defined]
        self.query_one("#targets", Static).update(self._targets_text(digest))
        self.query_one("#funnel", Static).update(self._funnel_text(conn))
        self.query_one("#attention", Static).update(self._attention_text(conn, digest))
        self.query_one("#tiers", Static).update(self._tiers_text(conn))

    def _targets_text(self, digest: today_core.Digest) -> str:
        """Applications this week against each track's target, and the streak."""
        target_total = sum(p.weekly_target for p in digest.weekly)
        lines = []
        for item in digest.weekly:
            colour = MOSS if item.applied_this_week >= item.weekly_target else GOLD
            lines.append(
                f"{item.track:<10} [{colour}]{bar(item.fraction)}[/] "
                f"{item.applied_this_week}/{item.weekly_target}"
                + (f"  [dim]{item.remaining} to go[/dim]" if item.remaining else f"  [{MOSS}]done[/]")
            )
        lines.append("")
        lines.append(f"[bold]all tracks[/bold] {digest.weekly_total}/{target_total} this week "
                     "(week starts Monday)")
        streak = digest.streak_days
        lines.append(f"[{EMBER if streak else ASH}]streak: {streak} day{'s' if streak != 1 else ''} "
                     "in a row with an application[/]")
        return "\n".join(lines)

    def _funnel_text(self, conn) -> str:
        """Current pipeline counts and the two rates that matter."""
        funnel = pipeline_core.funnel(conn)
        rows = [
            ("applied", funnel.applied), ("OA", funnel.oa), ("interview", funnel.interview),
            ("offer", funnel.offer), ("rejected", funnel.rejected), ("ghosted", funnel.ghosted),
        ]
        widest = max((count for _, count in rows), default=0) or 1
        lines = [
            f"{name:<10} {'▇' * max(1, round(count / widest * 10)) if count else ''} {count}"
            for name, count in rows
        ]
        lines.append("")
        lines.append(f"total {funnel.total}   response {funnel.response_rate:.0%}   "
                     f"reached OA+ {funnel.advance_rate:.0%}")
        return "\n".join(lines)

    def _attention_text(self, conn, digest: today_core.Digest) -> str:
        """Today's digest: what is due, what is new, and what needs a decision."""
        new_jobs = jobs_core.count_jobs(conn, jobs_core.JobFilter(queue_states=["new"]))
        recent = jobs_core.count_jobs(conn, jobs_core.JobFilter(queue_states=["new"], posted_within_days=14))
        queued = jobs_core.count_jobs(conn, jobs_core.JobFilter(          # what the Apply tab walks
            queue_states=["new", "queued"], tiers=["A", "B"],
            eligibility=jobs_core.NOT_FILTERED_OUT, not_applied=True))
        a_tier = jobs_core.count_jobs(conn, jobs_core.JobFilter(tiers=["A"], not_applied=True))
        proposals = len(applications_core.proposed_events(conn))
        questions_core.refresh_if_changed(conn, self.app.config)                # type: ignore[attr-defined]
        unanswered = len(questions_core.list_questions(conn))
        days = self.app.config.apply.archive_after_days                      # type: ignore[attr-defined]

        lines = []
        if digest.reminders:
            lines.append(f"[bold {CRIMSON}]Due today or overdue[/]")
            lines.extend(f"  {short_date(r.due_at)}  {r.kind:<8} {escape(r.label)}" for r in digest.reminders[:6])
            lines.append("")
        new_ab = "  ".join(f"{track} [{GOLD}]{counts.get('A', 0)}A[/]/{counts.get('B', 0)}B"
                           for track, counts in digest.new_by_track.items() if counts.get('A') or counts.get('B'))
        lines += [
            _link("inbox", f"{new_jobs:>4} new jobs ({recent} posted in the last 14 days)"),
            *([f"      [dim]by track:[/dim] {new_ab}"] if new_ab else []),
            _link("apply", f"{queued:>4} A/B jobs waiting in the apply queue"),
            _link("apply", f"{a_tier:>4} A-tier not applied yet"),
            _link("pipeline", f"{proposals:>4} status proposals to confirm"),
            _link("profile", f"{unanswered:>4} unanswered screening questions"),
            _link("pipeline", f"{len(digest.stale):>4} applications quiet for {days}+ days (consider archiving)"),
            "",
            self._sources_line(conn),
            self._triage_line(conn),
        ]
        if digest.deadlines:
            lines += ["", "[bold]Deadlines this week[/bold]"]
            lines.extend(f"  {short_date(job.deadline_at)}  {escape(job.company_name)} - {escape(job.title)}"
                         for job in digest.deadlines[:5])
        if digest.opens_soon:
            lines += ["", "[bold]Opens soon[/bold]"]
            lines.extend(f"  ~{o.opens_on:%b %d}  {escape(o.company)}" for o in digest.opens_soon[:5])
        return "\n".join(lines)

    def _triage_line(self, conn) -> str:
        """How many kept jobs still have no tier, and whether a triage batch is waiting."""
        untriaged = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE tier IS NULL AND is_active = 1 "
            "AND queue_state IN ('new', 'queued') AND eligibility != 'ineligible'"
        ).fetchone()[0]
        line = f"{untriaged:>4} jobs not triaged yet [dim](kitty triage)[/dim]"
        if triage_run.pending_batches(conn):
            line += f"  [{GOLD}]a triage batch is waiting: kitty triage --collect[/]"
        return line

    def _sources_line(self, conn) -> str:
        """One line on source health; a failing source must never look like 'no new jobs'.

        The colour is on a dot before the link, because links repaint their text.
        """
        ran = [s for s in sources_core.list_sources(conn) if s.enabled and s.last_status]
        if not ran:
            return f"[{ASH}]●[/] " + _link("sources", "no source has run yet - run kitty discover")
        failing = [s for s in ran if s.last_status in sources_core.PROBLEM_STATUSES]
        last = max(s.last_run_at or "" for s in ran)
        if failing:
            healthy = sum(s.last_status == "ok" for s in ran)
            summary = _link("sources", f"{len(failing)} source(s) need attention; {healthy} healthy")
            details = [
                "  " + escape(sources_core.source_warning(source, detail_limit=160))
                for source in failing
            ]
            return "\n".join([f"[{GOLD}]●[/] " + summary, *details])
        return f"[{MOSS}]●[/] " + _link("sources", f"{len(ran)} sources healthy, last run {short_date(last)}")

    def _tiers_text(self, conn) -> str:
        """Open, unapplied jobs grouped by tier."""
        counts = stats_core.counts_by_tier(conn)
        order = ["A", "B", "C", "untriaged"]
        lines = [
            f"{name:<10} {counts.get(name, 0)}"
            for name in order
        ]
        lines.append("")
        lines.append("[dim]states:[/dim] " + "  ".join(
            f"{state} {count}" for state, count in sorted(stats_core.counts_by_queue_state(conn).items())
        ))
        lines.append("")
        lines.append("[dim]" + "  ".join(status_markup(s) for s in
                                         ("applied", "oa", "interview", "offer")) + "[/dim]")
        return "\n".join(lines)

"""Where you can go, in the order the work flows, and the header that shows it.

The main screens follow one job's life: it is found by a Source, decided on in
the Inbox, kept in Jobs, and followed in the Pipeline. PLACES lists them once;
the F-keys, the tab bar, the ? overlay and the command palette are all built
from it, so they cannot disagree. Each place has a plain name (what you look
for) and a fantasy epithet (decoration only).
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


@dataclass(frozen=True)
class Place:
    screen: str        # name in ApplyAIApp.SCREENS
    label: str
    key: str           # the F-key that goes there
    epithet: str
    purpose: str       # one line: what you do here


PLACES: tuple[Place, ...] = (
    Place("dashboard", "Today", "f1", "the keep",
          "What is due today, what is new, and where you stand this week. Click a line to go there."),
    Place("inbox", "Inbox", "f2", "the gate",
          "New postings from your sources. Tier the good ones (t), star favorites (*), skip the rest (x). "
          "c shows one track."),
    Place("apply", "Apply", "f3", "the sortie",
          "One job at a time, A tier first: f fill the form, submit it yourself, then a applied. "
          "F autofills your ★ favorites in the background."),
    Place("jobs", "Jobs", "f4", "the archive",
          "Every job you kept. Open one, apply in your browser, then set its status (s). c shows one track."),
    Place("pipeline", "Pipeline", "f5", "the campaign",
          "Your applications by stage. Move them on (s) as replies come in."),
    Place("sources", "Sources", "f6", "the watchtowers",
          "Where postings come from and whether each source is healthy. enter runs one."),
    Place("resume", "Resume", "f7", "the forge",
          "Posting evidence and local candidate edits. g generates, v reviews versions, b rolls back; promotion requires approval."),
    Place("profile", "Profile", "f8", "the crest",
          "Who you are to an application: facts, resumes, preferences, answers. e edits, n adds a resume."),
    Place("finder", "Finder", "f9", "the scouts",
          "Companies worth watching that match your skills. a approves (joins the watchlist), r rejects."),
    Place("reports", "Reports", "f10", "the chronicle",
          "Which resumes, tracks, sources and tiers hear back. e / j export everything."),
)

BY_SCREEN: dict[str, Place] = {place.screen: place for place in PLACES}


def key_of(screen: str) -> str:
    """The F-key label of a main screen, e.g. 'F8', for hints that point there."""
    return BY_SCREEN[screen].key.upper()


class ScreenHeader(Vertical):
    """The top of every screen: clickable tabs, then what this screen is for.

    The current tab is lit blood red. Job Detail is not a tab; it passes
    place=None and shows the job as a crumb with the way back.
    """

    def __init__(self, place: str | None, *, crumb: str = "") -> None:
        super().__init__(id="header")
        self.place = place
        self.crumb = crumb

    def compose(self) -> ComposeResult:
        yield Static(self._tabs(), id="nav-tabs")
        yield Static(self._purpose(), id="nav-purpose")

    def set_crumb(self, crumb: str) -> None:
        """Show which job Job Detail is about."""
        self.crumb = crumb
        self.query_one("#nav-purpose", Static).update(self._purpose())

    def _tabs(self) -> str:
        """Clickable labels; the F-key stays outside the link so it keeps its ember colour."""
        tabs = []
        for place in PLACES:
            key = f"[$accent]{place.key.upper()}[/]"
            if place.screen == self.place:
                tabs.append(f"[bold $foreground on $primary] {place.label} {key} [/]")
            else:
                tabs.append(f" [@click=app.goto('{place.screen}')]{place.label}[/] {key} ")
        return "[bold $primary] ♜ [/][bold $accent]applyai[/]   " + " ".join(tabs)

    def _purpose(self) -> str:
        place = BY_SCREEN.get(self.place or "")
        if place is None:
            return f"[@click=screen.back]‹ back[/] [$accent]esc[/]   [bold]{escape(self.crumb)}[/]"
        return (f"[bold $accent]✦ {place.label.upper()}[/] [italic dim]{place.epithet}[/]   "
                f"{place.purpose}")

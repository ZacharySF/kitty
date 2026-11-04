"""Watching filled tabs: record the application once I have submitted it.

After the filler is done, the tabs stay open for me to check and submit. The
watcher looks at each tab every few seconds and, when a tab shows its board's
confirmation page, records the application (with follow-up reminders) the way
pressing "a" would. It only reads: the tab's URL (and its iframes' URLs) and,
when a board needs it, the page title and visible text, from the private
sandbox realm. It never clicks, types or submits anything.

Firefox allows one remote-control session at a time, so the watcher runs in
the filler's session right after filling (filler.fill_jobs). Watching ends when
every tab is confirmed or closed, when the stop flag is set (Ctrl-C, the TUI),
or after `watch_hours` from config/apply.yaml.

What each board shows after a submit (checked 2026-10-03):
- Greenhouse: the form's URL plus "/confirmation", e.g.
  job-boards.greenhouse.io/spacex/jobs/8636134002/confirmation, title "Thank
  you for applying"; the body text is the company's own message. Forms
  embedded on a company site go to /embed/job_app/confirmation in the iframe.
- Lever: the posting URL plus "/thanks", text "Application submitted!".
- Ashby: the same /application URL; the form is replaced by an element with
  class "ashby-application-form-success-container" (Ashby's documented styling
  hook) holding "Success" and, unless the company set its own, "Your
  application was successfully submitted. We'll contact you if there are next
  steps." (from Ashby's job-board JavaScript bundle).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable
from urllib.parse import urlsplit

from kitty.config import Config, _authentication_origin
from kitty.core import applications as applications_core
from kitty.core import followups
from kitty.core.apply.bidi import Bidi, BidiError, ConnectionLost, Frame, Tab, TabClosed
from kitty.db.connection import transaction

#: Seconds between two looks at the same tab: cheap reads, and I submit by hand anyway.
POLL_SECONDS = 4.0

#: Seconds a single look may take before it is skipped until the next round.
_LOOK_TIMEOUT = 10.0


# ------------------------------------------------------------------ the rules


@dataclass(frozen=True)
class ConfirmationRule:
    """How one board's confirmation page looks (see the module docstring)."""

    host: str                         # the board's domain, e.g. "greenhouse.io"
    path: str | None = None           # regex the confirmation URL's path matches (None: any)
    phrases: tuple[str, ...] = ()     # lower-case texts; one must show (empty: the URL is enough)
    selector: str | None = None       # an element only the confirmation shows (counts like a phrase)

    @property
    def needs_page(self) -> bool:
        """True when the URL alone does not prove it: the page's text must be read too."""
        return bool(self.phrases or self.selector)


RULES: dict[str, ConfirmationRule] = {
    # the /confirmation route is only reached by submitting, so the URL is enough
    # (and it is all we can read cheaply when the form sits in a company-site iframe)
    "greenhouse": ConfirmationRule(host="greenhouse.io", path=r"/confirmation/?$"),
    "lever": ConfirmationRule(
        host="lever.co", path=r"/thanks/?$",
        phrases=("application submitted", "thank you for applying", "thanks for applying"),
    ),
    "ashby": ConfirmationRule(
        host="ashbyhq.com",
        phrases=("your application was successfully submitted", "application submitted",
                 "thanks for applying", "thank you for applying"),
        selector=".ashby-application-form-success-container",
    ),
}


@dataclass(frozen=True)
class PageView:
    """What one look at a tab saw. Text fields stay empty when they were not read."""

    urls: list[str]                   # the tab's top document first, then its iframes
    title: str = ""
    text: str = ""
    marked: bool = False              # the rule's selector matched an element


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_NUMERIC_ID = re.compile(r"(?:gh_jid=|token=|/jobs/)(\d{4,})")


def posting_key(url: str) -> str | None:
    """The posting's own id in a form URL (Lever / Ashby UUID, Greenhouse number).

    A confirmation only counts when its URL carries the same id, so a tab I
    used to browse to another job never records the wrong one.
    """
    match = _UUID.search(url)
    if match:
        return match.group(0).lower()
    match = _NUMERIC_ID.search(url)
    return match.group(1) if match else None


def _on_host(url: str, host: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname == host or hostname.endswith("." + host)


def confirmation_url(rule: ConfirmationRule, urls: list[str], key: str | None) -> str | None:
    """The first URL that looks like this board's confirmation page for this posting."""
    for url in urls:
        if not _on_host(url, rule.host):
            continue
        if rule.path is not None and not re.search(rule.path, urlsplit(url).path):
            continue
        if key is not None and key not in url.lower():
            continue
        return url
    return None


def _normal(text: str) -> str:
    """Lower case, curly apostrophes made straight, runs of whitespace made one space."""
    return " ".join(text.replace("\u2019", "'").lower().split())


def confirmed(rule: ConfirmationRule, view: PageView, key: str | None) -> str | None:
    """The confirmation page's URL when `view` shows a submitted application, else None."""
    url = confirmation_url(rule, view.urls, key)
    if url is None or not rule.needs_page:
        return url
    text = _normal(f"{view.title}\n{view.text}")
    if view.marked or any(phrase in text for phrase in rule.phrases):
        return url
    return None


# ------------------------------------------------------------- the watching


@dataclass
class WatchedTab:
    """One filled tab waiting for my submit."""

    job_id: int
    context: str                      # live BiDi id, valid only in the current session
    ats: str                          # a key of RULES
    form_url: str                     # the page the filler opened
    label: str                        # "Company - Title", for messages
    resume_id: int | None             # recorded with the application
    track: str | None
    marker: str                       # stable browser-storage token across sessions
    attempt_id: int | None = None     # the ready apply_attempts row (per-field correctness)
    capture: object | None = None     # attempts.Capture: salt and sandbox keys, memory only


def _ignore(_: object) -> None:
    """Default callback: do nothing."""


@dataclass
class Watch:
    """How fill_jobs watches the tabs it filled.

    `tabs` may start with tabs left over from an earlier run (the TUI passes
    them on, so filling a second job does not forget the first; reattach()
    finds them again in the new session); fill_jobs adds the tabs it fills, and
    confirmed or closed tabs are removed. Whatever is left when watching ends
    is still open and unsubmitted.
    """

    stop: threading.Event = field(default_factory=threading.Event)   # set it to stop early
    hours: float = 4.0
    tabs: list[WatchedTab] = field(default_factory=list)
    on_start: Callable[[int], None] = _ignore                         # number of tabs watched
    on_applied: Callable[[WatchedTab], None] = _ignore                # a tab was just recorded


#: Reads what the confirmation rules need from the top document (sandbox realm, read-only).
_PROBE = """(selector) => ({
  title: document.title || "",
  text: document.body ? document.body.innerText.slice(0, 5000) : "",
  marked: selector ? document.querySelector(selector) !== null : false,
})"""


def look(bidi: Bidi, tab: WatchedTab) -> PageView | None:
    """One look at a tab; None when the tab was closed.

    Reads the frame URLs first (one cheap command) and the page text only when
    the URL already fits and the board's rule needs the text. A page that is
    busy navigating gives an empty view; the next round looks again. A lost
    connection to Firefox is raised: nothing more can be watched.
    """
    rule = RULES[tab.ats]
    try:
        urls = [frame.url for frame in Tab(bidi, tab.context).frames()]
        if not urls:
            return None
        if not rule.needs_page or confirmation_url(rule, urls, posting_key(tab.form_url)) is None:
            return PageView(urls=urls)
        seen = Frame(bidi, tab.context, urls[0]).call(_PROBE, rule.selector, timeout=_LOOK_TIMEOUT)
        return PageView(urls=urls, title=seen["title"], text=seen["text"], marked=seen["marked"])
    except TabClosed:
        return None
    except ConnectionLost:
        raise
    except BidiError:
        return PageView(urls=[])


CONTEXT_MARKER = "kitty.application.context"


def owned_context(
    bidi: Bidi, contexts: list[dict], saved_context: str, origins: set[str],
) -> dict | None:
    """Resolve an owned tab; cache marker reads within this context-tree snapshot."""
    current = next((context for context in contexts if context["context"] == saved_context), None)
    if current is not None:
        return current
    matches = []
    for context in contexts:
        if _authentication_origin(context.get("url", "")) not in origins:
            continue
        if "_kitty_marker" not in context:
            try:
                context["_kitty_marker"] = Tab(bidi, context["context"]).main.call("""key => {
                  try { return sessionStorage.getItem(key); } catch { return null; }
                }""", CONTEXT_MARKER)
            except ConnectionLost:
                raise
            except BidiError:
                context["_kitty_marker"] = None
        if context["_kitty_marker"] == saved_context:
            matches.append(context)
    return matches[0] if len(matches) == 1 else None


def reattach(bidi: Bidi, tabs: list[WatchedTab]) -> list[WatchedTab]:
    """Rebind owned tabs without adopting another tab for the same posting."""
    contexts = bidi.command("browsingContext.getTree", {}).get("contexts", [])
    found: list[WatchedTab] = []
    taken: set[str] = set()
    for tab in tabs:
        origin = _authentication_origin(tab.form_url)
        if origin is None:
            continue
        context = owned_context(bidi, contexts, tab.marker, {origin})
        if context is not None and context["context"] not in taken:
            taken.add(context["context"])
            found.append(replace(tab, context=context["context"]))
    return found


def record_submission(
    conn: sqlite3.Connection, config: Config, tab: WatchedTab, url: str,
    say: Callable[[str], None],
) -> bool:
    """Record the application a confirmation page proves; False if it was already recorded.

    The caller tells me about a new record (Watch.on_applied); only the
    already-recorded case is said here.
    """
    if applications_core.get_application_for_job(conn, tab.job_id) is not None:
        say(f"{tab.label}: submitted (already recorded as applied)")
        return False
    with transaction(conn):
        followups.mark_applied(conn, config, tab.job_id, resume_id=tab.resume_id, track=tab.track,
                               source="rule", evidence=f"confirmation page {url}")
    return True


def watch_tabs(
    looker: Callable[[WatchedTab], PageView | None],
    conn: sqlite3.Connection,
    config: Config,
    watch: Watch,
    say: Callable[[str], None],
    *,
    poll_seconds: float = POLL_SECONDS,
    observe: Callable[[WatchedTab, bool], None] | None = None,
) -> None:
    """Look at every tab in `watch.tabs` each round until none is left, stop is set or time is up.

    `looker` is `look` bound to the session (tests pass a fake). A confirmed tab
    is recorded once and dropped; a closed tab is dropped. `observe(tab, confirmed)`
    runs after each look at an open tab, before a confirmation is recorded
    (attempts.observe: value hashes for per-field correctness, read-only).
    """
    deadline = time.monotonic() + watch.hours * 3600
    while watch.tabs and not watch.stop.is_set() and time.monotonic() < deadline:
        for tab in list(watch.tabs):
            if watch.stop.is_set():
                return
            view = looker(tab)
            if view is None:
                watch.tabs.remove(tab)
                say(f"{tab.label}: tab closed, no longer watching it")
                continue
            url = confirmed(RULES[tab.ats], view, posting_key(tab.form_url))
            if observe is not None:
                observe(tab, url is not None)
            if url is not None:
                watch.tabs.remove(tab)
                if record_submission(conn, config, tab, url, say):
                    watch.on_applied(tab)
        if watch.tabs:
            watch.stop.wait(poll_seconds)

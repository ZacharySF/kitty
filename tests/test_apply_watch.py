"""The submission watcher: which pages count as a confirmation, and what gets recorded.

URLs and texts are the real ones seen on 2026-10-03 (Greenhouse, Lever) or taken
from the board's JavaScript bundle (Ashby); see core/apply/watch.py.
"""

from __future__ import annotations

import pytest
from kitty.core import applications as applications_core
from kitty.core import jobs as jobs_core
from kitty.core import reminders as reminders_core
from kitty.core.apply.watch import RULES, PageView, Watch, WatchedTab, confirmed, posting_key, watch_tabs

GH_FORM = "https://job-boards.greenhouse.io/example-space/jobs/900021"
LEVER_FORM = "https://jobs.lever.co/example-data/00000000-0000-4000-8000-000000900022/apply"
ASHBY_FORM = "https://jobs.ashbyhq.com/example-chip/00000000-0000-4000-8000-000000900024/application"

ASHBY_SUCCESS = ("Success\nYour application was successfully submitted. "
                 "We\u2019ll contact you if there are next steps.")


def _confirmed(ats: str, form: str, view: PageView) -> str | None:
    return confirmed(RULES[ats], view, posting_key(form))


@pytest.mark.parametrize(("ats", "form", "view"), [
    ("greenhouse", GH_FORM, PageView(urls=[GH_FORM + "/confirmation"])),
    # old boards.greenhouse.io host and a company-site embed (the iframe shows the confirmation)
    ("greenhouse", "https://boards.greenhouse.io/example-space/jobs/900021",
     PageView(urls=["https://boards.greenhouse.io/example-space/jobs/900021/confirmation"])),
    ("greenhouse", "https://space.example.invalid/careers/jobs?gh_jid=900021",
     PageView(urls=["https://space.example.invalid/careers/jobs?gh_jid=900021",
                    "https://job-boards.greenhouse.io/embed/job_app/confirmation?for=example-space&token=900021"])),
    ("lever", LEVER_FORM, PageView(
        urls=["https://jobs.lever.co/example-data/00000000-0000-4000-8000-000000900022/thanks"],
        title="Example Data - Year at Example Data - Software Engineer, Internship",
        text="Year at Example Data - Software Engineer, Internship\nApplication submitted!\nRETURN TO THE MAIN PAGE")),
    ("ashby", ASHBY_FORM, PageView(urls=[ASHBY_FORM], text=ASHBY_SUCCESS)),
    # a company's own success message: the success container still marks it
    ("ashby", ASHBY_FORM, PageView(urls=[ASHBY_FORM], text="Success\nWe got it, talk soon!", marked=True)),
])
def test_confirmation_pages_are_recognised(ats, form, view):
    assert _confirmed(ats, form, view) is not None


@pytest.mark.parametrize(("ats", "form", "view"), [
    # the form itself, before the submit
    ("greenhouse", GH_FORM, PageView(urls=[GH_FORM], title="Job Application for Starship Intern at SpaceX")),
    ("lever", LEVER_FORM, PageView(urls=[LEVER_FORM], text="Submit your application")),
    ("ashby", ASHBY_FORM, PageView(urls=[ASHBY_FORM], text="Application\nSubmit Application")),
    # the tab was used to apply for a different posting: not this job's confirmation
    ("greenhouse", GH_FORM, PageView(urls=["https://job-boards.greenhouse.io/example-space/jobs/1111111111/confirmation"])),
    ("lever", LEVER_FORM, PageView(
        urls=["https://jobs.lever.co/example-data/00000000-0000-4000-8000-000000900023/thanks"],
        text="Application submitted!")),
    # right path on another site
    ("greenhouse", GH_FORM, PageView(urls=["https://example.invalid/jobs/900021/confirmation"])),
    # /thanks whose text did not load yet (or is an error page)
    ("lever", LEVER_FORM, PageView(
        urls=["https://jobs.lever.co/example-data/00000000-0000-4000-8000-000000900022/thanks"], text="")),
    # a page the watcher could not read this round
    ("ashby", ASHBY_FORM, PageView(urls=[])),
])
def test_other_pages_are_not_confirmations(ats, form, view):
    assert _confirmed(ats, form, view) is None


# ---------------------------------------------------------------- the loop


class FakeBrowser:
    """Tabs by context id: a list of views shown one per look (the last one repeats); None = closed."""

    def __init__(self, pages: dict[str, list[PageView | None]]) -> None:
        self.pages = pages
        self.looks: list[str] = []

    def look(self, tab: WatchedTab) -> PageView | None:
        self.looks.append(tab.context)
        views = self.pages[tab.context]
        return views.pop(0) if len(views) > 1 else views[0]


def _tab(workspace, context: str, form: str, ats: str) -> WatchedTab:
    job, _ = jobs_core.upsert_job(workspace.conn, company=f"Co {context}", title="Intern", url=form)
    return WatchedTab(job_id=job.id, context=context, ats=ats, form_url=form,
                      label=f"Co {context} - Intern", resume_id=None, track=None, marker=context)


def _run(workspace, browser: FakeBrowser, tabs: list[WatchedTab]) -> tuple[Watch, list[int], list[str]]:
    applied: list[int] = []
    said: list[str] = []
    watch = Watch(hours=1, tabs=list(tabs), on_applied=lambda tab: applied.append(tab.job_id))
    watch_tabs(browser.look, workspace.conn, workspace.config, watch, said.append, poll_seconds=0)
    return watch, applied, said


def test_submitted_tab_is_recorded_once_and_closed_tab_is_dropped(workspace):
    submitted = _tab(workspace, "a", GH_FORM, "greenhouse")
    closed = _tab(workspace, "b", LEVER_FORM, "lever")
    browser = FakeBrowser({
        "a": [PageView(urls=[GH_FORM]), PageView(urls=[GH_FORM]),
              PageView(urls=[GH_FORM + "/confirmation"])],
        "b": [PageView(urls=[LEVER_FORM]), None],
    })

    watch, applied, said = _run(workspace, browser, [submitted, closed])

    assert watch.tabs == []
    assert applied == [submitted.job_id]
    assert browser.looks.count("b") == 2 and browser.looks.count("a") == 3
    application = applications_core.get_application_for_job(workspace.conn, submitted.job_id)
    assert application is not None and application.status == "applied"
    event = applications_core.history(workspace.conn, application.id)[0]
    assert (event.source, event.evidence) == ("rule", f"confirmation page {GH_FORM}/confirmation")
    days = workspace.config.apply.followup_days
    assert len(reminders_core.followups_for(workspace.conn, application.id)) == len(days)
    assert applications_core.get_application_for_job(workspace.conn, closed.job_id) is None
    assert any("tab closed" in line for line in said)


def test_job_already_applied_is_not_recorded_again(workspace):
    tab = _tab(workspace, "a", ASHBY_FORM, "ashby")
    earlier = applications_core.record_application(workspace.conn, tab.job_id, applied_at="2026-09-01T10:00:00")
    browser = FakeBrowser({"a": [PageView(urls=[ASHBY_FORM], text=ASHBY_SUCCESS)]})

    watch, applied, said = _run(workspace, browser, [tab])

    assert watch.tabs == [] and applied == []
    application = applications_core.get_application_for_job(workspace.conn, tab.job_id)
    assert application.applied_at == earlier.applied_at
    assert len(applications_core.history(workspace.conn, application.id)) == 1
    assert reminders_core.followups_for(workspace.conn, application.id) == []
    assert any("already recorded" in line for line in said)


def test_stop_flag_ends_watching_and_keeps_unsubmitted_tabs(workspace):
    tab = _tab(workspace, "a", GH_FORM, "greenhouse")
    browser = FakeBrowser({"a": [PageView(urls=[GH_FORM])]})
    watch = Watch(hours=1, tabs=[tab])

    def look_then_stop(watched: WatchedTab) -> PageView | None:
        watch.stop.set()                  # e.g. Ctrl-C or the TUI's w while a look runs
        return browser.look(watched)

    watch_tabs(look_then_stop, workspace.conn, workspace.config, watch, lambda line: None, poll_seconds=0)

    assert watch.tabs == [tab]
    assert browser.looks == ["a"]

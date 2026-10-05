"""TUI smoke tests: the screens render, the keys do what the help says.

These drive the real app through Textual's pilot, so a broken binding or a
renamed widget id fails here instead of in front of me.
"""

from textual.widgets import Button, DataTable, Input, RadioButton, Static

from applyai.core import applications as applications_core
from applyai.core import jobs as jobs_core
from applyai.tui.app import ApplyAIApp
from applyai.tui.navigation import PLACES
from applyai.tui.screens.help_screen import HelpScreen
from applyai.tui.screens.job_detail import JobDetailScreen
from applyai.tui.screens.jobs import JobsScreen
from applyai.tui.screens.modals import AddJobModal
from applyai.tui.screens.pipeline import PipelineScreen

SIZE = (180, 50)
#: Each main screen's key, so renumbering the tabs never breaks these tests.
KEY = {place.screen: place.key for place in PLACES}


def _seeded(workspace, count=3):
    """A few jobs, one of them already applied to."""
    ids = []
    for index in range(count):
        job, _ = jobs_core.upsert_job(
            workspace.conn,
            company=f"Co {index}",
            title="Embedded Intern",
            url=f"https://example.invalid/{index}",
            location="Waco, TX",
            tracks=["embedded"],
        )
        ids.append(job.id)
    applications_core.record_application(workspace.conn, ids[0], track="embedded")
    return ids


async def test_dashboard_is_the_landing_screen(workspace):
    _seeded(workspace)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE):
        assert app.screen.__class__.__name__ == "DashboardScreen"


async def test_jobs_screen_lists_jobs_and_switches_views(workspace):
    _seeded(workspace)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["jobs"])
        await pilot.pause()
        assert isinstance(app.screen, JobsScreen)
        table = app.screen.query_one("#jobs-table", DataTable)
        # View 1 is new jobs: the job already applied to has left it.
        assert table.row_count == 2

        await pilot.press("4")                 # "All jobs"
        await pilot.pause()
        assert app.screen.view_index == 3
        assert table.row_count == 3


async def test_filter_bar_narrows_and_clears(workspace):
    _seeded(workspace)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["jobs"])
        await pilot.press("slash")
        await pilot.pause()
        app.screen.query_one("#filter", Input).value = "Co 1"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.query_one("#jobs-table", DataTable).row_count == 1

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.filter_text == ""
        assert app.screen.query_one("#jobs-table", DataTable).row_count == 2


async def test_enter_opens_job_detail_and_escape_returns(workspace):
    _seeded(workspace)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["jobs"])
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, JobDetailScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, JobsScreen)


async def test_help_overlay_is_built_from_the_live_bindings(workspace):
    _seeded(workspace)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["jobs"])
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        descriptions = [description for _key, description, _where in app.screen.rows]
        assert "Undo status" in descriptions          # comes from JobsScreen.BINDINGS
        assert any("saved view" in text.lower() for text in descriptions)
        assert any("palette" in text.lower() for text in descriptions)


async def test_pipeline_columns_and_arrow_navigation(workspace):
    ids = _seeded(workspace)
    application = applications_core.get_application_for_job(workspace.conn, ids[0])
    applications_core.set_status(workspace.conn, application.id, "oa")

    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["pipeline"])
        await pilot.pause()
        assert isinstance(app.screen, PipelineScreen)
        assert app.screen.query_one("#cards-oa", DataTable).row_count == 1
        assert app.screen.column_index == 0

        await pilot.press("right")
        await pilot.pause()
        assert app.screen.column_index == 1      # the OA column
        assert app.screen.current_job_id() == ids[0]


async def test_add_job_modal_requires_company_and_title_then_writes(workspace):
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["jobs"])
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddJobModal)

        app.screen.query_one("#url", Input).value = "https://example.invalid/new?utm_source=x"
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, AddJobModal)       # nothing guessed, nothing saved
        assert jobs_core.count_jobs(workspace.conn) == 0

        app.screen.query_one("#company", Input).value = "Test Co"
        app.screen.query_one("#title", Input).value = "Firmware Intern"
        app.screen.query_one("#track", Input).value = "embedded"
        app.screen.query_one("#deadline", Input).value = "+14"
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.screen, JobDetailScreen)
        job = jobs_core.get_job(workspace.conn, app.screen.job_id)
        assert job.company_name == "Test Co"
        assert job.tracks == ["embedded"]
        assert job.url == "https://example.invalid/new"   # tracking parameter stripped
        assert job.deadline_at is not None


async def test_status_then_undo_through_the_ui(workspace):
    ids = _seeded(workspace, count=1)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        app.open_job(ids[0])
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        app.screen.query_one("#st-oa", RadioButton).value = True
        app.screen.query_one("#save", Button).press()
        await pilot.pause()

        application = applications_core.get_application_for_job(workspace.conn, ids[0])
        assert application.status == "oa"

        await pilot.press("u")
        await pilot.pause()
        assert applications_core.get_application(workspace.conn, application.id).status == "applied"
        # Undo appends, never deletes.
        assert len(applications_core.history(workspace.conn, application.id)) == 3


async def test_next_action_is_stored_from_the_prompt(workspace):
    ids = _seeded(workspace, count=1)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        app.open_job(ids[0])
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#value", Input).value = "+7 email the recruiter"
        await pilot.press("enter")
        await pilot.pause()

        application = applications_core.get_application_for_job(workspace.conn, ids[0])
        assert application.next_action_at is not None
        assert application.next_action_note == "email the recruiter"


def _inbox_jobs(workspace, count=3):
    """New, eligible jobs as discovery would store them."""
    ids = []
    for index in range(count):
        job, _ = jobs_core.upsert_job(
            workspace.conn, company=f"Inbox Co {index}", title="Software Engineer Intern",
            url=f"https://example.invalid/inbox/{index}", location="Austin, TX",
            source="greenhouse", tracks=["software"], eligibility="eligible",
            eligibility_reason="open to undergraduates — 'bachelor's'",
        )
        ids.append(job.id)
    return ids


async def test_apply_session_takes_a_tier_first_records_applied_and_drops_not_eligible(workspace):
    from applyai.core import reminders as reminders_core
    from applyai.core.jobs import HAND_NOT_ELIGIBLE

    track = workspace.config.track_names[0]
    first_b, a_job = _inbox_jobs(workspace, count=2)
    jobs_core.link_track(workspace.conn, first_b, track, "test")
    jobs_core.link_track(workspace.conn, a_job, track, "test")
    jobs_core.set_tier(workspace.conn, first_b, "B", reason="ok", tier_source="rules")
    jobs_core.set_tier(workspace.conn, a_job, "A", reason="great", tier_source="rules")
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["apply"])
        await pilot.pause()
        assert app.screen.current_job_id() == a_job                  # A before B

        await pilot.press("a")                                       # I submitted it myself
        await pilot.pause()
        application = applications_core.get_application_for_job(workspace.conn, a_job)
        assert application is not None
        followups = [r for r in reminders_core.upcoming_reminders(workspace.conn) if r.kind == "followup"]
        assert len(followups) == len(workspace.config.apply.followup_days)
        assert app.screen.current_job_id() == first_b               # moved on

        await pilot.press("x")
        await pilot.pause()
        assert jobs_core.get_job(workspace.conn, first_b).eligibility_reason == HAND_NOT_ELIGIBLE
        assert app.screen.current_job_id() is None                   # queue is empty



async def test_inbox_marks_rows_and_skips_them_all(workspace):
    from applyai.tui.screens.inbox import InboxScreen

    ids = _inbox_jobs(workspace)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["inbox"])
        await pilot.pause()
        assert isinstance(app.screen, InboxScreen)
        table = app.screen.query_one("#inbox-table", DataTable)
        assert table.row_count == 3

        await pilot.press("space", "space")          # marks two rows, moving down
        await pilot.pause()
        assert len(app.screen.marked) == 2
        await pilot.press("x")
        await pilot.pause()
        assert table.row_count == 1

        states = {i: jobs_core.get_job(workspace.conn, i).queue_state for i in ids}
        assert sorted(states.values()) == ["new", "skipped", "skipped"]
        # Skipping is a queue state only: no application rows appear.
        assert workspace.conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


async def test_inbox_sets_a_tier_on_the_cursor_row(workspace):
    ids = _inbox_jobs(workspace, count=1)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["inbox"], "t")
        await pilot.pause()
        await pilot.press("enter")                   # first option: A
        await pilot.pause()
        job = jobs_core.get_job(workspace.conn, ids[0])
        assert job.tier == "A" and job.tier_source == "manual"


async def test_inbox_hides_filtered_out_jobs(workspace):
    _inbox_jobs(workspace, count=1)
    jobs_core.upsert_job(workspace.conn, company="Far Co", title="Software Engineer Intern",
                         url="https://example.invalid/far", location="London, UK",
                         tracks=["software"], eligibility="ineligible",
                         eligibility_reason="outside the US — London, UK")
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["inbox"])
        await pilot.pause()
        assert app.screen.query_one("#inbox-table", DataTable).row_count == 1


async def test_star_key_favorites_the_cursor_row_and_v_shows_only_favorites(workspace):
    _inbox_jobs(workspace)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["inbox"])
        await pilot.pause()
        table = app.screen.query_one("#inbox-table", DataTable)
        starred = app.screen.current_job_id()
        await pilot.press("asterisk")
        await pilot.pause()
        assert jobs_core.get_job(workspace.conn, starred).is_favorite
        assert "★" in str(table.get_cell(str(starred), "fav"))

        await pilot.press("v")                       # favorites only
        await pilot.pause()
        assert table.row_count == 1 and app.screen.current_job_id() == starred

        await pilot.press("asterisk")                # unstar: it leaves the favorites view
        await pilot.pause()
        assert not jobs_core.get_job(workspace.conn, starred).is_favorite
        assert table.row_count == 0
        await pilot.press("v")
        await pilot.pause()
        assert table.row_count == 3


async def _pick(pilot, app, value: str) -> None:
    """Choose the PickModal option whose value is `value`."""
    from textual.widgets import OptionList

    options = app.screen.query_one(OptionList)
    options.highlighted = options.get_option_index(value)
    await pilot.press("enter")
    await pilot.pause()


async def test_c_shows_one_track_in_the_inbox_and_jobs_and_every_track_again(workspace):
    ids = _inbox_jobs(workspace)
    usual = set(jobs_core.get_job(workspace.conn, ids[1]).tracks)
    other = next(name for name in workspace.config.track_names if name not in usual)
    jobs_core.link_track(workspace.conn, ids[0], other, "test")
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["inbox"], "c")
        await pilot.pause()
        await _pick(pilot, app, other)
        table = app.screen.query_one("#inbox-table", DataTable)
        assert table.row_count == 1 and app.screen.current_job_id() == ids[0]
        await pilot.press("c")
        await pilot.pause()
        await _pick(pilot, app, "*every*")
        assert table.row_count == 3

        await pilot.press(KEY["jobs"], "4", "c")     # "All jobs", then one track
        await pilot.pause()
        await _pick(pilot, app, other)
        jobs_table = app.screen.query_one("#jobs-table", DataTable)
        assert [str(row.value) for row in jobs_table.rows] == [str(ids[0])]
        await pilot.press("c")
        await pilot.pause()
        await _pick(pilot, app, "*every*")
        assert jobs_table.row_count == 3


async def test_f_fills_every_waiting_favorite_but_marked_rows_still_win(workspace, monkeypatch):
    from applyai.core import favorites
    from applyai.core.apply import autofill, filler

    calls = []

    def fake_fill(settings, config, ids, **kwargs):
        calls.append((sorted(ids), kwargs.get("background", False)))
        return []

    monkeypatch.setattr(autofill, "fill_jobs", fake_fill)
    monkeypatch.setattr(filler, "fill_jobs", fake_fill)
    ids = _inbox_jobs(workspace)
    favorites.favorite(workspace.conn, ids[:2])
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["inbox"])
        await pilot.pause()
        table = app.screen.query_one("#inbox-table", DataTable)
        table.move_cursor(row=table.get_row_index(str(ids[2])))     # cursor on the unstarred job
        await pilot.press("f")
        await app.workers.wait_for_complete()
        assert calls == [(sorted(ids[:2]), True)]

        await pilot.press("space", "f")                              # mark the unstarred one: just it
        await app.workers.wait_for_complete()
        assert calls[-1] == ([ids[2]], False)


async def test_apply_f_fills_only_this_tracks_favorites_and_a_plain_fill_moves_on(workspace, monkeypatch):
    from applyai.core import favorites
    from applyai.core.apply import autofill, filler

    calls = []

    def fake_fill(settings, config, ids, **kwargs):
        calls.append(sorted(ids))
        return []

    monkeypatch.setattr(autofill, "fill_jobs", fake_fill)
    monkeypatch.setattr(filler, "fill_jobs", fake_fill)
    here, there = workspace.config.track_names[:2]
    ids = []
    for index, track in enumerate([here, here, there, here]):
        job, _ = jobs_core.upsert_job(
            workspace.conn, company=f"Apply Co {index}", title="Intern", url=f"https://example.invalid/a/{index}",
            location="Austin, TX", tracks=[track], eligibility="eligible")
        jobs_core.set_tier(workspace.conn, job.id, "A", reason="test", tier_source="manual")
        ids.append(job.id)
    favorites.favorite(workspace.conn, [ids[0], ids[2]])          # one favorite per track
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["apply"])
        await pilot.pause()
        screen = app.screen
        while screen.track != here:
            await pilot.press("t")
            await pilot.pause()
        screen.index = [job.id for job in screen.queue].index(ids[0])
        await pilot.press("f")                                     # on a starred job: this track's favorites
        await app.workers.wait_for_complete()
        assert calls == [[ids[0]]]

        screen.index = [job.id for job in screen.queue].index(ids[1])
        await pilot.press("f")                                     # an unstarred job: just it, then the next
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls[-1] == [ids[1]] and screen.current_job_id() != ids[1]


async def test_sources_screen_shows_health_and_refuses_unbuilt_sources(workspace):
    from applyai.core import sources as sources_core
    from applyai.tui.screens.sources import SourcesScreen

    sources_core.record_run(workspace.conn, "workday", status="rate_limited", error="429 from nvidia")
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["sources"])
        await pilot.pause()
        assert isinstance(app.screen, SourcesScreen)
        table = app.screen.query_one("#sources-table", DataTable)
        row = table.get_row("workday")
        assert "rate_limited" in str(row[6]) and "429" in str(row[8])

        # manual has no adapter (jobs are added by hand): enter explains instead of running.
        table.move_cursor(row=table.get_row_index("manual"))
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.running is None


async def test_sources_screen_runs_a_source_in_a_worker(workspace, monkeypatch):
    from applyai.core.discovery import pipeline

    calls = []

    def fake_run(settings, config, *, only=None, **kwargs):
        calls.append(only)
        return pipeline.DiscoverResult(1, "now", {only[0]: pipeline.SourceTally(new=4)})

    monkeypatch.setattr(pipeline, "run_discover", fake_run)
    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(KEY["sources"])
        await pilot.pause()
        table = app.screen.query_one("#sources-table", DataTable)
        table.move_cursor(row=table.get_row_index("greenhouse"))
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen.running is None
    assert calls == [["greenhouse"]]

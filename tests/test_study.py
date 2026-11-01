"""Deterministic consumer behavior for local timers, records and dated plans."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from kitty.core import study
from kitty.db import connect
from kitty import web_study
from kitty.web_server import APIError

UTC = timezone.utc
BASE = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def command(conn, action, *, now=BASE, request_id=None, prepared=True, **extra):
    # Existing focus/break invariants begin after a real, observed priming phase.
    # Priming-specific tests opt out so the actual start boundary stays visible.
    start_at = now - timedelta(seconds=study.PRIMING_SECONDS) if action == 'start' and prepared else now
    revision = study.timer_state(conn, now=start_at)['timer']['revision']
    result = study.timer_command(conn, {'action': action, 'revision': revision,
                                       'request_id': request_id or f'{action}-{now.isoformat()}', **extra}, now=start_at)
    return study.timer_state(conn, now=now) if action == 'start' and prepared else result


def short_focus(conn):
    revision = study.timer_state(conn, now=BASE)['settings']['revision']
    study.save_settings(conn, {'revision': revision, 'focus_seconds': 60,
                               'short_break_seconds': 60, 'timezone': 'America/New_York'})


def manual(conn, start, duration=600, **extra):
    return study.save_session(conn, {'request_id': f'manual-{start.isoformat()}', 'started_at': start.isoformat(),
                                    'duration_seconds': duration, 'timezone': 'America/New_York', **extra},
                              now=BASE + timedelta(days=2))['item']


def test_timer_reload_sleep_logs_once_and_starts_only_next_observed_phase(workspace):
    conn = workspace.conn
    short_focus(conn)
    started = command(conn, 'start', timezone='America/New_York')['timer']
    reloaded = study.timer_state(conn, now=BASE + timedelta(seconds=25))['timer']
    assert reloaded['phase_id'] == started['phase_id']
    assert reloaded['remaining_seconds'] == 35
    slept = study.workspace(conn, now=BASE + timedelta(hours=12))
    assert slept['timer']['status'] == 'running'
    assert slept['timer']['phase'] == 'short_break'
    assert slept['timer']['started_at'] == (BASE + timedelta(hours=12)).isoformat()
    assert slept['timer']['remaining_seconds'] == 60
    assert slept['timer']['completed_phase_id'] == started['phase_id']
    assert slept['timer']['completed_phase'] == 'focus'
    assert len(slept['sessions']) == 1
    assert slept['sessions'][0]['duration_seconds'] == 60
    assert slept['sessions'][0]['ended_at'] == (BASE + timedelta(seconds=60)).isoformat()
    again = study.workspace(conn, now=BASE + timedelta(days=1))
    assert len(again['sessions']) == 1
    assert again['timer']['phase'] == 'focus'
    assert again['timer']['started_at'] == (BASE + timedelta(days=1)).isoformat()
    assert again['timer']['remaining_seconds'] == 60
    unchanged = study.timer_state(conn, now=BASE + timedelta(days=1))['timer']
    assert unchanged['revision'] == again['timer']['revision']


def test_pause_resume_excludes_abandoned_pause_and_preserves_actual_day_totals(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    command(conn, 'pause', now=BASE + timedelta(seconds=20))
    paused = study.workspace(conn, now=BASE + timedelta(days=1))
    assert paused['timer']['remaining_seconds'] == 40
    assert paused['sessions'] == []
    resumed_at = BASE + timedelta(days=1)
    command(conn, 'resume', now=resumed_at)
    result = study.workspace(conn, now=resumed_at + timedelta(seconds=40))
    assert result['sessions'][0]['duration_seconds'] == 60
    days = {item['date']: item['seconds'] for item in result['stats']['days']}
    assert days == {'2026-10-14': 20, '2026-10-15': 40}
    assert result['stats']['daily_streak'] == 1
    # Notes-only edits must not turn the long pause into study time.
    session = result['sessions'][0]
    study.save_session(conn, {'id': session['id'], 'revision': session['revision'], 'request_id': 'reflection',
                             'notes': 'Recall was harder than expected'}, now=resumed_at + timedelta(minutes=2))
    assert study.statistics(conn, now=resumed_at + timedelta(minutes=2))['days'] == result['stats']['days']


def test_break_pause_skip_and_stop_never_count_as_study(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    study.timer_state(conn, now=BASE + timedelta(minutes=1))
    command(conn, 'pause', now=BASE + timedelta(minutes=1, seconds=15))
    assert study.workspace(conn, now=BASE + timedelta(hours=8))['stats']['total_seconds'] == 60
    command(conn, 'skip_break', now=BASE + timedelta(hours=8))
    skipped = study.timer_state(conn, now=BASE + timedelta(hours=8))['timer']
    assert skipped['phase'] == 'focus' and skipped['status'] == 'paused'
    command(conn, 'resume', now=BASE + timedelta(hours=8, seconds=1))
    command(conn, 'stop', now=BASE + timedelta(hours=8, seconds=10))
    result = study.workspace(conn, now=BASE + timedelta(days=1))
    assert result['stats']['total_seconds'] == 60
    assert result['timer']['status'] == 'idle'


def test_default_cycle_primes_then_repeats_focus_25_break_5_without_counting_preparation(workspace):
    conn = workspace.conn
    initial = study.timer_state(conn, now=BASE)
    assert initial['settings']['sound'] is True
    assert initial['settings']['focus_seconds'] == 1500
    assert initial['settings']['short_break_seconds'] == 300
    started = command(conn, 'start', prepared=False)['timer']
    assert started['phase'] == 'priming' and started['duration_seconds'] == 300
    primed = study.workspace(conn, now=BASE + timedelta(minutes=5))
    assert primed['timer']['phase'] == 'focus'
    assert primed['timer']['completed_phase'] == 'priming'
    assert primed['stats']['total_seconds'] == 0
    assert primed['timer']['session_id'] == started['session_id']
    for cycle in range(1, 6):
        finished = BASE + timedelta(minutes=5 + 30 * (cycle - 1) + 25)
        result = study.workspace(conn, now=finished)
        assert result['timer']['phase'] == 'short_break'
        assert result['timer']['status'] == 'running'
        assert result['timer']['duration_seconds'] == 300
        assert result['timer']['cycle'] == cycle
        assert result['stats']['total_seconds'] == cycle * 1500
        next_focus = study.workspace(conn, now=finished + timedelta(minutes=5))
        assert next_focus['timer']['phase'] == 'focus'
        assert next_focus['timer']['status'] == 'running'
        assert next_focus['timer']['duration_seconds'] == 1500
        assert next_focus['stats']['total_seconds'] == cycle * 1500
    command(conn, 'stop', now=BASE + timedelta(minutes=155, seconds=10))
    assert study.workspace(conn, now=BASE + timedelta(days=1))['stats']['total_seconds'] == 7500


def test_timer_command_replay_and_stale_cross_tab_are_safe(workspace):
    conn = workspace.conn
    short_focus(conn)
    revision = study.timer_state(conn, now=BASE)['timer']['revision']
    body = {'action': 'start', 'revision': revision, 'request_id': 'stable-start'}
    first = study.timer_command(conn, body, now=BASE - timedelta(minutes=5))
    assert study.timer_command(conn, body, now=BASE - timedelta(minutes=4, seconds=50)) == first
    with pytest.raises(study.StudyConflict):
        study.timer_command(conn, {'action': 'stop', 'revision': revision, 'request_id': 'other-window'}, now=BASE)
    with pytest.raises(study.StudyConflict):
        study.timer_command(conn, {**body, 'action': 'stop'}, now=BASE)
    study.timer_state(conn, now=BASE)
    completed = study.workspace(conn, now=BASE + timedelta(minutes=1))
    assert completed['stats']['session_count'] == 1
    study.timer_command(conn, body, now=BASE + timedelta(minutes=2))
    assert study.workspace(conn, now=BASE + timedelta(minutes=3))['stats']['session_count'] == 1


def test_manual_replay_edit_delete_adjusts_goals_and_streaks(workspace):
    conn = workspace.conn
    subject = study.save_entity(conn, 'subjects', {'name': 'Control theory'})['item']
    goal = study.save_entity(conn, 'targets', {'title': 'Recall practice', 'subject_id': subject['id'],
                                            'period': 'daily', 'minutes': 30})['item']
    body = {'request_id': 'manual-once', 'started_at': (BASE - timedelta(hours=1)).isoformat(),
            'duration_seconds': 1200, 'timezone': 'UTC', 'subject_id': subject['id']}
    first = study.save_session(conn, body, now=BASE)['item']
    assert study.save_session(conn, body, now=BASE)['item']['id'] == first['id']
    stats = study.statistics(conn, timezone_name='UTC', now=BASE)
    assert stats['daily_streak'] == 1 and stats['weekly_streak'] == 1
    assert stats['goals'] == [{'id': goal['id'], 'seconds': 1200, 'goal_seconds': 1800}]
    updated = study.save_session(conn, {'id': first['id'], 'revision': first['revision'], 'request_id': 'edit-log',
                                        'duration_seconds': 1800}, now=BASE)['item']
    assert study.statistics(conn, now=BASE)['total_seconds'] == 1800
    with pytest.raises(study.StudyConflict):
        study.save_session(conn, {'id': first['id'], 'revision': first['revision'], 'request_id': 'stale-edit', 'notes': 'stale'}, now=BASE)
    study.save_session(conn, {'action': 'delete', 'id': updated['id'], 'revision': updated['revision'], 'request_id': 'delete-log'}, now=BASE)
    stats = study.statistics(conn, now=BASE)
    assert stats['total_seconds'] == 0 and stats['daily_streak'] == 0 and stats['weekly_streak'] == 0


def test_midnight_timezone_split_and_completion_day_streak(workspace):
    conn = workspace.conn
    # 23:50 New York, ending at 00:10; equivalent UTC date is October 14.
    start = datetime(2026, 10, 14, 3, 50, tzinfo=UTC)
    manual(conn, start, 1200)
    local = study.statistics(conn, timezone_name='America/New_York', now=BASE)
    assert local['days'] == [{'date': '2026-10-13', 'seconds': 600, 'subjects': [{'id': None, 'seconds': 600}]},
                             {'date': '2026-10-14', 'seconds': 600, 'subjects': [{'id': None, 'seconds': 600}]}]
    assert local['daily_streak'] == 1
    utc = study.statistics(conn, timezone_name='UTC', now=BASE)
    assert utc['days'] == [{'date': '2026-10-14', 'seconds': 1200, 'subjects': [{'id': None, 'seconds': 1200}]}]
    assert utc['daily_streak'] == 1
    assert study.statistics(conn, timezone_name='Pacific/Honolulu', now=BASE)['today'] == '2026-10-13'


def test_days_split_study_time_by_subject_largest_first(workspace):
    conn = workspace.conn
    chem = study.save_entity(conn, 'subjects', {'name': 'Chemistry', 'color': '#e06c75'})['item']
    math = study.save_entity(conn, 'subjects', {'name': 'Maths', 'color': '#61afef'})['item']
    day_start = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    manual(conn, day_start, 600, subject_id=chem['id'])
    manual(conn, day_start + timedelta(hours=1), 1500, subject_id=math['id'])
    manual(conn, day_start + timedelta(hours=2), 300)
    stats = study.statistics(conn, timezone_name='America/New_York', now=BASE)
    expected = [{'id': math['id'], 'seconds': 1500}, {'id': chem['id'], 'seconds': 600}, {'id': None, 'seconds': 300}]
    assert stats['days'] == [{'date': '2026-10-13', 'seconds': 2400, 'subjects': expected}]
    tuesday = next(item for item in stats['history']['days'] if item['date'] == '2026-10-13')
    assert tuesday['subjects'] == expected
    assert next(item for item in stats['history']['days'] if item['date'] == '2026-10-12')['subjects'] == []


def test_spring_dst_actual_duration_and_login_never_create_streak(workspace):
    conn = workspace.conn
    start = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    manual(conn, start, 7200)
    stats = study.statistics(conn, timezone_name='America/New_York', now=start + timedelta(hours=4))
    assert stats['days'] == [{'date': '2026-03-08', 'seconds': 7200, 'subjects': [{'id': None, 'seconds': 7200}]}]
    assert stats['daily_streak'] == 1
    empty_day = study.workspace(conn, now=BASE)
    assert empty_day['stats']['daily_streak'] == 0
    assert empty_day['stats']['weekly_streak'] == 0


def test_manual_requires_explicit_offset_timezone_and_completed_duration(workspace):
    conn = workspace.conn
    for changes in ({'started_at': '2026-10-14T08:00:00'}, {'timezone': 'Not/AZone'},
                    {'duration_seconds': 0}, {'duration_seconds': True}, {'started_at': (BASE + timedelta(days=1)).isoformat()}):
        with pytest.raises(ValueError):
            study.save_session(conn, {'request_id': f'invalid-{changes}', 'started_at': (BASE - timedelta(hours=1)).isoformat(),
                                      'duration_seconds': 600, 'timezone': 'UTC', **changes}, now=BASE)
    assert study.workspace(conn, now=BASE)['sessions'] == []


def test_task_checklist_revision_and_subject_delete_preserve_session(workspace):
    conn = workspace.conn
    subject = study.save_entity(conn, 'subjects', {'name': 'Physics'})['item']
    task = study.save_entity(conn, 'tasks', {'title': 'Practice problems', 'subject_id': subject['id'], 'priority': 'high',
                                           'due_date': '2026-10-16', 'checklist': [{'id': 'one', 'text': 'Recall formulas', 'done': False}]})['item']
    updated = study.save_entity(conn, 'tasks', {'id': task['id'], 'revision': task['revision'],
                                               'checklist': [{'id': 'one', 'text': 'Recall formulas', 'done': True}]})['item']
    assert updated['checklist'][0]['done'] is True
    manual(conn, BASE - timedelta(hours=1), subject_id=subject['id'])
    study.save_entity(conn, 'subjects', {'action': 'delete', 'id': subject['id'], 'revision': subject['revision']})
    state = study.workspace(conn, now=BASE)
    assert state['sessions'][0]['subject_id'] is None
    assert state['tasks'][0]['subject_id'] is None
    assert state['stats']['total_seconds'] == 600


def test_exam_plan_has_actual_pre_exam_dates_and_requires_explicit_replacement(workspace):
    conn = workspace.conn
    exam = study.save_entity(conn, 'exams', {'title': 'Signals exam', 'exam_date': '2026-10-20', 'timezone': 'UTC'})['item']
    body = {'exam_id': exam['id'], 'exam_revision': exam['revision'], 'start_date': '2026-10-14',
            'minutes': 180, 'block_minutes': 60, 'local_time': '17:00'}
    plan = study.generate_plan(conn, body, now=BASE)['items']
    assert [item['start'][:10] for item in plan] == ['2026-10-14', '2026-10-16', '2026-10-18']
    assert all(study.timestamp(item['end']) < study.timestamp('2026-10-20T00:00:00+00:00') for item in plan)
    with pytest.raises(study.StudyConflict):
        study.generate_plan(conn, body, now=BASE)
    replacement = study.generate_plan(conn, {**body, 'replace': True, 'minutes': 120}, now=BASE)['items']
    assert len(replacement) == 2
    assert {item['id'] for item in plan}.isdisjoint({item['id'] for item in replacement})
    assert len(study.workspace(conn, now=BASE)['plans']) == 2


def test_deleted_completed_focus_is_not_recreated_by_timer_polling(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    session = study.workspace(conn, now=BASE + timedelta(minutes=1))['sessions'][0]
    study.save_session(conn, {'action': 'delete', 'id': session['id'], 'revision': session['revision'], 'request_id': 'erase-completion'},
                       now=BASE + timedelta(minutes=2))
    assert study.workspace(conn, now=BASE + timedelta(days=1))['stats']['session_count'] == 0


def test_independent_connections_reconcile_one_completion_and_reject_stale_edit(workspace):
    conn = workspace.conn
    short_focus(conn)
    started = command(conn, 'start')['timer']

    def observe():
        other = connect(workspace.settings.db_path)
        try:
            return study.timer_state(other, now=BASE + timedelta(minutes=1))['timer']
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        observed = list(pool.map(lambda _: observe(), range(2)))
    assert observed[0]['revision'] == observed[1]['revision']
    assert all(item['status'] == 'running' and item['phase'] == 'short_break' for item in observed)
    assert observed[0]['phase_id'] == observed[1]['phase_id']
    assert observed[0]['completed_phase_id'] == started['phase_id']
    assert study.workspace(conn, now=BASE + timedelta(minutes=2))['stats']['session_count'] == 1
    with pytest.raises(study.StudyConflict):
        study.timer_command(conn, {'action': 'stop', 'revision': started['revision'], 'request_id': 'late-tab-stop'},
                            now=BASE + timedelta(minutes=2))


def test_focus_notes_are_saved_with_pause_without_paused_time(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    command(conn, 'pause', now=BASE + timedelta(seconds=20), notes='Retrieved two definitions', distractions='Phone')
    paused = study.timer_state(conn, now=BASE + timedelta(hours=1))['timer']
    assert paused['notes'] == 'Retrieved two definitions'
    command(conn, 'resume', now=BASE + timedelta(hours=1))
    completed = study.workspace(conn, now=BASE + timedelta(hours=1, seconds=40))['sessions'][0]
    assert completed['notes'] == 'Retrieved two definitions'
    assert completed['distractions'] == 'Phone'
    assert completed['duration_seconds'] == 60


def test_streaks_skip_empty_days_and_weeks_instead_of_counting_login(workspace):
    conn = workspace.conn
    for days_ago in (8, 7, 2, 1):
        manual(conn, BASE - timedelta(days=days_ago))
    stats = study.statistics(conn, timezone_name='UTC', now=BASE)
    assert stats['daily_streak'] == 2
    assert stats['weekly_streak'] == 2
    later = study.workspace(conn, now=BASE + timedelta(days=14))['stats']
    assert later['daily_streak'] == 0 and later['weekly_streak'] == 0


def test_web_extension_allows_local_demo_actions_and_exposes_conflicts(workspace):
    conn = workspace.conn
    app = SimpleNamespace(settings=SimpleNamespace(demo=True), _no_query=lambda query: None)
    created = web_study.post(app, conn, '/api/study/subjects', {'name': 'Synthetic demo subject'})['item']
    assert web_study.get(app, conn, '/api/study', {})['subjects'][0]['id'] == created['id']
    web_study.post(app, conn, '/api/study/subjects', {'id': created['id'], 'revision': created['revision'], 'name': 'Revised'})
    with pytest.raises(APIError) as conflict:
        web_study.post(app, conn, '/api/study/subjects', {'id': created['id'], 'revision': created['revision'], 'name': 'Stale'})
    assert conflict.value.status == 409
    with pytest.raises(APIError) as invalid:
        web_study.get(app, conn, '/api/study', {'unknown': '1'})
    assert invalid.value.status == 400


def test_settings_round_trip_keeps_active_phase_duration_and_uses_new_next_phase(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    preferences = study.workspace(conn, now=BASE)['settings']
    study.save_settings(conn, {**preferences, 'focus_seconds': 120, 'short_break_seconds': 90, 'sound': True})
    active = study.timer_state(conn, now=BASE + timedelta(seconds=30))
    assert active['timer']['duration_seconds'] == 60
    assert active['timer']['remaining_seconds'] == 30
    assert active['settings']['sound'] is True
    completed = study.timer_state(conn, now=BASE + timedelta(seconds=60))['timer']
    assert completed['phase'] == 'short_break'
    assert completed['duration_seconds'] == 90


def test_pause_resume_break_and_skip_running_break_keep_cycle_continuous(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    focus_done = study.timer_state(conn, now=BASE + timedelta(minutes=1))['timer']
    command(conn, 'pause', now=BASE + timedelta(minutes=1, seconds=20))
    paused = study.timer_state(conn, now=BASE + timedelta(days=1))['timer']
    assert paused['remaining_seconds'] == 40
    assert paused['completed_phase_id'] == focus_done['completed_phase_id']
    command(conn, 'resume', now=BASE + timedelta(days=1))
    next_focus = study.timer_state(conn, now=BASE + timedelta(days=1, seconds=40))['timer']
    assert next_focus['phase'] == 'focus' and next_focus['status'] == 'running'
    assert next_focus['completed_phase'] == 'short_break'
    study.timer_state(conn, now=BASE + timedelta(days=1, seconds=100))
    skipped = command(conn, 'skip_break', now=BASE + timedelta(days=1, seconds=110))['timer']
    assert skipped['phase'] == 'focus' and skipped['status'] == 'running'
    assert skipped['remaining_seconds'] == 60
    assert study.statistics(conn, now=BASE + timedelta(days=1, seconds=110))['total_seconds'] == 120


def test_month_history_splits_focus_and_manual_entries_at_local_month_boundaries(workspace):
    conn = workspace.conn
    short_focus(conn)
    subject = study.save_entity(conn, 'subjects', {'name': 'Synthetic concepts'})['item']
    # 23:59:30 New York on September 30, crossing into October in one focus.
    start = datetime(2026, 10, 1, 3, 59, 30, tzinfo=UTC)
    command(conn, 'start', now=start, subject_id=subject['id'], timezone='America/New_York')
    command(conn, 'pause', now=start + timedelta(seconds=20))
    resumed = start + timedelta(seconds=40)
    command(conn, 'resume', now=resumed)
    study.timer_state(conn, now=resumed + timedelta(seconds=40))
    # Explicit manual study straddles October/November: 10 minutes in each.
    observed = datetime(2026, 11, 2, 12, tzinfo=UTC)
    study.save_session(conn, {'request_id': 'month-crossing-manual',
                             'started_at': '2026-11-01T03:50:00+00:00', 'duration_seconds': 1200,
                             'timezone': 'America/New_York', 'subject_id': subject['id']}, now=observed)
    expected = [('2026-09-15', 20, [20]), ('2026-10-15', 640, [600, 40]),
                ('2026-11-15', 600, [600])]
    for anchor, seconds, contributions in expected:
        result = study.workspace(conn, now=observed, timezone_name='America/New_York',
                                 history_period='month', history_date=anchor)
        history = result['stats']['history']
        assert history['seconds'] == seconds
        assert sum(item['seconds'] for item in history['days']) == seconds
        assert history['subjects'][0]['seconds'] == seconds
        assert [item['period_seconds'] for item in result['sessions']] == contributions
    october = study.statistics(conn, now=observed, timezone_name='America/New_York',
                               history_period='month', history_date='2026-10-31')['history']
    assert october['start'] == '2026-10-01' and october['end'] == '2026-11-01'
    assert october['previous'] == '2026-09-01' and october['next'] == '2026-11-01'
    week = study.workspace(conn, now=observed, timezone_name='America/New_York',
                           history_period='week', history_date='2026-10-01')
    assert week['stats']['history']['seconds'] == 60
    assert [item['source'] for item in week['sessions']] == ['focus']
    assert week['stats']['total_seconds'] == 1260
    assert week['stats']['month_seconds'] == 600


def test_month_history_handles_leap_year_and_year_navigation(workspace):
    conn = workspace.conn
    leap = study.statistics(conn, now=BASE, history_period='month', history_date='2024-02-29')['history']
    assert len(leap['days']) == 29
    january = study.statistics(conn, now=BASE, history_period='month', history_date='2026-01-15')['history']
    assert january['previous'] == '2025-12-01'
    assert january['next'] == '2026-02-01'
    december = study.statistics(conn, now=BASE, history_period='month', history_date='2026-12-15')['history']
    assert december['next'] == '2027-01-01'


def test_history_period_api_rejects_unknown_period_and_invalid_date(workspace):
    conn = workspace.conn
    app = SimpleNamespace(_no_query=lambda query: None)
    for query in ({'period': 'year'}, {'period': 'month', 'date': '2026-02-30'}):
        with pytest.raises(APIError) as invalid:
            web_study.get(app, conn, '/api/study', query)
        assert invalid.value.status == 400
    result = web_study.get(app, conn, '/api/study', {'period': 'month', 'date': '2026-02-20', 'timezone': 'UTC'})
    assert result['stats']['history']['start'] == '2026-02-01'
    assert result['stats']['history']['end'] == '2026-03-01'
    assert len(result['stats']['history']['days']) == 28


def test_priming_pause_reload_and_skip_guard_never_create_focus_time(workspace):
    conn = workspace.conn
    started = command(conn, 'start', prepared=False)['timer']
    command(conn, 'pause', now=BASE + timedelta(seconds=120))
    other = connect(workspace.settings.db_path)
    try:
        paused = study.timer_state(other, now=BASE + timedelta(hours=1))['timer']
        assert paused['phase'] == 'priming' and paused['remaining_seconds'] == 180
        assert paused['session_id'] == started['session_id']
        with pytest.raises(study.StudyConflict):
            command(other, 'skip_break', now=BASE + timedelta(hours=1))
        command(other, 'resume', now=BASE + timedelta(hours=1))
        focused = study.workspace(other, now=BASE + timedelta(hours=1, seconds=180))
        assert focused['timer']['phase'] == 'focus'
        assert focused['timer']['remaining_seconds'] == 1500
        assert focused['stats']['total_seconds'] == 0 and focused['sessions'] == []
        finished = study.workspace(other, now=BASE + timedelta(hours=1, seconds=1680))
        assert finished['timer']['phase'] == 'short_break'
        assert finished['timer']['remaining_seconds'] == 300
        assert finished['stats']['total_seconds'] == 1500
    finally:
        other.close()


def test_guided_checklist_reflection_and_return_survive_stop_and_new_session(workspace):
    conn = workspace.conn
    state = {'checked': [True] + [False] * 13, 'responses': ['Explain the relationship'] + [''] * 13,
             'reflection': '', 'next_review': ''}
    first = command(conn, 'start', prepared=False, guide=state)['timer']
    guide = study.workspace(conn, now=BASE)['guide']
    state.update(reflection='I connected two ideas; next time test the counterexample.', next_review='2026-10-16')
    saved = study.save_guide(conn, {'session_id': first['session_id'], 'revision': guide['revision'], 'state': state})['guide']
    with pytest.raises(study.StudyConflict):
        study.save_guide(conn, {'session_id': first['session_id'], 'revision': guide['revision'], 'state': state})
    command(conn, 'stop', now=BASE + timedelta(minutes=1))
    stopped = study.workspace(conn, now=BASE + timedelta(minutes=2))
    assert stopped['guide']['state'] == state
    assert stopped['guide']['ended_at'] is not None
    assert stopped['stats']['total_seconds'] == 0
    second = command(conn, 'start', now=BASE + timedelta(minutes=2), prepared=False)['timer']
    assert second['session_id'] != first['session_id']
    history = study.workspace(conn, now=BASE + timedelta(minutes=2))['guides']
    assert next(item for item in history if item['id'] == saved['id'])['state']['reflection'] == state['reflection']
    assert next(item for item in history if item['id'] == second['session_id'])['state']['checked'] == [False] * 14
    with pytest.raises(ValueError):
        study.save_guide(conn, {'session_id': second['session_id'], 'revision': 1,
                                'state': {**state, 'checked': [True]}})

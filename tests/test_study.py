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


def command(conn, action, *, now=BASE, request_id=None, **extra):
    revision = study.timer_state(conn, now=now)['timer']['revision']
    return study.timer_command(conn, {'action': action, 'revision': revision,
                                     'request_id': request_id or f'{action}-{now.isoformat()}', **extra}, now=now)


def short_focus(conn):
    study.save_settings(conn, {'revision': 1, 'focus_seconds': 60, 'short_break_seconds': 60,
                               'long_break_seconds': 120, 'cycle_count': 2, 'timezone': 'America/New_York'})


def manual(conn, start, duration=600, **extra):
    return study.save_session(conn, {'request_id': f'manual-{start.isoformat()}', 'started_at': start.isoformat(),
                                    'duration_seconds': duration, 'timezone': 'America/New_York', **extra},
                              now=BASE + timedelta(days=2))['item']


def test_timer_reload_sleep_logs_one_completed_focus_and_does_not_auto_chain(workspace):
    conn = workspace.conn
    short_focus(conn)
    started = command(conn, 'start', timezone='America/New_York')['timer']
    reloaded = study.timer_state(conn, now=BASE + timedelta(seconds=25))['timer']
    assert reloaded['phase_id'] == started['phase_id']
    assert reloaded['remaining_seconds'] == 35
    slept = study.workspace(conn, now=BASE + timedelta(hours=12))
    assert slept['timer']['status'] == 'awaiting_next'
    assert slept['timer']['phase'] == 'short_break'
    assert len(slept['sessions']) == 1
    assert slept['sessions'][0]['duration_seconds'] == 60
    assert slept['sessions'][0]['ended_at'] == (BASE + timedelta(seconds=60)).isoformat()
    again = study.workspace(conn, now=BASE + timedelta(days=1))
    assert len(again['sessions']) == 1
    assert again['timer']['revision'] == slept['timer']['revision']


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


def test_break_choice_pause_skip_and_stop_never_count_as_study(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    study.timer_state(conn, now=BASE + timedelta(minutes=1))
    command(conn, 'start', now=BASE + timedelta(minutes=2))
    command(conn, 'pause', now=BASE + timedelta(minutes=2, seconds=15))
    assert study.workspace(conn, now=BASE + timedelta(hours=8))['stats']['total_seconds'] == 60
    command(conn, 'skip_break', now=BASE + timedelta(hours=8))
    command(conn, 'start', now=BASE + timedelta(hours=8, seconds=1))
    command(conn, 'stop', now=BASE + timedelta(hours=8, seconds=10))
    result = study.workspace(conn, now=BASE + timedelta(days=1))
    assert result['stats']['total_seconds'] == 60
    assert result['timer']['status'] == 'idle'


def test_completed_break_waits_and_cycle_uses_long_break(workspace):
    conn = workspace.conn
    short_focus(conn)
    command(conn, 'start')
    command(conn, 'start', now=BASE + timedelta(seconds=61))
    after_break = study.timer_state(conn, now=BASE + timedelta(seconds=121))['timer']
    assert after_break['status'] == 'awaiting_next' and after_break['phase'] == 'focus'
    command(conn, 'start', now=BASE + timedelta(seconds=122))
    result = study.workspace(conn, now=BASE + timedelta(seconds=182))
    assert result['timer']['phase'] == 'long_break'
    assert result['timer']['duration_seconds'] == 120
    assert result['stats']['total_seconds'] == 120


def test_timer_command_replay_and_stale_cross_tab_are_safe(workspace):
    conn = workspace.conn
    short_focus(conn)
    revision = study.timer_state(conn, now=BASE)['timer']['revision']
    body = {'action': 'start', 'revision': revision, 'request_id': 'stable-start'}
    first = study.timer_command(conn, body, now=BASE)
    assert study.timer_command(conn, body, now=BASE + timedelta(seconds=10)) == first
    with pytest.raises(study.StudyConflict):
        study.timer_command(conn, {'action': 'stop', 'revision': revision, 'request_id': 'other-window'}, now=BASE)
    with pytest.raises(study.StudyConflict):
        study.timer_command(conn, {**body, 'action': 'stop'}, now=BASE)
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
    assert local['days'] == [{'date': '2026-10-13', 'seconds': 600}, {'date': '2026-10-14', 'seconds': 600}]
    assert local['daily_streak'] == 1
    utc = study.statistics(conn, timezone_name='UTC', now=BASE)
    assert utc['days'] == [{'date': '2026-10-14', 'seconds': 1200}]
    assert utc['daily_streak'] == 1
    assert study.statistics(conn, timezone_name='Pacific/Honolulu', now=BASE)['today'] == '2026-10-13'


def test_spring_dst_actual_duration_and_login_never_create_streak(workspace):
    conn = workspace.conn
    start = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)
    manual(conn, start, 7200)
    stats = study.statistics(conn, timezone_name='America/New_York', now=start + timedelta(hours=4))
    assert stats['days'] == [{'date': '2026-03-08', 'seconds': 7200}]
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
    assert all(item['status'] == 'awaiting_next' for item in observed)
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

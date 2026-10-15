"""Consumer behavior against isolated SQLite; no real account or submission dates."""
from datetime import datetime, timedelta, timezone

import pytest

from kitty.core import calendar, jobs
from kitty.web_server import APIError


def event(conn, **changes):
    return calendar.create_event(conn, {'title': 'Study block', 'start': '2026-10-26T09:00:00-05:00',
                                        'end': '2026-10-26T10:00:00-05:00', 'timezone': 'America/Chicago',
                                        'all_day': False, **changes})


def occurrences(conn, start='2026-10-01T00:00:00Z', end='2026-12-01T00:00:00Z'):
    return calendar.occurrences(conn, start, end)


def test_multiple_calendars_visibility_search_and_revision_conflict(workspace):
    conn = workspace.conn
    cal = calendar.save_calendar(conn, {'name': 'School', 'color': '#123abc', 'timezone': 'America/Chicago'})
    item = event(conn, calendar_id=cal['id'], description='Read chapter about graph theory')
    assert len(calendar.occurrences(conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z', search='graph theory')) == 1
    hidden = calendar.save_calendar(conn, {'id': cal['id'], 'revision': cal['revision'], 'visible': False})
    assert occurrences(conn) == []
    assert len(calendar.occurrences(conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z', include_hidden=True)) == 1
    with pytest.raises(APIError) as error:
        calendar.save_calendar(conn, {'id': cal['id'], 'revision': cal['revision'], 'name': 'stale'})
    assert error.value.status == 409
    with pytest.raises(APIError):
        calendar.delete_calendar(conn, {'id': cal['id'], 'revision': hidden['revision']})
    assert calendar.get_event(conn, item['id'])['title'] == 'Study block'


def test_rrule_preserves_wall_clock_across_dst_and_exclusions(workspace):
    conn = workspace.conn
    item = event(conn, rrule='FREQ=WEEKLY;COUNT=4', exdates=['2026-11-09T09:00:00-06:00'])
    expanded = occurrences(conn)
    assert [v['start'] for v in expanded] == ['2026-10-26T09:00:00-05:00', '2026-11-02T09:00:00-06:00', '2026-11-16T09:00:00-06:00']
    assert all(v['id'] == item['id'] for v in expanded)
    assert all(v['end'][11:16] == '10:00' for v in expanded)


def test_dst_gap_is_rejected_and_count_does_not_consume_nonexistent_time(workspace):
    conn = workspace.conn
    with pytest.raises(APIError, match='does not exist'):
        calendar.wall_instant('2027-03-14T02:30', 'America/Chicago')
    assert calendar.wall_instant('2026-11-01T01:30', 'America/Chicago', 0).endswith('-05:00')
    assert calendar.wall_instant('2026-11-01T01:30', 'America/Chicago', 1).endswith('-06:00')
    event(conn, start='2027-03-13T02:30:00-06:00', end='2027-03-13T03:00:00-06:00', rrule='FREQ=DAILY;COUNT=3')
    result = occurrences(conn, '2027-03-12T00:00:00Z', '2027-03-18T00:00:00Z')
    assert [v['start'][:10] for v in result] == ['2027-03-13', '2027-03-15', '2027-03-16']


def test_future_split_keeps_count_boundary_exceptions_and_undo(workspace):
    conn = workspace.conn
    original = event(conn, rrule='FREQ=WEEKLY;COUNT=5')
    edited = calendar.edit_event(conn, original['id'], {'revision': 1, 'scope': 'this', 'occurrence': '2026-11-16T09:00:00-06:00', 'changes': {'title': 'Special session'}})
    split = calendar.edit_event(conn, original['id'], {'revision': edited['revision'], 'scope': 'future',
                                                      'occurrence': '2026-11-09T09:00:00-06:00', 'changes': {'description': 'New plan'}})
    values = occurrences(conn)
    assert len(values) == 5
    assert len([v for v in values if v['id'] == original['id']]) == 2
    assert split['future_event']['rrule'] == 'FREQ=WEEKLY;COUNT=3'
    assert next(v for v in values if v['occurrence'].startswith('2026-11-16'))['title'] == 'Special session'
    calendar.undo(conn, split['undo_id'])
    restored = occurrences(conn)
    assert len(restored) == 5 and all(v['id'] == original['id'] for v in restored)
    assert next(v for v in restored if v['occurrence'].startswith('2026-11-16'))['title'] == 'Special session'


def test_this_occurrence_move_delete_and_all_shift_preserves_override(workspace):
    conn = workspace.conn
    original = event(conn, rrule='FREQ=WEEKLY;COUNT=3')
    moved = calendar.edit_event(conn, original['id'], {'revision': 1, 'scope': 'this', 'occurrence': '2026-11-02T09:00:00-06:00',
                                                      'changes': {'start': '2026-11-03T11:00:00-06:00', 'end': '2026-11-03T12:00:00-06:00', 'title': 'Moved session'}})
    assert [v['start'][:10] for v in occurrences(conn)] == ['2026-10-26', '2026-11-03', '2026-11-09']
    shifted = calendar.edit_event(conn, original['id'], {'revision': moved['revision'], 'scope': 'all',
                                                        'changes': {'start': '2026-10-26T10:00:00-05:00', 'end': '2026-10-26T11:00:00-05:00'}})
    exception = next(v for v in occurrences(conn) if v['title'] == 'Moved session')
    assert exception['start'] == '2026-11-03T12:00:00-06:00'
    deleted = calendar.edit_event(conn, original['id'], {'revision': shifted['revision'], 'scope': 'this', 'occurrence': exception['occurrence'], 'delete': True})
    assert len(occurrences(conn)) == 2
    calendar.undo(conn, deleted['undo_id'])
    assert len(occurrences(conn)) == 3


def test_all_day_multiday_until_date_and_half_open_range(workspace):
    conn = workspace.conn
    event(conn, start='2026-10-20', end='2026-10-23', all_day=True, rrule='FREQ=WEEKLY;UNTIL=20261103')
    assert len(occurrences(conn, '2026-10-01T00:00:00Z', '2026-11-10T00:00:00Z')) == 3
    assert occurrences(conn, '2026-10-23T05:00:00Z', '2026-10-24T05:00:00Z') == []
    assert len(occurrences(conn, '2026-10-22T05:00:00Z', '2026-10-23T05:00:00Z')) == 1


@pytest.mark.parametrize('changes', [
    {'start': '2026-10-26T09:00:00'}, {'timezone': 'Not/AZone'}, {'end': '2026-10-26T08:00:00-05:00'},
    {'all_day': True}, {'rrule': 'FREQ=SECONDLY'}, {'rrule': 'FREQ=DAILY;COUNT=3;UNTIL=20261030T000000Z'},
    {'url': 'javascript:alert(1)'}, {'reminders': [-1]}, {'attendees': [{'email': 'invalid'}]},
])
def test_event_validation_errors_do_not_mutate(workspace, changes):
    with pytest.raises(APIError):
        event(workspace.conn, **changes)
    assert workspace.conn.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0


def test_optimistic_edits_and_undo_never_overwrite_later_change(workspace):
    conn = workspace.conn
    original = event(conn)
    first = calendar.edit_event(conn, original['id'], {'revision': 1, 'changes': {'title': 'First'}})
    with pytest.raises(APIError) as error:
        calendar.edit_event(conn, original['id'], {'revision': 1, 'changes': {'title': 'Stale'}})
    assert error.value.status == 409
    calendar.edit_event(conn, original['id'], {'revision': first['revision'], 'changes': {'title': 'Later'}})
    with pytest.raises(APIError) as error:
        calendar.undo(conn, first['undo_id'])
    assert error.value.status == 409
    assert calendar.get_event(conn, original['id'])['title'] == 'Later'


def test_task_workflows_and_completion_activity_preserve_actual_completion(workspace):
    conn = workspace.conn
    item = calendar.save_task(conn, {'title': 'Review trees', 'due': '2026-10-28', 'timezone': 'America/Chicago', 'tags': ['DSA', 'DSA'], 'project': 'Interview', 'priority': 'high'})
    assert item['tags'] == ['DSA']
    completed = calendar.save_task(conn, {'id': item['id'], 'revision': item['revision'], 'status': 'done'})
    edited = calendar.save_task(conn, {'id': item['id'], 'revision': completed['revision'], 'title': 'Reviewed trees'})
    assert edited['completed_at'] == completed['completed_at']
    with pytest.raises(APIError):
        calendar.save_task(conn, {'id': item['id'], 'revision': 1, 'status': 'doing'})
    calendar.undo(conn, edited['undo_id'])
    assert calendar.tasks(conn)[0]['title'] == 'Review trees'


def test_availability_conflicts_and_free_events(workspace):
    conn = workspace.conn
    first = event(conn)
    second = event(conn, title='Overlapping', start='2026-10-26T09:30:00-05:00', end='2026-10-26T10:30:00-05:00')
    event(conn, title='Free note', start='2026-10-26T11:00:00-05:00', end='2026-10-26T12:00:00-05:00', busy=False)
    result = calendar.availability(occurrences(conn), '2026-10-26T13:00:00Z', '2026-10-26T18:00:00Z')
    assert len(result['busy']) == 1 and len(result['free']) == 2
    assert result['conflicts'][0]['event_id'] == second['id'] and first['id'] in result['conflicts'][0]['with']
    assert len(calendar.availability(occurrences(conn), '2026-10-26T13:00:00Z', '2026-10-26T18:00:00Z', exclude_id=second['id'])['conflicts']) == 0


def test_reminders_dismissal_is_occurrence_specific(workspace):
    conn = workspace.conn
    item = event(conn, rrule='FREQ=DAILY;COUNT=2', reminders=[10])
    at = datetime.fromisoformat('2026-10-26T08:55:00-05:00')
    reminder = calendar.due_reminders(conn, at=at)[0]
    calendar.dismiss_reminder(conn, reminder)
    assert calendar.due_reminders(conn, at=at) == []
    following = calendar.due_reminders(conn, at=at + timedelta(days=1))
    assert following[0]['event_id'] == item['id'] and following[0]['occurrence'] != reminder['occurrence']


def test_source_refresh_idempotent_manual_override_and_no_source_writes(workspace):
    conn = workspace.conn
    job, _ = jobs.upsert_job(conn, company='Synthetic calendar company', title='Intern', url='https://example.invalid/job/calendar', source='manual', source_job_id='calendar')
    conn.execute('UPDATE jobs SET deadline_at=? WHERE id=?', ('2026-11-01', job.id))
    assert calendar.import_jobs(conn)['changed'] == 1
    assert calendar.import_jobs(conn)['changed'] == 0
    linked = occurrences(conn)[0]
    assert linked['source_key'] == f'job:{job.id}:deadline'
    same = calendar.create_event(conn, {'title': 'Would overwrite', 'start': '2026-11-02', 'end': '2026-11-03', 'timezone': 'UTC', 'all_day': True, 'source_key': linked['source_key']})
    assert same['id'] == linked['id'] and same['title'] == linked['title']
    calendar.edit_event(conn, linked['id'], {'revision': linked['revision'], 'changes': {'title': 'My copy'}})
    conn.execute('UPDATE jobs SET deadline_at=? WHERE id=?', ('2026-11-03', job.id))
    assert calendar.import_jobs(conn)['changed'] == 0
    assert calendar.get_event(conn, linked['id'])['title'] == 'My copy'
    assert conn.execute('SELECT deadline_at FROM jobs WHERE id=?', (job.id,)).fetchone()[0] == '2026-11-03'


def test_ics_roundtrip_preserves_recurrence_exclusions_exceptions_and_dedup(workspace):
    conn = workspace.conn
    first = event(conn, rrule='FREQ=WEEKLY;COUNT=4', exdates=['2026-11-09T09:00:00-06:00'], attendees=[{'email': 'person@example.invalid', 'name': 'Person', 'response': 'tentative'}])
    calendar.edit_event(conn, first['id'], {'revision': 1, 'scope': 'this', 'occurrence': '2026-11-02T09:00:00-06:00', 'changes': {'title': 'Exception'}})
    content = calendar.export_ics(conn)['content']
    preview = calendar.preview_ics(conn, content, 'America/Chicago')
    assert len(preview['items']) == 1 and 'error' not in preview['items'][0]
    result = calendar.import_reviewed(conn, preview['items'])
    imported = calendar.get_event(conn, result['created'][0])
    assert imported['rrule'] == first['rrule'] and imported['exdates'] == first['exdates']
    assert imported['attendees'][0]['response'] == 'tentative'
    assert len(conn.execute('SELECT * FROM calendar_exceptions WHERE event_id=?', (imported['id'],)).fetchall()) == 1
    duplicate = calendar.import_reviewed(conn, calendar.preview_ics(conn, content, 'America/Chicago')['items'])
    assert duplicate['created'] == [] and duplicate['duplicates'] == [imported['id']]


def test_csv_preview_review_and_atomic_validation(workspace):
    conn = workspace.conn
    preview = calendar.preview_csv(conn, 'title,date,timezone\nApply,2026-11-01,UTC\nInvalid,not-a-date,UTC\n')
    assert preview['items'][0]['event']['all_day'] is True
    assert 'error' in preview['items'][1]
    with pytest.raises(APIError):
        calendar.import_reviewed(conn, [preview['items'][0], {'event': {'title': 'Invalid'}}])
    assert conn.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0] == 0
    result = calendar.import_reviewed(conn, [preview['items'][0]])
    assert len(result['created']) == 1
    assert calendar.preview_csv(conn, 'title,date,timezone\nApply,2026-11-01,UTC\n')['items'][0]['duplicate'] is True


def test_nullable_source_key_and_empty_optional_fields_roundtrip_on_edit(workspace):
    conn = workspace.conn
    original = event(conn, source_key=None, source_url='', attendees=[], attachments=[], google_options={})
    edited = calendar.edit_event(conn, original['id'], {'revision': original['revision'], 'changes': {'title': 'Renamed'}})
    assert edited['source_key'] is None and edited['source_url'] == ''
    assert edited['attendees'] == [] and edited['attachments'] == [] and edited['google_options'] == {}
    calendar.undo(conn, edited['undo_id'])
    assert calendar.get_event(conn, original['id'])['title'] == 'Study block'
    with pytest.raises(APIError, match='Source key must be text'):
        event(conn, source_key=0)


@pytest.mark.parametrize(('start', 'end', 'at', 'due'), [
    ('2026-11-01T01:30:00-06:00', '2026-11-01T02:30:00-06:00',
     '2026-11-01T01:35:00-05:00', '2026-11-01T01:30:00-05:00'),
    ('2027-03-14T03:30:00-05:00', '2027-03-14T04:30:00-05:00',
     '2027-03-14T01:35:00-06:00', '2027-03-14T01:30:00-06:00'),
])
def test_reminder_offsets_use_elapsed_minutes_across_dst(workspace, start, end, at, due):
    event(workspace.conn, start=start, end=end, reminders=[60])
    result = calendar.due_reminders(workspace.conn, at=datetime.fromisoformat(at))
    assert len(result) == 1 and result[0]['due'] == due


def test_calendar_cannot_be_deleted_while_a_task_references_it(workspace):
    conn = workspace.conn
    cal = calendar.save_calendar(conn, {'name': 'Task calendar'})
    task = calendar.save_task(conn, {'title': 'Keep this task', 'calendar_id': cal['id']})
    with pytest.raises(APIError, match='tasks'):
        calendar.delete_calendar(conn, {'id': cal['id'], 'revision': cal['revision']})
    with pytest.raises(APIError, match='Delete must be boolean'):
        calendar.save_task(conn, {'id': task['id'], 'revision': task['revision'], 'delete': 'false'})
    calendar.save_task(conn, {'id': task['id'], 'revision': task['revision'], 'calendar_id': 'personal'})
    assert calendar.delete_calendar(conn, {'id': cal['id'], 'revision': cal['revision']})['ok']
    assert calendar.tasks(conn)[0]['calendar_id'] == 'personal'


def test_removed_source_milestone_disappears_and_restores_without_overwriting_manual_copy(workspace):
    conn = workspace.conn
    job, _ = jobs.upsert_job(conn, company='Synthetic source calendar', title='Intern',
                            url='https://example.invalid/job/source', source='manual', source_job_id='source')
    conn.execute('UPDATE jobs SET deadline_at=? WHERE id=?', ('2026-11-01', job.id))
    assert calendar.import_jobs(conn)['changed'] == 1
    original = occurrences(conn)[0]
    conn.execute('UPDATE jobs SET deadline_at=NULL WHERE id=?', (job.id,))
    assert calendar.import_jobs(conn)['changed'] == 1 and occurrences(conn) == []
    conn.execute('UPDATE jobs SET deadline_at=? WHERE id=?', ('2026-11-01', job.id))
    assert calendar.import_jobs(conn)['changed'] == 1
    restored = occurrences(conn)[0]
    assert restored['id'] == original['id']
    calendar.edit_event(conn, restored['id'], {'revision': restored['revision'], 'changes': {'title': 'Protected manual copy'}})
    conn.execute('UPDATE jobs SET deadline_at=NULL WHERE id=?', (job.id,))
    assert calendar.import_jobs(conn)['changed'] == 0
    assert occurrences(conn)[0]['title'] == 'Protected manual copy'


def test_this_scope_does_not_silently_discard_series_or_calendar_edits(workspace):
    conn = workspace.conn
    original = event(conn, rrule='FREQ=WEEKLY;COUNT=3')
    cal = calendar.save_calendar(conn, {'name': 'Another calendar'})
    for changes in ({'rrule': 'FREQ=DAILY;COUNT=3'}, {'exdates': [original['start']]}, {'calendar_id': cal['id']}):
        with pytest.raises(APIError, match='individual occurrence'):
            calendar.edit_event(conn, original['id'], {'revision': original['revision'], 'scope': 'this',
                                                        'occurrence': original['start'], 'changes': changes})
    assert len(occurrences(conn)) == 3


def test_timed_duration_and_fold_survive_dst_expansion_and_occurrence_edits(workspace):
    conn = workspace.conn
    item = event(conn, start='2026-11-01T01:30:00-06:00', end='2026-11-01T02:30:00-06:00',
                 rrule='FREQ=DAILY;COUNT=2')
    values = occurrences(conn)
    assert values[0]['start'] == '2026-11-01T01:30:00-06:00'
    assert values[0]['end'] == '2026-11-01T02:30:00-06:00'
    with pytest.raises(APIError, match='not in this series'):
        calendar.edit_event(conn, item['id'], {'revision': item['revision'], 'scope': 'this',
                                               'occurrence': '2026-11-01T01:30:00-05:00', 'changes': {'title': 'Wrong fold'}})
    edited = calendar.edit_event(conn, item['id'], {'revision': item['revision'], 'scope': 'this',
                                                   'occurrence': values[0]['occurrence'], 'changes': {'title': 'Correct fold'}})
    calendar.edit_event(conn, item['id'], {'revision': edited['revision'], 'scope': 'all', 'changes': {'description': 'Unrelated edit'}})
    assert occurrences(conn)[0]['start'] == '2026-11-01T01:30:00-06:00'


def test_recurrence_end_uses_elapsed_duration_and_never_lands_in_spring_gap(workspace):
    conn = workspace.conn
    original = event(conn, start='2027-03-13T01:30:00-06:00', end='2027-03-13T02:30:00-06:00',
                     rrule='FREQ=DAILY;COUNT=3')
    values = occurrences(conn, '2027-03-12T00:00:00Z', '2027-03-17T00:00:00Z')
    assert values[1]['start'] == '2027-03-14T01:30:00-06:00'
    assert values[1]['end'] == '2027-03-14T03:30:00-05:00'
    assert all(datetime.fromisoformat(v['end']) - datetime.fromisoformat(v['start']) == timedelta(hours=1) for v in values)
    edited = calendar.edit_event(conn, original['id'], {'revision': original['revision'], 'scope': 'this',
                                                        'occurrence': values[1]['occurrence'], 'changes': {'title': 'Spring occurrence'}})
    assert next(v for v in occurrences(conn, '2027-03-12T00:00:00Z', '2027-03-17T00:00:00Z')
                if v['title'] == 'Spring occurrence')['end'] == '2027-03-14T03:30:00-05:00'
    assert edited['revision'] == original['revision'] + 1


def test_future_scope_at_spring_transition_keeps_exact_count_boundary(workspace):
    conn = workspace.conn
    original = event(conn, start='2027-03-13T03:00:00-06:00', end='2027-03-13T04:00:00-06:00',
                     rrule='FREQ=DAILY;COUNT=3')
    split = calendar.edit_event(conn, original['id'], {'revision': original['revision'], 'scope': 'future',
                                                      'occurrence': '2027-03-14T03:00:00-05:00', 'changes': {'title': 'Future'}})
    assert split['future_event']['rrule'] == 'FREQ=DAILY;COUNT=2'
    values = occurrences(conn, '2027-03-12T00:00:00Z', '2027-03-17T00:00:00Z')
    assert len(values) == 3
    assert [v['title'] for v in values] == ['Study block', 'Future', 'Future']


def repeating_task(conn, **changes):
    return calendar.save_task(conn, {'title': 'Recurring review', 'due': '2026-10-01',
                                    'timezone': 'America/Chicago', 'repeat_frequency': 'DAILY', **changes})


def complete_task(conn, item):
    return calendar.save_task(conn, {'id': item['id'], 'revision': item['revision'], 'status': 'done'})


def test_recurring_tasks_skip_missed_dates_and_preserve_real_history(workspace, monkeypatch):
    conn = workspace.conn
    monkeypatch.setattr(calendar, 'now', lambda: '2026-10-14T17:00:00+00:00')
    item = repeating_task(conn, tags=['DSA'], description='Keep notes', priority='high', project='Practice')
    completed = complete_task(conn, item)
    following = completed['next_task']
    assert completed['completed_at'] == '2026-10-14T17:00:00+00:00'
    assert completed['due'] == '2026-10-01'
    assert following['due'] == '2026-10-15'
    assert following['status'] == 'todo' and following['completed_at'] is None
    assert all(following[key] == completed[key] for key in
               ('title', 'tags', 'description', 'priority', 'project', 'timezone', 'repeat_frequency', 'repeat_interval'))
    values = calendar.tasks(conn)
    assert len(values) == 2
    assert sum(v['status'] == 'done' for v in values) == 1
    days = calendar.activity(conn, '2026-10-01T00:00:00Z', '2026-10-20T00:00:00Z', 'America/Chicago')
    assert [(v['date'], v.get('tasks_completed')) for v in days] == [('2026-10-14', 1)]
    repeated = complete_task(conn, completed)
    assert repeated['next_task'] is None and repeated['completed_at'] == completed['completed_at']
    assert len(calendar.tasks(conn)) == 2
    with pytest.raises(APIError) as error:
        complete_task(conn, item)
    assert error.value.status == 409
    assert len(calendar.tasks(conn)) == 2


@pytest.mark.parametrize(('frequency', 'interval', 'due', 'expected'), [
    ('DAILY', 3, '2026-10-01', '2026-10-16'),
    ('WEEKLY', 2, '2026-09-30', '2026-10-28'),
    ('MONTHLY', 2, '2026-08-31', '2026-10-31'),
    ('YEARLY', 2, '2024-02-29', '2028-02-29'),
])
def test_recurring_task_intervals_skip_to_future_anchor_boundary(workspace, monkeypatch, frequency, interval, due, expected):
    monkeypatch.setattr(calendar, 'now', lambda: '2026-10-14T17:00:00+00:00')
    item = repeating_task(workspace.conn, due=due, repeat_frequency=frequency, repeat_interval=interval)
    assert complete_task(workspace.conn, item)['next_task']['due'] == expected


@pytest.mark.parametrize(('frequency', 'due', 'first_due', 'later_clock', 'second_due'), [
    ('MONTHLY', '2027-01-31', '2027-02-28', '2027-02-28T17:00:00+00:00', '2027-03-31'),
    ('YEARLY', '2024-02-29', '2025-02-28', '2027-02-28T17:00:00+00:00', '2028-02-29'),
])
def test_recurring_task_month_end_and_leap_anchor_never_drift(workspace, monkeypatch, frequency, due, first_due, later_clock, second_due):
    conn = workspace.conn
    monkeypatch.setattr(calendar, 'now', lambda: due + 'T17:00:00+00:00')
    original = repeating_task(conn, due=due, repeat_frequency=frequency)
    first = complete_task(conn, original)['next_task']
    assert first['due'] == first_due
    # Ordinary edits of an instance must not turn a clamped date into its anchor.
    first = calendar.save_task(conn, {'id': first['id'], 'revision': first['revision'], 'title': 'Updated next title'})
    monkeypatch.setattr(calendar, 'now', lambda: later_clock)
    second = complete_task(conn, first)['next_task']
    assert second['due'] == second_due
    rows = conn.execute('SELECT repeat_anchor,task_series_id FROM calendar_tasks WHERE deleted=0').fetchall()
    assert {row['repeat_anchor'] for row in rows} == {due}
    assert {row['task_series_id'] for row in rows} == {original['id']}


@pytest.mark.parametrize(('due', 'clock', 'expected'), [
    ('2026-10-31T09:00:00-05:00', '2026-10-31T15:00:00+00:00', '2026-11-01T09:00:00-06:00'),
    ('2027-03-13T02:30:00-06:00', '2027-03-13T09:00:00+00:00', '2027-03-15T02:30:00-05:00'),
    ('2026-11-01T01:30:00-06:00', '2026-11-01T07:00:00+00:00', '2026-11-02T01:30:00-06:00'),
    ('2026-10-14T18:00:00Z', '2026-10-14T17:00:00+00:00', '2026-10-15T13:00:00-05:00'),
])
def test_recurring_task_times_normalize_and_preserve_wall_clock_across_dst(workspace, monkeypatch, due, clock, expected):
    monkeypatch.setattr(calendar, 'now', lambda: clock)
    item = repeating_task(workspace.conn, due=due)
    assert item['due'] == calendar.instant(due, 'America/Chicago').isoformat()
    result = complete_task(workspace.conn, item)
    assert result['next_task']['due'] == expected
    assert len(calendar.tasks(workspace.conn)) == 2


@pytest.mark.parametrize(('due', 'clock', 'until', 'expected'), [
    ('2026-10-14', '2026-10-14T17:00:00+00:00', '2026-10-15', '2026-10-15'),
    ('2026-10-14', '2026-10-15T17:00:00+00:00', '2026-10-15', None),
    ('2026-10-14T23:30:00-05:00', '2026-10-15T05:00:00+00:00', '2026-10-15', '2026-10-15T23:30:00-05:00'),
    ('9999-12-31', '2026-10-14T17:00:00+00:00', '', None),
])
def test_recurring_task_stop_date_is_inclusive_in_task_timezone(workspace, monkeypatch, due, clock, until, expected):
    monkeypatch.setattr(calendar, 'now', lambda: clock)
    item = repeating_task(workspace.conn, due=due, repeat_until=until)
    result = complete_task(workspace.conn, item)
    assert (result['next_task']['due'] if result['next_task'] else None) == expected
    if result['next_task']:
        monkeypatch.setattr(calendar, 'now', lambda: expected[:10] + 'T17:00:00+00:00')
        assert complete_task(workspace.conn, result['next_task'])['next_task'] is None
    assert sum(v['status'] == 'done' for v in calendar.tasks(workspace.conn)) == (2 if expected else 1)


def test_recurring_task_completion_undo_tombstones_successor_and_can_complete_again(workspace, monkeypatch):
    conn = workspace.conn
    monkeypatch.setattr(calendar, 'now', lambda: '2026-10-14T17:00:00+00:00')
    item = repeating_task(conn)
    completed = complete_task(conn, item)
    next_id = completed['next_task']['id']
    calendar.undo(conn, completed['undo_id'])
    restored = calendar.tasks(conn)
    assert len(restored) == 1 and restored[0]['status'] == 'todo' and restored[0]['completed_at'] is None
    assert conn.execute('SELECT deleted FROM calendar_tasks WHERE id=?', (next_id,)).fetchone()['deleted'] == 1
    repeated = complete_task(conn, restored[0])
    assert repeated['next_task']['id'] != next_id and repeated['next_task']['due'] == '2026-10-15'
    assert len(calendar.tasks(conn)) == 2
    assert conn.execute('SELECT COUNT(*) FROM calendar_tasks WHERE previous_task_id=? AND deleted=0', (item['id'],)).fetchone()[0] == 1
    with pytest.raises(APIError) as error:
        calendar.undo(conn, completed['undo_id'])
    assert error.value.status == 404


@pytest.mark.parametrize('change', [{'title': 'Edited next'}, {'status': 'done'}, {'delete': True}])
def test_recurring_task_undo_rejects_changed_successor_atomically(workspace, monkeypatch, change):
    conn = workspace.conn
    monkeypatch.setattr(calendar, 'now', lambda: '2026-10-14T17:00:00+00:00')
    completed = complete_task(conn, repeating_task(conn))
    following = completed['next_task']
    calendar.save_task(conn, {'id': following['id'], 'revision': following['revision'], **change})
    before = [dict(row) for row in conn.execute('SELECT * FROM calendar_tasks ORDER BY id')]
    with pytest.raises(APIError) as error:
        calendar.undo(conn, completed['undo_id'])
    assert error.value.status == 409
    assert [dict(row) for row in conn.execute('SELECT * FROM calendar_tasks ORDER BY id')] == before
    assert conn.execute('SELECT undone FROM calendar_history WHERE id=?', (completed['undo_id'],)).fetchone()[0] == 0


def test_recurring_task_history_edits_do_not_rewrite_recurrence_or_generate(workspace, monkeypatch):
    conn = workspace.conn
    monkeypatch.setattr(calendar, 'now', lambda: '2026-10-14T17:00:00+00:00')
    completed = complete_task(conn, repeating_task(conn))
    edited = calendar.save_task(conn, {'id': completed['id'], 'revision': completed['revision'], 'title': 'History note'})
    assert edited['completed_at'] == completed['completed_at'] and edited['next_task'] is None
    following = completed['next_task']
    for changes in ({'status': 'todo'}, {'repeat_frequency': 'WEEKLY'}, {'repeat_interval': 2}, {'repeat_until': '2026-12-31'}):
        with pytest.raises(APIError) as error:
            calendar.save_task(conn, {'id': edited['id'], 'revision': edited['revision'], **changes})
        assert error.value.status == 409
    assert next(v for v in calendar.tasks(conn) if v['id'] == following['id'])['repeat_frequency'] == 'DAILY'
    changed_next = calendar.save_task(conn, {'id': following['id'], 'revision': following['revision'],
                                           'repeat_frequency': 'WEEKLY', 'repeat_interval': 2})
    assert complete_task(conn, changed_next)['next_task']['due'] == '2026-10-29'
    assert next(v for v in calendar.tasks(conn) if v['id'] == completed['id'])['repeat_frequency'] == 'DAILY'


def test_recurring_task_deletion_cancellation_and_creation_undo_keep_crud_behavior(workspace, monkeypatch):
    conn = workspace.conn
    monkeypatch.setattr(calendar, 'now', lambda: '2026-10-14T17:00:00+00:00')
    plain = calendar.save_task(conn, {'title': 'Plain task'})
    assert (plain['repeat_frequency'], plain['repeat_interval'], plain['repeat_until']) == ('', 1, '')
    calendar.undo(conn, plain['undo_id'])
    assert calendar.tasks(conn) == []
    recurring = repeating_task(conn)
    cancelled = calendar.save_task(conn, {'id': recurring['id'], 'revision': recurring['revision'], 'status': 'cancelled'})
    assert cancelled['next_task'] is None and cancelled['completed_at'] is None
    deleted = calendar.save_task(conn, {'id': recurring['id'], 'revision': cancelled['revision'], 'delete': True, 'status': 'done'})
    assert deleted['next_task'] is None and calendar.tasks(conn) == []
    calendar.undo(conn, deleted['undo_id'])
    assert calendar.tasks(conn)[0]['status'] == 'cancelled'
    created_done = repeating_task(conn, status='done')
    assert created_done['next_task'] is not None
    calendar.undo(conn, created_done['undo_id'])
    assert len(calendar.tasks(conn)) == 1


@pytest.mark.parametrize('changes', [
    {'repeat_frequency': 'HOURLY'}, {'repeat_frequency': None}, {'repeat_interval': 0},
    {'repeat_interval': -1}, {'repeat_interval': True}, {'repeat_interval': 1.5}, {'repeat_interval': '2'},
    {'due': ''}, {'due': None}, {'due': '2026-10-14T09:00:00'}, {'timezone': 'Invalid/Zone'},
    {'repeat_until': '2026-02-30'}, {'repeat_until': '2026-10-15T00:00:00Z'}, {'repeat_until': '2026-09-30'},
])
def test_recurring_task_validation_is_atomic(workspace, monkeypatch, changes):
    monkeypatch.setattr(calendar, 'now', lambda: '2026-10-14T17:00:00+00:00')
    with pytest.raises(APIError):
        repeating_task(workspace.conn, **changes)
    assert calendar.tasks(workspace.conn) == []
    assert workspace.conn.execute('SELECT COUNT(*) FROM calendar_history').fetchone()[0] == 0

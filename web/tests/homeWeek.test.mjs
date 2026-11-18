// Home's This week widget: which day an event falls on, and which event comes next.
import assert from 'node:assert/strict';
import test from 'node:test';
import { upcoming, weekDays } from '../src/lib/home/week.ts';

const local = (year, month, day, hour = 0, minute = 0) => new Date(year, month - 1, day, hour, minute);
const start = local(2026, 11, 18).getTime();

test('seven calendar days from today, each holding the events that start on it', () => {
  const days = weekDays([
    { title: 'Seminar', start: local(2026, 11, 18, 23, 30).toISOString() },
    { title: 'Lab', start: local(2026, 11, 19, 0, 0).toISOString() },
    { title: 'Deadline', start: '2026-11-19', all_day: true },
    { title: 'Next week', start: local(2026, 11, 25, 9).toISOString() },
    { title: 'Yesterday', start: local(2026, 11, 17, 22).toISOString() },
  ], start);
  assert.deepEqual(days.map((day) => new Date(day.at).getDate()), [18, 19, 20, 21, 22, 23, 24]);
  assert.deepEqual(days.map((day) => day.events.map((event) => event.title)), [['Seminar'], ['Lab', 'Deadline'], [], [], [], [], []]);
});

test('a week across a clock change still holds seven different dates at local midnight', () => {
  // Late October and early November hold the autumn change, and March the spring one, wherever this runs.
  for (const from of [local(2026, 10, 22), local(2026, 10, 29), local(2026, 3, 5), local(2026, 3, 26)]) {
    const days = weekDays([], from.getTime());
    assert.equal(new Set(days.map((day) => new Date(day.at).toDateString())).size, 7);
    for (const day of days) assert.equal(new Date(day.at).getHours(), 0);
  }
});

test('what comes next: events that have not started, soonest first; an all-day one counts from the day after today', () => {
  const now = local(2026, 11, 18, 10).getTime();
  const events = [
    { title: 'Earlier today', start: local(2026, 11, 18, 9).toISOString() },
    { title: 'All day today', start: '2026-11-18', all_day: true },
    { title: 'Friday', start: local(2026, 11, 20, 9).toISOString() },
    { title: 'All day tomorrow', start: '2026-11-19', all_day: true },
    { title: 'This afternoon', start: local(2026, 11, 18, 15).toISOString() },
  ];
  assert.deepEqual(upcoming(events, now, 2).map((event) => event.title), ['This afternoon', 'All day tomorrow']);
  assert.deepEqual(upcoming(events, now, 5).map((event) => event.title), ['This afternoon', 'All day tomorrow', 'Friday']);
  assert.deepEqual(upcoming(events.slice(0, 2), now, 2), []);
});

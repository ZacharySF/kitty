// Q60 notifications (docs/frontend-redesign-design.md): toast delivery, quiet hours and the morning digest.
// node --experimental-strip-types --test tests/notify.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_QUIET, QUIET_KEY, calendarNotice, clock, digestLine, emptyLedger, holds, inQuietHours,
  jobNotice, quietSpan, readQuiet, step, writeQuiet,
} from '../src/lib/notify.ts';

function memory(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: key => (values.has(key) ? values.get(key) : null), setItem: (key, value) => values.set(key, String(value)), values };
}
const at = (hh, mm = 0) => new Date(2026, 10, 1, hh, mm);
const lecture = calendarNotice({ event_id: 'ev1', occurrence: '2026-11-01T09:00', minutes: 15, title: 'Linear algebra lecture', due: '2026-11-01T08:45:00+00:00' });
const followup = jobNotice({ id: 7, kind: 'followup', due_at: '2026-11-01T00:00:00Z', note: null, job_id: 3, label: 'Northwind Labs (demo) - Quant intern' });
const night = { ...DEFAULT_QUIET, enabled: true, from: 22 * 60, to: 7 * 60 + 30, letCalendar: false };

test('reminders from both sources become notices with stable keys', () => {
  assert.deepEqual(lecture, { key: 'calendar:ev1:2026-11-01T09:00:15', source: 'calendar', title: 'Linear algebra lecture',
    body: '15 min before', due: '2026-11-01T08:45:00+00:00', target: { kind: 'calendar', event_id: 'ev1', occurrence: '2026-11-01T09:00', minutes: 15 } });
  assert.equal(followup.key, 'job:7:2026-11-01T00:00:00Z');
  assert.equal(followup.source, 'job');
  assert.equal(followup.title, 'Northwind Labs (demo) - Quant intern');
  assert.equal(followup.body, 'Follow-up');
  assert.deepEqual(followup.target, { kind: 'job', id: 7, job_id: 3 });
  assert.equal(calendarNotice({ event_id: 'e', occurrence: 'o', minutes: 0, title: 'Standup', due: 'x' }).body, 'Starting now');
});

test('quiet hours wrap past midnight and an empty window is never quiet', () => {
  assert.equal(inQuietHours(night, 23 * 60), true);
  assert.equal(inQuietHours(night, 3 * 60), true);
  assert.equal(inQuietHours(night, 7 * 60 + 30), false);
  assert.equal(inQuietHours(night, 12 * 60), false);
  assert.equal(inQuietHours({ ...night, enabled: false }, 23 * 60), false);
  assert.equal(inQuietHours({ ...night, from: 600, to: 600 }, 600), false);
  assert.equal(quietSpan(night), 9 * 60 + 30);
  assert.equal(clock(7 * 60 + 30), '07:30');
  assert.equal(clock(-15), '23:45');
});

test('calendar events can ring through quiet hours', () => {
  assert.equal(holds(night, lecture, 23 * 60), true);
  assert.equal(holds({ ...night, letCalendar: true }, lecture, 23 * 60), false);
  assert.equal(holds({ ...night, letCalendar: true }, followup, 23 * 60), true);
});

test('quiet hours are remembered and bad values fall back to the defaults', () => {
  const storage = memory();
  assert.deepEqual(readQuiet(storage), DEFAULT_QUIET);
  writeQuiet(storage, night);
  assert.deepEqual(readQuiet(storage), night);
  storage.setItem(QUIET_KEY, JSON.stringify({ enabled: true, from: 5000, to: 'x' }));
  assert.deepEqual(readQuiet(storage), { ...DEFAULT_QUIET, enabled: true });
});

test('a new reminder toasts once; a seen one stays quiet', () => {
  const first = step(emptyLedger(), [lecture, followup], DEFAULT_QUIET, at(10));
  assert.deepEqual(first.toasts.map(n => n.key), [lecture.key, followup.key]);
  assert.equal(first.digest, null);
  const again = step(first.ledger, [lecture, followup], DEFAULT_QUIET, at(10, 1));
  assert.deepEqual(again.toasts, []);
});

test('quiet hours hold reminders, then hand them over as one morning digest', () => {
  const held = step(emptyLedger(), [lecture, followup], night, at(23));
  assert.deepEqual(held.toasts, []);
  assert.deepEqual(held.ledger.held, [lecture.key, followup.key]);
  const still = step(held.ledger, [lecture, followup], night, at(6));
  assert.equal(still.digest, null);
  const morning = step(still.ledger, [lecture, followup], night, at(7, 30));
  assert.deepEqual(morning.toasts, []);
  assert.deepEqual(morning.digest.map(n => n.key), [lecture.key, followup.key]);
  assert.deepEqual(morning.ledger.held, []);
  assert.deepEqual(step(morning.ledger, [lecture, followup], night, at(8)).toasts, []);
  assert.equal(digestLine(morning.digest), '1 calendar · 1 job search');
  // Without the digest, held reminders wait unread in the notification center instead.
  const silent = step(held.ledger, [lecture, followup], { ...night, digest: false }, at(8));
  assert.equal(silent.digest, null);
  assert.deepEqual(silent.toasts, []);
  assert.deepEqual(silent.ledger.held, []);
});

test('a held reminder that was cleared meanwhile is dropped from the digest', () => {
  const held = step(emptyLedger(), [lecture, followup], night, at(23));
  const morning = step(held.ledger, [followup], night, at(8));
  assert.deepEqual(morning.digest.map(n => n.key), [followup.key]);
});

test('a snoozed reminder hides until its snooze ends, then toasts again', () => {
  const first = step(emptyLedger(), [lecture], DEFAULT_QUIET, at(10));
  const snoozed = { ...first.ledger, snoozed: { [lecture.key]: at(10, 10).toISOString() } };
  const during = step(snoozed, [lecture], DEFAULT_QUIET, at(10, 5));
  assert.deepEqual(during.toasts, []);
  assert.deepEqual(during.visible, []);
  const after = step(during.ledger, [lecture], DEFAULT_QUIET, at(10, 10));
  assert.deepEqual(after.toasts.map(n => n.key), [lecture.key]);
  assert.deepEqual(after.visible.map(n => n.key), [lecture.key]);
  assert.deepEqual(after.ledger.snoozed, {});
});

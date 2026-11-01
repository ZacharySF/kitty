// Run with Node >=22.6: node --experimental-strip-types --test tests/calendarQuickParse.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {parseQuickEvent} from '../src/lib/calendar/quickParse.ts';

const today = '2026-10-31'; // a Saturday

test('a sentence becomes a title, a weekday date and a time', () => {
  const p = parseQuickEvent('lunch with Mira fri 12:30', today);
  assert.equal(p.title, 'Lunch with Mira');
  assert.equal(p.date, '2026-11-06');
  assert.equal(p.start, 12 * 60 + 30);
  assert.equal(p.end, null);
  assert.deepEqual(p.spans.map((s) => s.kind), ['day', 'time']);
});

test('relative days, pm times, durations, ranges, places and repeats', () => {
  const a = parseQuickEvent('orgo mechanisms tomorrow 3pm for 90m', today);
  assert.deepEqual([a.title, a.date, a.start, a.minutes], ['Orgo mechanisms', '2026-11-01', 900, 90]);
  const b = parseQuickEvent('call Northwind mon 10-10:30', today);
  assert.deepEqual([b.title, b.date, b.start, b.end], ['Call Northwind', '2026-11-02', 600, 630]);
  const c = parseQuickEvent('arch report due nov 12 17:00', today);
  assert.deepEqual([c.date, c.start], ['2026-11-12', 1020]);
  const d = parseQuickEvent('coffee sat 9:00 at Café Rose', today);
  assert.deepEqual([d.title, d.date, d.where], ['Coffee', '2026-11-07', 'Café Rose']);
  const e = parseQuickEvent('kanji review every day 20:00', today);
  assert.deepEqual([e.title, e.rrule, e.repeat, e.start], ['Kanji review', 'FREQ=DAILY', 'every day', 1200]);
  assert.equal(parseQuickEvent('standup every monday 9am', today).rrule, 'FREQ=WEEKLY;BYDAY=MO');
});

test('plain titles are left alone', () => {
  const p = parseQuickEvent('Team retro', today);
  assert.deepEqual([p.title, p.date, p.start, p.spans.length], ['Team retro', null, null, 0]);
});

// Run with Node >=22.6: node --experimental-strip-types --test tests/calendarDates.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {addDays, dayLoad, placeTimed, slot, wallParts, weekStart} from '../src/lib/calendar/dates.ts';

const at = (wall) => `${wall}:00Z`;
const lanes = (placements) => placements.map((p) => [p.event.id, p.column, p.columns]);

test('overlapping events share lanes; a group ends when nothing still runs', () => {
  const day = '2026-11-11';
  const placed = placeTimed([
    {id: 'a', start: at(`${day}T09:00`), end: at(`${day}T11:00`)},
    {id: 'b', start: at(`${day}T09:30`), end: at(`${day}T10:00`)},
    {id: 'c', start: at(`${day}T10:00`), end: at(`${day}T10:30`)}, // reuses b's lane
    {id: 'd', start: at(`${day}T12:00`), end: at(`${day}T13:00`)}, // a new group
  ], day, 'UTC');
  assert.deepEqual(lanes(placed), [['a', 0, 2], ['b', 1, 2], ['c', 1, 2], ['d', 0, 1]]);
});

test('a short event still blocks 30 minutes of its lane', () => {
  const day = '2026-11-11';
  const placed = placeTimed([
    {id: 'a', start: at(`${day}T09:00`), end: at(`${day}T09:05`)},
    {id: 'b', start: at(`${day}T09:15`), end: at(`${day}T09:45`)},
  ], day, 'UTC');
  assert.deepEqual(lanes(placed), [['a', 0, 2], ['b', 1, 2]]);
});

test('events crossing midnight are clipped to the day being drawn', () => {
  const event = {id: 'night', start: at('2026-11-10T22:00'), end: at('2026-11-11T02:00')};
  assert.deepEqual(placeTimed([event], '2026-11-10', 'UTC').map((p) => [p.start, p.end]), [[1320, 1440]]);
  assert.deepEqual(placeTimed([event], '2026-11-11', 'UTC').map((p) => [p.start, p.end]), [[0, 120]]);
});

test('day load splits timed events at midnight and books no time for all-day ones', () => {
  assert.deepEqual(dayLoad([
    {start: at('2026-11-10T22:00'), end: at('2026-11-12T01:30'), all_day: false},
    {start: '2026-11-11', end: '2026-11-12', all_day: true},
  ], 'UTC'), {
    '2026-11-10': {minutes: 120, events: 1},
    '2026-11-11': {minutes: 1440, events: 1},
    '2026-11-12': {minutes: 90, events: 0},
  });
});

test('wall parts and weeks follow the display zone and Monday starts', () => {
  assert.deepEqual(wallParts('2026-11-11T03:30:00Z', 'America/Chicago'),
    {date: '2026-11-10', time: '21:30', hour: 21, minute: 30});
  assert.equal(weekStart('2026-11-15'), '2026-11-09'); // a Sunday belongs to the week before
  assert.equal(addDays('2026-02-28', 1), '2026-03-01');
  assert.equal(slot('2026-11-11', 49), '2026-11-12T00:30', 'slots past 47 run into the next day');
});

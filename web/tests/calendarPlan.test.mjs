// Run with Node >=22.6: node --experimental-strip-types --test tests/calendarPlan.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {nextFreeGap, isoWeek} from '../src/lib/calendar/plan.ts';

test('the next free gap skips busy ranges and starts on a quarter hour', () => {
  assert.equal(nextFreeGap([[480, 540], [540, 630]], 480, 30), 630);
  assert.equal(nextFreeGap([[480, 540], [560, 600]], 480, 30), 600, '20 minutes is too short for a 30 minute task');
  assert.equal(nextFreeGap([[480, 540], [570, 600]], 480, 30), 540);
  assert.equal(nextFreeGap([], 607, 30), 615, 'rounds the start up to a quarter hour');
  assert.equal(nextFreeGap([[600, 700]], 480, 30), 480);
});

test('no gap before the end of the day gives null', () => {
  assert.equal(nextFreeGap([[480, 1260]], 480, 30), null);
  assert.equal(nextFreeGap([], 1250, 30, 1260), null);
});

test('ISO week numbers follow ISO-8601', () => {
  assert.equal(isoWeek('2026-10-26'), 44);
  assert.equal(isoWeek('2026-12-28'), 53);
  assert.equal(isoWeek('2027-01-04'), 1);
  assert.equal(isoWeek('2024-12-30'), 1);
});

// Kitty's roll (themes/aero/random.ts): the same seed gives the same frame, seeds differ, and the words are what they say.
import assert from 'node:assert/strict';
import test from 'node:test';
import { INKS, STILLS, roll, stamp, word } from '../src/lib/themes/aero/random.ts';

test('a seed always rolls the same frame; across loads every still, ink and both edges turn up', () => {
  assert.deepEqual(roll(42), roll(42));
  const rolls = Array.from({ length: 300 }, (_, seed) => roll(seed));
  assert.deepEqual(new Set(rolls.map((item) => item.still)), new Set(STILLS));
  assert.deepEqual(new Set(rolls.map((item) => item.ink)), new Set(INKS));
  assert.deepEqual(new Set(rolls.map((item) => item.edge)), new Set(['left', 'right']));
  assert.ok(new Set(rolls.map((item) => JSON.stringify(item))).size > 200);
  for (const item of rolls) assert.ok(item.x >= 20 && item.x <= 80 && item.y >= 30 && item.y <= 70, JSON.stringify(item));
});

test('the date stamp is the day, two digits each, and the edge word is the page', () => {
  assert.equal(stamp(new Date(2026, 10, 5)), "'26 11 05");
  assert.equal(word('#quick-notes/3'), 'QUICK NOTES');
  assert.equal(word('#jobs?focus=1'), 'JOBS');
  assert.equal(word('#today'), 'RIKA!');
  assert.equal(word(''), 'RIKA!');
});

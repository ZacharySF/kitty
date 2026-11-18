// Where Rika's panel stands on home (the build's ui.home.rika_side): left of the widgets
// (the default), between the two widget columns, or right of them.
import assert from 'node:assert/strict';
import test from 'node:test';
import { nextRikaSide, rikaSide, rikaSideAt } from '../src/lib/home/layout.ts';

test('a dragged Rika lands in the third of home the pointer is over; with one widget column, the half', () => {
  assert.deepEqual([0.05, 0.33, 0.34, 0.5, 0.66, 0.67, 0.95].map((at) => rikaSideAt(at, 2)), ['left', 'left', 'middle', 'middle', 'middle', 'right', 'right']);
  assert.deepEqual([0.05, 0.49, 0.51, 0.95].map((at) => rikaSideAt(at, 1)), ['left', 'left', 'right', 'right']);
  // Past either edge of home it stays at that edge.
  assert.equal(rikaSideAt(-0.2, 2), 'left');
  assert.equal(rikaSideAt(1.3, 2), 'right');
});

test('an arrow key moves Rika one place and stops at the ends; the middle exists only between two columns', () => {
  assert.equal(nextRikaSide('right', -1, 2), 'middle');
  assert.equal(nextRikaSide('middle', -1, 2), 'left');
  assert.equal(nextRikaSide('left', -1, 2), 'left');
  assert.equal(nextRikaSide('left', 1, 2), 'middle');
  assert.equal(nextRikaSide('right', 1, 2), 'right');
  assert.equal(nextRikaSide('right', -1, 1), 'left');
  assert.equal(nextRikaSide('left', 1, 1), 'right');
});

test('the side drawn: a saved middle without two widget columns, or a side this rika! does not know, is the left', () => {
  assert.equal(rikaSide('middle', 2), 'middle');
  assert.equal(rikaSide('middle', 1), 'left');
  assert.equal(rikaSide('right', 1), 'right');
  assert.equal(rikaSide('upstairs', 2), 'left');
  assert.equal(rikaSide(undefined, 2), 'left');
});

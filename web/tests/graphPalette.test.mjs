// Run with Node >=22.6: node --experimental-strip-types --test tests/graphPalette.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {harmonize} from '../src/lib/graphPalette.ts';

const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const distance = (a, b) => Math.hypot(...rgb(a).map((v, i) => v - rgb(b)[i]));

test('rainbow graph palettes are pulled into the accent family but stay distinguishable', () => {
  const rainbow = ['#bd93f9', '#8be9fd', '#ff79c6', '#50fa7b', '#f1fa8c', '#ffb86c'];
  const result = harmonize(rainbow);
  assert.equal(result.length, rainbow.length);
  assert.equal(result[0], rainbow[0]);
  for (let i = 1; i < rainbow.length; i++) {
    assert.ok(distance(result[i], rainbow[0]) < distance(rainbow[i], rainbow[0]) * 0.65, `color ${i} not pulled toward accent`);
    for (let j = 0; j < i; j++) assert.ok(distance(result[i], result[j]) > 12, `colors ${i} and ${j} collapsed`);
  }
});

test('invalid or empty palettes are left alone', () => {
  assert.deepEqual(harmonize([]), []);
  assert.deepEqual(harmonize(['not-a-color', '#123456']), ['not-a-color', '#123456']);
});

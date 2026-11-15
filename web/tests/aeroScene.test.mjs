// Kitty's rolled scene (themes/aero/random.ts): the same seed gives the same scene, seeds differ, and the art stays quiet.
import assert from 'node:assert/strict';
import test from 'node:test';
import { scene } from '../src/lib/themes/aero/random.ts';

test('a seed always rolls the same scene, and different seeds roll different ones', () => {
  assert.deepEqual(scene(42), scene(42));
  const rolled = new Set(Array.from({ length: 40 }, (_, seed) => JSON.stringify(scene(seed))));
  assert.equal(rolled.size, 40);
  const glyphs = new Set(Array.from({ length: 200 }, (_, seed) => scene(seed).glyph));
  assert.ok(glyphs.size >= 6, `only ${glyphs.size} different title marks in 200 loads`);
});

test('every roll stays inside the contrast budget and on the page', () => {
  for (let seed = 0; seed < 300; seed += 1) {
    const rolled = scene(seed);
    for (const streak of rolled.streaks) assert.ok(streak.alpha <= 0.16 && streak.height <= 7, `seed ${seed}: a streak is too bright or thick`);
    for (const scrap of rolled.scraps) {
      assert.ok(scrap.alpha <= 0.2 && scrap.text.length > 0 && !scrap.text.includes('\n'), `seed ${seed}: scrap ${JSON.stringify(scrap.text)}`);
      assert.ok(scrap.top >= 0 && scrap.top <= 100 && scrap.left >= 0 && scrap.left <= 100);
    }
    // The departure board is a box: every row as wide as its rules.
    assert.equal(new Set(rolled.board.map((row) => row.length)).size, 1, `seed ${seed}: ragged board`);
  }
});

// Run with Node >=22.6: node --experimental-strip-types --test tests/icons.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {ICONS, ICON_STROKE} from '../src/lib/icons.ts';
import {navigation} from '../src/lib/workspace.ts';

test('every navigation view has its own drawn icon, never the search fallback', () => {
  for (const item of navigation) assert.ok(ICONS[item.icon], `${item.id} uses missing icon "${item.icon}"`);
});

test('navigation icons carry a blossom accent inside the 24px box; control glyphs stay plain', () => {
  const nav = new Set(navigation.map(item => item.icon));
  for (const name of nav) {
    const {accents} = ICONS[name];
    assert.ok(accents.length > 0, `${name} has no blossom accent`);
    for (const [x, y, r] of accents) {
      assert.ok(r >= 2 && r <= 4.5, `${name} accent radius ${r}`);
      assert.ok(x - r * 0.8 >= 0 && x + r * 0.8 <= 24 && y - r * 0.8 >= 0 && y + r * 0.8 <= 24, `${name} accent at ${x},${y} leaves the icon box`);
    }
  }
  for (const name of ['arrow', 'close', 'check', 'menu', 'refresh', 'external', 'download'])
    assert.deepEqual(ICONS[name].accents, [], `${name} control glyph should not be decorated`);
});

test('icons are drawn with the hairline stroke', () => {
  assert.equal(ICON_STROKE, 1.25);
});

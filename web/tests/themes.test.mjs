// Run with Node >=22.6: node --experimental-strip-types --test tests/themes.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_THEME, getTheme} from '../src/lib/themeCatalog.ts';

test('a first launch with no saved theme opens on Kitty', () => {
  assert.equal(DEFAULT_THEME, 'kitty');
  assert.equal(getTheme(null).id, 'kitty');
  assert.equal(getTheme(undefined).id, 'kitty');
});

test('an unknown saved theme falls back to Kitty, a known one is kept', () => {
  assert.equal(getTheme('no-such-theme').id, 'kitty');
  assert.equal(getTheme('harness').id, 'harness');
  assert.equal(getTheme('nord').id, 'nord');
});

test('Liquid Glass is offered as a theme with see-through glass surfaces', () => {
  const theme = getTheme('liquid-glass');
  assert.equal(theme.id, 'liquid-glass');
  assert.equal(theme.label, 'Liquid Glass');
  assert.equal(theme.motif, 'glass');
  // Glass needs much more of the backdrop to show through than the default 62% panels.
  assert.ok(theme.glass && theme.glass.panel <= 35, `panel alpha ${theme.glass?.panel}`);
  for (const role of ['panel', 'inset', 'raised']) assert.ok(theme.glass[role] > 0 && theme.glass[role] < 62, role);
});

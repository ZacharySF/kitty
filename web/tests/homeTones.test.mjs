// Colour-coded widgets: reading and changing the build's ui.home.tones list.
import assert from 'node:assert/strict';
import test from 'node:test';
import { TONES, toneColor, withTone } from '../src/lib/home/tones.ts';

test('a widget reads its own tone; none, or one this build does not know, draws nothing', () => {
  const tones = ['agenda:c2', 'note:from-a-newer-rika', 'next:c6'];
  assert.equal(toneColor(tones, 'agenda'), 'var(--c2)');
  assert.equal(toneColor(tones, 'note'), null);
  assert.equal(toneColor(tones, 'focus'), null);
  // "next" is not a prefix match for "next-up".
  assert.equal(toneColor(['next-up:c4'], 'next'), null);
});

test('a tone replaces the widget\'s own entry, clearing removes it, and other entries are kept', () => {
  const tones = ['agenda:c2', 'note:from-a-newer-rika'];
  assert.deepEqual(withTone(tones, 'agenda', 'c4'), ['note:from-a-newer-rika', 'agenda:c4']);
  assert.deepEqual(withTone(tones, 'agenda', null), ['note:from-a-newer-rika']);
  assert.deepEqual(withTone(tones, 'focus', 'c6'), [...tones, 'focus:c6']);
});

test('tone ids fit the build schema and are unique', () => {
  assert.equal(new Set(TONES.map((tone) => tone.id)).size, TONES.length);
  for (const tone of TONES) assert.match(`agenda:${tone.id}`, /^[a-z][a-z0-9-]{0,39}:[a-z][a-z0-9-]{0,19}$/);
});

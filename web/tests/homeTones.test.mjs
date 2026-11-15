// Colour-coded widgets: reading and changing the build's ui.home.tones list.
import assert from 'node:assert/strict';
import test from 'node:test';
import { TONES, toneColor, withTone } from '../src/lib/home/tones.ts';

test('a widget reads its own tone; none, or one this build does not know, draws nothing', () => {
  const tones = ['agenda:slate', 'note:from-a-newer-rika', 'next:ink'];
  assert.equal(toneColor(tones, 'agenda'), TONES.find((tone) => tone.id === 'slate').color);
  assert.equal(toneColor(tones, 'note'), null);
  assert.equal(toneColor(tones, 'focus'), null);
  // "next" is not a prefix match for "next-up".
  assert.equal(toneColor(['next-up:moss'], 'next'), null);
});

test('a tone replaces the widget\'s own entry, clearing removes it, and other entries are kept', () => {
  const tones = ['agenda:slate', 'note:from-a-newer-rika'];
  assert.deepEqual(withTone(tones, 'agenda', 'moss'), ['note:from-a-newer-rika', 'agenda:moss']);
  assert.deepEqual(withTone(tones, 'agenda', null), ['note:from-a-newer-rika']);
  assert.deepEqual(withTone(tones, 'focus', 'ink'), [...tones, 'focus:ink']);
});

test('tone ids fit the build schema and are unique', () => {
  assert.equal(new Set(TONES.map((tone) => tone.id)).size, TONES.length);
  for (const tone of TONES) assert.match(`agenda:${tone.id}`, /^[a-z][a-z0-9-]{0,39}:[a-z][a-z0-9-]{0,19}$/);
});

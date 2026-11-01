// Run with Node >=22.6: node --experimental-strip-types --test tests/calendarPalette.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { blockText, contrast, cssColor, itemPaint, calendarPaint, nextSlot, subjectOf } from '../src/lib/calendar/palette.ts';
import { THEMES } from '../src/lib/themeCatalog.ts';

const subjects = [{ id: 1, name: 'Physics', color: '#31a2bc' }, { id: 2, name: 'Math', color: '#c290ee' }];
const calendars = [
  { id: 'personal', color: 'c1', subject_id: null },
  { id: 'school', color: '#123abc' },
  { id: 'lab', color: 'c4', subject_id: 2 },
];

test('subject-linked events take the study subject colour over any picked colour', () => {
  assert.deepEqual(itemPaint({ calendar_id: 'personal', subject_id: 1, color: 'c5' }, calendars, subjects),
    { color: '#31a2bc', subject: subjects[0] });
  assert.equal(itemPaint({ calendar_id: 'lab' }, calendars, subjects).color, '#c290ee', 'a subject calendar paints its events');
  assert.equal(itemPaint({ calendar_id: 'lab', subject_id: 1 }, calendars, subjects).color, '#31a2bc', 'the event’s own subject wins');
  const recoloured = [{ ...subjects[0], color: '#aabbcc' }];
  assert.equal(itemPaint({ calendar_id: 'personal', subject_id: 1 }, calendars, recoloured).color, '#aabbcc', 'follows Study live');
});

test('unlinked events use their picked palette slot, else their calendar colour', () => {
  assert.deepEqual(itemPaint({ calendar_id: 'personal', color: 'c3' }, calendars, subjects), { color: 'var(--c3)', subject: null });
  assert.equal(itemPaint({ calendar_id: 'personal', subject_id: null, color: null }, calendars, subjects).color, 'var(--c1)');
  assert.equal(itemPaint({ calendar_id: 'school' }, calendars, subjects).color, '#123abc', 'existing hex colours still render');
  assert.equal(itemPaint({ calendar_id: 'personal', subject_id: 99 }, calendars, subjects).color, 'var(--c1)',
    'a deleted subject leaves no stale colour');
  assert.equal(itemPaint({ calendar_id: 'missing' }, calendars, subjects).color, 'var(--c1)');
  assert.equal(subjectOf({ subject_id: 2 }, subjects), subjects[1]);
  assert.equal(subjectOf({}, subjects), null);
});

test('calendars resolve to their subject or palette slot; new ones take the next slot', () => {
  assert.deepEqual(calendarPaint(calendars[2], subjects), { color: '#c290ee', subject: subjects[1] });
  assert.deepEqual(calendarPaint(calendars[0], subjects), { color: 'var(--c1)', subject: null });
  for (let slot = 1; slot <= 6; slot++) assert.equal(cssColor(`c${slot}`), `var(--c${slot})`);
  assert.equal(cssColor('#123abc'), '#123abc');
  assert.deepEqual([0, 1, 5, 6, 7].map(nextSlot), ['c1', 'c2', 'c6', 'c1', 'c2']);
});

test('block text picks the better of void and ink on palette slots and subject hex colours', () => {
  for (const theme of THEMES) {
    for (const [fill, hex] of [...theme.graph.map((value, i) => [`var(--c${i + 1})`, value]), ['#31a2bc', '#31a2bc'], ['#fafafa', '#fafafa'], ['#101010', '#101010']]) {
      const best = contrast(hex, theme.roles.void) >= contrast(hex, theme.roles.ink) ? 'var(--solid-void)' : 'var(--ink)';
      assert.equal(blockText(theme, fill), best, `${theme.id} ${fill}`);
    }
  }
});

test('no fixed #6d5dfc calendar default remains in the frontend', () => {
  const root = new URL('../src/lib/', import.meta.url);
  const files = readdirSync(root, { recursive: true }).filter((f) => /\.(svelte|ts)$/.test(f));
  for (const file of files) assert.ok(!/6d5dfc/i.test(readFileSync(new URL(file, root), 'utf8')), file);
});

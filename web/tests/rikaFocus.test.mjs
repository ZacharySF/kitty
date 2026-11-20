import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanFocus, focusContext, focusSuggestions } from '../src/lib/rika/rika.ts';

test('a message names the view and the open item', () => {
  const item = { kind: 'note_page', id: 4, title: 'Cell biology', detail: '3 blocks' };
  assert.deepEqual(focusContext('notes', item), { view: 'notes', item });
  assert.deepEqual(focusContext('calendar', null), { view: 'calendar' });
});

test("on Rika's own page and on home with nothing open, nothing is sent", () => {
  assert.equal(focusContext('rika', null), null);
  assert.equal(focusContext('today', null), null);
  assert.equal(focusContext('', null), null);
  assert.deepEqual(focusContext('today', { kind: 'goal', id: 2, title: 'Run 10k' }), { view: 'today', item: { kind: 'goal', id: 2, title: 'Run 10k' } });
});

test('the item is cut to what the backend accepts, so a send never fails on it', () => {
  const long = cleanFocus({ kind: 'note_page', title: `${'é'.repeat(250)}`, detail: 'a\nb\u0001c  d' });
  assert.equal(Array.from(long.title).length, 200);
  assert.ok(long.title.endsWith('…'));
  assert.equal(long.detail, 'a b c d');
  const emoji = cleanFocus({ kind: 'deck', title: '🧬'.repeat(300) });
  assert.equal(Array.from(emoji.title).length, 200);
  assert.ok(!/[\ud800-\udbff](?![\udc00-\udfff])/.test(emoji.title), 'no lone surrogate');
});

test('a bad kind, an empty title or a strange id never reaches the backend', () => {
  assert.equal(cleanFocus({ kind: 'Note Page', title: 'x' }), null);
  assert.equal(cleanFocus({ kind: 'note_page', title: '   ' }), null);
  assert.equal(cleanFocus(null), null);
  assert.deepEqual(cleanFocus({ kind: 'job', id: 1.5, title: 'Engineer' }), { kind: 'job', title: 'Engineer' });
  assert.deepEqual(cleanFocus({ kind: 'job', id: 'a\tb', title: 'Engineer' }), { kind: 'job', title: 'Engineer' });
  assert.deepEqual(cleanFocus({ kind: 'job', id: ' j-7 ', title: 'Engineer' }), { kind: 'job', id: 'j-7', title: 'Engineer' });
});

test('each kind of item offers two or three things to ask', () => {
  for (const kind of ['note_page', 'calendar_range', 'calendar_event', 'deck', 'study_subject', 'math_topic', 'job', 'application',
    'resume_document', 'resume_version', 'profile_section', 'reminder', 'document', 'opportunity', 'map', 'goal', 'brain_node', 'quick_note']) {
    const asks = focusSuggestions(kind);
    assert.ok(asks.length >= 2 && asks.length <= 3, kind);
  }
  assert.ok(focusSuggestions('deck').includes('Quiz me on this deck'));
  assert.ok(focusSuggestions('job').includes('How well do I match this job?'));
  assert.deepEqual(focusSuggestions('unknown'), []);
  assert.deepEqual(focusSuggestions(null), []);
});

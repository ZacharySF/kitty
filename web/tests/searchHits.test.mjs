// GET /api/search hits in the Ctrl K palette: labels, icons, marked runs and where a hit opens.
// node --test tests/searchHits.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { hitIcon, hitLabel, hitTarget, oneLine, plain, searchPath } from '../src/lib/searchHits.ts';

const hit = (kind, id, view = 'notes') => ({ kind, id, title: 'T', title_marks: [['T', false]], snippet: [], view, match: 'word' });

test('the query is encoded and the limit sent', () => {
  assert.equal(searchPath('café & co', 6), '/search?q=caf%C3%A9%20%26%20co&limit=6');
});

test('marked runs keep their text; snippets fold to one line', () => {
  assert.equal(plain([['Mito', true], ['chondria', false]]), 'Mitochondria');
  assert.deepEqual(oneLine([['a\n\n b', false], ['ATP', true], ['', false]]), [['a b', false], ['ATP', true]]);
});

test('every kind has a label and an icon', () => {
  assert.equal(hitLabel(hit('note_page', 1)), 'Note');
  assert.equal(hitLabel(hit('flashcard_note', 1)), 'Card');
  assert.equal(hitIcon(hit('job', 1, 'jobs')), 'jobs');
  assert.equal(hitLabel(hit('unknown', 1)), 'Result');
});

test('a hit opens its record: a note page by link, a job in its drawer, a card through the session', () => {
  assert.deepEqual(hitTarget(hit('note_page', 12)), { hash: '#notes/12' });
  assert.deepEqual(hitTarget(hit('job', 7, 'jobs')), { job: 7 });
  assert.deepEqual(hitTarget(hit('tag', 'abc', 'settings')), { hash: '#settings/tags' });
  assert.deepEqual(hitTarget(hit('flashcard_note', 3, 'flashcards')),
    { view: 'flashcards', storage: ['kitty.flashcards.open-note', '3'] });
  assert.deepEqual(hitTarget(hit('calendar_event', 'evt', 'calendar')), { view: 'calendar' });
});

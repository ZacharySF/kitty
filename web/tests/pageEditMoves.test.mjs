// Page edit mode: reading and changing the build's ui.pages.moves list.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { MAX_MOVES, MOVE_PATTERN, formatMove, moveOf, movesFor, parseMove, snap, withMove, withoutMove, withoutView } from '../src/lib/pageEdit/moves.ts';

const path = 'section.opportunities > section[aria-label="Job results"]';

test('an entry reads back as it was written; an empty size is unchanged', () => {
  const move = { view: 'jobs', dx: 16, dy: -8, w: 480, h: null, path };
  assert.equal(formatMove(move), `jobs|16,-8,480,|${path}`);
  assert.deepEqual(parseMove(formatMove(move)), move);
  assert.deepEqual(parseMove('agent-skills|0,0,,96|nav'), { view: 'agent-skills', dx: 0, dy: 0, w: null, h: 96, path: 'nav' });
  // A path may hold a bar of its own only inside a label the path never writes; the first two split.
  for (const bad of ['', 'jobs|1,2|nav', 'Jobs|0,0,,|nav', 'jobs|a,0,,|nav', 'jobs|0,0,,|', 'jobs|0,0,,|caf\u00e9', 'jobs|1234567,0,,|nav'])
    assert.equal(parseMove(bad), null, bad);
});

test('what is written always fits the build schema', () => {
  const schema = JSON.parse(readFileSync(new URL('../../src/kitty/core/builds/kitty-build.schema.json', import.meta.url), 'utf8'));
  const moves = schema.properties.ui.properties.pages.properties.moves;
  assert.equal(moves.items.pattern, MOVE_PATTERN.source);
  assert.equal(moves.maxItems, MAX_MOVES);
  for (const move of [
    { view: 'jobs', dx: 3.6, dy: -0.2, w: 12.4, h: null, path },
    { view: 'calendar', dx: 1e9, dy: -1e9, w: 1e9, h: 1e9, path: 'nav' },
  ]) assert.match(formatMove(move), MOVE_PATTERN);
  assert.equal(formatMove({ view: 'jobs', dx: 3.6, dy: -0.2, w: 12.4, h: null, path: 'nav' }), 'jobs|4,0,40,|nav');
});

test('a move replaces its block\'s earlier one and leaves the rest; one that changes nothing is dropped', () => {
  const entries = ['jobs|8,8,,|nav', `jobs|0,16,,|${path}`, 'calendar|8,8,,|nav', 'kept-from-a-newer-rika'];
  assert.deepEqual(withMove(entries, { view: 'jobs', dx: 24, dy: 0, w: null, h: 200, path: 'nav' }),
    [`jobs|0,16,,|${path}`, 'calendar|8,8,,|nav', 'kept-from-a-newer-rika', 'jobs|24,0,,200|nav']);
  assert.deepEqual(withMove(entries, { view: 'jobs', dx: 0, dy: 0, w: null, h: null, path: 'nav' }), entries.slice(1));
  assert.deepEqual(withoutMove(entries, 'jobs', 'nav'), entries.slice(1));
  // "nav" is not a suffix match for another block's path.
  assert.deepEqual(withoutMove(['jobs|8,8,,|header > nav'], 'jobs', 'nav'), ['jobs|8,8,,|header > nav']);
  assert.deepEqual(withoutView(entries, 'jobs'), ['calendar|8,8,,|nav', 'kept-from-a-newer-rika']);
  // A path the schema would refuse is never written.
  assert.deepEqual(withMove([], { view: 'jobs', dx: 8, dy: 0, w: null, h: null, path: 'caf\u00e9' }), []);
});

test('a view reads its own moves; a block without one starts from nothing', () => {
  const entries = ['jobs|8,8,,|nav', 'calendar|16,0,320,|nav', 'not an entry'];
  assert.deepEqual(movesFor(entries, 'calendar'), [{ view: 'calendar', dx: 16, dy: 0, w: 320, h: null, path: 'nav' }]);
  assert.deepEqual(movesFor(entries, 'notes'), []);
  assert.deepEqual(moveOf(entries, 'jobs', 'nav'), { view: 'jobs', dx: 8, dy: 8, w: null, h: null, path: 'nav' });
  assert.deepEqual(moveOf(entries, 'jobs', 'header'), { view: 'jobs', dx: 0, dy: 0, w: null, h: null, path: 'header' });
});

test('the list never outgrows the build; drags land on the 8px grid unless Alt is held', () => {
  let entries = [];
  for (let index = 0; index < MAX_MOVES + 5; index += 1) entries = withMove(entries, { view: 'jobs', dx: 8, dy: 0, w: null, h: null, path: `nav:nth-of-type(${index + 1})` });
  assert.equal(entries.length, MAX_MOVES);
  assert.equal(entries.at(-1), `jobs|8,0,,|nav:nth-of-type(${MAX_MOVES + 5})`);
  assert.equal(snap(13), 16);
  assert.equal(snap(-11), -8);
  assert.equal(snap(13.4, true), 13);
});

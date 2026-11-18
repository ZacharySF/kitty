// Run with Node >=22.6: node --experimental-strip-types --test tests/calendarDelete.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {deleteBody, deleteTarget, eventKey, lockedCalendars} from '../src/lib/calendar/eventDelete.ts';
import {shortcutsFor} from '../src/lib/workspace.ts';

const event = (extra = {}) => ({id: 'e1', revision: 3, title: 'Gym', calendar_id: 'personal', occurrence: '2026-11-18T15:00:00+00:00', rrule: '', ...extra});
const calendars = [{id: 'personal', read_only: 0}, {id: 'outlook', read_only: 1}, {id: 'old'}];

test('each occurrence of a series has its own key', () => {
  const second = event({occurrence: '2026-11-19T15:00:00+00:00'});
  assert.notEqual(eventKey(event()), eventKey(second));
  assert.equal(eventKey(event()), eventKey(event({title: 'Renamed', revision: 4})));
});

test('a single event is deleted whole; a repeating one loses only the chosen occurrence', () => {
  assert.deepEqual(deleteBody(event()), {revision: 3, scope: 'all', occurrence: '2026-11-18T15:00:00+00:00', changes: {}, delete: true});
  assert.equal(deleteBody(event({rrule: 'FREQ=WEEKLY'})).scope, 'this');
});

test('calendars imported from an outside account are locked', () => {
  assert.deepEqual([...lockedCalendars(calendars)], ['outlook']);
});

test('Delete takes the focused event before the selected one, and never a locked or missing one', () => {
  const mine = event();
  const other = event({id: 'e2', title: 'Lunch'});
  const theirs = event({id: 'e3', title: 'Design review', calendar_id: 'outlook'});
  const all = [mine, other, theirs];
  const locked = lockedCalendars(calendars);
  assert.equal(deleteTarget(all, locked, eventKey(other), eventKey(mine)), other, 'the focused event wins');
  assert.equal(deleteTarget(all, locked, undefined, eventKey(mine)), mine, 'else the selected one');
  assert.equal(deleteTarget(all, locked, undefined, null), null, 'nothing selected');
  assert.equal(deleteTarget(all, locked, undefined, eventKey(theirs)), null, 'a read-only event is never deleted');
  assert.equal(deleteTarget(all, locked, eventKey(theirs), eventKey(mine)), null, 'a focused read-only event does not fall through to the selection');
  assert.equal(deleteTarget([other], locked, undefined, eventKey(mine)), null, 'the selected event is gone');
});

test('Keyboard shortcuts lists the calendar delete keys only with the calendar', () => {
  const rows = (enabled) => shortcutsFor(enabled).find((group) => group.title === 'Calendar')?.shortcuts ?? [];
  assert.ok(rows(['workspace']).some(([keys, what]) => keys === 'Delete / Backspace' && /delete/i.test(what)));
  assert.deepEqual(rows(['applyai']), []);
  // No other group claims the same keys.
  const claimed = shortcutsFor(['workspace', 'applyai', 'brain', 'voice']).filter((group) =>
    group.shortcuts.some(([keys]) => /\b(Delete|Backspace)\b/.test(keys)));
  assert.deepEqual(claimed.map((group) => group.title), ['Calendar']);
});

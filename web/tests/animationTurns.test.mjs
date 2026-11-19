// Animations: which way a page turns (animations/turns.ts).
import assert from 'node:assert/strict';
import test from 'node:test';
import { turnOf, viewOf } from '../src/lib/animations/turns.ts';

test('a later section arrives from the right, an earlier one from the left', () => {
  assert.equal(turnOf('calendar', 'jobs', false), 'forward');
  assert.equal(turnOf('jobs', 'calendar', false), 'back');
});

test('pages of one section step sideways in their own order', () => {
  assert.equal(turnOf('jobs', 'queue', false), 'next');
  assert.equal(turnOf('queue', 'jobs', false), 'prev');
});

test('home steps back, and anything opened from a widget or a row grows out of it', () => {
  assert.equal(turnOf('jobs', 'today', false), 'home');
  assert.equal(turnOf('jobs', 'today', true), 'home');
  assert.equal(turnOf('today', 'calendar', true), 'zoom');
});

test('the view is the first part of the address', () => {
  assert.equal(viewOf('#notes/12'), 'notes');
  assert.equal(viewOf('#settings/modules'), 'settings');
  assert.equal(viewOf(''), '');
});

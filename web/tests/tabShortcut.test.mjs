// Ctrl+W closes the active workspace tab, even from terminals and text fields.
// node --experimental-strip-types --test tests/tabShortcut.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { isCloseTabShortcut } from '../src/lib/tabShortcut.ts';

const key = (overrides = {}) => ({ key: 'w', ctrlKey: true, metaKey: false, altKey: false, shiftKey: false, defaultPrevented: false, ...overrides });

test('Ctrl+W and Cmd+W close the active tab', () => {
  assert.equal(isCloseTabShortcut(key(), false), true);
  assert.equal(isCloseTabShortcut(key({ key: 'W' }), false), true);
  assert.equal(isCloseTabShortcut(key({ ctrlKey: false, metaKey: true }), false), true);
});

test('other chords, handled events and open dialogs are left alone', () => {
  assert.equal(isCloseTabShortcut(key({ ctrlKey: false }), false), false);
  assert.equal(isCloseTabShortcut(key({ shiftKey: true }), false), false);
  assert.equal(isCloseTabShortcut(key({ altKey: true }), false), false);
  assert.equal(isCloseTabShortcut(key({ key: 'q' }), false), false);
  assert.equal(isCloseTabShortcut(key({ defaultPrevented: true }), false), false);
  assert.equal(isCloseTabShortcut(key(), true), false);
});

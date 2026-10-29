// Q3/Q8 dock state and Q1 terminal view helpers (docs/terminal-dock-design.md).
// node --experimental-strip-types --test tests/dock.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DOCK_MIN_HEIGHT, MAIN_MIN_HEIGHT, DOCK_STORAGE_KEY,
  clampDockHeight, firstDockHeight, readDock, writeDock,
} from '../src/lib/dock.ts';
import { terminalSocketUrl, terminalTheme } from '../src/lib/terminal.ts';

function memory(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: key => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    values,
  };
}

test('a fresh dock opens on the terminal with no remembered height', () => {
  assert.deepEqual(readDock(memory()), { mode: 'terminal', height: null, shown: 'terminal' });
});

test('the dock remembers its state, height and the view the handle brings back', () => {
  const storage = memory();
  writeDock(storage, { mode: 'hidden', height: 287, shown: 'activity' });
  assert.deepEqual(readDock(storage), { mode: 'hidden', height: 287, shown: 'activity' });
  writeDock(storage, { mode: 'activity', height: 300, shown: 'activity' });
  assert.deepEqual(readDock(storage), { mode: 'activity', height: 300, shown: 'activity' });
  // A visible dock always restores to what it shows.
  storage.setItem(DOCK_STORAGE_KEY, '{"mode":"terminal","height":300,"shown":"activity"}');
  assert.equal(readDock(storage).shown, 'terminal');
});

test('corrupt or foreign saved dock state falls back instead of failing', () => {
  for (const saved of ['not json', '[]', 'null', '{"mode":"drawer","height":"tall"}', '{"mode":"terminal","height":-5}', '{"mode":"hidden","shown":"hidden"}']) {
    const state = readDock(memory({ [DOCK_STORAGE_KEY]: saved }));
    assert.ok(['terminal', 'activity', 'hidden'].includes(state.mode), saved);
    assert.ok(['terminal', 'activity'].includes(state.shown), saved);
    assert.ok(state.height === null || state.height > 0, saved);
  }
  assert.deepEqual(readDock(memory({ [DOCK_STORAGE_KEY]: '{"mode":"activity","height":-5}' })), { mode: 'activity', height: null, shown: 'activity' });
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.deepEqual(readDock(broken), { mode: 'terminal', height: null, shown: 'terminal' });
  assert.doesNotThrow(() => writeDock(broken, { mode: 'hidden', height: 200, shown: 'terminal' }));
});

test('the first dock size is the empty area below the main panel', () => {
  // Window 900 high, main panel content ends at 540: the dock fills the 360 below it.
  assert.equal(firstDockHeight(900, 540), 360);
  // Content taller than the window leaves no empty area: the dock still opens usable.
  assert.equal(firstDockHeight(900, 1400), DOCK_MIN_HEIGHT);
  // A nearly empty page cannot push the main panel out of the window.
  assert.equal(firstDockHeight(900, 40), 900 - MAIN_MIN_HEIGHT);
});

test('dragging keeps both the dock and the main panel usable', () => {
  assert.equal(clampDockHeight(10, 900), DOCK_MIN_HEIGHT);
  assert.equal(clampDockHeight(5000, 900), 900 - MAIN_MIN_HEIGHT);
  assert.equal(clampDockHeight(412.6, 900), 413);
  // A window too short for both favours the dock's minimum.
  assert.equal(clampDockHeight(400, 200), DOCK_MIN_HEIGHT);
});

test('the terminal socket stays on the page origin with ws/wss matching http/https', () => {
  assert.equal(
    terminalSocketUrl({ protocol: 'http:', host: '127.0.0.1:8765' }, '/api/terminal/socket', 'ab12', 120, 30),
    'ws://127.0.0.1:8765/api/terminal/socket?token=ab12&cols=120&rows=30');
  assert.equal(
    terminalSocketUrl({ protocol: 'https:', host: 'kitty.local' }, '/api/terminal/socket', 'a b', 80, 24),
    'wss://kitty.local/api/terminal/socket?token=a+b&cols=80&rows=24');
});

test('the terminal is transparent and drawn in the given theme roles', () => {
  const roles = {
    void: '#141414', panel: '#1c1c1c', inset: '#111111', raised: '#262626', line: '#434343', lineHot: '#8d8d8d',
    ink: '#e6e6e6', mute: '#b4b4b4', dim: '#8d8d8d', structure: '#deb2c3', signal: '#e4bdcc', go: '#a8d5b5',
    reference: '#9cc7f0', caution: '#e8c872', alternate: '#c6b5f0', danger: '#ee928c',
  };
  const theme = terminalTheme(roles);
  assert.equal(theme.background, '#00000000');
  assert.equal(theme.foreground, roles.ink);
  assert.equal(theme.cursor, roles.structure);
  assert.equal(theme.red, roles.danger);
  assert.equal(theme.green, roles.go);
  assert.equal(theme.yellow, roles.caution);
  assert.equal(theme.blue, roles.reference);
  assert.equal(theme.magenta, roles.structure);
  for (const key of ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white',
    'brightBlack', 'brightRed', 'brightGreen', 'brightYellow', 'brightBlue', 'brightMagenta', 'brightCyan', 'brightWhite'])
    assert.match(theme[key], /^#[0-9a-f]{6}$/, key);
});

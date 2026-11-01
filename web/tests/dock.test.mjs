// Q3/Q8 dock state and Q1 terminal view helpers (docs/terminal-dock-design.md).
// node --experimental-strip-types --test tests/dock.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DOCK_MARGIN, DOCK_MIN_HEIGHT, DOCK_MIN_WIDTH, DOCK_STEPS, DOCK_STORAGE_KEY, MAIN_MIN_HEIGHT,
  clampDockOffset, clampDockSize, defaultDockSize, dockedHeight, readDock, writeDock,
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

const CORNER = { right: 16, bottom: 16 };
const FRESH = { mode: 'terminal', shown: 'terminal', float: false, step: 'm', size: null, offset: CORNER };

test('a fresh dock opens on the terminal docked at the bottom, medium height', () => {
  assert.deepEqual(readDock(memory()), FRESH);
});

test('the dock remembers its state, float, height step, card size, position and the view the handle brings back', () => {
  const storage = memory();
  const state = { mode: 'hidden', shown: 'activity', float: true, step: 'l', size: { width: 600, height: 380 }, offset: { right: 40, bottom: 90 } };
  writeDock(storage, state);
  assert.deepEqual(readDock(storage), state);
  // A visible dock always restores to what it shows.
  storage.setItem(DOCK_STORAGE_KEY, '{"mode":"terminal","shown":"activity"}');
  assert.equal(readDock(storage).shown, 'terminal');
});

test('v0.3.22 floating-only state loads docked, keeping its card size and position for floating', () => {
  const legacy = readDock(memory({ [DOCK_STORAGE_KEY]: '{"mode":"activity","shown":"activity","size":{"width":600,"height":380},"offset":{"right":40,"bottom":90}}' }));
  assert.deepEqual(legacy, { mode: 'activity', shown: 'activity', float: false, step: 'm', size: { width: 600, height: 380 }, offset: { right: 40, bottom: 90 } });
  // Older docked-panel state: a dragged height is dropped; an explicit float stays.
  const old = readDock(memory({ [DOCK_STORAGE_KEY]: '{"mode":"hidden","height":420,"shown":"activity","float":true}' }));
  assert.deepEqual(old, { mode: 'hidden', shown: 'activity', float: true, step: 'm', size: null, offset: CORNER });
  assert.ok(!('height' in old));
});

test('the docked height is a fixed step of the window, never dragged', () => {
  assert.deepEqual(DOCK_STEPS, ['s', 'm', 'l']);
  assert.equal(dockedHeight('m', 1000), 400);
  assert.equal(dockedHeight('s', 1000), 250);
  assert.equal(dockedHeight('l', 1000), 600);
  // Short windows keep the dock usable and the page above it.
  assert.equal(dockedHeight('s', 500), DOCK_MIN_HEIGHT);
  assert.equal(dockedHeight('l', 600), 600 - MAIN_MIN_HEIGHT);
  assert.equal(dockedHeight('m', 500), 500 - MAIN_MIN_HEIGHT);
  assert.equal(dockedHeight('l', 300), DOCK_MIN_HEIGHT);
});

test('corrupt or foreign saved dock state falls back instead of failing', () => {
  for (const saved of ['not json', '[]', 'null', '{"mode":"drawer","size":"tall"}', '{"mode":"terminal","size":{"width":-5,"height":300}}',
    '{"mode":"hidden","shown":"hidden"}', '{"mode":"terminal","offset":{"right":"x","bottom":-3}}', '{"float":"yes","step":"xl"}']) {
    const state = readDock(memory({ [DOCK_STORAGE_KEY]: saved }));
    assert.ok(['terminal', 'activity', 'hidden'].includes(state.mode), saved);
    assert.ok(['terminal', 'activity'].includes(state.shown), saved);
    assert.equal(state.size, null, saved);
    assert.deepEqual(state.offset, CORNER, saved);
    assert.equal(state.float, false, saved);
    assert.equal(state.step, 'm', saved);
  }
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.deepEqual(readDock(broken), FRESH);
  assert.doesNotThrow(() => writeDock(broken, FRESH));
});

test('the default card is min(720px, 92vw) wide and min(440px, 60vh) tall', () => {
  assert.deepEqual(defaultDockSize(1600, 1000), { width: 720, height: 440 });
  assert.deepEqual(defaultDockSize(600, 500), { width: 552, height: 300 });
});

test('resizing keeps the card usable and inside the window', () => {
  assert.deepEqual(clampDockSize({ width: 10, height: 10 }, 1000, 800), { width: DOCK_MIN_WIDTH, height: DOCK_MIN_HEIGHT });
  assert.deepEqual(clampDockSize({ width: 5000, height: 5000 }, 1000, 800), { width: 1000 - 2 * DOCK_MARGIN, height: 800 - 2 * DOCK_MARGIN });
  assert.deepEqual(clampDockSize({ width: 600.4, height: 412.6 }, 1000, 800), { width: 600, height: 413 });
  // A window too small for the minimum favours the minimum.
  assert.deepEqual(clampDockSize({ width: 400, height: 400 }, 200, 100), { width: DOCK_MIN_WIDTH, height: DOCK_MIN_HEIGHT });
});

test('moving keeps the whole card on screen', () => {
  const size = { width: 600, height: 400 };
  assert.deepEqual(clampDockOffset({ right: -50, bottom: -50 }, size, 1000, 800), { right: DOCK_MARGIN, bottom: DOCK_MARGIN });
  assert.deepEqual(clampDockOffset({ right: 900, bottom: 900 }, size, 1000, 800), { right: 1000 - 600 - DOCK_MARGIN, bottom: 800 - 400 - DOCK_MARGIN });
  assert.deepEqual(clampDockOffset({ right: 120.6, bottom: 33.2 }, size, 1000, 800), { right: 121, bottom: 33 });
  // A card wider than the window stays pinned to the margin.
  assert.deepEqual(clampDockOffset({ right: 200, bottom: 200 }, size, 500, 300), { right: DOCK_MARGIN, bottom: DOCK_MARGIN });
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

// Q3/Q8 dock sizing and Q1 terminal view helpers (docs/terminal-dock-design.md). The dock's
// state lives in the build (`ui.dock`); its legacy localStorage move is in builds.test.mjs.
// node --experimental-strip-types --test tests/dock.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DOCK_MARGIN, DOCK_MIN_HEIGHT, DOCK_MIN_WIDTH, DOCK_STEPS, MAIN_MIN_HEIGHT,
  clampDockOffset, clampDockSize, defaultDockSize, dockedHeight,
} from '../src/lib/dock.ts';
import {
  terminalSocketUrl, terminalTheme, ternBlockState, ternEventLabel, ternPaneLabel, ternPanes, ternPluginState, ternTarget,
} from '../src/lib/terminal.ts';

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

test('the dock acts only on panes of kitty!\'s own Tern session, with their live state', () => {
  const block = (id, extra = {}) => ({ id, title: '', cwd: '/w', program: '/run/current-system/sw/bin/bash', cols: 80, rows: 24,
    focused: false, live: true, exited: null, working: false, ...extra });
  const status = { sessions: [
    { id: 1, name: 'other', shown: false, tabs: [{ id: 2, number: 1, name: null, shown: true, blocks: [block(3)] }] },
    { id: 4, name: 'kitty', shown: true, tabs: [
      { id: 5, number: 1, name: null, shown: false, blocks: [block(6, { title: 'echo kitty-42' })] },
      { id: 7, number: 2, name: null, shown: true, blocks: [block(8, { exited: 3, focused: true }), block(9, { working: true, program: null })] },
    ] },
  ] };
  const panes = ternPanes(status);
  assert.deepEqual(panes.map((pane) => [pane.id, pane.tab]), [[6, 1], [8, 2], [9, 2]]);
  assert.deepEqual(panes.map(ternPaneLabel), ['echo kitty-42', 'bash', 'pane 9']);
  assert.deepEqual(panes.map((pane) => ternBlockState(pane).label), ['idle', 'exited 3', 'working']);
  assert.equal(ternBlockState(block(1, { exited: 0, working: true })).state, 'exited');
  assert.equal(ternEventLabel({ event: 'pane_exited', pane: 8, status: 3, at: '' }), 'pane 8 exited 3');
  assert.equal(ternEventLabel({ event: 'pane_exited', pane: 8, status: null, at: '' }), 'pane 8 exited');
  assert.equal(ternEventLabel({ event: 'pane_spawned', pane: 9, status: null, at: '' }), 'pane 9 started');
});

test('the Run target is the chosen pane, else the focused pane of the shown tab', () => {
  const pane = (id, tab, shown, focused) => ({ id, tab, shown, focused, title: '', cwd: '/w', program: '/bin/bash', cols: 80, rows: 24,
    live: true, exited: null, working: false });
  // Tern marks one focused block per tab; only the shown tab's is the one on screen.
  const panes = [pane(3, 1, false, true), pane(9, 2, true, true), pane(10, 2, true, false)];
  assert.equal(ternTarget(panes, null).id, 9);
  assert.equal(ternTarget(panes, 10).id, 10);
  assert.equal(ternTarget(panes, 99).id, 9, 'a chosen pane that closed falls back');
  assert.equal(ternTarget([pane(3, 1, false, false), pane(4, 2, false, false)], null).id, 3);
  assert.equal(ternTarget([], null), null);
  const status = { sessions: [{ id: 1, name: 'kitty', shown: true, tabs: [
    { id: 2, number: 1, name: null, shown: false, blocks: [pane(3)] },
    { id: 5, number: 2, name: null, shown: true, blocks: [pane(6)] },
  ] }] };
  assert.deepEqual(ternPanes(status).map((item) => [item.id, item.shown]), [[3, false], [6, true]]);
});

test('the Tern plugin panel offers install, update or nothing, and shows problems', () => {
  const plugin = (fields) => ({
    available: true, version: '0.1.0', dir: '/w/.private/tern/plugin', installed: false, installed_version: null,
    current: false, linked_elsewhere: null, tern_status: null, problems: [], enabled: true, ...fields,
  });
  assert.deepEqual(ternPluginState(plugin({})), { label: 'Not installed in Tern', action: 'install', problem: null });
  assert.equal(ternPluginState(plugin({ linked_elsewhere: '/other/.private/tern/plugin' })).label,
    'Tern uses the plugin of another kitty! workspace');
  const installed = plugin({ installed: true, installed_version: '0.1.0', current: true, tern_status: 'ready' });
  assert.deepEqual(ternPluginState(installed), { label: 'Installed (0.1.0)', action: null, problem: null });
  assert.equal(ternPluginState({ ...installed, current: false }).action, 'update');
  assert.equal(ternPluginState({ ...installed, tern_status: 'failed: host.luau:1: boom' }).problem,
    'Tern reports: failed: host.luau:1: boom');
  assert.equal(ternPluginState(plugin({ problems: ['/x: duplicate id kitty'] })).problem, '/x: duplicate id kitty');
  assert.equal(ternPluginState(plugin({ enabled: false })).action, null);
  assert.equal(ternPluginState(plugin({ available: false })).action, null);
});

// Q3/Q8 dock sizing and Q1 terminal view helpers (docs/terminal-dock-design.md). The dock's
// state lives in the build (`ui.dock`); its legacy localStorage move is in builds.test.mjs.
// node --experimental-strip-types --test tests/dock.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { DOCK_MIN_HEIGHT, MAIN_MIN_HEIGHT, clampDockHeight, firstDockHeight } from '../src/lib/dock.ts';
import {
  terminalSocketUrl, terminalTheme, ternBlockState, ternEventLabel, ternPaneLabel, ternPanes, ternPluginState, ternTarget,
} from '../src/lib/terminal.ts';

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

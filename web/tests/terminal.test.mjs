// Q1 terminal view helpers (docs/terminal-dock-design.md): the herdr view's socket and theme.
import test from 'node:test';
import assert from 'node:assert/strict';
import { terminalSocketUrl, terminalTheme } from '../src/lib/terminal.ts';

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

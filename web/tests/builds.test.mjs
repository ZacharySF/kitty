// Q8/Q9 builds: the pure helpers behind the live build store (src/lib/buildCore.ts).
// node --test tests/builds.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_BUILD, LEGACY_KEYS, collectLegacy, deepMerge, describeValue, hasCustomStyle, hasSafeParam, isSafeShortcut,
  lineOffset, problemLines, resolveSafe, scopedCss, tokenPlan, tokenProperty, withSafeParam,
} from '../src/lib/buildCore.ts';

const storage = (values) => ({ getItem: (key) => (key in values ? values[key] : null) });

test('every legacy key is listed for removal, including the delete-only tree marker', () => {
  assert.deepEqual([...LEGACY_KEYS].sort(), [
    'kitty.atlas.hidden', 'kitty.atlas.physics', 'kitty.coding-agent', 'kitty.dock', 'kitty.graph.default-layout',
    'kitty.graph.look', 'kitty.graph.tree-default.v2', 'kitty.notes.pen-only', 'kitty.theme',
  ]);
  // Device and window state stays local and is never collected.
  const { keys } = collectLegacy(storage({ 'kitty.notes.tabs': '[1]', 'kitty-calendar-notifications': 'true', 'kitty.ink.recent': '[]' }));
  assert.deepEqual(keys, []);
});

test('legacy preferences become one patch for the build', () => {
  const { patch, keys } = collectLegacy(storage({
    'kitty.theme': 'dracula',
    'kitty.dock': JSON.stringify({ mode: 'hidden', height: 287.4, shown: 'activity' }),
    'kitty.graph.look': '3d',
    'kitty.graph.default-layout': 'force',
    'kitty.graph.tree-default.v2': '1',
    'kitty.atlas.physics': JSON.stringify({ repulsion: 900, spring: 0.3, damping: 'x' }),
    'kitty.atlas.hidden': JSON.stringify(['calendar', 7, 'calendar', 'Bad Id', 'jobs']),
    'kitty.notes.pen-only': 'true',
    'kitty.coding-agent': 'codex',
  }));
  assert.equal(keys.length, LEGACY_KEYS.length);
  assert.deepEqual(patch, {
    theme: { base: 'dracula' },
    ui: {
      dock: { mode: 'hidden', height: 287, shown: 'activity' },
      graph: { look: '3d', layout: 'force', physics: { repulsion: 300, spring: 0.3 } },
      atlas: { hidden: ['calendar', 'jobs'] },
      notes: { pen_only: true },
      terminal: { coding_agent: 'codex' },
    },
  });
});

test('values the build would refuse are left out, and a stored Sphere from before tree-default.v2 is not a choice', () => {
  const { patch, keys } = collectLegacy(storage({
    'kitty.theme': 'Not A Theme!',
    'kitty.dock': 'not json',
    'kitty.graph.look': 'hologram',
    'kitty.graph.default-layout': 'sphere',
    'kitty.atlas.hidden': '{"calendar":true}',
    'kitty.notes.pen-only': 'yes',
    'kitty.coding-agent': 'vim',
  }));
  assert.deepEqual(patch, {});
  assert.equal(keys.length, 7);
  assert.deepEqual(collectLegacy(storage({ 'kitty.dock': '{"mode":"terminal","height":40}' })).patch,
    { ui: { dock: { mode: 'terminal', height: null, shown: 'terminal' } } });
  const denied = { getItem() { throw new Error('denied'); } };
  assert.deepEqual(collectLegacy(denied), { patch: {}, keys: [] });
});

test('deep merge replaces arrays and values, merges objects and never mutates its inputs', () => {
  const before = structuredClone(DEFAULT_BUILD);
  const merged = deepMerge(DEFAULT_BUILD, { ui: { dock: { mode: 'hidden' }, atlas: { hidden: ['notes'] } }, theme: { base: 'nord' } });
  assert.deepEqual(DEFAULT_BUILD, before);
  assert.equal(merged.ui.dock.mode, 'hidden');
  assert.equal(merged.ui.dock.shown, 'terminal');
  assert.deepEqual(merged.ui.atlas.hidden, ['notes']);
  assert.deepEqual(deepMerge(merged, { ui: { atlas: { hidden: [] } } }).ui.atlas.hidden, []);
  assert.equal(merged.theme.base, 'nord');
  assert.equal(merged.theme.css, '');
  assert.equal(deepMerge({ height: 300 }, { height: null }).height, null);
  assert.equal(deepMerge({ a: 1 }, { a: undefined }).a, 1);
});

test('tokens map to Bracket roles, graph colours, the shadow tint and custom properties', () => {
  assert.equal(tokenProperty('graph1'), '--c1');
  assert.equal(tokenProperty('graph6'), '--c6');
  assert.equal(tokenProperty('graph7'), '--graph7');
  assert.equal(tokenProperty('shadow'), '--shadow-tint');
  assert.equal(tokenProperty('radius-card'), '--radius-card');
  const plan = tokenPlan({ ink: '#ffffff', lineHot: '#ff00ff', graph2: '#123456', shadow: '#000000', accent: 'hotpink' });
  assert.deepEqual(plan.roles, { ink: '#ffffff', lineHot: '#ff00ff' });
  assert.deepEqual(plan.set, { '--c2': '#123456', '--shadow-tint': '#000000', '--accent': 'hotpink' });
  assert.deepEqual(plan.remove, []);
});

test('properties a removed token set are removed; those still set are not', () => {
  const plan = tokenPlan({ accent: 'red' }, ['--accent', '--radius', '--c3']);
  assert.deepEqual(plan.remove, ['--radius', '--c3']);
  assert.deepEqual(tokenPlan({}, ['--accent']).remove, ['--accent']);
});

test('custom CSS is injected only inside @scope (#app)', () => {
  assert.equal(scopedCss('.card { color: red }'), '@scope (#app) {\n.card { color: red }\n}');
  assert.equal(scopedCss('  \n '), '');
  assert.equal(hasCustomStyle({ tokens: {}, css: '' }), false);
  assert.equal(hasCustomStyle({ tokens: {}, css: ' * {} ' }), true);
  assert.equal(hasCustomStyle({ tokens: { ink: '#fff' }, css: '' }), true);
});

test('problems mark their lines, clamped to the text, and the caret goes to line and column', () => {
  const problems = [
    { message: 'Expected ","', line: 3, column: 5, path: '', severity: 'error' },
    { message: 'Unknown key', line: 3, column: 1, path: 'theme.x', severity: 'warning' },
    { message: 'Beyond the end', line: 40, column: 1, path: '', severity: 'error' },
    { message: 'No line', line: 0, column: 0, path: '', severity: 'error' },
  ];
  const marks = problemLines(problems, 5);
  assert.deepEqual([...marks.keys()].sort(), [1, 3, 5]);
  assert.equal(marks.get(3).length, 2);
  assert.equal(marks.get(5)[0].message, 'Beyond the end');
  const text = 'ab\ncdef\nghij';
  assert.equal(lineOffset(text, 1), 0);
  assert.equal(lineOffset(text, 2, 3), 5);
  assert.equal(lineOffset(text, 2, 99), 7);
  assert.equal(lineOffset(text, 9), text.length);
});

test('safe mode: the window toggle wins over ?safe and the server flag', () => {
  assert.equal(resolveSafe({ url: false, server: false, toggle: null }), false);
  assert.equal(resolveSafe({ url: true, server: false, toggle: null }), true);
  assert.equal(resolveSafe({ url: false, server: true, toggle: null }), true);
  assert.equal(resolveSafe({ url: false, server: true, toggle: false }), false);
  assert.equal(resolveSafe({ url: false, server: false, toggle: true }), true);
  assert.equal(hasSafeParam('?safe'), true);
  assert.equal(hasSafeParam('?a=1&safe='), true);
  assert.equal(hasSafeParam('?unsafe=1'), false);
  assert.equal(withSafeParam('http://127.0.0.1:8765/#notes', true), 'http://127.0.0.1:8765/?safe#notes');
  assert.equal(withSafeParam('http://127.0.0.1:8765/?a=1&safe#notes', false), 'http://127.0.0.1:8765/?a=1#notes');
  assert.equal(withSafeParam('http://127.0.0.1:8765/?safe', false), 'http://127.0.0.1:8765/');
  const key = { ctrlKey: true, altKey: true, shiftKey: true, metaKey: false, code: 'KeyS', key: 'S' };
  assert.equal(isSafeShortcut(key), true);
  assert.equal(isSafeShortcut({ ...key, shiftKey: false }), false);
  assert.equal(isSafeShortcut({ ...key, code: 'KeyD', key: 'D' }), false);
});

test('diff cells summarise long CSS and values', () => {
  assert.equal(describeValue('theme.css', 'a {}\nb {}\nc {}'), '3 lines of CSS');
  assert.equal(describeValue('theme.css', ''), 'no CSS');
  assert.equal(describeValue('theme.base', 'nord'), '"nord"');
  assert.equal(describeValue('modules', ['workspace']), '["workspace"]');
  assert.equal(describeValue('ui.dock.height', null), 'null');
  assert.equal(describeValue('theme.tokens.ink', undefined), '(not set)');
  assert.equal(describeValue('description', 'x'.repeat(300)), '1 line (300 characters)');
});

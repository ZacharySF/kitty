// Templates (home/templates.ts) and home's saved arrangement (home/layout.ts): the four
// templates agree with the module registry, the build schema and home's widgets; Rika's
// offer follows what is on; applying one arranges home and names the modules it lacks.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DEFAULT_BUILD, deepMerge } from '../src/lib/buildCore.ts';
import { HOME_WIDGETS, placeWidget, resolveHome, savedHome, spareWidgets } from '../src/lib/home/layout.ts';
import {
  TEMPLATES, arrangement, missingLine, missingModules, suggestTemplate, suggestionLine, wantedModules,
} from '../src/lib/home/templates.ts';

const registry = JSON.parse(readFileSync(new URL('../../src/kitty/modules.json', import.meta.url), 'utf8'));
const schema = JSON.parse(readFileSync(new URL('../../src/kitty/core/builds/kitty-build.schema.json', import.meta.url), 'utf8'));
const home = schema.properties.ui.properties.home.properties;
const template = (id) => TEMPLATES.find((item) => item.id === id);
const widgets = HOME_WIDGETS.map((widget) => widget.id);

test('the four templates ship, each with a name, an answer, known modules and known widgets', () => {
  assert.deepEqual(TEMPLATES.map((item) => item.name), ['Student', 'Job search', 'Builder', 'Minimal']);
  assert.deepEqual(TEMPLATES.map((item) => item.use), ['Studying', 'Finding a job', 'Building with agents', 'Notes and a calendar']);
  const modules = registry.modules.map((item) => item.id);
  for (const item of TEMPLATES) {
    assert.ok(item.modules.length && item.modules.every((id) => modules.includes(id)), `${item.id} names an unknown module`);
    const placed = [...item.home.columns.flat(), ...item.home.hidden];
    assert.deepEqual([...placed].sort(), [...widgets].sort(), `${item.id} must place or hide every home widget exactly once`);
  }
});

test('the question\'s answers preselect their templates\' modules, each once', () => {
  assert.deepEqual(wantedModules([]), []);
  assert.deepEqual(wantedModules(['student']), ['workspace', 'math']);
  assert.deepEqual(wantedModules(['job-search']), ['applyai', 'workspace']);
  assert.deepEqual(wantedModules(['builder']), ['terminal', 'workspace', 'brain']);
  assert.deepEqual(wantedModules(['minimal']), ['workspace']);
  assert.deepEqual(wantedModules(['student', 'builder']), ['workspace', 'math', 'terminal', 'brain']);
});

test('the build schema accepts every template\'s arrangement and defaults to today\'s home', () => {
  assert.deepEqual(home.columns.default, DEFAULT_BUILD.ui.home.columns);
  assert.deepEqual([home.hidden.default, home.template_offered.default], [DEFAULT_BUILD.ui.home.hidden, DEFAULT_BUILD.ui.home.template_offered]);
  const id = new RegExp(home.columns.items.items.pattern);
  for (const widget of widgets) assert.match(widget, id);
  for (const item of TEMPLATES) {
    assert.ok(item.home.columns.length <= home.columns.maxItems);
    assert.ok(item.home.hidden.length <= home.hidden.maxItems);
  }
  assert.deepEqual(resolveHome(DEFAULT_BUILD.ui.home, ['workspace', 'terminal']), [['agenda', 'note', 'focus'], ['next', 'agents']]);
});

test('home draws only widgets whose module is on, keeps the saved order and adds what the file does not place', () => {
  const saved = { columns: [['next', 'note'], ['agenda']], hidden: ['agents', 'focus'] };
  assert.deepEqual(resolveHome(saved, ['workspace', 'terminal']), [['next', 'note'], ['agenda']]);
  // Up next also lists job reminders: ApplyAI alone still draws it; the notes widgets go.
  assert.deepEqual(resolveHome(saved, ['applyai']), [['next']]);
  assert.deepEqual(resolveHome(saved, []), []);
  // An id from a newer rika! stays in the file and is not drawn.
  assert.deepEqual(resolveHome({ columns: [['whiteboard', 'agenda']], hidden: [] }, ['workspace']), [['agenda', 'note', 'focus'], ['next']]);
  // A widget neither placed nor hidden joins its usual column.
  assert.deepEqual(resolveHome({ columns: [['note']], hidden: [] }, ['workspace', 'terminal']), [['note', 'agenda', 'focus'], ['next', 'agents']]);
  assert.deepEqual(resolveHome({ columns: [['note'], ['agenda']], hidden: ['next', 'agents', 'focus'] }, ['workspace', 'terminal']), [['note'], ['agenda']]);
  // Hidden wins over a stale placement.
  assert.deepEqual(resolveHome({ columns: [['agenda', 'note', 'next']], hidden: ['next', 'agents', 'focus'] }, ['workspace']), [['agenda', 'note']]);
});

test('edit mode moves a widget within and across columns, and Done saves what is on and what is off', () => {
  const start = [['agenda', 'note'], ['next']];
  assert.deepEqual(placeWidget(start, 'note', 0, 'agenda'), [['note', 'agenda'], ['next']]);
  assert.deepEqual(placeWidget(start, 'agenda', 0), [['note', 'agenda'], ['next']]);
  assert.deepEqual(placeWidget(start, 'note', 1, 'next'), [['agenda'], ['note', 'next']]);
  // One past the last column starts a new one, up to two; past that it lands in the second.
  assert.deepEqual(placeWidget([['agenda', 'note']], 'note', 1), [['agenda'], ['note']]);
  assert.deepEqual(placeWidget(start, 'note', 2), [['agenda'], ['next', 'note']]);
  // Dropping a widget on itself leaves it where it is; a new widget is simply added.
  assert.deepEqual(placeWidget(start, 'note', 0, 'note'), start);
  assert.deepEqual(placeWidget(start, 'focus', 1), [['agenda', 'note'], ['next', 'focus']]);
  assert.deepEqual(spareWidgets(start, ['workspace', 'terminal']).map((widget) => widget.id), ['focus', 'agents']);
  assert.deepEqual(spareWidgets(start, ['workspace']).map((widget) => widget.id), ['focus']);
  // Emptied columns go; a drawable widget left off is hidden; one whose module is off is neither,
  // so it shows when its module comes on. What was hidden and is now placed is no longer hidden.
  assert.deepEqual(savedHome([[], ['next', 'focus'], ['agenda']], { columns: [], hidden: ['focus', 'old'] }, ['workspace']),
    { columns: [['next', 'focus'], ['agenda']], hidden: ['old', 'note'] });
});

test('Rika offers the template that fits what is on; job records tip a tie', () => {
  assert.equal(suggestTemplate({ enabled: ['workspace', 'math'] }).id, 'student');
  assert.equal(suggestTemplate({ enabled: ['applyai', 'workspace'] }).id, 'job-search');
  assert.equal(suggestTemplate({ enabled: ['workspace', 'terminal', 'brain'] }).id, 'builder');
  assert.equal(suggestTemplate({ enabled: ['workspace'] }).id, 'minimal');
  assert.equal(suggestTemplate({ enabled: [] }).id, 'minimal');
  const everything = ['applyai', 'workspace', 'math', 'learnharness', 'terminal', 'voice'];
  assert.equal(suggestTemplate({ enabled: everything, counts: { jobs: 0, applications: 0 } }).id, 'student');
  assert.equal(suggestTemplate({ enabled: everything, counts: { jobs: 37, applications: 39 } }).id, 'job-search');
  // Records do not count while ApplyAI is off.
  assert.equal(suggestTemplate({ enabled: ['workspace', 'math'], counts: { jobs: 37, applications: 39 } }).id, 'student');
});

test('the offer is one or two short sentences', () => {
  assert.equal(suggestionLine(template('student'), { enabled: ['workspace', 'math'] }), 'The Student layout puts Up next, Focus and Quick note first.');
  assert.equal(suggestionLine(template('job-search'), { enabled: ['applyai', 'workspace'], counts: { jobs: 37, applications: 39 } }),
    'You have 39 applications and 37 jobs. The Job search layout puts Today and Up next first.');
  assert.equal(suggestionLine(template('job-search'), { enabled: ['applyai', 'workspace'], counts: { jobs: 1, applications: 1 } }),
    'You have 1 application and 1 job. The Job search layout puts Today and Up next first.');
  assert.equal(suggestionLine(template('job-search'), { enabled: ['workspace'], counts: { jobs: 37, applications: 39 } }),
    'The Job search layout puts Today and Up next first.');
  assert.equal(suggestionLine(template('minimal'), { enabled: [] }), 'The Minimal layout puts Today and Quick note first.');
});

test('applying a template arranges home and marks the offer made; it never touches modules', () => {
  for (const item of TEMPLATES) {
    const patch = arrangement(item);
    assert.deepEqual(Object.keys(patch), ['ui']);
    assert.deepEqual(Object.keys(patch.ui), ['home']);
    assert.deepEqual(patch.ui.home, { columns: item.home.columns, hidden: item.home.hidden, template_offered: true });
    assert.notEqual(patch.ui.home.columns, item.home.columns, 'the patch shares the template\'s arrays');
    const build = deepMerge(DEFAULT_BUILD, patch);
    assert.deepEqual(build.modules, DEFAULT_BUILD.modules);
    assert.equal(build.ui.home.look, DEFAULT_BUILD.ui.home.look);
    // With its own modules on, home shows exactly the template's columns.
    assert.deepEqual(resolveHome(build.ui.home, item.modules), item.home.columns);
  }
  // Minimal on a workspace with everything on still shows only its two widgets.
  assert.deepEqual(resolveHome(deepMerge(DEFAULT_BUILD, arrangement(template('minimal'))).ui.home, ['applyai', 'workspace', 'terminal']), [['agenda', 'note']]);
});

test('each module a template lacks is offered by name', () => {
  assert.deepEqual(missingModules(template('builder'), ['workspace']), ['terminal', 'brain']);
  assert.deepEqual(missingModules(template('student'), ['workspace', 'math', 'voice']), []);
  assert.equal(missingLine([]), '');
  assert.equal(missingLine(['Math']), 'It also uses Math, which is off.');
  assert.equal(missingLine(['Terminal', 'Brain']), 'It also uses Terminal and Brain, which are off.');
  assert.equal(missingLine(['ApplyAI', 'Terminal', 'Brain']), 'It also uses ApplyAI, Terminal and Brain, which are off.');
});

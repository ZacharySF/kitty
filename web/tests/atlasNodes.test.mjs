// Q13: which sections the atlas draws. Persistence and the graph it builds.
// node --experimental-strip-types --test tests/atlasNodes.test.mjs
import { beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';

const store = new Map();
globalThis.localStorage = {
  getItem: key => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: key => store.delete(key),
  clear: () => store.clear(),
};
globalThis.window = new EventTarget();

const { ATLAS_HIDDEN_KEY, ATLAS_NODES_EVENT, atlasGraph, atlasSections: sectionsOf, hiddenSections, setSectionHidden } = await import('../src/lib/atlasNodes.ts');
const { navigation, visibleGroups, visibleRelations } = await import('../src/lib/workspace.ts');
const atlasSections = enabled => sectionsOf(visibleGroups(enabled), navigation);

beforeEach(() => store.clear());

const ids = graph => graph.nodes.map(node => node.id).sort();
const touches = (graph, id) => graph.links.some(link => link.source === id || link.target === id);
const build = (enabled, hidden = new Set()) => atlasGraph(atlasSections(enabled), hidden, visibleRelations(enabled), new Map());

test('hidden choices default to none and survive malformed storage', () => {
  assert.deepEqual([...hiddenSections()], []);
  for (const raw of ['not json', '{"calendar":true}', '42', 'null']) {
    localStorage.setItem(ATLAS_HIDDEN_KEY, raw);
    assert.deepEqual([...hiddenSections()], [], raw);
  }
  localStorage.setItem(ATLAS_HIDDEN_KEY, JSON.stringify(['calendar', 7, null, 'jobs']));
  assert.deepEqual([...hiddenSections()].sort(), ['calendar', 'jobs']);
});

test('hiding and showing persists and tells every open atlas', () => {
  const heard = [];
  const listener = () => heard.push([...hiddenSections()].sort());
  window.addEventListener(ATLAS_NODES_EVENT, listener);
  setSectionHidden('calendar', true);
  setSectionHidden('notes', true);
  setSectionHidden('calendar', false);
  window.removeEventListener(ATLAS_NODES_EVENT, listener);
  assert.deepEqual(JSON.parse(localStorage.getItem(ATLAS_HIDDEN_KEY)), ['notes']);
  assert.deepEqual(heard, [['calendar'], ['calendar', 'notes'], ['notes']]);
});

test('a hidden section loses its node and every link touching it; the list keeps the row', () => {
  const all = build(['workspace', 'applyai']);
  assert.ok(ids(all).includes('view:calendar'));
  const hidden = build(['workspace', 'applyai'], new Set(['calendar']));
  assert.ok(!ids(hidden).includes('view:calendar'));
  assert.ok(!touches(hidden, 'view:calendar'));
  assert.deepEqual(ids(hidden), ids(all).filter(id => id !== 'view:calendar'));
  assert.ok(atlasSections(['workspace', 'applyai']).some(group => group.items.some(item => item.id === 'calendar')));
  const known = new Set(hidden.nodes.map(node => node.id));
  for (const link of hidden.links) assert.ok(known.has(link.source) && known.has(link.target), `${link.source} → ${link.target} dangles`);
});

test('hiding a group root keeps its other sections on the atlas', () => {
  const graph = build(['workspace'], new Set(['study']));
  assert.ok(!ids(graph).includes('view:study'));
  assert.ok(ids(graph).includes('view:flashcards'));
  assert.ok(!touches(graph, 'view:study'));
});

test('a group hub with every section hidden disappears, and comes back when one is shown', () => {
  const planViews = atlasSections(['workspace', 'applyai']).find(group => group.label === 'Plan').items.map(item => item.id);
  const hiddenPlan = build(['workspace', 'applyai'], new Set(planViews));
  assert.ok(!ids(hiddenPlan).includes('group:Plan'));
  const oneShown = build(['workspace', 'applyai'], new Set(planViews.slice(1)));
  assert.ok(ids(oneShown).includes('group:Plan'));
  assert.ok(ids(oneShown).includes(`view:${planViews[0]}`));
});

test('hidden choices for a disabled module neither resurrect nor break the atlas', () => {
  localStorage.setItem(ATLAS_HIDDEN_KEY, JSON.stringify(['jobs', 'pipeline', 'calendar']));
  const workspaceOnly = build(['workspace'], hiddenSections());
  assert.ok(!ids(workspaceOnly).some(id => ['view:jobs', 'view:pipeline'].includes(id)));
  assert.ok(!ids(workspaceOnly).includes('view:calendar'));
  assert.ok(ids(workspaceOnly).includes('view:notes'));
  // Turning the module back on keeps the earlier choice instead of resurrecting it.
  const both = build(['workspace', 'applyai'], hiddenSections());
  assert.ok(!ids(both).includes('view:jobs') && !ids(both).includes('view:pipeline'));
  assert.ok(ids(both).includes('view:reminders'));
  assert.deepEqual(JSON.parse(localStorage.getItem(ATLAS_HIDDEN_KEY)), ['jobs', 'pipeline', 'calendar']);
  // Every module off: the core sections remain (Goals under Plan) and nothing throws.
  assert.deepEqual(ids(build([], hiddenSections())), ['group:Plan', 'view:ai', 'view:goals', 'view:settings']);
});

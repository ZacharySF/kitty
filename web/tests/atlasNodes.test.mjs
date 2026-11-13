// Q13: which sections the atlas draws, from the hidden sections the build keeps
// (`ui.atlas.hidden`; saved and pushed by graphPreferences.ts, see atlasNodes.browser.test.mjs).
// node --experimental-strip-types --test tests/atlasNodes.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { atlasGraph, atlasSections as sectionsOf } from '../src/lib/atlasNodes.ts';
import { navItem, visibleGroups, visibleRelations } from '../src/lib/workspace.ts';

const atlasSections = enabled => sectionsOf(visibleGroups(enabled), id => navItem(id));
const ids = graph => graph.nodes.map(node => node.id).sort();
const touches = (graph, id) => graph.links.some(link => link.source === id || link.target === id);
const build = (enabled, hidden = new Set()) => atlasGraph(atlasSections(enabled), hidden, visibleRelations(enabled), new Map());

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
  const hidden = new Set(['jobs', 'pipeline', 'calendar']);
  const workspaceOnly = build(['workspace'], hidden);
  assert.ok(!ids(workspaceOnly).some(id => ['view:jobs', 'view:pipeline'].includes(id)));
  assert.ok(!ids(workspaceOnly).includes('view:calendar'));
  assert.ok(ids(workspaceOnly).includes('view:notes'));
  // Turning the module back on keeps the earlier choice instead of resurrecting it.
  const both = build(['workspace', 'applyai'], hidden);
  assert.ok(!ids(both).includes('view:jobs') && !ids(both).includes('view:pipeline'));
  assert.ok(ids(both).includes('view:reminders'));
  // Every module off: the core sections remain (Goals under Plan) and nothing throws.
  assert.deepEqual(ids(build([], hidden)), ['group:Plan', 'view:ai', 'view:goals', 'view:settings']);
});

// Brain layouts and view settings: deterministic, one point per node, anatomy grows edits up the spine.
// node --experimental-strip-types --test tests/brainLayout.test.mjs
import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { syntheticBrain } from "./brainFixture.mjs";

const store = new Map();
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
  clear: () => store.clear(),
};
const { brainGroups, layoutBrain, brainStyle, brainScaffold, BRAIN_PALETTE } = await import("../src/lib/brain/brainLayout.ts");
const { BRAIN_VIEW_KEY, DEFAULT_BRAIN_VIEW, loadBrainView, saveBrainView } = await import("../src/lib/brain/brainSettings.ts");

const data = syntheticBrain({ total: 4000 });
const index = (id) => data.nodes.findIndex((node) => node.id === id);
const at = (positions, i) => [positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]];

beforeEach(() => store.clear());

test("groups follow the chosen key, largest first, with vivid distinct colours", () => {
  const bySection = brainGroups(data, "section");
  assert.deepEqual(new Set(bySection.groups.map((group) => group.key)), new Set(data.sections.map((section) => section.id)));
  assert.equal(bySection.groups.reduce((sum, group) => sum + group.records + group.edits, 0), data.nodes.length);
  const totals = bySection.groups.map((group) => group.records + group.edits);
  assert.deepEqual(totals, [...totals].sort((a, b) => b - a));
  assert.equal(bySection.groups.find((group) => group.key === "notes").label, "Notes");
  assert.equal(new Set(bySection.groups.map((group) => group.hex)).size, bySection.groups.length);
  for (const hex of BRAIN_PALETTE) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    assert.ok(Math.max(r, g, b) - Math.min(r, g, b) > 0.5, `${hex} is not saturated`);
  }
  const byGroup = brainGroups(data, "group");
  assert.deepEqual(new Set(byGroup.groups.map((group) => group.key)), new Set(["Study", "Plan", "Job search"]));
  const byType = brainGroups(data, "type");
  assert.ok(byType.groups.some((group) => group.key === "page"));
  assert.equal(byType.groupOf.length, data.nodes.length);
  assert.equal(byType.groups[byType.groupOf[index("notes.page:0")]].key, "page");
});

test("every layout places one finite point per node, deterministically", () => {
  const model = brainGroups(data, "section");
  for (const layout of ["anatomy", "spheres", "wheel"]) {
    const first = layoutBrain(data, model, layout);
    const again = layoutBrain(data, model, layout);
    assert.equal(first.positions.length, data.nodes.length * 3, layout);
    assert.equal(first.births.length, data.nodes.length, layout);
    assert.ok(first.positions.every(Number.isFinite), layout);
    assert.ok(first.births.every((value) => value >= 0 && value <= 1), layout);
    assert.deepEqual(first.positions, again.positions, layout);
    assert.equal(first.anchors.length, model.groups.length * 3, layout);
    assert.ok(first.radius > 0, layout);
    assert.ok(brainScaffold(layout, model).length > 0, layout);
  }
  const anatomy = layoutBrain(data, model, "anatomy").positions;
  const wheel = layoutBrain(data, model, "wheel").positions;
  assert.notDeepEqual(at(anatomy, index("notes.page:0")), at(wheel, index("notes.page:0")));
});

test("anatomy: records form the brain above the spine; newer edits sit nearer the brain", () => {
  const model = brainGroups(data, "section");
  const { positions, births } = layoutBrain(data, model, "anatomy");
  const records = data.nodes.flatMap((node, i) => (node.kind === "record" ? [i] : []));
  const lowestRecord = Math.min(...records.map((i) => positions[i * 3 + 1]));
  const edits = data.nodes.flatMap((node, i) => (node.kind === "edit" ? [i] : []));
  const below = edits.filter((i) => positions[i * 3 + 1] < lowestRecord);
  assert.ok(below.length > edits.length * 0.5, "most edits hang below the brain on the spinal cord");
  // Edits arrive newest-first: compare the newest and oldest tenth that sit on the cord.
  const tenth = Math.floor(below.length / 10);
  const height = (list) => list.reduce((sum, i) => sum + positions[i * 3 + 1], 0) / list.length;
  assert.ok(height(below.slice(0, tenth)) > height(below.slice(-tenth)), "the spine grows upward with time");
  // The brainstem is born first, the far cortex and the bottom of the spine last.
  const birth = (list) => list.reduce((sum, i) => sum + births[i], 0) / list.length;
  assert.ok(birth(below.slice(0, tenth)) < birth(below.slice(-tenth)));
});

test("style: edits are dimmer and redder than records of the same group", () => {
  const model = brainGroups(data, "section");
  const { colors, sizes, seeds } = brainStyle(data, model);
  assert.equal(colors.length, data.nodes.length * 3);
  assert.equal(sizes.length, data.nodes.length);
  assert.equal(seeds.length, data.nodes.length);
  const record = index("notes.page:0");
  const edit = data.nodes.findIndex((node) => node.kind === "edit" && node.section === "notes");
  const sum = (i) => colors[i * 3] + colors[i * 3 + 1] + colors[i * 3 + 2];
  const redShare = (i) => colors[i * 3] / sum(i);
  assert.ok(sum(edit) < sum(record));
  assert.ok(redShare(edit) >= redShare(record));
});

test("view settings persist, coerce junk and reset to defaults", () => {
  assert.deepEqual(loadBrainView(), DEFAULT_BRAIN_VIEW);
  saveBrainView({ ...DEFAULT_BRAIN_VIEW, layout: "wheel", speed: 1.5 });
  assert.equal(loadBrainView().layout, "wheel");
  assert.equal(loadBrainView().speed, 1.5);
  localStorage.setItem(BRAIN_VIEW_KEY, JSON.stringify({ layout: "donut", groupBy: "type", speed: 99, pointSize: "big", lines: "yes" }));
  assert.deepEqual(loadBrainView(), { ...DEFAULT_BRAIN_VIEW, groupBy: "type", speed: 3 });
  localStorage.setItem(BRAIN_VIEW_KEY, "{not json");
  assert.deepEqual(loadBrainView(), DEFAULT_BRAIN_VIEW);
});

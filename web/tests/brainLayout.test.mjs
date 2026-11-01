// Brain layouts and view settings: deterministic, one point per node; anatomy is a cat whose spine and tail carry the edits.
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
const { brainGroups, layoutBrain, brainStyle, brainScaffold, expandPalette, isolateModel, BRAIN_PALETTE } = await import("../src/lib/brain/brainLayout.ts");
const { BRAIN_COLOURS_KEY, BRAIN_VIEW_KEY, DEFAULT_BRAIN_VIEW, loadBrainColours, loadBrainView, saveBrainColours, saveBrainView } = await import("../src/lib/brain/brainSettings.ts");

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

test("anatomy: a sitting cat, brain in the skull, two ears above it", () => {
  const model = brainGroups(data, "section");
  const { positions } = layoutBrain(data, model, "anatomy");
  // Layout axes: x points where the cat faces, y is up, z runs across its body (left/right).
  const count = data.nodes.length;
  const ys = Array.from({ length: count }, (_, i) => positions[i * 3 + 1]);
  const top = Math.max(...ys);
  const ears = ys.flatMap((y, i) => (y > top - 0.6 ? [i] : []));
  const side = (sign) => ears.filter((i) => sign * positions[i * 3 + 2] > 0.25).length;
  const middle = ears.filter((i) => Math.abs(positions[i * 3 + 2]) < 0.12).length;
  assert.ok(ears.length >= 20, "the ears hold points");
  assert.ok(side(1) > ears.length * 0.3 && side(-1) > ears.length * 0.3, "one pointed ear on each side of the head");
  assert.ok(middle < ears.length * 0.1, "a gap between the ears");
  // The records' brain sits inside the head, under the ears.
  const records = data.nodes.flatMap((node, i) => (node.kind === "record" ? [i] : []));
  const brain = records.filter((i) => positions[i * 3 + 1] > top - 2.4 && positions[i * 3 + 1] < top - 0.9);
  assert.ok(brain.length > records.length * 0.6, "most records cluster in the brain");
});

test("anatomy: edits run down the spine into a tail behind the cat, newest nearest the brain", () => {
  const model = brainGroups(data, "section");
  const { positions, births } = layoutBrain(data, model, "anatomy");
  const records = data.nodes.flatMap((node, i) => (node.kind === "record" ? [i] : []));
  const lowestRecord = Math.min(...records.map((i) => positions[i * 3 + 1]));
  const rearmostRecord = Math.min(...records.map((i) => positions[i * 3]));
  const brain = [0, 1, 2].map((axis) => records.reduce((sum, i) => sum + positions[i * 3 + axis], 0) / records.length);
  // The fixture lists edits newest first.
  const edits = data.nodes.flatMap((node, i) => (node.kind === "edit" ? [i] : []));
  const below = edits.filter((i) => positions[i * 3 + 1] < lowestRecord);
  assert.ok(below.length > edits.length * 0.5, "most edits hang below the head, on the spine and tail");
  const tenth = Math.floor(below.length / 10);
  const newest = below.slice(0, tenth);
  const oldest = below.slice(-tenth);
  const mean = (list, axis) => list.reduce((sum, i) => sum + positions[i * 3 + axis], 0) / list.length;
  const reach = (list) => list.reduce((sum, i) => sum + Math.hypot(...[0, 1, 2].map((axis) => positions[i * 3 + axis] - brain[axis])), 0) / list.length;
  assert.ok(reach(newest) < reach(oldest) - 3, "time flows from the brain down the spine to the tail tip");
  assert.ok(mean(oldest, 0) < rearmostRecord - 3, "the tail extends behind the cat");
  assert.ok(mean(oldest, 1) < lowestRecord - 1, "and below its head");
  // Spine-first build-up: the tail tip is born first, the brain last.
  const birth = (list) => list.reduce((sum, i) => sum + births[i], 0) / list.length;
  assert.ok(birth(oldest) < birth(newest));
  assert.ok(birth(newest) < birth(records));
});

test("style: kinds of things differ in colour; edits are dimmer and warmer", () => {
  const model = brainGroups(data, "section");
  const { colors, sizes, seeds } = brainStyle(data, model);
  assert.equal(colors.length, data.nodes.length * 3);
  assert.equal(sizes.length, data.nodes.length);
  assert.equal(seeds.length, data.nodes.length);
  const colour = (i) => [0, 1, 2].map((c) => colors[i * 3 + c]);
  const distance = (a, b) => Math.hypot(...a.map((value, c) => value - b[c]));
  const page = index("notes.page:0");
  const notebook = data.nodes.findIndex((node) => node.section === "notes" && node.type === "notebook");
  assert.ok(distance(colour(page), colour(notebook)) > 0.15, "page and notebook in one section get different shades");
  const sum = (i) => colors[i * 3] + colors[i * 3 + 1] + colors[i * 3 + 2];
  const save = data.nodes.findIndex((node) => node.kind === "edit" && node.section === "notes" && node.type === "save");
  const rename = data.nodes.findIndex((node) => node.kind === "edit" && node.section === "notes" && node.type === "rename");
  assert.ok(sum(save) < sum(page), "edits are dimmer than records");
  for (const edit of [save, rename]) assert.ok(colors[edit * 3] >= colors[edit * 3 + 2], "edits are warm");
  assert.ok(distance(colour(save), colour(rename)) > 0.08, "each action has its own variant");
  // The legend's per-group key lists the shades a group holds.
  const notes = model.groups.find((group) => group.key === "notes");
  assert.ok(notes.shades.some((shade) => shade.label === "page") && notes.shades.some((shade) => shade.label === "notebook"));
  assert.ok(notes.shades.some((shade) => shade.edit && shade.label === "save"));
});

test("style: the mocked 20k workspace shows at least eight distinct colours; type groups use twelve hues", () => {
  const big = syntheticBrain({ total: 20000 });
  const { colors } = brainStyle(big, brainGroups(big, "section"));
  const distinct = new Set();
  for (let i = 0; i < colors.length; i += 3) distinct.add([0, 1, 2].map((c) => Math.round(colors[i + c] * 20)).join());
  assert.ok(distinct.size >= 8, `${distinct.size} colours`);
  const byType = brainGroups(big, "type");
  const hues = byType.groups.slice(0, 12).map(({ rgb: [r, g, b] }) => {
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const d = max - min || 1;
    const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return (h * 60 + 360) % 360;
  });
  hues.forEach((hue, a) => hues.slice(a + 1).forEach((other) => {
    const gap = Math.min(Math.abs(hue - other), 360 - Math.abs(hue - other));
    assert.ok(gap >= 12, `type hues ${hue.toFixed(0)} and ${other.toFixed(0)} are too close`);
  }));
});

test("colours come from the theme, stretched to every group; the user's picks win", () => {
  const kitty = ["#deb2c3", "#cba7c0", "#c6b5d5", "#b8c5d6", "#f5e8ee", "#e1c7ad", "#becbb7", "#e7a7ae"];
  const gruvbox = ["#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#fe8019"];
  const stretched = expandPalette(kitty, 24);
  assert.equal(stretched.length, 24);
  assert.equal(new Set(stretched).size, 24, "every group gets its own colour");
  const themed = brainGroups(data, "section", { seeds: gruvbox });
  const plain = brainGroups(data, "section");
  assert.notDeepEqual(themed.groups.map((group) => group.hex), plain.groups.map((group) => group.hex));
  assert.ok(themed.groups.slice(0, 7).every((group) => gruvbox.includes(group.hex)), "the theme's own colours come first");
  const picked = brainGroups(data, "section", { seeds: gruvbox, overrides: { notes: "#123456" } });
  assert.equal(picked.groups.find((group) => group.key === "notes").hex, "#123456");
  assert.equal(picked.groups.find((group) => group.key === "jobs").hex, themed.groups.find((group) => group.key === "jobs").hex);
  const actions = brainGroups(data, "section", { seeds: gruvbox, actions: { save: "#00ff00" } });
  const save = data.nodes.findIndex((node) => node.kind === "edit" && node.type === "save");
  const green = (model) => brainStyle(data, model).colors[save * 3 + 1];
  assert.ok(green(actions) > green(themed), "edit actions take the theme's tones");
});

test("isolating one group lays out only its nodes, filling the stage", () => {
  const model = brainGroups(data, "section");
  const notes = model.groups.findIndex((group) => group.key === "notes");
  const members = data.nodes.flatMap((_, i) => (model.groupOf[i] === notes ? [i] : []));
  for (const layout of ["spheres", "wheel", "anatomy"]) {
    const all = layoutBrain(data, model, layout);
    const alone = layoutBrain(data, model, layout, notes);
    assert.equal(alone.positions.length, all.positions.length, layout);
    assert.ok(alone.positions.every(Number.isFinite), layout);
    assert.equal(alone.groups.filter((group) => group >= 0).length, members.length, `${layout}: only the group is shown`);
    assert.equal(new Set(members.map((i) => alone.groups[i])).size, model.groups[notes].shades.length, `${layout}: its kinds become the groups`);
    if (layout === "anatomy") continue;
    const spread = (placement) => {
      const xs = members.map((i) => placement.positions[i * 3]);
      return Math.max(...xs) - Math.min(...xs);
    };
    assert.ok(spread(alone) / alone.radius > spread(all) / all.radius, `${layout}: the group fills more of the frame`);
  }
  // A group holding most of the workspace (10.7k jobs) still separates its kinds.
  const jobs = syntheticBrain({ total: 10000, jobs: 10700 });
  const jobModel = brainGroups(jobs, "section");
  const placed = layoutBrain(jobs, jobModel, "spheres", 0);
  assert.equal(jobModel.groups[0].key, "jobs");
  assert.ok(placed.positions.every(Number.isFinite));
  const opened = isolateModel(jobModel, 0, jobs);
  const quarters = opened.groups.filter((group) => /^job · \d{4} Q[1-4]$/.test(group.label));
  assert.ok(quarters.length >= 6, "the dominant kind splits by quarter");
  assert.deepEqual(quarters.map((group) => group.label), [...quarters.map((group) => group.label)].sort().reverse(), "newest first");
  assert.equal(opened.groups.reduce((sum, group) => sum + group.records + group.edits, 0), jobModel.groups[0].records + jobModel.groups[0].edits);
  assert.equal(new Set(placed.groups.filter((group) => group >= 0)).size, opened.groups.length);
});

test("view settings persist, coerce junk and reset to defaults", () => {
  assert.deepEqual(loadBrainView(), DEFAULT_BRAIN_VIEW);
  saveBrainView({ ...DEFAULT_BRAIN_VIEW, layout: "wheel", speed: 1.5 });
  assert.equal(loadBrainView().layout, "wheel");
  assert.equal(loadBrainView().speed, 1.5);
  saveBrainView({ ...DEFAULT_BRAIN_VIEW, background: "black" });
  assert.equal(loadBrainView().background, "black");
  assert.equal(DEFAULT_BRAIN_VIEW.background, "transparent");
  localStorage.setItem(BRAIN_VIEW_KEY, JSON.stringify({ layout: "donut", groupBy: "type", speed: 99, pointSize: "big", lines: "yes", background: "plaid" }));
  assert.deepEqual(loadBrainView(), { ...DEFAULT_BRAIN_VIEW, groupBy: "type", speed: 3 });
  localStorage.setItem(BRAIN_VIEW_KEY, "{not json");
  assert.deepEqual(loadBrainView(), DEFAULT_BRAIN_VIEW);
});

test("picked colours persist per grouping and drop junk", () => {
  assert.deepEqual(loadBrainColours(), { section: {}, group: {}, type: {} });
  saveBrainColours({ section: { notes: "#AABBCC" }, group: {}, type: { page: "#112233" } });
  assert.deepEqual(loadBrainColours(), { section: { notes: "#aabbcc" }, group: {}, type: { page: "#112233" } });
  localStorage.setItem(BRAIN_COLOURS_KEY, JSON.stringify({ section: { notes: "red", jobs: "#00ff00" }, type: 4 }));
  assert.deepEqual(loadBrainColours(), { section: { jobs: "#00ff00" }, group: {}, type: {} });
  localStorage.setItem(BRAIN_COLOURS_KEY, "{oops");
  assert.deepEqual(loadBrainColours(), { section: {}, group: {}, type: {} });
});

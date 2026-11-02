// Brain layouts and view settings: deterministic, one point per node; anatomy is a neural-network brain.
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
const { brainGroups, layoutBrain, brainStyle, brainScaffold, brainAnatomy, brainRegionAt, brainShell, BRAIN_HUB, BRAIN_REGIONS, expandPalette, isolateModel, BRAIN_PALETTE } = await import("../src/lib/brain/brainLayout.ts");
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

/** Finds, for any point, whether one of `points` lies within `radius` of it (a uniform grid). */
function nearness(points, radius) {
  const cells = new Map();
  const key = (x, y, z) => `${Math.floor(x / radius)},${Math.floor(y / radius)},${Math.floor(z / radius)}`;
  for (const p of points) {
    const k = key(...p);
    if (!cells.has(k)) cells.set(k, []);
    cells.get(k).push(p);
  }
  return (q) => {
    const [cx, cy, cz] = q.map((value) => Math.floor(value / radius));
    for (let dx = -1; dx <= 1; dx += 1)
      for (let dy = -1; dy <= 1; dy += 1)
        for (let dz = -1; dz <= 1; dz += 1)
          for (const p of cells.get(`${cx + dx},${cy + dy},${cz + dz}`) ?? [])
            if (Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]) <= radius) return true;
    return false;
  };
}
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const REGION_NAMES = [
  "left frontal", "left temporal", "left parietal", "left occipital", "left cerebellum", "brainstem",
  "right cerebellum", "right occipital", "right parietal", "right temporal", "right frontal",
];
const turn = (a, b, c) => {
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - b[0], c[1] - b[1], c[2] - b[2]];
  const cos = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (Math.hypot(...u) * Math.hypot(...v) || 1);
  return Math.acos(Math.max(-1, Math.min(1, cos)));
};
const segment = (network, k) => [network.starts.subarray(k * 3, k * 3 + 3), network.ends.subarray(k * 3, k * 3 + 3)].map((p) => [...p]);
const recordsOf = (nodes) => nodes.flatMap((node, i) => (node.kind === "record" ? [i] : []));
const mean = (list) => list.reduce((a, b) => a + b, 0) / list.length;

test("brain: two hemispheres split by a longitudinal fissure, four lobes each, a cerebellum below and behind, a brainstem", () => {
  assert.deepEqual(BRAIN_REGIONS, REGION_NAMES);
  const model = brainGroups(data, "section");
  const { positions } = layoutBrain(data, model, "anatomy");
  const records = recordsOf(data.nodes).map((i) => at(positions, i));
  const left = records.filter((p) => p[2] > 0.1).length / records.length;
  const right = records.filter((p) => p[2] < -0.1).length / records.length;
  assert.ok(left > 0.3 && right > 0.3, `both hemispheres hold neurons (${left.toFixed(2)} / ${right.toFixed(2)})`);
  assert.ok(records.filter((p) => Math.abs(p[2]) < 0.06 && p[1] > 0.6).length < records.length * 0.01, "no neurons in the fissure between the hemispheres");
  // The surface dips along the top midline: the longitudinal fissure.
  const shell = brainShell();
  const top = [];
  for (let i = 0; i < shell.positions.length; i += 3) {
    const p = [shell.positions[i], shell.positions[i + 1], shell.positions[i + 2]];
    if (Math.abs(p[0]) < 1 && p[1] > 0.8) top.push(p);
  }
  const midline = mean(top.filter((p) => Math.abs(p[2]) < 0.06).map((p) => p[1]));
  const beside = mean(top.filter((p) => Math.abs(p[2]) > 0.3 && Math.abs(p[2]) < 0.6).map((p) => p[1]));
  assert.ok(beside - midline > 0.12, `the fissure dips ${(beside - midline).toFixed(2)} below the hemispheres`);
  // Every region is where anatomy puts it.
  const centre = (name) => {
    const index = REGION_NAMES.indexOf(name);
    const inside = records.filter((p) => brainRegionAt(p) === index);
    assert.ok(inside.length, `${name} holds neurons`);
    return [0, 1, 2].map((axis) => mean(inside.map((p) => p[axis])));
  };
  for (const side of ["left", "right"]) {
    const [frontal, parietal, temporal, occipital, cerebellum] = ["frontal", "parietal", "temporal", "occipital", "cerebellum"].map((lobe) => centre(`${side} ${lobe}`));
    assert.ok(frontal[0] > parietal[0] && parietal[0] > occipital[0], `${side}: frontal, parietal, occipital from front to back`);
    assert.ok(temporal[1] < parietal[1] && temporal[1] < frontal[1], `${side}: the temporal lobe lies low`);
    assert.ok(cerebellum[1] < occipital[1] - 0.6 && cerebellum[0] < parietal[0], `${side}: the cerebellum sits below and behind`);
    assert.ok(side === "left" ? frontal[2] > 0.3 : frontal[2] < -0.3, `${side} hemisphere on its side`);
  }
  const stem = centre("brainstem");
  assert.ok(records.every((p) => p[1] >= stem[1] - 1.5) && stem[1] < centre("left cerebellum")[1], "the brainstem is lowest");
});

test("brain: the cortex is folded into gyri and sulci", () => {
  const { positions, indices, sulci } = brainShell();
  assert.ok(indices.length >= 3 && indices.length % 3 === 0);
  const count = positions.length / 3;
  assert.equal(sulci.length, count);
  // Sulci are a labyrinth of narrow lines over the cortex, not a stripe or a blanket.
  const inSulcus = Array.from(sulci).filter((value) => value < 0.015).length / count;
  assert.ok(inSulcus > 0.03 && inSulcus < 0.25, `${(inSulcus * 100).toFixed(1)}% of the surface lies in a sulcus`);
  // Each sulcus is a groove: lower than the surface around it, while the gyral crowns stand proud.
  const sum = new Float64Array(count), seen = new Uint16Array(count);
  const radius = (v) => Math.hypot(positions[v * 3], positions[v * 3 + 1], positions[v * 3 + 2]);
  for (let k = 0; k < indices.length; k += 3)
    for (const [a, b] of [[0, 1], [1, 2], [2, 0]]) {
      const u = indices[k + a], v = indices[k + b];
      sum[u] += radius(v); seen[u] += 1;
      sum[v] += radius(u); seen[v] += 1;
    }
  const relief = (keep) => mean(Array.from({ length: count }, (_, v) => v).filter((v) => seen[v] && keep(sulci[v])).map((v) => radius(v) - sum[v] / seen[v]));
  const pits = relief((value) => value < 0.015), crowns = relief((value) => value > 0.12);
  assert.ok(pits < -0.003 && crowns > pits + 0.004, `sulci sink ${pits.toFixed(4)} against the crowns' ${crowns.toFixed(4)}`);
  const model = brainGroups(data, "section");
  assert.ok(brainScaffold("anatomy", model).length / 3 > 3000, "the faint cortex shell");
});

test("brain: each group is a region; big groups span adjacent regions, small ones share; neurons sit in their group's regions", () => {
  for (const by of ["section", "group", "type"]) {
    const model = brainGroups(data, by);
    const { regions } = brainAnatomy(model);
    assert.deepEqual(regions.map((region) => region.name), REGION_NAMES);
    regions.forEach((region) => assert.ok(region.groups.length >= 1, `${by}: ${region.name} belongs to a group`));
    const owned = model.groups.map((_, g) => regions.flatMap((region, index) => (region.groups.includes(g) ? [index] : [])));
    model.groups.forEach((group, g) => assert.ok(owned[g].length >= 1, `${by}: ${group.label} has a region`));
    const { positions } = layoutBrain(data, model, "anatomy");
    const records = recordsOf(data.nodes);
    const astray = records.filter((i) => !owned[model.groupOf[i]].includes(brainRegionAt(at(positions, i))));
    assert.ok(astray.length < records.length * 0.05, `${by}: ${astray.length} of ${records.length} neurons lie outside their group's regions`);
    if (by === "group") {
      const biggest = owned[0];
      assert.ok(biggest.length >= 2, "the largest group spans several regions");
      biggest.slice(1).forEach((index, k) => assert.equal((index - biggest[k] + REGION_NAMES.length) % REGION_NAMES.length, 1, "adjacent ones"));
    }
    if (by === "type") assert.ok(regions.some((region) => region.groups.length > 1), "with many groups, regions are shared");
  }
});

test("brain: a neural network of smooth spline filaments, with long tracts, local branches and the records' own links", () => {
  for (const total of [200, 4000, 20000]) {
    const brain = syntheticBrain({ total });
    const { network } = layoutBrain(brain, brainGroups(brain, "section"), "anatomy");
    assert.ok(network && network.count > 0, `${total}: a network`);
    let worst = 0, longest = 0;
    for (let k = 0; k < network.count; k += 1) {
      const [a, b] = segment(network, k);
      longest = Math.max(longest, distance(a, b));
      if (k + 1 < network.count) {
        const [c, d] = segment(network, k + 1);
        // Consecutive pieces of one filament (same pulse phase) that join.
        if (distance(b, c) < 1e-6 && network.seeds[k] === network.seeds[k + 1]) worst = Math.max(worst, turn(a, b, d));
      }
    }
    assert.ok(worst < (24 * Math.PI) / 180, `${total}: sharpest bend ${((worst * 180) / Math.PI).toFixed(1)}°`);
    assert.ok(longest <= 0.09, `${total}: longest straight piece ${longest.toFixed(3)}`);
    assert.ok(network.widths.every((width) => width > 0 && width < 0.08), `${total}: thin filaments`);
    assert.ok(network.dists.every(Number.isFinite) && network.seeds.every(Number.isFinite), `${total}: pulse phases`);
  }
  // Long-range tracts (between hemispheres) and records' own parent links appear as connections.
  const model = brainGroups(data, "section");
  const { network, positions } = layoutBrain(data, model, "anatomy");
  let crossings = 0;
  for (let k = 0; k < network.count; k += 1) if (network.starts[k * 3 + 2] > 0.05 !== network.ends[k * 3 + 2] > 0.05) crossings += 1;
  assert.ok(crossings >= 20, `${crossings} fibres cross the corpus callosum`);
  const ends = nearness(Array.from({ length: network.count }, (_, k) => segment(network, k)).flat(), 0.03);
  const ids = new Map(data.nodes.map((node, i) => [node.id, i]));
  const linked = data.nodes.flatMap((node, i) => (node.kind === "record" && node.parent && data.nodes[ids.get(node.parent)]?.kind === "record" ? [i] : []));
  assert.ok(linked.length > 10, "the fixture links records");
  for (const i of linked.slice(0, 200)) {
    assert.ok(ends(at(positions, i)), `record ${data.nodes[i].id} has a connection`);
    assert.ok(ends(at(positions, ids.get(data.nodes[i].parent))), "to its parent");
  }
});

test("brain: the network grows with the data, and 200 nodes still draw every region", () => {
  const big = syntheticBrain({ total: 20000 });
  const tiny = syntheticBrain({ total: 200 });
  const bigNet = layoutBrain(big, brainGroups(big, "section"), "anatomy").network;
  const tinyNet = layoutBrain(tiny, brainGroups(tiny, "section"), "anatomy").network;
  assert.ok(bigNet.count > tinyNet.count * 3, `20k nodes branch further (${bigNet.count}) than 200 (${tinyNet.count})`);
  const covered = new Set(Array.from({ length: tinyNet.count }, (_, k) => brainRegionAt(segment(tinyNet, k)[1])));
  REGION_NAMES.forEach((name, index) => assert.ok(covered.has(index), `200 nodes still wire the ${name}`));
  const model = brainGroups(data, "section");
  assert.deepEqual(layoutBrain(data, model, "anatomy"), layoutBrain(data, model, "anatomy"), "deterministic");
});

test("brain: edits ride their group's tracts by time, newest nearest the hub; the brain lights from the hub outward", () => {
  const model = brainGroups(data, "section");
  const { positions, births, network } = layoutBrain(data, model, "anatomy");
  const onFibres = model.groups.map((_, g) => {
    const points = [];
    for (let k = 0; k < network.count; k += 1) if (network.groups[k] === g) points.push(...segment(network, k));
    return nearness(points, 0.15);
  });
  const edits = data.nodes.flatMap((node, i) => (node.kind === "edit" ? [i] : []));
  const astray = edits.filter((i) => !onFibres[model.groupOf[i]](at(positions, i)));
  assert.ok(astray.length < edits.length * 0.01, `${astray.length} edits lie off their group's fibres`);
  // The fixture lists edits newest first.
  const notes = model.groups.findIndex((group) => group.key === "notes");
  const own = edits.filter((i) => model.groupOf[i] === notes);
  const tenth = Math.floor(own.length / 10);
  const reach = (i) => distance(at(positions, i), BRAIN_HUB);
  const newest = own.slice(0, tenth), oldest = own.slice(-tenth);
  assert.ok(mean(oldest.map(reach)) > mean(newest.map(reach)) + 0.6, "time flows out from the hub along the tracts");
  assert.ok(mean(oldest.map((i) => births[i])) > mean(newest.map((i) => births[i])), "and the newest are born first");
  const all = data.nodes.map((_, i) => i);
  const inner = all.filter((i) => reach(i) < 1.2), outer = all.filter((i) => reach(i) > 2.4);
  assert.ok(mean(inner.map((i) => births[i])) < mean(outer.map((i) => births[i])) - 0.2, "the brain builds from its hub outward");
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
  assert.equal(DEFAULT_BRAIN_VIEW.panel, false, "the View panel starts closed");
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

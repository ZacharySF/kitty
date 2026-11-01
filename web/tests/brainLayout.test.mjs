// Brain layouts and view settings: deterministic, one point per node; anatomy is a standing cat drawn only as its brain and vessels.
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
const { brainGroups, layoutBrain, brainStyle, brainScaffold, catAnatomy, CAT_BRAIN, CAT_REGIONS, expandPalette, isolateModel, BRAIN_PALETTE } = await import("../src/lib/brain/brainLayout.ts");
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
const REGION_NAMES = ["chest", "withers", "back", "flank", "belly", "rump", "tail base", "tail", "forelegs", "paws", "hindlegs", "hock", "feet", "toes"];
/** Reach of the brain (cerebrum and cerebellum) and of the whole head (ears, eyes, whiskers) from the brain's centre. */
const BRAIN = 1.25;
const HEAD = 2.6;

test("anatomy: every region of the cat is vessels made of one group's nodes, with no body shell", () => {
  const model = brainGroups(data, "section");
  const cat = catAnatomy(model);
  assert.deepEqual([...CAT_REGIONS].sort(), [...REGION_NAMES].sort());
  assert.deepEqual(cat.regions.map((region) => region.name), CAT_REGIONS);
  cat.regions.forEach((region, index) => {
    const vessels = cat.vessels.filter((vessel) => vessel.region === index);
    assert.ok(vessels.length >= 1, `${region.name} has vessels`);
    assert.ok(region.groups.length >= 1, `${region.name} is a group's`);
    assert.ok(vessels.every((vessel) => region.groups.includes(vessel.group)), `${region.name}'s vessels belong to its groups`);
  });
  model.groups.forEach((group, g) => assert.ok(cat.vessels.some((vessel) => vessel.group === g), `${group.label} owns vessels`));
  assert.ok(cat.vessels.some((vessel) => vessel.level >= 2), "vessels fork into finer branches");
  // Each edit glows on one of its own group's vessels.
  const { positions } = layoutBrain(data, model, "anatomy");
  const onVessels = model.groups.map((_, g) => nearness(cat.vessels.filter((vessel) => vessel.group === g).flatMap((vessel) => vessel.points), 0.22));
  const edits = data.nodes.flatMap((node, i) => (node.kind === "edit" ? [i] : []));
  const astray = edits.filter((i) => !onVessels[model.groupOf[i]](at(positions, i)));
  assert.ok(astray.length < edits.length * 0.01, `${astray.length} edits lie off their group's vessels`);
  // The faint scaffold traces the same vessels and the brain: no skin, no silhouette.
  const scaffold = brainScaffold("anatomy", model);
  const vessels = nearness(cat.vessels.flatMap((vessel) => vessel.points), 0.25);
  let shell = 0;
  for (let i = 0; i < scaffold.length; i += 3) {
    const p = [scaffold[i], scaffold[i + 1], scaffold[i + 2]];
    if (!vessels(p) && distance(p, CAT_BRAIN) > HEAD) shell += 1;
  }
  assert.equal(shell, 0, "every scaffold point is a vessel or part of the head");
});

test("anatomy: the brain is the focal point, the densest cluster, in the head between two ears", () => {
  const model = brainGroups(data, "section");
  const { positions } = layoutBrain(data, model, "anatomy");
  const count = data.nodes.length;
  const inBrain = Array.from({ length: count }, (_, i) => i).filter((i) => distance(at(positions, i), CAT_BRAIN) < BRAIN);
  assert.ok(inBrain.length > count * 0.15, `the brain holds ${inBrain.length} of ${count} points`);
  const records = data.nodes.flatMap((node, i) => (node.kind === "record" ? [i] : []));
  assert.ok(records.filter((i) => distance(at(positions, i), CAT_BRAIN) < BRAIN).length > records.length * 0.7, "most records form the brain");
  // No ball of the brain's size anywhere in the body holds nearly as many points: the brain is the densest cluster.
  const ball = (centre) => Array.from({ length: count }, (_, i) => i).filter((i) => distance(at(positions, i), centre) < BRAIN).length;
  let body = 0;
  for (let i = 0; i < count; i += 10) if (distance(at(positions, i), CAT_BRAIN) > 2 * BRAIN) body = Math.max(body, ball(at(positions, i)));
  assert.ok(inBrain.length > body * 1.25, `the brain's ${inBrain.length} points outshine the densest body cluster's ${body}`);
  // Two ears rise above the brain, apart (seen from the side the head turns toward the viewer).
  const head = records.filter((i) => distance(at(positions, i), CAT_BRAIN) < HEAD);
  const top = Math.max(...head.map((i) => positions[i * 3 + 1]));
  const tips = head.filter((i) => positions[i * 3 + 1] > top - 0.45).map((i) => positions[i * 3]).sort((a, b) => a - b);
  assert.ok(tips.length >= 10, "the ears hold points");
  assert.ok(top > CAT_BRAIN[1] + 0.9, "the ears stand above the brain");
  const gaps = tips.slice(1).map((x, k) => x - tips[k]);
  assert.ok(Math.max(...gaps) > 0.5, "a gap between the two ears");
});

test("anatomy: the reference pose: head up, facing right, tail out behind, four legs down to the floor", () => {
  const model = brainGroups(data, "section");
  const cat = catAnatomy(model);
  const points = (name) => cat.vessels.filter((vessel) => cat.regions[vessel.region].name === name).flatMap((vessel) => vessel.points);
  const mean = (name, axis) => { const list = points(name); return list.reduce((sum, p) => sum + p[axis], 0) / list.length; };
  const least = (name, axis) => Math.min(...points(name).map((p) => p[axis]));
  const most = (name, axis) => Math.max(...points(name).map((p) => p[axis]));
  assert.ok(CAT_BRAIN[1] > most("withers", 1), "the head is held above the withers");
  // Like the reference, the head sits over the front of the chest: both lead, then flank, rump and tail.
  assert.ok(CAT_BRAIN[0] > mean("withers", 0) && mean("chest", 0) > mean("flank", 0) && mean("flank", 0) > mean("rump", 0) && mean("rump", 0) > mean("tail", 0), "head and chest, flank, rump and tail run from right to left: the cat faces right");
  assert.ok(least("tail", 0) < least("rump", 0) - 2.5, "the tail extends well behind the rump");
  assert.ok(mean("tail", 1) > mean("belly", 1), "the tail is held out, not dragged on the floor");
  assert.ok(mean("back", 1) > mean("flank", 1) && mean("flank", 1) > mean("belly", 1), "back over flank over belly");
  assert.ok(mean("forelegs", 0) > mean("hindlegs", 0), "forelegs in front of the hind legs");
  for (const low of ["paws", "toes"]) assert.ok(least(low, 1) < 0.2, `${low} reach the floor`);
  for (const name of CAT_REGIONS.filter((name) => !["paws", "toes", "feet", "hock", "forelegs", "hindlegs"].includes(name)))
    assert.ok(least(name, 1) > 1.5, `${name} is off the floor`);
  assert.ok(mean("feet", 1) < mean("hock", 1) && mean("hock", 1) < mean("hindlegs", 1), "hindleg, hock, foot in order down the leg");
  // Four leg ends on the floor: front and back, near and far.
  const ends = [...points("paws"), ...points("toes")].filter((p) => p[1] < 0.25);
  const quarters = new Set(ends.map((p) => `${p[0] > 1 ? "front" : "back"}:${p[2] > 0 ? "near" : "far"}`));
  assert.equal(quarters.size, 4, `four feet on the floor: ${[...quarters]}`);
});

test("anatomy: the vessels grow with the data, and a tiny workspace still draws the whole cat", () => {
  const length = (cat) => cat.vessels.reduce((sum, vessel) => sum + vessel.points.slice(1).reduce((run, p, k) => run + distance(p, vessel.points[k]), 0), 0);
  const big = catAnatomy(brainGroups(syntheticBrain({ total: 20000 }), "section"));
  const tiny = catAnatomy(brainGroups(syntheticBrain({ total: 200 }), "section"));
  assert.ok(length(big) > length(tiny) * 2, `20k nodes branch further (${length(big).toFixed(0)}) than 200 (${length(tiny).toFixed(0)})`);
  assert.ok(big.vessels.length > tiny.vessels.length * 2, "and into more vessels");
  tiny.regions.forEach((region, index) => assert.ok(tiny.vessels.some((vessel) => vessel.region === index), `200 nodes still draw the ${region.name}`));
  // Fewer groups than regions: groups span several regions; more groups than regions: regions are shared.
  for (const by of ["group", "type"]) {
    const model = brainGroups(data, by);
    const cat = catAnatomy(model);
    cat.regions.forEach((region, index) => assert.ok(region.groups.length >= 1 && cat.vessels.some((vessel) => vessel.region === index), `${by}: ${region.name}`));
    model.groups.forEach((group, g) => assert.ok(cat.vessels.some((vessel) => vessel.group === g), `${by}: ${group.label} owns vessels`));
  }
  const model = brainGroups(data, "section");
  assert.deepEqual(catAnatomy(model), catAnatomy(model), "deterministic");
});

test("anatomy: newer edits sit nearer the brain along their vessels; the cat builds from the brain outward", () => {
  const model = brainGroups(data, "section");
  const { positions, births } = layoutBrain(data, model, "anatomy");
  const cat = catAnatomy(model);
  const tail = cat.regions.find((region) => region.name === "tail").groups[0];
  // The fixture lists edits newest first.
  const edits = data.nodes.flatMap((node, i) => (node.kind === "edit" && model.groupOf[i] === tail ? [i] : []));
  const tenth = Math.floor(edits.length / 10);
  const newest = edits.slice(0, tenth);
  const oldest = edits.slice(-tenth);
  const mean = (list, values) => list.reduce((sum, i) => sum + values(i), 0) / list.length;
  const reach = (i) => distance(at(positions, i), CAT_BRAIN);
  assert.ok(mean(oldest, reach) > mean(newest, reach) + 1, "time flows out from the brain to the vessel tips");
  assert.ok(mean(oldest, (i) => births[i]) > mean(newest, (i) => births[i]), "and the newest are born first");
  const brain = data.nodes.flatMap((_, i) => (distance(at(positions, i), CAT_BRAIN) < BRAIN ? [i] : []));
  const body = data.nodes.flatMap((_, i) => (distance(at(positions, i), CAT_BRAIN) > 3 ? [i] : []));
  assert.ok(mean(brain, (i) => births[i]) < 0.3 && mean(body, (i) => births[i]) > 0.45, "the brain lights first, then the vessels outward");
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

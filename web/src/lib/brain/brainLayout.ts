// Procedural layouts for the Brain look. Pure and deterministic: every node's
// randomness comes from a hash of its id, so a point keeps its place when the
// rest of the workspace changes. No DOM or three.js here (unit-tested in Node).
import type { BrainData, BrainNode } from "../api";

export type BrainLayout = "anatomy" | "spheres" | "wheel";
export type BrainGroupBy = "section" | "group" | "type";
export type Rgb = [number, number, number];
export type BrainGroup = { key: string; label: string; hex: string; rgb: Rgb; records: number; edits: number };
/** Groups (largest first) and each node's group index. */
export type BrainModel = { groups: BrainGroup[]; groupOf: Uint16Array };
export type BrainPlacement = {
  /** xyz per node. */
  positions: Float32Array;
  /** 0–1 per node: when the point appears while the brain builds itself. */
  births: Float32Array;
  /** xyz per group: where its label floats. */
  anchors: Float32Array;
  /** Index of each node's parent, or -1. */
  parents: Int32Array;
  center: [number, number, number];
  radius: number;
  /** Auto-turn axis: the wheel spins in its own plane. */
  turnAxis: "y" | "z";
  /** Home camera direction in radians: anatomy in three-quarter view, spheres from above their ring. */
  view: { azimuth: number; elevation: number };
  /** Opacity of the Lines web; anatomy's lines converge on the cord, so they are fainter. */
  lineAlpha: number;
};

/** Vivid, saturated hues in rainbow order (mindcorpus-like), not the theme's pastels. */
export const BRAIN_PALETTE = [
  "#ff3d3d", "#ff931f", "#ffe03a", "#5cff5c", "#25e8ff", "#3f7bff",
  "#a35cff", "#ff4fc8", "#b8ff2e", "#ff6a4d", "#2effb7", "#cf6bff",
];
const EDIT_RED: Rgb = [1, 0.17, 0.08];
const GOLDEN = Math.PI * (3 - Math.sqrt(5));

export function hash32(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35);
  return (h ^ (h >>> 16)) >>> 0;
}
/** mulberry32: a small, fast, seedable stream of uniform numbers in [0, 1). */
function stream(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), state | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
type Random = () => number;
const gauss = (r: Random) => Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r());
const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

function hexRgb(hex: string): Rgb {
  const value = Number.parseInt(hex.slice(1), 16);
  return [((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255];
}
/** Palette colour for the `index`-th group; beyond the palette, golden-angle hues stay vivid. */
function groupColour(index: number): string {
  if (index < BRAIN_PALETTE.length) return BRAIN_PALETTE[index];
  const hue = (index * 137.508) % 360;
  const f = (n: number) => {
    const k = (n + hue / 30) % 12;
    const value = 0.58 - 0.42 * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(value * 255).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}
const title = (text: string) => (text ? text[0].toUpperCase() + text.slice(1) : "Other");

export function brainGroups(data: BrainData, by: BrainGroupBy): BrainModel {
  const sections = new Map(data.sections.map((section) => [section.id, section]));
  const keyOf = (node: BrainNode) =>
    by === "section" ? node.section : by === "group" ? sections.get(node.section)?.group || "Other" : node.type || "other";
  const tallies = new Map<string, { records: number; edits: number }>();
  const keys = data.nodes.map((node) => {
    const key = keyOf(node);
    const tally = tallies.get(key) ?? { records: 0, edits: 0 };
    if (node.kind === "record") tally.records += 1;
    else tally.edits += 1;
    tallies.set(key, tally);
    return key;
  });
  const label = (key: string) => (by === "section" ? sections.get(key)?.label || title(key) : by === "group" ? key : title(key));
  const groups = [...tallies]
    .map(([key, tally]) => ({ key, label: label(key), ...tally }))
    .sort((a, b) => b.records + b.edits - (a.records + a.edits) || a.label.localeCompare(b.label))
    .map((group, index) => {
      const hex = groupColour(index);
      return { ...group, hex, rgb: hexRgb(hex) };
    });
  const order = new Map(groups.map((group, index) => [group.key, index]));
  return { groups, groupOf: Uint16Array.from(keys, (key) => order.get(key)!) };
}

/** Per-node colour, size and twinkle seed. Edits are dimmer and redder, like a glowing spinal cord. */
export function brainStyle(data: BrainData, model: BrainModel) {
  const count = data.nodes.length;
  const colors = new Float32Array(count * 3);
  const sizes = new Float32Array(count);
  const seeds = new Float32Array(count);
  data.nodes.forEach((node, i) => {
    const rgb = model.groups[model.groupOf[i]].rgb;
    const edit = node.kind === "edit";
    for (let c = 0; c < 3; c += 1)
      colors[i * 3 + c] = edit ? (rgb[c] * 0.38 + EDIT_RED[c] * 0.62) * 0.62 : rgb[c];
    const seed = hash32(node.id);
    sizes[i] = edit ? 0.78 : 1;
    seeds[i] = (seed & 0xffff) / 0xffff;
  });
  return { colors, sizes, seeds };
}

type Vec = [number, number, number];
const normalise = (v: Vec): Vec => {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
};
function randomUnit(r: Random): Vec {
  const y = 1 - 2 * r();
  const ring = Math.sqrt(1 - y * y);
  const angle = 2 * Math.PI * r();
  return [ring * Math.cos(angle), y, ring * Math.sin(angle)];
}
/** Uniform direction within `angle` of `axis`. */
function capDirection(axis: Vec, angle: number, r: Random): Vec {
  const cos = 1 - r() * (1 - Math.cos(angle));
  const sin = Math.sqrt(Math.max(0, 1 - cos * cos));
  const turn = 2 * Math.PI * r();
  const helper: Vec = Math.abs(axis[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
  const u = normalise([axis[1] * helper[2] - axis[2] * helper[1], axis[2] * helper[0] - axis[0] * helper[2], axis[0] * helper[1] - axis[1] * helper[0]]);
  const v: Vec = [axis[1] * u[2] - axis[2] * u[1], axis[2] * u[0] - axis[0] * u[2], axis[0] * u[1] - axis[1] * u[0]];
  const a = sin * Math.cos(turn), b = sin * Math.sin(turn);
  return [axis[0] * cos + u[0] * a + v[0] * b, axis[1] * cos + u[1] * a + v[1] * b, axis[2] * cos + u[2] * a + v[2] * b];
}

/** Times (ms, NaN when unknown) and parents shared by every layout. */
function nodeIndex(data: BrainData) {
  const ids = new Map(data.nodes.map((node, i) => [node.id, i]));
  const parents = Int32Array.from(data.nodes, (node) => (node.parent ? ids.get(node.parent) ?? -1 : -1));
  const times = Float64Array.from(data.nodes, (node) => (node.at ? Date.parse(node.at) : Number.NaN));
  return { parents, times };
}
/** 0 (oldest) to 1 (newest) for each listed node, ordered by time then position. */
function timeRank(indices: number[], times: Float64Array): Map<number, number> {
  const dated = indices.filter((i) => !Number.isNaN(times[i])).sort((a, b) => times[a] - times[b] || a - b);
  const last = Math.max(1, dated.length - 1);
  return new Map(dated.map((i, rank) => [i, rank / last]));
}

// ── Anatomy ────────────────────────────────────────────────────────────────
// Units: the cerebrum is about two units wide; the spinal cord hangs ~7.4 below.
const CEREBRUM = { rx: 1.02, ry: 0.8, rz: 1.28 };
const CEREBELLUM = { x: 0, y: -0.5, z: -0.86, rx: 0.56, ry: 0.27, rz: 0.36 };
const STEM_TOP: Vec = [0, -0.45, -0.3];
const SPINE_LENGTH = 7.4;
const BRANCH_LEVELS = 15;

/** Point on the spinal cord's centre line, s = 0 at the brainstem, 1 at the tail. */
function spineAt(s: number): Vec {
  return [
    0.035 * Math.sin(s * Math.PI * 3.2),
    -0.2 - s * SPINE_LENGTH,
    -0.36 + 0.26 * Math.sin(s * Math.PI * 1.7 + 0.5) - 0.14 * s - 0.12,
  ];
}
const spineRadius = (s: number) => (s < 0.08 ? 0.22 - 0.09 * (s / 0.08) : 0.13 - 0.07 * ((s - 0.08) / 0.92));
/** Ridges of a folded cortex, in roughly [-1, 1]. */
function gyri(d: Vec): number {
  return 0.6 * Math.sin(9 * d[0] + 4 * Math.sin(5 * d[2])) * Math.cos(8 * d[1] + 3 * Math.sin(6 * d[0]))
    + 0.4 * Math.sin(13 * d[2] + 7 * d[1] + 2 * Math.cos(9 * d[0]));
}
/** Lobe centres spread over the upper ~70% of the cerebrum, one per group. */
function lobes(model: BrainModel) {
  const records = model.groups.reduce((sum, group) => sum + group.records, 0) || 1;
  const count = model.groups.length;
  return model.groups.map((group, index) => {
    const y = 1 - ((index + 0.5) / count) * 1.32;
    const ring = Math.sqrt(1 - y * y);
    const angle = index * GOLDEN + 0.6;
    const share = group.records / records;
    const angleSpan = Math.min(1.6, Math.max(0.24, Math.acos(Math.max(-1, 1 - 2 * share * 0.75))));
    return { dir: [ring * Math.cos(angle), y, ring * Math.sin(angle)] as Vec, angle: angleSpan };
  });
}
function cortexPoint(dir: Vec, r: Random): Vec {
  let d = dir;
  if (d[1] < -0.45) d = normalise([d[0], -0.45 - (d[1] + 0.45) * 0.25, d[2]]);
  const fold = gyri(d);
  const depth = (1 - 0.14 * Math.pow(r(), 2.4)) * (1 + 0.05 * fold);
  const side = d[0] >= 0 ? 1 : -1;
  const y = d[1] * CEREBRUM.ry * depth;
  return [side * 0.05 + d[0] * CEREBRUM.rx * depth, y < 0 ? y * 0.82 : y, d[2] * CEREBRUM.rz * depth];
}
/** The lobe a cortex direction belongs to: nearest centre, scaled by each lobe's size (a weighted Voronoi). */
function lobeAt(d: Vec, lobeOf: { dir: Vec; angle: number }[]) {
  let best = 0;
  let bestScore = Infinity;
  lobeOf.forEach((lobe, index) => {
    const score = Math.acos(Math.max(-1, Math.min(1, d[0] * lobe.dir[0] + d[1] * lobe.dir[1] + d[2] * lobe.dir[2]))) / lobe.angle;
    if (score < bestScore) {
      best = index;
      bestScore = score;
    }
  });
  return best;
}
function anatomy(data: BrainData, model: BrainModel, parents: Int32Array, times: Float64Array, out: Float32Array, births: Float32Array) {
  const lobeOf = lobes(model);
  const records: number[] = [];
  const edits: number[] = [];
  data.nodes.forEach((node, i) => (node.kind === "record" ? records : edits).push(i));
  for (const i of records) {
    const r = stream(hash32(data.nodes[i].id));
    let p: Vec;
    if (r() < 0.12) {
      // Cerebellum: a folded shell tucked under the back of the cerebrum.
      const d = randomUnit(r);
      const depth = 0.84 + 0.16 * r();
      const folia = 0.03 * Math.sin(Math.atan2(d[1], d[2]) * 24);
      p = [CEREBELLUM.x + d[0] * CEREBELLUM.rx * depth, CEREBELLUM.y + d[1] * CEREBELLUM.ry * depth + folia, CEREBELLUM.z + d[2] * CEREBELLUM.rz * depth];
    } else {
      // Each group owns a coloured lobe; draws are kept inside it and favour the ridges of the folds.
      const g = model.groupOf[i];
      const lobe = lobeOf[g];
      let d = capDirection(lobe.dir, Math.min(Math.PI, lobe.angle * 1.5), r);
      for (let attempt = 0; attempt < 12; attempt += 1) {
        if (lobeAt(d, lobeOf) === g && (gyri(d) + 1) / 2 >= r() * 0.8) break;
        d = capDirection(lobe.dir, Math.min(Math.PI, lobe.angle * 1.5), r);
      }
      p = cortexPoint(d, r);
    }
    out.set(p, i * 3);
    births[i] = 0.32 + 0.68 * clamp01(Math.hypot(p[0] - STEM_TOP[0], p[1] - STEM_TOP[1], p[2] - STEM_TOP[2]) / 2.1);
  }
  // Edits arrive along the cord in time order, newest nearest the brain so it grows upward.
  const rank = timeRank(edits, times);
  for (const i of edits) {
    const r = stream(hash32(data.nodes[i].id));
    const newness = rank.get(i) ?? r();
    const s = Math.min(1, (1 - newness) * 0.985 + r() * 0.015);
    const parent = parents[i];
    const roll = r();
    let p: Vec;
    let birth: number;
    if (parent >= 0 && data.nodes[parent].kind === "record" && roll < 0.2) {
      // A faint halo of changes around the record they touched.
      const d = randomUnit(r);
      const distance = 0.03 + 0.1 * Math.pow(r(), 1.5);
      p = [out[parent * 3] + d[0] * distance, out[parent * 3 + 1] + d[1] * distance, out[parent * 3 + 2] + d[2] * distance];
      birth = Math.min(1, births[parent] + 0.04);
    } else if (roll < 0.36 && s > 0.08) {
      // Sparse nerve branches leaving the cord in pairs, like a peripheral nervous system.
      const level = Math.min(BRANCH_LEVELS - 1, Math.floor(((s - 0.08) / 0.92) * BRANCH_LEVELS));
      const anchor = 0.1 + (level / BRANCH_LEVELS) * 0.86;
      const base = spineAt(anchor);
      const side = r() < 0.5 ? -1 : 1;
      const tone = hash32(`${level}:${side}`) / 4294967296;
      const length = (level < 3 ? 1.7 : 0.75) + 0.8 * tone;
      const droop = 0.25 + 0.55 * (level / BRANCH_LEVELS);
      const t = Math.pow(r(), 0.75);
      const fork = t > 0.45 && r() < 0.45 ? (r() < 0.5 ? -1 : 1) * (t - 0.45) * length * 0.5 : 0;
      p = [
        base[0] + side * t * length * Math.cos(droop) + gauss(r) * 0.018,
        base[1] - t * length * Math.sin(droop) - 0.3 * t * t * length + fork * 0.4 + gauss(r) * 0.018,
        base[2] + 0.22 * Math.sin(t * 2.6 + level) * t * length + fork + gauss(r) * 0.018,
      ];
      birth = 0.42 * anchor + 0.22 * t;
    } else {
      const centre = spineAt(s);
      const radius = spineRadius(s) * (r() < 0.3 ? 0.4 : 1);
      const angle = 2 * Math.PI * r();
      const spread = Math.abs(gauss(r)) * radius;
      p = [centre[0] + Math.cos(angle) * spread, centre[1] + gauss(r) * 0.01, centre[2] + Math.sin(angle) * spread];
      birth = 0.42 * s;
    }
    out.set(p, i * 3);
    births[i] = clamp01(birth);
  }
  const anchors = new Float32Array(model.groups.length * 3);
  lobeOf.forEach((lobe, g) => anchors.set(cortexPoint(lobe.dir, () => 0).map((value) => value * 1.18), g * 3));
  // Framed a little low so the figure clears the legend along the bottom of the stage.
  return { anchors, center: [0, -3.45, 0] as Vec, radius: 4.85 };
}

// ── Spheres ────────────────────────────────────────────────────────────────
function sphereFrames(model: BrainModel) {
  const totals = model.groups.map((group) => group.records + group.edits);
  const largest = Math.max(1, ...totals);
  const radii = totals.map((total) => 0.28 + 0.9 * Math.cbrt(total / largest));
  const gap = 0.4;
  const circumference = radii.reduce((sum, radius) => sum + 2 * radius + gap, 0);
  const ring = Math.max(1.2, circumference / (2 * Math.PI));
  let travelled = 0;
  const centres = radii.map((radius) => {
    const angle = (2 * Math.PI * (travelled + radius + gap / 2)) / circumference;
    travelled += 2 * radius + gap;
    return [ring * Math.cos(angle), 0, ring * Math.sin(angle)] as Vec;
  });
  return { radii, centres, ring };
}
function spheres(data: BrainData, model: BrainModel, out: Float32Array, births: Float32Array) {
  const { radii, centres, ring } = sphereFrames(model);
  const count = model.groups.length;
  data.nodes.forEach((node, i) => {
    const r = stream(hash32(node.id));
    const g = model.groupOf[i];
    const d = randomUnit(r);
    const reach = node.kind === "record" ? 0.88 + 0.12 * r() : 0.9 * Math.cbrt(r());
    const c = centres[g];
    out.set([c[0] + d[0] * radii[g] * reach, c[1] + d[1] * radii[g] * reach, c[2] + d[2] * radii[g] * reach], i * 3);
    births[i] = clamp01(0.25 * (g / Math.max(1, count)) + 0.75 * reach);
  });
  const anchors = new Float32Array(count * 3);
  centres.forEach((c, g) => anchors.set([c[0], c[1] + radii[g] + 0.2, c[2]], g * 3));
  return { anchors, center: [0, 0, 0] as Vec, radius: ring + Math.max(...radii, 0.5) };
}

// ── Wheel ──────────────────────────────────────────────────────────────────
const WHEEL = { inner: 0.6, outer: 2.8, gap: 0.03 };
function wheelSectors(model: BrainModel) {
  const totals = model.groups.map((group) => group.records + group.edits);
  const sum = totals.reduce((a, b) => a + b, 0) || 1;
  const floor = 0.02 * 2 * Math.PI;
  const raw = totals.map((total) => Math.max(floor, (total / sum) * 2 * Math.PI));
  const scale = (2 * Math.PI) / raw.reduce((a, b) => a + b, 0);
  let start = Math.PI / 2;
  return raw.map((span) => {
    const sector = { start, span: span * scale };
    start -= span * scale;
    return sector;
  });
}
function wheel(data: BrainData, model: BrainModel, times: Float64Array, out: Float32Array, births: Float32Array) {
  const sectors = wheelSectors(model);
  const rank = timeRank(data.nodes.map((_, i) => i), times);
  const { inner, outer, gap } = WHEEL;
  data.nodes.forEach((node, i) => {
    const r = stream(hash32(node.id));
    const sector = sectors[model.groupOf[i]];
    const when = rank.get(i) ?? r();
    // Area-uniform radius: older inside, newest on the rim.
    const radius = Math.sqrt(inner * inner + when * (outer * outer - inner * inner));
    const angle = sector.start - gap / 2 - r() * Math.max(0.001, sector.span - gap);
    out.set([Math.cos(angle) * radius, Math.sin(angle) * radius, (r() - 0.5) * 0.06], i * 3);
    births[i] = clamp01((radius - inner) / (outer - inner));
  });
  const anchors = new Float32Array(sectors.length * 3);
  sectors.forEach((sector, g) => {
    const angle = sector.start - sector.span / 2;
    anchors.set([Math.cos(angle) * (outer + 0.32), Math.sin(angle) * (outer + 0.32), 0], g * 3);
  });
  return { anchors, center: [0, 0, 0] as Vec, radius: outer + 0.5 };
}

export function layoutBrain(data: BrainData, model: BrainModel, layout: BrainLayout): BrainPlacement {
  const { parents, times } = nodeIndex(data);
  const positions = new Float32Array(data.nodes.length * 3);
  const births = new Float32Array(data.nodes.length);
  const placed =
    layout === "anatomy" ? anatomy(data, model, parents, times, positions, births)
    : layout === "spheres" ? spheres(data, model, positions, births)
    : wheel(data, model, times, positions, births);
  const view = layout === "anatomy" ? { azimuth: 0.6, elevation: 0.1 } : layout === "spheres" ? { azimuth: 0, elevation: 0.62 } : { azimuth: 0, elevation: 0 };
  return { positions, births, parents, ...placed, turnAxis: layout === "wheel" ? "z" : "y", view, lineAlpha: layout === "anatomy" ? 0.022 : 0.08 };
}

const MAX_LINES = 6000;
/**
 * "Nerve" segments from each edit (or child record) to its parent record: two xyz positions and
 * two colours each, glowing at the record and fading to nothing at the child. Evenly thinned to MAX_LINES.
 */
export function brainLines(placement: BrainPlacement, colors: Float32Array) {
  const { parents, positions } = placement;
  const linked: number[] = [];
  parents.forEach((parent, i) => { if (parent >= 0) linked.push(i); });
  const stride = Math.max(1, linked.length / MAX_LINES);
  const count = Math.min(linked.length, MAX_LINES);
  const lines = new Float32Array(count * 6);
  const tints = new Float32Array(count * 6);
  for (let segment = 0; segment < count; segment += 1) {
    const i = linked[Math.floor(segment * stride)];
    const parent = parents[i];
    lines.set(positions.subarray(i * 3, i * 3 + 3), segment * 6);
    lines.set(positions.subarray(parent * 3, parent * 3 + 3), segment * 6 + 3);
    tints.set(colors.subarray(parent * 3, parent * 3 + 3), segment * 6 + 3);
  }
  return { positions: lines, colors: tints };
}

/** A faint, non-interactive shell giving each layout its shape: a body, sphere outlines, wheel rings. */
export function brainScaffold(layout: BrainLayout, model: BrainModel): Float32Array {
  const points: number[] = [];
  const r = stream(hash32(`scaffold:${layout}`));
  if (layout === "anatomy") {
    // Head shell, neck and a torso whose outline narrows at the waist.
    for (let n = 0; n < 1800; n += 1) {
      const d = randomUnit(r);
      if (d[1] < -0.55) continue;
      const fuzz = 1 + gauss(r) * 0.02;
      points.push(d[0] * 1.38 * fuzz, 0.05 + d[1] * 1.2 * fuzz, 0.05 + d[2] * 1.55 * fuzz);
    }
    const profile: [number, number][] = [[0, 0.5], [0.05, 0.62], [0.12, 1.9], [0.2, 2.05], [0.55, 1.42], [0.82, 1.78], [1, 1.25]];
    const width = (t: number) => {
      for (let k = 1; k < profile.length; k += 1)
        if (t <= profile[k][0]) {
          const [t0, w0] = profile[k - 1], [t1, w1] = profile[k];
          const f = (t - t0) / (t1 - t0);
          return w0 + (w1 - w0) * f * f * (3 - 2 * f);
        }
      return profile[profile.length - 1][1];
    };
    for (let n = 0; n < 7000; n += 1) {
      const t = r();
      const y = -1.05 - t * 6.6;
      const half = width(t) / 2;
      const angle = 2 * Math.PI * r();
      const fuzz = 1 + gauss(r) * 0.035;
      const spine = spineAt(Math.min(1, (-y - 0.2) / SPINE_LENGTH));
      points.push(Math.cos(angle) * half * fuzz, y, spine[2] + 0.42 + Math.sin(angle) * half * 0.52 * fuzz);
    }
  } else if (layout === "spheres") {
    const { radii, centres, ring } = sphereFrames(model);
    centres.forEach((c, g) => {
      for (let n = 0; n < 420; n += 1) {
        const d = randomUnit(r);
        const radius = radii[g] * (1.06 + gauss(r) * 0.01);
        points.push(c[0] + d[0] * radius, c[1] + d[1] * radius, c[2] + d[2] * radius);
      }
    });
    for (let n = 0; n < 900; n += 1) {
      const angle = 2 * Math.PI * r();
      points.push(Math.cos(angle) * ring, gauss(r) * 0.01, Math.sin(angle) * ring);
    }
  } else {
    const { inner, outer } = WHEEL;
    for (const radius of [inner, (inner + outer) / 2, outer, outer + 0.08])
      for (let n = 0; n < 700; n += 1) {
        const angle = 2 * Math.PI * r();
        points.push(Math.cos(angle) * radius, Math.sin(angle) * radius, 0);
      }
    for (const sector of wheelSectors(model))
      for (let n = 0; n < 120; n += 1) {
        const radius = inner + r() * (outer - inner);
        points.push(Math.cos(sector.start) * radius, Math.sin(sector.start) * radius, 0);
      }
  }
  return Float32Array.from(points);
}

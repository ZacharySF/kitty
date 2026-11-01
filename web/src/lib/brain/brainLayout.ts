// Procedural layouts for the Brain look. Pure and deterministic: every node's
// randomness comes from a hash of its id, so a point keeps its place when the
// rest of the workspace changes. No DOM or three.js here (unit-tested in Node).
import type { BrainData, BrainNode } from "../api";

export type BrainLayout = "anatomy" | "spheres" | "wheel";
export type BrainGroupBy = "section" | "group" | "type";
export type Rgb = [number, number, number];
/** One kind of thing inside a group: a record type or an edit action (a section, when grouped by type). */
export type BrainShade = { key: string; label: string; hex: string; rgb: Rgb; count: number; edit: boolean };
export type BrainGroup = { key: string; label: string; hex: string; rgb: Rgb; records: number; edits: number; shades: BrainShade[] };
/** Groups (largest first), each node's group index and its shade within that group. */
export type BrainModel = { groups: BrainGroup[]; groupOf: Uint16Array; shadeOf: Uint16Array };
export type BrainPlacement = {
  /** xyz per node. */
  positions: Float32Array;
  /** 0–1 per node: when the point appears while the brain builds itself. */
  births: Float32Array;
  /** Shown group per node; -2 hides the node (outside an isolated group). */
  groups: Float32Array;
  /** xyz per shown group: where its label floats. */
  anchors: Float32Array;
  /** Spheres only: xyz + radius per shown group, so hovering anywhere over a sphere finds it. */
  regions: Float32Array | null;
  /** Anatomy only: the nodes drawn as whiskers, which take the theme's ink rather than their group's colour. */
  whiskers: Int32Array;
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
const rgbHex = (rgb: Rgb) => `#${rgb.map((value) => Math.round(clamp01(value) * 255).toString(16).padStart(2, "0")).join("")}`;
// OKLCH (lightness 0–1, chroma, hue in degrees): perceptual, so hue turns and lightness steps look even.
type Lch = [number, number, number];
const linear = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const gamma = (v: number) => (v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055);
function toOklch(rgb: Rgb): Lch {
  const [r, g, b] = rgb.map(linear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, Math.hypot(A, B), ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360];
}
function oklchLinear([L, C, h]: Lch): Rgb {
  const A = C * Math.cos((h * Math.PI) / 180), B = C * Math.sin((h * Math.PI) / 180);
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
}
/** Back to sRGB, giving up chroma (not hue or lightness) until the colour fits the screen. */
function fromOklch([L, C, h]: Lch): Rgb {
  const fits = (rgb: Rgb) => rgb.every((v) => v >= -1e-4 && v <= 1 + 1e-4);
  let rgb = oklchLinear([L, C, h]);
  if (!fits(rgb)) {
    let lo = 0, hi = C;
    for (let step = 0; step < 18; step += 1) {
      const mid = (lo + hi) / 2;
      if (fits(oklchLinear([L, mid, h]))) lo = mid;
      else hi = mid;
    }
    rgb = oklchLinear([L, lo, h]);
  }
  return rgb.map((v) => gamma(clamp01(v))) as Rgb;
}
const hueGap = (a: number, b: number) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));
function fromHsl(hue: number, saturation: number, lightness: number): Rgb {
  const f = (n: number) => {
    const k = (n + (((hue % 360) + 360) % 360) / 30) % 12;
    return lightness - saturation * Math.min(lightness, 1 - lightness) * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}
/** Palette colour for the `index`-th group; beyond the palette, golden-angle hues stay vivid. */
function groupColour(index: number): string {
  if (index < BRAIN_PALETTE.length) return BRAIN_PALETTE[index];
  return rgbHex(fromHsl(index * 137.508, 0.84, 0.58));
}
/** Grouped by type there are many small groups: golden-angle hues with stepped lightness keep twelve or more apart. */
const typeColour = (index: number) => rgbHex(fromHsl(index * 137.508 + 8, 0.9, [0.6, 0.68, 0.54][index % 3]));
/**
 * A theme's colours stretched to `count` distinct ones: the theme's own hues first (alternating
 * across the hue circle so neighbours contrast), then hues between them, then lighter and deeper
 * variants. Pastel themes stay pastel; near-greys and near-duplicates are dropped.
 */
export function expandPalette(seeds: string[], count: number): string[] {
  const unique: Lch[] = [];
  for (const hex of seeds) {
    if (!/^#[0-9a-f]{6}$/i.test(hex.trim())) continue;
    const colour = toOklch(hexRgb(hex.trim()));
    if (colour[1] < 0.03 || colour[0] < 0.3) continue;
    if (unique.some((other) => hueGap(other[2], colour[2]) < 8 && Math.abs(other[0] - colour[0]) < 0.08)) continue;
    unique.push(colour);
  }
  if (unique.length < 3) return Array.from({ length: count }, (_, index) => groupColour(index));
  const interleave = (list: Lch[]) => {
    const half = Math.ceil(list.length / 2);
    return list.slice(0, half).flatMap((colour, index) => (list[index + half] ? [colour, list[index + half]] : [colour]));
  };
  let ring = [...unique].sort((a, b) => a[2] - b[2]);
  const out = interleave(ring);
  for (let round = 0; out.length < count && round < 3; round += 1) {
    const between = ring.map((colour, index) => {
      const next = ring[(index + 1) % ring.length];
      const turn = ((next[2] - colour[2] + 540) % 360) - 180;
      return [(colour[0] + next[0]) / 2, (colour[1] + next[1]) / 2, (colour[2] + turn / 2 + 360) % 360] as Lch;
    }).filter((colour, index) => hueGap(colour[2], ring[index][2]) >= 6);
    out.push(...interleave(between));
    ring = [...ring, ...between].sort((a, b) => a[2] - b[2]);
  }
  const base = out.length;
  for (let k = 0; out.length < count; k += 1) {
    const [L, C, h] = out[k % base];
    const step = Math.floor(k / base) % 2 ? -0.14 : 0.1;
    out.push([Math.min(0.94, Math.max(0.45, L + step)), C, h]);
  }
  return out.slice(0, count).map((colour) => rgbHex(fromOklch(colour)));
}
/** OKLCH blend of two colours, `t` of the way from `a` to `b`. */
export function mixColours(a: string, b: string, t: number): string {
  const x = toOklch(hexRgb(a)), y = toOklch(hexRgb(b));
  const turn = ((y[2] - x[2] + 540) % 360) - 180;
  return rgbHex(fromOklch([x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t, (x[2] + turn * t + 360) % 360]));
}

/** Hue (degrees) and lightness steps that tell the kinds inside one group apart, most common kind first. */
const SHADES: [number, number][] = [[0, 0], [32, 0.06], [-32, -0.05], [16, -0.1], [-16, 0.09], [48, -0.02], [-48, 0.04], [0, 0.12], [24, -0.11], [-24, 0.11]];
function shadeOf(base: Rgb, rank: number): Rgb {
  const [lightness, chroma, hue] = toOklch(base);
  const [shift, step] = SHADES[rank % SHADES.length];
  const extra = rank >= SHADES.length ? ((rank * 67) % 40) - 20 : 0;
  // Capped lightness and a chroma floor keep pastel themes colourful where thousands of points add up.
  return fromOklch([Math.min(0.76, Math.max(0.5, lightness + step)), Math.max(0.11, chroma), hue + shift + extra]);
}
/** What an edit did, by the action names the backend records. */
export type ActionTone = "remove" | "move" | "save" | "create" | "progress" | "review";
const ACTION_TONES: Record<string, ActionTone> = {
  delete: "remove", archive: "remove", bury: "remove", suspend: "remove", skip: "remove", not_eligible: "remove",
  rename: "move", move: "move", state: "move", reorder: "move", organize: "move", outline: "move", reschedule: "move",
  save: "save", edit: "save", notes: "save", items: "save", tracks: "save", "project-details": "save", undo: "save", restore: "save",
  create: "create", import: "create", "import-jobs": "create", generate: "create", upload: "create", "add-node": "create",
  add_track: "create", link: "create", "to-note": "create", project: "create", queue: "create", calendar: "create",
  complete: "progress", status: "progress", tier: "progress", deadline: "progress", next_action: "progress", favorite: "progress", resume: "progress",
  review: "review", grade: "review", study: "review", session: "review",
};
const TONE_ORDER: ActionTone[] = ["remove", "move", "save", "create", "progress", "review"];
/** Without a theme, edits glow warm (OKLCH hues): removals red, moves crimson, saves orange, creations amber, progress gold, reviews magenta. */
const WARM_HUES: Record<ActionTone, number> = { remove: 27, move: 3, save: 52, create: 75, progress: 100, review: 330 };
/** An edit takes its action's hue, pulled a little toward its group, and is deeper than the group's records. */
function editColour(base: Rgb, rank: number, action: string, actions: BrainPalette["actions"]): Rgb {
  const toneOf = ACTION_TONES[action] ?? TONE_ORDER[hash32(action) % TONE_ORDER.length];
  const themed = actions?.[toneOf];
  const [, chroma, hue] = themed ? toOklch(hexRgb(themed)) : [0, 0.16, WARM_HUES[toneOf]];
  const pull = ((toOklch(base)[2] - hue + 540) % 360) - 180;
  // Actions sharing a tone (rename and move) still differ by a few degrees.
  const own = (hash32(action) % 17) - 8;
  return fromOklch([0.55 + (rank % 2) * 0.04, Math.max(0.12, chroma), hue + pull * 0.15 + own]);
}
/**
 * Where colours come from: the theme's colours (`seeds`, expanded to every group), theme tones for
 * edit actions, and the user's own picks per group key, which win. Empty: the built-in vivid palette.
 */
export type BrainPalette = { seeds?: string[]; actions?: Partial<Record<ActionTone, string>>; overrides?: Record<string, string> };
const title = (text: string) => (text ? text[0].toUpperCase() + text.slice(1) : "Other");

export function brainGroups(data: BrainData, by: BrainGroupBy, palette: BrainPalette = {}): BrainModel {
  const sections = new Map(data.sections.map((section) => [section.id, section]));
  const keyOf = (node: BrainNode) =>
    by === "section" ? node.section : by === "group" ? sections.get(node.section)?.group || "Other" : node.type || "other";
  // Inside a group, kinds are record types and edit actions; grouped by type, they are sections.
  const kindOf = (node: BrainNode) => (by === "type" ? node.section : node.type || "other");
  const tallies = new Map<string, { records: number; edits: number; kinds: Map<string, number> }>();
  const keys = data.nodes.map((node) => {
    const key = keyOf(node);
    const tally = tallies.get(key) ?? { records: 0, edits: 0, kinds: new Map() };
    if (node.kind === "record") tally.records += 1;
    else tally.edits += 1;
    const kind = `${node.kind}:${kindOf(node)}`;
    tally.kinds.set(kind, (tally.kinds.get(kind) ?? 0) + 1);
    tallies.set(key, tally);
    return key;
  });
  const label = (key: string) => (by === "section" ? sections.get(key)?.label || title(key) : by === "group" ? key : title(key));
  const kindLabel = (kind: string) => (by === "type" ? sections.get(kind)?.label || title(kind) : kind);
  const themed = palette.seeds?.length ? expandPalette(palette.seeds, tallies.size) : null;
  const groups = [...tallies]
    .map(([key, tally]) => ({ key, label: label(key), ...tally }))
    .sort((a, b) => b.records + b.edits - (a.records + a.edits) || a.label.localeCompare(b.label))
    .map(({ kinds, ...group }, index) => {
      const picked = palette.overrides?.[group.key];
      const hex = picked && /^#[0-9a-f]{6}$/i.test(picked) ? picked.toLowerCase() : themed?.[index] ?? (by === "type" ? typeColour(index) : groupColour(index));
      const rgb = hexRgb(hex);
      const ranked = [...kinds].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      const ranks = { record: 0, edit: 0 };
      const shades = ranked.map(([key, count]) => {
        const edit = key.startsWith("edit:");
        const kind = key.slice(key.indexOf(":") + 1);
        const rank = edit ? ranks.edit++ : ranks.record++;
        const tone = edit ? editColour(rgb, rank, by === "type" ? group.key : kind, palette.actions) : shadeOf(rgb, rank);
        return { key, label: kindLabel(kind), hex: rgbHex(tone), rgb: tone, count, edit };
      });
      return { ...group, hex, rgb, shades };
    });
  const order = new Map(groups.map((group, index) => [group.key, index]));
  const shadeIndex = groups.map((group) => new Map(group.shades.map((shade, index) => [shade.key, index])));
  const groupOf = Uint16Array.from(keys, (key) => order.get(key)!);
  const shadeOfNode = Uint16Array.from(data.nodes, (node, i) => shadeIndex[groupOf[i]].get(`${node.kind}:${kindOf(node)}`)!);
  return { groups, groupOf, shadeOf: shadeOfNode };
}

/**
 * One group's kinds as groups of their own: what an isolated group is laid out and labelled by.
 * A kind that dominates the group (say 10k jobs) is split further by quarter, newest first and
 * brightest, so opening it shows when things arrived rather than one undivided ball.
 */
export function isolateModel(model: BrainModel, group: number, data: BrainData): BrainModel {
  const { shades } = model.groups[group];
  const total = shades.reduce((sum, shade) => sum + shade.count, 0);
  const dominant = shades.findIndex((shade) => shade.count > 500 && shade.count > total * 0.5);
  const quarter = (at: string | null) => {
    const date = at ? new Date(at) : null;
    return date && !Number.isNaN(date.getTime()) ? `${date.getUTCFullYear()} Q${Math.floor(date.getUTCMonth() / 3) + 1}` : "undated";
  };
  const buckets = new Map<string, number>();
  if (dominant >= 0)
    data.nodes.forEach((node, i) => {
      if (model.groupOf[i] === group && model.shadeOf[i] === dominant) buckets.set(quarter(node.at), (buckets.get(quarter(node.at)) ?? 0) + 1);
    });
  const periods = [...buckets.keys()].sort((a, b) => (a === "undated" ? 1 : b === "undated" ? -1 : b.localeCompare(a)));
  const asGroup = (shade: BrainShade) => ({
    key: shade.key,
    label: shade.label,
    hex: shade.hex,
    rgb: shade.rgb,
    records: shade.edit ? 0 : shade.count,
    edits: shade.edit ? shade.count : 0,
    shades: [shade],
  });
  const split = dominant >= 0 ? shades[dominant] : null;
  const timed = split ? periods.map((period, rank) => {
    const [L, C, h] = toOklch(split.rgb);
    const rgb = fromOklch([Math.min(0.88, Math.max(0.45, L + 0.08 - 0.04 * rank)), C, h]);
    return asGroup({ ...split, key: `${split.key}@${period}`, label: `${split.label} · ${period}`, hex: rgbHex(rgb), rgb, count: buckets.get(period)! });
  }) : [];
  const rest = shades.flatMap((shade, index) => (index === dominant ? [] : [asGroup(shade)]));
  const groups = [...timed, ...rest];
  const restIndex = new Map(shades.flatMap((_, index) => (index === dominant ? [] : [index])).map((index, k) => [index, timed.length + k]));
  const periodIndex = new Map(periods.map((period, k) => [period, k]));
  const groupOf = Uint16Array.from(data.nodes, (node, i) =>
    model.groupOf[i] !== group ? 0 : model.shadeOf[i] === dominant ? periodIndex.get(quarter(node.at))! : restIndex.get(model.shadeOf[i])!);
  return { groups, groupOf, shadeOf: new Uint16Array(data.nodes.length) };
}

/** Per-node colour (its kind's shade), size and twinkle seed. Edits are smaller as well as dimmer and warmer. */
export function brainStyle(data: BrainData, model: BrainModel) {
  const count = data.nodes.length;
  const colors = new Float32Array(count * 3);
  const sizes = new Float32Array(count);
  const seeds = new Float32Array(count);
  data.nodes.forEach((node, i) => {
    colors.set(model.groups[model.groupOf[i]].shades[model.shadeOf[i]].rgb, i * 3);
    sizes[i] = node.kind === "edit" ? 0.78 : 1;
    seeds[i] = (hash32(node.id) & 0xffff) / 0xffff;
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

// ── Anatomy: a sitting cat ────────────────────────────────────────────────
// Axes: x points where the cat faces, y is up, z runs across its body. One unit is
// about half a head; the cat sits on y = 0 and its tail curls up behind it.
const BRAIN_AT: Vec = [1.12, 4.98, 0];
// Brain-local axes (as the cortex is drawn): x across, y up, z forward.
const CEREBRUM = { rx: 0.8, ry: 0.6, rz: 1.0 };
const CEREBELLUM = { x: 0, y: -0.3, z: -0.84, rx: 0.5, ry: 0.26, rz: 0.27 };
const STEM_TOP: Vec = [0, -0.5, -0.72];
const toCat = (p: Vec): Vec => [BRAIN_AT[0] + p[2], BRAIN_AT[1] + p[1], BRAIN_AT[2] + p[0]];

type Path = { points: Vec[]; lengths: number[]; total: number };
/** A Catmull-Rom curve through `knots`, sampled densely so points can be placed by arc length. */
function curve(knots: Vec[], steps = 48): Path {
  const points: Vec[] = [];
  for (let k = 0; k < knots.length - 1; k += 1) {
    const p0 = knots[Math.max(0, k - 1)], p1 = knots[k], p2 = knots[k + 1], p3 = knots[Math.min(knots.length - 1, k + 2)];
    for (let step = 0; step < steps; step += 1) {
      const t = step / steps, t2 = t * t, t3 = t2 * t;
      points.push([0, 1, 2].map((c) => 0.5 * (2 * p1[c] + (p2[c] - p0[c]) * t + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2 + (3 * p1[c] - p0[c] - 3 * p2[c] + p3[c]) * t3)) as Vec);
    }
  }
  points.push(knots[knots.length - 1]);
  const lengths = [0];
  for (let i = 1; i < points.length; i += 1) lengths.push(lengths[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1], points[i][2] - points[i - 1][2]));
  return { points, lengths, total: lengths[lengths.length - 1] };
}
/** Point at fraction `s` (0–1) of the curve's length. */
function along(path: Path, s: number): Vec {
  const goal = clamp01(s) * path.total;
  let lo = 0, hi = path.lengths.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (path.lengths[mid] < goal) lo = mid;
    else hi = mid;
  }
  const span = path.lengths[hi] - path.lengths[lo] || 1;
  const f = (goal - path.lengths[lo]) / span;
  return [0, 1, 2].map((c) => path.points[lo][c] + (path.points[hi][c] - path.points[lo][c]) * f) as Vec;
}
const arcAt = (path: Path, knot: number, steps = 48) => path.lengths[knot * steps] / path.total;

// The cord leaves the brainstem, runs down the sloping back to the hips and out along a long tail
// that lies on the floor behind the cat and curls up at its tip.
const SPINE_KNOTS: Vec[] = [
  toCat(STEM_TOP), [0.1, 3.85, 0], [-0.32, 3.1, 0], [-0.82, 2.25, 0], [-1.28, 1.45, 0], [-1.62, 0.8, 0], [-1.95, 0.36, 0],
  [-2.7, 0.16, -0.05], [-3.55, 0.18, -0.12], [-4.25, 0.48, -0.2], [-4.72, 1.08, -0.26], [-4.78, 1.8, -0.3], [-4.48, 2.32, -0.3], [-4.02, 2.42, -0.28],
];
const SPINE = curve(SPINE_KNOTS);
const TAIL_FROM = arcAt(SPINE, 6);
const SHOULDER = arcAt(SPINE, 2);
const HIP = arcAt(SPINE, 4);
const spineRadius = (s: number) => (s < TAIL_FROM ? 0.19 - 0.06 * (s / TAIL_FROM) : 0.13 - 0.06 * ((s - TAIL_FROM) / (1 - TAIL_FROM)));
/** Nerves from the cord down each leg to the toes: front legs from the shoulders, hind legs folded under the haunch. */
const legs = (root: number, knots: Vec[]) => [-1, 1].map((side) => curve([along(SPINE, root), ...knots.map(([x, y, z]) => [x, y, z * side] as Vec)], 24));
const FRONT_LEGS = legs(SHOULDER, [[0.2, 2.75, 0.28], [0.72, 2.15, 0.38], [0.9, 1.1, 0.4], [0.98, 0.32, 0.4], [1.12, 0.1, 0.4]]);
const HIND_LEGS = legs(HIP, [[-1.1, 1.05, 0.55], [-0.12, 0.9, 0.66], [-0.85, 0.18, 0.68], [0.3, 0.1, 0.7]]);

const HEAD = { c: [1.25, 4.88, 0] as Vec, r: [1.27, 1.06, 1.16] as Vec };
const MUZZLE = { c: [2.45, 4.45, 0] as Vec, r: [0.42, 0.36, 0.55] as Vec };
const ear = (side: number) => ({ base: [1.05, 5.66, 0.62 * side] as Vec, tip: [0.88, 6.78, 0.96 * side] as Vec, ra: 0.46, rb: 0.3 });
const EARS = [ear(-1), ear(1)];

/** Surfaces the cat is drawn from: each can be sampled and asked whether a point lies inside. */
type Shape = { area: number; sample: (r: Random) => Vec; inside: (p: Vec) => boolean };
function ellipsoid(c: Vec, radii: Vec): Shape {
  const [a, b, d] = radii;
  const p = 1.6;
  return {
    area: 4 * Math.PI * Math.pow((Math.pow(a * b, p) + Math.pow(a * d, p) + Math.pow(b * d, p)) / 3, 1 / p),
    sample: (r) => {
      const u = randomUnit(r);
      return [c[0] + u[0] * a, c[1] + u[1] * b, c[2] + u[2] * d];
    },
    inside: (q) => ((q[0] - c[0]) / a) ** 2 + ((q[1] - c[1]) / b) ** 2 + ((q[2] - c[2]) / d) ** 2 < 1,
  };
}
/** A cone with an elliptical base: an ear. */
function cone({ base, tip, ra, rb }: { base: Vec; tip: Vec; ra: number; rb: number }): Shape {
  const axis: Vec = [tip[0] - base[0], tip[1] - base[1], tip[2] - base[2]];
  const height = Math.hypot(...axis);
  const w = normalise(axis);
  const u = normalise([w[1], -w[0], 0]);
  const v: Vec = [w[1] * u[2] - w[2] * u[1], w[2] * u[0] - w[0] * u[2], w[0] * u[1] - w[1] * u[0]];
  return {
    area: Math.PI * ((ra + rb) / 2) * Math.hypot(height, (ra + rb) / 2),
    sample: (r) => {
      const t = 1 - Math.sqrt(r());
      const angle = 2 * Math.PI * r();
      const a = (1 - t) * ra * Math.cos(angle), b = (1 - t) * rb * Math.sin(angle);
      return [0, 1, 2].map((c) => base[c] + axis[c] * t + u[c] * a + v[c] * b) as Vec;
    },
    inside: (q) => {
      const d: Vec = [q[0] - base[0], q[1] - base[1], q[2] - base[2]];
      const t = (d[0] * w[0] + d[1] * w[1] + d[2] * w[2]) / height;
      if (t < 0 || t > 1) return false;
      const a = (d[0] * u[0] + d[1] * u[1] + d[2] * u[2]) / (ra * (1 - t));
      const b = (d[0] * v[0] + d[1] * v[1] + d[2] * v[2]) / (rb * (1 - t));
      return a * a + b * b < 1;
    },
  };
}
/** A tube of radius `radius(s)` around part of a path (a leg or the tail). */
function tube(path: Path, from: number, to: number, radius: (s: number) => number): Shape {
  const samples = Array.from({ length: 64 }, (_, k) => {
    const s = from + ((to - from) * k) / 63;
    return { s, p: along(path, s) };
  });
  return {
    area: 2 * Math.PI * radius((from + to) / 2) * (to - from) * path.total,
    sample: (r) => {
      const s = from + (to - from) * r();
      const centre = along(path, s);
      const ahead = along(path, Math.min(1, s + 0.01)), behind = along(path, Math.max(0, s - 0.01));
      const tangent = normalise([ahead[0] - behind[0], ahead[1] - behind[1], ahead[2] - behind[2]]);
      const d = randomUnit(r);
      const dot = d[0] * tangent[0] + d[1] * tangent[1] + d[2] * tangent[2];
      const out = normalise([d[0] - dot * tangent[0], d[1] - dot * tangent[1], d[2] - dot * tangent[2]]);
      const width = radius(s);
      return [centre[0] + out[0] * width, centre[1] + out[1] * width, centre[2] + out[2] * width];
    },
    inside: (q) => samples.some(({ s, p }) => Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) < radius(s)),
  };
}
const HEAD_SHAPES: Shape[] = [ellipsoid(HEAD.c, HEAD.r), ellipsoid(MUZZLE.c, MUZZLE.r), ...EARS.map(cone)];
/** The whole cat, for the scaffold: head, neck, chest, back, haunches, legs, paws and tail. */
const BODY_SHAPES: Shape[] = [
  ...HEAD_SHAPES,
  ellipsoid([0.55, 3.75, 0], [0.62, 0.72, 0.62]),
  ellipsoid([0.4, 2.55, 0], [0.78, 1.0, 0.72]),
  ellipsoid([-0.3, 2.2, 0], [0.9, 1.0, 0.78]),
  ellipsoid([-0.75, 1.0, 0], [1.2, 1.0, 0.9]),
  ...[-1, 1].flatMap((side) => [
    ellipsoid([-0.7, 0.85, 0.5 * side], [1.0, 0.82, 0.48]),
    tube(curve([[0.72, 2.1, 0.38 * side], [0.98, 0.2, 0.38 * side]], 8), 0, 1, () => 0.19),
    ellipsoid([1.12, 0.1, 0.38 * side], [0.28, 0.11, 0.19]),
    ellipsoid([0.3, 0.1, 0.7 * side], [0.4, 0.11, 0.2]),
  ]),
  tube(SPINE, TAIL_FROM, 1, (s) => 0.21 - 0.09 * ((s - TAIL_FROM) / (1 - TAIL_FROM))),
];
/** A point on the union's surface: drawn from one shape (by area) and kept only if no other shape covers it. */
function surfacePoint(shapes: Shape[], r: Random, weights = shapes.map((shape) => shape.area)): Vec {
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  let p: Vec = [0, 0, 0];
  for (let attempt = 0; attempt < 12; attempt += 1) {
    let roll = r() * total;
    let pick = 0;
    while (pick < shapes.length - 1 && (roll -= weights[pick]) > 0) pick += 1;
    p = shapes[pick].sample(r);
    if (!shapes.some((shape, index) => index !== pick && shape.inside(p))) return p;
  }
  return p;
}
/** Whiskers: three bright lines fanning out from each side of the muzzle. */
function whiskerPoint(r: Random): Vec {
  const side = r() < 0.5 ? -1 : 1;
  const row = Math.floor(r() * 3);
  const t = r();
  const direction = normalise([0.3, 0.12 - 0.12 * row, side]);
  const length = 1.35;
  return [
    2.7 + direction[0] * t * length + gauss(r) * 0.006,
    4.4 - 0.07 * row + direction[1] * t * length - 0.16 * t * t + gauss(r) * 0.006,
    0.32 * side + direction[2] * t * length + gauss(r) * 0.006,
  ];
}

/** Ridges of a folded cortex, in roughly [-1, 1]. */
function gyri(d: Vec): number {
  return 0.6 * Math.sin(9 * d[0] + 4 * Math.sin(5 * d[2])) * Math.cos(8 * d[1] + 3 * Math.sin(6 * d[0]))
    + 0.4 * Math.sin(13 * d[2] + 7 * d[1] + 2 * Math.cos(9 * d[0]));
}
/** Lobe centres spread over the upper ~70% of the cerebrum, one per group that has records. */
function lobes(model: BrainModel) {
  const records = model.groups.reduce((sum, group) => sum + group.records, 0) || 1;
  const count = model.groups.length;
  return model.groups.map((group, index) => {
    const y = 1 - ((index + 0.5) / count) * 1.32;
    const ring = Math.sqrt(1 - y * y);
    const angle = index * GOLDEN + 0.6;
    const share = group.records / records;
    const angleSpan = group.records ? Math.min(1.6, Math.max(0.24, Math.acos(Math.max(-1, 1 - 2 * share * 0.75)))) : 1e-3;
    return { dir: [ring * Math.cos(angle), y, ring * Math.sin(angle)] as Vec, angle: angleSpan };
  });
}
/** A point on the folded cortex, brain-local. */
function cortexPoint(dir: Vec, r: Random): Vec {
  let d = dir;
  if (d[1] < -0.45) d = normalise([d[0], -0.45 - (d[1] + 0.45) * 0.25, d[2]]);
  const fold = gyri(d);
  const depth = (1 - 0.14 * Math.pow(r(), 2.4)) * (1 + 0.05 * fold);
  const side = d[0] >= 0 ? 1 : -1;
  const y = d[1] * CEREBRUM.ry * depth;
  return [side * 0.04 + d[0] * CEREBRUM.rx * depth, y < 0 ? y * 0.82 : y, d[2] * CEREBRUM.rz * depth];
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
  const stem = toCat(STEM_TOP);
  // Ears weigh more than their area so the silhouette reads at a glance.
  const headWeights = HEAD_SHAPES.map((shape, index) => shape.area * (index >= 2 ? 3.2 : 1));
  const whiskers: number[] = [];
  for (const i of records) {
    const r = stream(hash32(data.nodes[i].id));
    const roll = r();
    let p: Vec;
    if (roll < 0.04) {
      p = whiskerPoint(r);
      births[i] = 0.9 + 0.08 * r();
      whiskers.push(i);
    } else if (roll < 0.17) {
      // A sparse glittering skull, muzzle and ears around the brain.
      p = surfacePoint(HEAD_SHAPES, r, headWeights);
      births[i] = 0.8 + 0.16 * r();
    } else {
      if (roll < 0.27) {
        // Cerebellum: a folded shell tucked behind the cerebrum.
        const d = randomUnit(r);
        const depth = 0.84 + 0.16 * r();
        const folia = 0.025 * Math.sin(Math.atan2(d[1], d[2]) * 24);
        p = toCat([CEREBELLUM.x + d[0] * CEREBELLUM.rx * depth, CEREBELLUM.y + d[1] * CEREBELLUM.ry * depth + folia, CEREBELLUM.z + d[2] * CEREBELLUM.rz * depth]);
      } else {
        // Each group owns a coloured lobe; draws are kept inside it and favour the ridges of the folds.
        const g = model.groupOf[i];
        const lobe = lobeOf[g];
        const spread = Math.min(Math.PI, Math.max(0.36, lobe.angle * 1.5));
        let d = capDirection(lobe.dir, spread, r);
        for (let attempt = 0; attempt < 12; attempt += 1) {
          if (lobeAt(d, lobeOf) === g && (gyri(d) + 1) / 2 >= r() * 0.8) break;
          d = capDirection(lobe.dir, spread, r);
        }
        p = toCat(cortexPoint(d, r));
      }
      births[i] = 0.45 + 0.4 * clamp01(Math.hypot(p[0] - stem[0], p[1] - stem[1], p[2] - stem[2]) / 1.9);
    }
    out.set(p, i * 3);
  }
  // Edits arrive along the cord in time order: the oldest at the tail tip, the newest nearest the brain.
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
      const distance = 0.03 + 0.08 * Math.pow(r(), 1.5);
      p = [out[parent * 3] + d[0] * distance, out[parent * 3 + 1] + d[1] * distance, out[parent * 3 + 2] + d[2] * distance];
      birth = Math.min(1, births[parent] + 0.04);
    } else if (roll < 0.36 && s > 0.03 && s < TAIL_FROM) {
      // Sparse nerves down the four legs, forking into toes at the paws.
      const front = s < (SHOULDER + HIP) / 2;
      const leg = (front ? FRONT_LEGS : HIND_LEGS)[r() < 0.5 ? 0 : 1];
      const t = Math.pow(r(), 0.7);
      const base = along(leg, Math.min(t, 0.94));
      let toe: Vec = [0, 0, 0];
      if (t > 0.94) {
        const f = (t - 0.94) / 0.06;
        toe = [0.24 * f, -0.02 * f, (Math.floor(r() * 4) - 1.5) * 0.08 * f];
      }
      p = [base[0] + toe[0] + gauss(r) * 0.016, base[1] + toe[1] + gauss(r) * 0.016, base[2] + toe[2] + gauss(r) * 0.016];
      birth = 0.45 * (1 - (front ? SHOULDER : HIP)) + 0.12 * t;
    } else {
      const centre = along(SPINE, s);
      const radius = spineRadius(s) * (r() < 0.3 ? 0.4 : 1);
      const d = randomUnit(r);
      const spread = Math.abs(gauss(r)) * radius;
      p = [centre[0] + d[0] * spread, centre[1] + d[1] * spread, centre[2] + d[2] * spread];
      birth = 0.45 * (1 - s);
    }
    out.set(p, i * 3);
    births[i] = clamp01(birth);
  }
  // Labels float off their lobe; groups of edits alone (by type: save, create…) are labelled along the cord instead.
  const anchors = new Float32Array(model.groups.length * 3);
  const cordOnly = model.groups.flatMap((group, g) => (group.records ? [] : [g]));
  lobeOf.forEach((lobe, g) => {
    if (model.groups[g].records) anchors.set(toCat(cortexPoint(lobe.dir, () => 0).map((value) => value * 1.5) as Vec), g * 3);
  });
  cordOnly.forEach((g, k) => {
    const c = along(SPINE, 0.06 + (0.88 * (k + 0.5)) / cordOnly.length);
    anchors.set([c[0], c[1] + 0.22, c[2] + 0.3], g * 3);
  });
  // Framed low, so the cat sits above the legend; the open View panel reframes it (scene inset).
  return { anchors, regions: null, whiskers: Int32Array.from(whiskers), center: [-0.2, 3.1, 0] as Vec, radius: 4.4 };
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
  const regions = new Float32Array(count * 4);
  centres.forEach((c, g) => {
    anchors.set([c[0], c[1] + radii[g] + 0.2, c[2]], g * 3);
    regions.set([c[0], c[1], c[2], radii[g]], g * 4);
  });
  return { anchors, regions, whiskers: new Int32Array(0), center: [0, 0, 0] as Vec, radius: ring + Math.max(...radii, 0.5) };
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
  return { anchors, regions: null, whiskers: new Int32Array(0), center: [0, 0, 0] as Vec, radius: outer + 0.5 };
}

function placeAll(data: BrainData, model: BrainModel, layout: BrainLayout) {
  const { parents, times } = nodeIndex(data);
  const positions = new Float32Array(data.nodes.length * 3);
  const births = new Float32Array(data.nodes.length);
  const placed =
    layout === "anatomy" ? anatomy(data, model, parents, times, positions, births)
    : layout === "spheres" ? spheres(data, model, positions, births)
    : wheel(data, model, times, positions, births);
  const view = layout === "anatomy" ? { azimuth: 0.7, elevation: 0.12 } : layout === "spheres" ? { azimuth: 0, elevation: 0.62 } : { azimuth: 0, elevation: 0 };
  const turnAxis: "y" | "z" = layout === "wheel" ? "z" : "y";
  return { positions, births, parents, groups: Float32Array.from(model.groupOf), ...placed, turnAxis, view, lineAlpha: layout === "anatomy" ? 0.022 : 0.08 };
}

/**
 * Places every node. With `isolate`, only that group is shown: its kinds become the groups and are laid
 * out to fill the stage; every other node keeps its place in the full layout but is hidden (group -2).
 */
export function layoutBrain(data: BrainData, model: BrainModel, layout: BrainLayout, isolate: number | null = null): BrainPlacement {
  const full = placeAll(data, model, layout);
  if (isolate === null || !model.groups[isolate]) return full;
  const members = data.nodes.flatMap((_, i) => (model.groupOf[i] === isolate ? [i] : []));
  const inner = isolateModel(model, isolate, data);
  const memberOf = new Map(members.map((i, j) => [i, j]));
  const sub = placeAll(
    { ...data, nodes: members.map((i) => data.nodes[i]) },
    { groups: inner.groups, groupOf: Uint16Array.from(members, (i) => inner.groupOf[i]), shadeOf: new Uint16Array(members.length) },
    layout,
  );
  const positions = full.positions.slice();
  const births = new Float32Array(data.nodes.length).fill(1);
  const groups = new Float32Array(data.nodes.length).fill(-2);
  members.forEach((i, j) => {
    positions.set(sub.positions.subarray(j * 3, j * 3 + 3), i * 3);
    births[i] = sub.births[j];
    groups[i] = sub.groups[j];
  });
  const parents = full.parents.map((parent, i) => (memberOf.has(i) && memberOf.has(parent) ? parent : -1));
  return { ...sub, positions, births, groups, parents, whiskers: sub.whiskers.map((j) => members[j]) };
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

/** A faint, non-interactive shell giving each layout its shape: the cat's body, sphere outlines, wheel rings. */
export function brainScaffold(layout: BrainLayout, model: BrainModel): Float32Array {
  const points: number[] = [];
  const r = stream(hash32(`scaffold:${layout}`));
  if (layout === "anatomy") {
    // The cat's silhouette: the surface of its head, ears, body, legs, paws and tail.
    for (let n = 0; n < 9000; n += 1) {
      const p = surfacePoint(BODY_SHAPES, r);
      const fuzz = 0.012;
      points.push(p[0] + gauss(r) * fuzz, p[1] + gauss(r) * fuzz, p[2] + gauss(r) * fuzz);
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

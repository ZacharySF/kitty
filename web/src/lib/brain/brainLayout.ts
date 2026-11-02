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
  /** Anatomy only: the neural network's filaments. */
  network: BrainNetwork | null;
  /** Index of each node's parent, or -1. */
  parents: Int32Array;
  center: [number, number, number];
  radius: number;
  /** Auto-turn axis: the wheel spins in its own plane. */
  turnAxis: "y" | "z";
  /** Home camera direction in radians: anatomy in three-quarter view, spheres from above their ring. */
  view: { azimuth: number; elevation: number };
  /** Opacity of the Lines web; anatomy draws its own network, so its lines are fainter. */
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

// ── Anatomy: a neural-network brain ───────────────────────────────────────
// Axes: x points forward, y up, z across (+z is the left hemisphere, which faces the home camera).
// One unit is about 3 cm. The folded cerebrum surrounds the origin, the cerebellum tucks under its
// back and the brainstem descends from its middle. Records are neurons in the cortex of their
// group's region; fibre tracts fan from the thalamic hub out to every region and run between
// regions, and each group's edits travel its own tracts in time order.
const distance = (a: Vec, b: Vec) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const lerp = (a: Vec, b: Vec, t: number): Vec => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const add = (a: Vec, b: Vec, scale = 1): Vec => [a[0] + b[0] * scale, a[1] + b[1] * scale, a[2] + b[2] * scale];
const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const smooth = (from: number, to: number, x: number) => {
  const t = clamp01((x - from) / (to - from));
  return t * t * (3 - 2 * t);
};
const jitter = (r: Random, size: number): Vec => {
  const u = randomUnit(r);
  return [u[0] * size, u[1] * size, u[2] * size];
};

/** The brain's regions in a ring (each beside the next), so a group spanning several takes neighbours. */
export const BRAIN_REGIONS = [
  "left frontal", "left temporal", "left parietal", "left occipital", "left cerebellum", "brainstem",
  "right cerebellum", "right occipital", "right parietal", "right temporal", "right frontal",
];
/** Each region's room for nodes, relative to the others (roughly its share of the cortex). */
const REGION_WEIGHTS = [1.4, 0.95, 1.15, 0.75, 0.6, 0.35, 0.6, 0.75, 1.15, 0.95, 1.4];
/** Region of each lobe (frontal, temporal, parietal, occipital) on the left (+z) and on the right. */
const LOBE_REGIONS = [[0, 1, 2, 3], [10, 9, 8, 7]];
const CEREBELLUM_REGIONS = [4, 6];
const STEM_REGION = 5;
/** The thalamus: projection fibres fan out from here to every region. */
export const BRAIN_HUB: Vec = [-0.25, -0.3, 0];
/** Farthest reach from the hub: the brain builds itself from the hub outward over this distance. */
export const BRAIN_REACH = 3.4;
const birthAt = (p: Vec) => clamp01(0.03 + (0.9 * distance(p, BRAIN_HUB)) / BRAIN_REACH);

/**
 * A smooth random field of fixed sine waves in random directions, about unit variance. Its zero set is
 * a meandering labyrinth of lines about half a wavelength apart: the sulci, with the gyri between them.
 */
const WAVES = (() => {
  const r = stream(0x2545f491);
  return Array.from({ length: 14 }, () => {
    const d = randomUnit(r);
    const k = 5.2 + 2.4 * r();
    return [d[0] * k, d[1] * k, d[2] * k, 2 * Math.PI * r()];
  });
})();
function gyral(p: Vec): number {
  // A gentle warp keeps the folds from repeating like a wave pattern.
  const q = add(p, [Math.sin(1.7 * p[1] + 1), Math.sin(1.9 * p[2] + 2), Math.sin(1.3 * p[0] + 3)], 0.25);
  let sum = 0;
  for (const [a, b, c, phase] of WAVES) sum += Math.sin(a * q[0] + b * q[1] + c * q[2] + phase);
  return sum / Math.sqrt(WAVES.length / 2);
}
/** 1 on a gyral crown, 0 at the floor of a sulcus. */
function crown(p: Vec): number {
  const f = gyral(p);
  return 1 - Math.exp(-1.6 * f * f);
}
/** Roughly how far `p` is from the floor of the nearest sulcus, in layout units: the field over its gradient. */
function sulcusDistance(p: Vec): number {
  const h = 0.005;
  const at = gyral(p);
  const slope = Math.hypot(gyral(add(p, [h, 0, 0])) - at, gyral(add(p, [0, h, 0])) - at, gyral(add(p, [0, 0, h])) - at) / h;
  return Math.min(0.5, Math.abs(at) / Math.max(slope, 1e-3));
}

/** Distance from the centre to the unfolded cerebrum in unit direction `d`; `groove` 0 leaves out the fissure. */
function cerebrumRadius(d: Vec, groove = 1): number {
  const [x, y, z] = d;
  // The temporal lobes hang low at the sides; the underside is flat at the front and lifts over the cerebellum at the back.
  const temporal = Math.exp(-(((x - 0.1) / 0.32) ** 2)) * smooth(0.35, 0.75, Math.abs(z));
  const below = (1.15 + 0.6 * temporal) * (1 - 0.22 * smooth(0.3, 0.8, x)) * (1 - 0.38 * smooth(0.15, 0.65, -x) * (1 - temporal));
  const rx = x >= 0 ? 2.7 : 2.85;
  const ry = y >= 0 ? 1.9 : below;
  const rz = 2.25 * (1 - 0.12 * smooth(0.25, 0.9, x)) * (1 - 0.1 * smooth(0.45, 0.95, -x));
  // The longitudinal fissure between the hemispheres, deepest on top.
  const fissure = 1 - groove * 0.14 * Math.exp(-((z / 0.055) ** 2)) * smooth(-0.6, 0.1, y);
  return fissure / Math.hypot(x / rx, y / ry, z / rz);
}
/** Height of the Sylvian fissure (the temporal lobe below it) at `x`. */
const sylvian = (x: number) => -0.2 - 0.38 * (x - 0.3);
/** Where the central sulcus (frontal lobe in front, parietal behind) crosses height `y`. */
const central = (y: number) => 0.3 - 0.32 * y;
/** The folded cortex in unit direction `d`: gyri, with the deeper Sylvian fissure and central sulcus. */
function cortexSurface(d: Vec): Vec {
  const r = cerebrumRadius(d);
  const p: Vec = [d[0] * r, d[1] * r, d[2] * r];
  const lateral = smooth(0.45, 0.7, Math.abs(d[2]));
  const fissure = lateral * smooth(-1.5, -0.7, p[0]) * (1 - smooth(1.0, 1.5, p[0])) * Math.exp(-(((p[1] - sylvian(p[0])) / 0.1) ** 2));
  const sulcus = smooth(-0.2, 0.5, p[1]) * Math.exp(-(((p[0] - central(p[1])) / 0.07) ** 2));
  const fold = 1 + 0.05 * (crown(p) - 0.7) - 0.05 * fissure - 0.025 * sulcus;
  // The hemispheres stand a little apart across the fissure.
  return [p[0] * fold, p[1] * fold, p[2] * fold + 0.07 * Math.tanh(p[2] / 0.02)];
}
/** 0 frontal, 1 temporal, 2 parietal, 3 occipital. */
function lobeAt(p: Vec): number {
  if (p[0] < -1.85 + 0.3 * p[1]) return 3;
  if (p[1] < sylvian(p[0]) && p[0] > -1.9 && Math.abs(p[2]) > 0.45) return 1;
  return p[0] > central(p[1]) ? 0 : 2;
}

const CEREBELLUM = { c: [-2.05, -1.42, 0] as Vec, r: [0.95, 0.72, 1.5] as Vec };
/** The cerebellum's surface in direction `u` of its own ellipsoid: a notch for the vermis, and fine folia. */
function cerebellumSurface(u: Vec): Vec {
  const vermis = 1 - 0.1 * Math.exp(-((u[2] / 0.16) ** 2)) * smooth(-0.3, 0.5, -u[0]);
  const f = vermis * (1 + 0.028 * Math.cos(26 * Math.atan2(u[1], -u[0])));
  const { c, r } = CEREBELLUM;
  return [c[0] + u[0] * r[0] * f, c[1] + u[1] * r[1] * f, c[2] + u[2] * r[2] * f];
}
const STEM_TOP: Vec = [-0.4, -0.7, 0];
const STEM_BOTTOM: Vec = [-0.85, -3.0, 0];
const STEM_LENGTH = distance(STEM_TOP, STEM_BOTTOM);
const STEM_AXIS = normalise([STEM_BOTTOM[0] - STEM_TOP[0], STEM_BOTTOM[1] - STEM_TOP[1], 0]);
/** Forward, square to the brainstem. */
const STEM_FRONT: Vec = [-STEM_AXIS[1], STEM_AXIS[0], 0];
/** The brainstem's radius `t` of the way down; the pons bulges near the top. */
const stemRadius = (t: number) => 0.4 - 0.12 * t + 0.1 * Math.exp(-(((t - 0.32) / 0.12) ** 2));
function stemSurface(t: number, angle: number): Vec {
  const r = stemRadius(t);
  const centre = lerp(STEM_TOP, STEM_BOTTOM, t);
  return [centre[0] + STEM_FRONT[0] * Math.cos(angle) * r, centre[1] + STEM_FRONT[1] * Math.cos(angle) * r, Math.sin(angle) * r];
}
const stemAt = (p: Vec) => dot([p[0] - STEM_TOP[0], p[1] - STEM_TOP[1], p[2]], STEM_AXIS) / STEM_LENGTH;

/** Index into BRAIN_REGIONS of the structure a point lies in or nearest the surface of. */
export function brainRegionAt(p: Vec): number {
  const cerebrum = Math.hypot(p[0], p[1], p[2]) / cerebrumRadius(normalise(p));
  const { c, r } = CEREBELLUM;
  const cerebellum = Math.hypot((p[0] - c[0]) / r[0], (p[1] - c[1]) / r[1], (p[2] - c[2]) / r[2]);
  const t = stemAt(p);
  const stem = t < 0 || t > 1.1 ? Infinity : distance(p, lerp(STEM_TOP, STEM_BOTTOM, t)) / stemRadius(Math.min(1, t));
  if (cerebellum < cerebrum && cerebellum <= stem) return CEREBELLUM_REGIONS[p[2] >= 0 ? 0 : 1];
  if (stem < cerebrum) return STEM_REGION;
  return LOBE_REGIONS[p[2] >= 0 ? 0 : 1][lobeAt(p)];
}

type Area = {
  /** A random point on the region's surface. */
  draw: (r: Random) => Vec;
  /** The surface point over `p`, and the point depth below it is measured toward. */
  surface: (p: Vec) => Vec;
  core: (p: Vec) => Vec;
  /** Orders a region's points along the ring of regions, so neighbouring groups meet at its edges. */
  key: (p: Vec) => number;
  /** Sorted keys of a sample of the region's surface: their quantiles split a shared region between groups. */
  keys: number[];
};
const fibonacci = (i: number, n: number): Vec => {
  const y = 1 - (2 * (i + 0.5)) / n;
  const ring = Math.sqrt(1 - y * y);
  return [ring * Math.cos(i * GOLDEN), y, ring * Math.sin(i * GOLDEN)];
};
const AREAS: Area[] = (() => {
  // Each lobe's cone of directions, from an even scan of the cortex.
  const members: Vec[][] = BRAIN_REGIONS.map(() => []);
  for (let i = 0; i < 4000; i += 1) {
    const d = fibonacci(i, 4000);
    members[brainRegionAt(cortexSurface(d))].push(d);
  }
  /** Up to 24 draws until one lands in the region; the last otherwise. */
  const until = (region: number, candidate: () => Vec) => () => {
    let p = candidate();
    for (let attempt = 0; attempt < 24 && brainRegionAt(p) !== region; attempt += 1) p = candidate();
    return p;
  };
  const origin: Vec = [0, 0, 0];
  return BRAIN_REGIONS.map((name, region) => {
    let area: Omit<Area, "keys">;
    if (region === STEM_REGION) {
      const angle = (p: Vec) => Math.atan2(p[2], dot([p[0] - STEM_TOP[0], p[1] - STEM_TOP[1], 0], STEM_FRONT));
      area = {
        draw: (r) => until(region, () => stemSurface(0.22 + 0.78 * r(), 2 * Math.PI * r()))(),
        surface: (p) => stemSurface(clamp01(stemAt(p)), angle(p)),
        core: (p) => lerp(STEM_TOP, STEM_BOTTOM, clamp01(stemAt(p))),
        key: (p) => -p[2],
      };
    } else if (CEREBELLUM_REGIONS.includes(region)) {
      const side = region === CEREBELLUM_REGIONS[0] ? 1 : -1;
      const { c, r: radii } = CEREBELLUM;
      area = {
        draw: (r) => until(region, () => {
          const u = randomUnit(r);
          return cerebellumSurface([u[0], u[1], Math.abs(u[2]) * side]);
        })(),
        // Its ellipsoid without the folia: paths wandering under it stay smooth.
        surface: (p) => {
          const u = normalise([(p[0] - c[0]) / radii[0], (p[1] - c[1]) / radii[1], (p[2] - c[2]) / radii[2]]);
          return [c[0] + u[0] * radii[0], c[1] + u[1] * radii[1], c[2] + u[2] * radii[2]];
        },
        core: () => c,
        key: (p) => -p[2],
      };
    } else {
      const list = members[region];
      const dir = normalise(list.reduce((sum, d) => add(sum, d), [0, 0, 0] as Vec));
      const spread = Math.min(Math.PI, Math.max(...list.map((d) => Math.acos(Math.min(1, dot(d, dir))))) + 0.05);
      const left = LOBE_REGIONS[0].includes(region);
      area = {
        draw: (r) => until(region, () => cortexSurface(capDirection(dir, spread, r)))(),
        // The unfolded surface without its fissure: paths wandering under it stay smooth.
        surface: (p) => {
          const d = normalise(p);
          const r = cerebrumRadius(d, 0);
          return [d[0] * r, d[1] * r, d[2] * r];
        },
        core: () => origin,
        key: (p) => (left ? -p[0] : p[0]),
      };
    }
    const r = stream(hash32(`area:${name}`));
    return { ...area, keys: Array.from({ length: 300 }, () => area.key(area.draw(r))).sort((a, b) => a - b) };
  });
})();
/** `p` moved to `depth` below its region's surface. */
function deepen(region: number, p: Vec, depth: number): Vec {
  const area = AREAS[region];
  const top = area.surface(p);
  const core = area.core(p);
  return lerp(top, core, Math.min(0.9, depth / (distance(top, core) || 1)));
}

type Owner = { group: number; nodes: number };
/** A group's slice of a shared region: the range of the region's keys it holds. */
type Patch = { group: number; lo: number; hi: number };
/** Counts summing to `total`, proportional to `weights`, each at least one (largest remainder). */
function apportion(weights: number[], total: number): number[] {
  const sum = weights.reduce((a, b) => a + b, 0) || 1;
  const counts = weights.map(() => 1);
  for (let left = total - weights.length; left > 0; left -= 1) {
    let best = 0;
    weights.forEach((weight, k) => {
      if ((weight / sum) * total - counts[k] > (weights[best] / sum) * total - counts[best]) best = k;
    });
    counts[best] += 1;
  }
  return counts;
}
/**
 * Which groups own each region, and how many of their nodes it holds. The groups are laid end to
 * end around the ring of regions, each over a stretch as long as its share of the nodes, so every
 * region holds nodes in proportion to its room: a large group spans a run of neighbouring regions,
 * small neighbours share one. A sliver of a group at a region's edge folds back into its other regions.
 */
function territories(model: BrainModel): Owner[][] {
  const owners: Owner[][] = BRAIN_REGIONS.map(() => []);
  const nodes = model.groups.map((group) => group.edits + group.records);
  const total = nodes.reduce((a, b) => a + b, 0);
  if (!total) {
    if (nodes.length) owners.forEach((own) => own.push({ group: 0, nodes: 0 }));
    return owners;
  }
  const capacity = REGION_WEIGHTS.reduce((a, b) => a + b, 0);
  const spans: { region: number; overlap: number }[][] = nodes.map(() => []);
  let start = 0;
  nodes.forEach((count, g) => {
    const end = start + (count / total) * capacity;
    let edge = 0;
    REGION_WEIGHTS.forEach((weight, region) => {
      const overlap = Math.min(end, edge + weight) - Math.max(start, edge);
      if (overlap > 1e-9 || (count === 0 && start >= edge && start < edge + weight)) spans[g].push({ region, overlap: Math.max(overlap, 1e-9) });
      edge += weight;
    });
    start = end;
  });
  spans.forEach((span, g) => {
    // Keep the group's slivers only where it is a region's main owner.
    const main = (region: number) => !spans.some((other, h) => h !== g && other.some((s) => s.region === region && s.overlap > span.find((own) => own.region === region)!.overlap));
    const kept = span.filter((s) => s.overlap >= 0.12 * REGION_WEIGHTS[s.region] || main(s.region));
    const use = kept.length ? kept : [span.reduce((a, b) => (b.overlap > a.overlap ? b : a))];
    const sum = use.reduce((a, s) => a + s.overlap, 0);
    for (const s of use) owners[s.region].push({ group: g, nodes: (nodes[g] * s.overlap) / sum });
  });
  return owners;
}
/** Each owner's slice of a region, as a range of the region's keys: owners in ring order, sized by their nodes. */
function patches(region: number, owners: Owner[]): Patch[] {
  const keys = AREAS[region].keys;
  const total = owners.reduce((sum, owner) => sum + owner.nodes, 0);
  const share = (owner: Owner) => owner.nodes + total * 0.02 + 1e-9;
  const sum = owners.reduce((a, owner) => a + share(owner), 0);
  const quantile = (f: number) => (f <= 1e-9 ? -Infinity : f >= 1 - 1e-9 ? Infinity : keys[Math.min(keys.length - 1, Math.floor(f * keys.length))]);
  let run = 0;
  return [...owners].sort((a, b) => a.group - b.group).map((owner) => {
    const lo = quantile(run / sum);
    run += share(owner);
    return { group: owner.group, lo, hi: quantile(run / sum) };
  });
}
/** A point on region `region`'s surface inside `patch`; folded regions favour the gyral crowns. */
function drawIn(region: number, patch: Patch, r: Random, folded: boolean): Vec {
  const area = AREAS[region];
  let best = area.draw(r);
  let miss = Infinity;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const p = attempt ? area.draw(r) : best;
    const key = area.key(p);
    const off = key < patch.lo ? patch.lo - key : key > patch.hi ? key - patch.hi : 0;
    if (off === 0 && (!folded || r() < 0.15 + 0.85 * crown(p) ** 2)) return p;
    if (off < miss) {
      best = p;
      miss = off;
    }
  }
  return best;
}
const ownerAt = (region: number, slices: Patch[], p: Vec) => {
  const key = AREAS[region].key(p);
  return (slices.find((patch) => key >= patch.lo && key <= patch.hi) ?? slices[key < slices[0].lo ? 0 : slices.length - 1]).group;
};

type Path = { points: Vec[]; lengths: number[]; total: number };
/** A centripetal Catmull-Rom curve through `knots` (no cusps or loops, however unevenly they are spaced), sampled densely so points can be placed by arc length. */
function curve(knots: Vec[], steps: number): Path {
  const points: Vec[] = [];
  const last = knots.length - 1;
  for (let k = 0; k < last; k += 1) {
    const p1 = knots[k], p2 = knots[k + 1];
    // Beyond the ends, mirrored phantom knots.
    const p0 = k > 0 ? knots[k - 1] : add(p1, add(p2, p1, -1), -1);
    const p3 = k + 2 <= last ? knots[k + 2] : add(p2, add(p2, p1, -1));
    const t1 = Math.sqrt(distance(p0, p1)) || 1e-6;
    const t2 = t1 + (Math.sqrt(distance(p1, p2)) || 1e-6);
    const t3 = t2 + (Math.sqrt(distance(p2, p3)) || 1e-6);
    for (let step = 0; step < steps; step += 1) {
      const t = t1 + ((t2 - t1) * step) / steps;
      const a1 = lerp(p0, p1, t / t1), a2 = lerp(p1, p2, (t - t1) / (t2 - t1)), a3 = lerp(p2, p3, (t - t2) / (t3 - t2));
      points.push(lerp(lerp(a1, a2, t / t2), lerp(a2, a3, (t - t1) / (t3 - t1)), (t - t1) / (t2 - t1)));
    }
  }
  points.push(knots[knots.length - 1]);
  const lengths = [0];
  for (let i = 1; i < points.length; i += 1) lengths.push(lengths[i - 1] + distance(points[i], points[i - 1]));
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
  return lerp(path.points[lo], path.points[hi], (goal - path.lengths[lo]) / span);
}
/** Longest straight piece of a filament: short enough to read as a smooth curve. */
const STEP = 0.075;
/** A smooth spline through `knots`, in even pieces of at most STEP (and at least four). */
function spline(knots: Vec[]): Vec[] {
  // Longer runs of knots first lose their kinks to two passes of a moving average (the ends stay put).
  if (knots.length >= 4)
    for (let pass = 0; pass < 2; pass += 1)
      for (let k = knots.length - 2; k > 0; k -= 1) knots[k] = lerp(lerp(knots[k - 1], knots[k + 1], 0.5), knots[k], 0.5);
  const path = curve(knots, 16);
  const pieces = Math.max(5, Math.ceil(path.total / STEP));
  const points = Array.from({ length: pieces + 1 }, (_, k) => along(path, k / pieces));
  // Where knots still meet at a corner, relax the samples around it until no bend exceeds ~20°.
  const sharp = (k: number) => {
    const u = add(points[k], points[k - 1], -1), v = add(points[k + 1], points[k], -1);
    return dot(u, v) < 0.94 * Math.hypot(...u) * Math.hypot(...v);
  };
  for (let pass = 0; pass < 40; pass += 1) {
    const corners = points.slice(1, -1).flatMap((_, k) => (sharp(k + 1) ? [k + 1] : []));
    if (!corners.length) break;
    for (const corner of corners)
      for (let k = Math.max(1, corner - 2); k <= Math.min(points.length - 2, corner + 2); k += 1) points[k] = lerp(lerp(points[k - 1], points[k + 1], 0.5), points[k], 0.5);
  }
  return points;
}
/** A smooth path wandering `length` under region `region`'s surface from `from`, rising steadily to `depth` below it. */
function wander(region: number, from: Vec, length: number, depth: number, r: Random): Vec[] {
  const area = AREAS[region];
  const tangent = (p: Vec, v: Vec) => {
    const n = normalise(add(area.surface(p), area.core(p), -1));
    return normalise(add(v, n, -dot(v, n)));
  };
  const start = distance(area.surface(from), from);
  const steps = Math.max(2, Math.round(length / 0.12));
  const knots: Vec[] = [from];
  let p = from;
  let heading = tangent(p, randomUnit(r));
  for (let k = 1; k <= steps; k += 1) {
    heading = tangent(p, add(heading, randomUnit(r), 0.25));
    p = deepen(region, add(p, heading, length / steps), start + ((depth - start) * k) / steps);
    knots.push(p);
  }
  return spline(knots);
}

/** One filament of the network: a smooth polyline with its width at each end, colour and pulse phase. */
type Filament = { points: Vec[]; width: [number, number]; group: number; other: number; base: number; seed: number };
/**
 * The network as segments (instanced by the renderer): xyz at each end, width and path distance at each
 * end (pulses travel along it), a pulse phase, and the two groups whose colours blend (`mixes`: 0 to 1) along it.
 */
export type BrainNetwork = {
  count: number;
  starts: Float32Array;
  ends: Float32Array;
  widths: Float32Array;
  dists: Float32Array;
  seeds: Float32Array;
  groups: Float32Array;
  others: Float32Array;
  mixes: Float32Array;
};
function pack(filaments: Filament[]): BrainNetwork {
  const count = filaments.reduce((sum, filament) => sum + filament.points.length - 1, 0);
  const network: BrainNetwork = {
    count,
    starts: new Float32Array(count * 3), ends: new Float32Array(count * 3), widths: new Float32Array(count * 2), dists: new Float32Array(count * 2),
    seeds: new Float32Array(count), groups: new Float32Array(count), others: new Float32Array(count), mixes: new Float32Array(count),
  };
  let k = 0;
  for (const { points, width, group, other, base, seed } of filaments) {
    const pieces = points.length - 1;
    let run = base;
    for (let j = 0; j < pieces; j += 1, k += 1) {
      network.starts.set(points[j], k * 3);
      network.ends.set(points[j + 1], k * 3);
      network.widths[k * 2] = width[0] + ((width[1] - width[0]) * j) / pieces;
      network.widths[k * 2 + 1] = width[0] + ((width[1] - width[0]) * (j + 1)) / pieces;
      network.dists[k * 2] = run;
      run += distance(points[j], points[j + 1]);
      network.dists[k * 2 + 1] = run;
      network.seeds[k] = seed;
      network.groups[k] = group;
      network.others[k] = other;
      network.mixes[k] = (j + 0.5) / pieces;
    }
  }
  return network;
}
const runLength = (points: Vec[], upTo = points.length - 1) => {
  let sum = 0;
  for (let k = 0; k < upTo; k += 1) sum += distance(points[k], points[k + 1]);
  return sum;
};

/** Where a projection fibre to `end` (deep in `region`) leaves the hub and the knots it bends through. */
function projection(region: number, end: Vec, r: Random): Vec[] {
  if (region === STEM_REGION) {
    const start = add(lerp(STEM_TOP, BRAIN_HUB, 0.5), jitter(r, 0.05));
    return [start, add(lerp(start, end, 0.5), jitter(r, 0.06)), end];
  }
  if (CEREBELLUM_REGIONS.includes(region)) {
    // Through the cerebellar peduncles from the pons.
    const side = region === CEREBELLUM_REGIONS[0] ? 1 : -1;
    return [add(lerp(STEM_TOP, STEM_BOTTOM, 0.3), jitter(r, 0.06)), add([-1.35, -1.35, 0.5 * side], jitter(r, 0.1)), end];
  }
  // Each fibre leaves the side of its hemisphere's thalamus that faces its target, so they fan rather than meet in a point.
  const thalamus: Vec = [BRAIN_HUB[0], BRAIN_HUB[1], end[2] >= 0 ? 0.3 : -0.3];
  const toward = normalise(add(end, thalamus, -1));
  const start = add(add(thalamus, [toward[0] * 0.32, toward[1] * 0.2, toward[2] * 0.14]), jitter(r, 0.07));
  const bend = 0.1 * Math.min(1, distance(start, end) / 2);
  return [start, add(lerp(start, end, 0.35), add([0, bend, 0], jitter(r, bend))), add(lerp(start, end, 0.7), jitter(r, bend)), end];
}
/** Long association tracts within each hemisphere: from one region to another through `knots` (left side; z mirrors). */
const TRACTS: { from: number; to: number; hints: [Vec, Vec]; knots: Vec[] }[] = [
  // Arcuate fasciculus: frontal around the Sylvian fissure to temporal.
  { from: 0, to: 1, hints: [[1.3, 0.3, 1.5], [-0.3, -0.9, 1.7]], knots: [[0.6, 0.15, 1.45], [-0.3, 0.5, 1.5], [-1.05, 0.3, 1.45], [-1.25, -0.2, 1.45], [-0.9, -0.6, 1.5]] },
  // Superior longitudinal fasciculus: frontal over to occipital.
  { from: 0, to: 3, hints: [[1.5, 0.9, 1.2], [-2.3, 0.4, 0.9]], knots: [[0.9, 0.85, 1.25], [-0.5, 1.05, 1.2], [-1.6, 0.7, 1.0]] },
  // Inferior longitudinal fasciculus: temporal to occipital.
  { from: 1, to: 3, hints: [[0.6, -1.0, 1.6], [-2.4, -0.3, 1.0]], knots: [[-0.3, -0.75, 1.35], [-1.3, -0.55, 1.15]] },
  // Uncinate fasciculus: the underside of the frontal lobe hooked to the temporal pole.
  { from: 0, to: 1, hints: [[1.8, -0.4, 0.8], [1.1, -1.1, 1.6]], knots: [[1.15, -0.5, 0.95], [0.95, -0.8, 1.3]] },
  // Cingulum: along the medial wall, frontal to parietal.
  { from: 0, to: 2, hints: [[1.6, 0.7, 0.4], [-1.2, 1.1, 0.4]], knots: [[1.0, 0.85, 0.32], [-0.2, 1.05, 0.3], [-1.0, 0.95, 0.32]] },
  // Cerebellar peduncle: brainstem to cerebellum.
  { from: -1, to: -2, hints: [[-0.65, -1.6, 0.15], [-2.2, -1.3, 0.9]], knots: [[-0.95, -1.55, 0.3], [-1.3, -1.45, 0.6]] },
];
/** Commissures between the hemispheres: the corpus callosum (frontal, parietal, occipital) and the anterior commissure (temporal). */
const COMMISSURES: { lobe: number; hint: Vec; knots: Vec[] }[] = [
  { lobe: 0, hint: [1.4, 0.9, 1.0], knots: [[0.9, 0.5, 0.6], [0.9, 0.68, 0], [0.9, 0.5, -0.6]] },
  { lobe: 2, hint: [-0.7, 1.3, 1.0], knots: [[-0.4, 0.55, 0.6], [-0.4, 0.72, 0], [-0.4, 0.55, -0.6]] },
  { lobe: 3, hint: [-2.3, 0.4, 0.8], knots: [[-1.3, 0.4, 0.6], [-1.3, 0.55, 0], [-1.3, 0.4, -0.6]] },
  { lobe: 1, hint: [0.6, -0.9, 1.6], knots: [[0.3, -0.4, 0.7], [0.3, -0.3, 0], [0.3, -0.4, -0.7]] },
];
const mirror = (p: Vec, side: number): Vec => [p[0], p[1], p[2] * side];
/** Most fibres in one tract, and most neuron-to-neuron and record links drawn. */
const MOST_FIBRES = 8;
const MOST_LOCAL = 4000;
const MOST_LINKS = 900;

function anatomy(data: BrainData, model: BrainModel, times: Float64Array, parents: Int32Array, out: Float32Array, births: Float32Array) {
  const owners = territories(model);
  const slices = owners.map((own, region) => (own.length ? patches(region, own) : []));
  const regionsOf = model.groups.map((_, g) => owners.flatMap((own, region) => own.filter((owner) => owner.group === g).map((owner) => ({ region, nodes: owner.nodes }))));
  const total = data.nodes.length;
  const filaments: Filament[] = [];
  const branches = total < 600 ? 2 : total < 3000 ? 3 : 4;
  const twigs = total < 300 ? 0 : total < 1500 ? 1 : 2;
  /** Fine branches spreading under the cortex from near the end of `fibre`, in `group`'s colour. */
  const arbor = (region: number, fibre: Vec[], base: number, group: number, count: number, r: Random) => {
    const fork = Math.max(0, fibre.length - 3);
    const forkDist = base + runLength(fibre, fork);
    for (let b = 0; b < count; b += 1) {
      const points = wander(region, fibre[fork], 0.3 + 0.35 * r(), 0.07, r);
      filaments.push({ points, width: [0.011, 0.006], group, other: group, base: forkDist, seed: r() });
      for (let t = 0; t < twigs; t += 1) {
        const k = Math.floor((0.35 + 0.5 * r()) * (points.length - 1));
        filaments.push({ points: wander(region, points[k], 0.15 + 0.2 * r(), 0.04, r), width: [0.006, 0.0035], group, other: group, base: forkDist + runLength(points, k), seed: r() });
      }
    }
  };

  // Projection fibres from the hub to every region, dealt to its groups by size.
  owners.forEach((own, region) => {
    if (!own.length) return;
    const nodes = own.reduce((sum, owner) => sum + owner.nodes, 0);
    const deal = apportion(own.map((owner) => owner.nodes || 1), Math.max(own.length, Math.min(12, 2 + Math.floor(nodes / 220))));
    let f = 0;
    own.forEach((owner, k) => {
      for (let n = 0; n < deal[k]; n += 1, f += 1) {
        const r = stream(hash32(`fibre:${BRAIN_REGIONS[region]}:${f}`));
        const end = deepen(region, drawIn(region, slices[region].find((patch) => patch.group === owner.group)!, r, false), 0.28);
        const points = spline(projection(region, end, r));
        filaments.push({ points, width: [0.024, 0.012], group: owner.group, other: owner.group, base: 0, seed: r() });
        arbor(region, points, 0, owner.group, branches, r);
      }
    });
  });
  // Association tracts within each hemisphere and commissures across: bundles of fibres between regions.
  const bundle = Math.min(MOST_FIBRES, 2 + Math.floor(total / 800));
  /** The region point (deep in its white matter) nearest to `hint` among a few draws. */
  const endIn = (region: number, hint: Vec, r: Random) => {
    let best = AREAS[region].draw(r);
    for (let k = 0; k < 5; k += 1) {
      const p = AREAS[region].draw(r);
      if (distance(p, hint) < distance(best, hint)) best = p;
    }
    return deepen(region, best, 0.3);
  };
  const tract = (name: string, a: number, b: number, hints: [Vec, Vec], knots: Vec[]) => {
    if (!slices[a].length || !slices[b].length) return;
    for (let n = 0; n < bundle; n += 1) {
      const r = stream(hash32(`tract:${name}:${n}`));
      const from = endIn(a, hints[0], r), to = endIn(b, hints[1], r);
      const offset = jitter(r, 0.12);
      const points = spline([from, ...knots.map((knot) => add(add(knot, offset), jitter(r, 0.04))), to]);
      const [g, h] = [ownerAt(a, slices[a], from), ownerAt(b, slices[b], to)];
      filaments.push({ points, width: [0.018, 0.014], group: g, other: h, base: 0, seed: r() });
      arbor(b, points, 0, h, 1, r);
      arbor(a, [...points].reverse(), 0, g, 1, r);
    }
  };
  [1, -1].forEach((side, s) => {
    const lobes = LOBE_REGIONS[s];
    const regionOf = (index: number) => (index === -1 ? STEM_REGION : index === -2 ? CEREBELLUM_REGIONS[s] : lobes[index]);
    TRACTS.forEach((t, k) => tract(`${side}:${k}`, regionOf(t.from), regionOf(t.to), [mirror(t.hints[0], side), mirror(t.hints[1], side)], t.knots.map((knot) => mirror(knot, side))));
  });
  COMMISSURES.forEach((c, k) => tract(`commissure:${k}`, LOBE_REGIONS[0][c.lobe], LOBE_REGIONS[1][c.lobe], [c.hint, mirror(c.hint, -1)], c.knots));

  // Neurons: each record in the cortex of one of its group's regions, on the gyral crowns of its group's patch.
  const somata: number[][] = BRAIN_REGIONS.map(() => []);
  const regionOfNode = new Int16Array(total).fill(-1);
  const edits: number[][] = model.groups.map(() => []);
  data.nodes.forEach((node, i) => {
    const g = model.groupOf[i];
    if (node.kind === "edit") {
      edits[g].push(i);
      return;
    }
    const r = stream(hash32(node.id));
    const mine = regionsOf[g];
    let roll = r() * mine.reduce((sum, own) => sum + own.nodes + 1e-9, 0), pick = 0;
    while (pick < mine.length - 1 && (roll -= mine[pick].nodes + 1e-9) > 0) pick += 1;
    const region = mine[pick].region;
    // In the cortical layers just under the folded surface.
    const top = drawIn(region, slices[region].find((patch) => patch.group === g)!, r, region !== STEM_REGION && !CEREBELLUM_REGIONS.includes(region));
    const core = AREAS[region].core(top);
    const p = lerp(top, core, (0.02 + 0.22 * r() ** 1.6) / (distance(top, core) || 1));
    out.set(p, i * 3);
    births[i] = birthAt(p);
    somata[region].push(i);
    regionOfNode[i] = region;
  });
  const position = (i: number): Vec => [out[i * 3], out[i * 3 + 1], out[i * 3 + 2]];
  // Edits travel their group's own fibres (projections and the arbors they fan into) in time order:
  // the newest nearest the hub, the oldest out at the finest branches under the cortex.
  const own = model.groups.map(() => [] as Filament[]);
  for (const filament of filaments) if (filament.group === filament.other && filament.group >= 0) own[filament.group].push(filament);
  edits.forEach((members, g) => {
    const pieces: { a: Vec; b: Vec; dist: number; length: number }[] = [];
    for (const { points, base } of own[g]) {
      let run = base;
      for (let k = 0; k + 1 < points.length; k += 1) {
        const length = distance(points[k], points[k + 1]);
        pieces.push({ a: points[k], b: points[k + 1], dist: run + length / 2, length });
        run += length;
      }
    }
    if (!pieces.length) return;
    pieces.sort((x, y) => x.dist - y.dist);
    const cumulative = new Float64Array(pieces.length);
    let sum = 0;
    pieces.forEach((piece, k) => (cumulative[k] = sum += piece.length));
    const rank = timeRank(members, times);
    for (const i of members) {
      const r = stream(hash32(data.nodes[i].id) ^ 0x5bd1e995);
      const age = 1 - (rank.get(i) ?? r());
      const goal = clamp01(0.04 + 0.92 * age + 0.04 * (r() - 0.5)) * sum;
      let lo = 0, hi = pieces.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (cumulative[mid] < goal) lo = mid + 1;
        else hi = mid;
      }
      const p = lerp(pieces[lo].a, pieces[lo].b, r());
      const spread = 0.012;
      const placed: Vec = [p[0] + gauss(r) * spread, p[1] + gauss(r) * spread, p[2] + gauss(r) * spread];
      out.set(placed, i * 3);
      births[i] = birthAt(placed);
    }
  });
  // Local circuits: each neuron reaches its nearest neighbour in the same region along a short curved dendrite.
  const cell = 0.5;
  const pairs = new Set<string>();
  let local = 0;
  somata.forEach((members, region) => {
    const grid = new Map<string, number[]>();
    const cellOf = (p: Vec) => p.map((value) => Math.floor(value / cell));
    for (const i of members) {
      const key = cellOf(position(i)).join(",");
      grid.set(key, [...(grid.get(key) ?? []), i]);
    }
    for (const i of members) {
      if (local >= MOST_LOCAL) return;
      const a = position(i);
      const [cx, cy, cz] = cellOf(a);
      let best = -1, bestDistance = cell;
      for (let dx = -1; dx <= 1; dx += 1)
        for (let dy = -1; dy <= 1; dy += 1)
          for (let dz = -1; dz <= 1; dz += 1)
            for (const j of grid.get(`${cx + dx},${cy + dy},${cz + dz}`) ?? []) {
              const d = distance(a, position(j));
              if (j !== i && d < bestDistance) {
                best = j;
                bestDistance = d;
              }
            }
      const key = `${Math.min(i, best)}:${Math.max(i, best)}`;
      if (best < 0 || pairs.has(key)) continue;
      pairs.add(key);
      local += 1;
      const r = stream(hash32(`local:${data.nodes[i].id}`));
      const b = position(best);
      const mid = lerp(a, b, 0.5);
      const inward = normalise(add(AREAS[region].core(mid), AREAS[region].surface(mid), -1));
      const points = spline([a, add(add(mid, inward, 0.08 * bestDistance), jitter(r, 0.03 * bestDistance)), b]);
      filaments.push({ points, width: [0.005, 0.005], group: model.groupOf[i], other: model.groupOf[best], base: 0, seed: r() });
    }
  });
  // The records' own links (a page in its notebook, a card in its deck): arcs through the white matter.
  const linked = data.nodes.flatMap((_, i) => (regionOfNode[i] >= 0 && parents[i] >= 0 && regionOfNode[parents[i]] >= 0 ? [i] : []));
  const stride = Math.max(1, linked.length / MOST_LINKS);
  for (let n = 0; n < Math.min(linked.length, MOST_LINKS); n += 1) {
    const i = linked[Math.floor(n * stride)];
    const a = position(i), b = position(parents[i]);
    const span = distance(a, b);
    if (span < 1e-4) continue;
    const mid = lerp(a, b, 0.5);
    const sag = span * (0.12 + 0.13 * Math.min(1, span / 2.5));
    const points = spline([a, add(mid, normalise(add(BRAIN_HUB, mid, -1)), sag), b]);
    filaments.push({ points, width: [0.008, 0.008], group: model.groupOf[i], other: model.groupOf[parents[i]], base: 0, seed: stream(hash32(`link:${data.nodes[i].id}`))() });
  }

  // Labels float just outside the brain, over each group's own stretch of cortex.
  const anchors = new Float32Array(model.groups.length * 3);
  const middle: Vec = [-0.2, -0.4, 0];
  const { c: bulb, r: bulbRadii } = CEREBELLUM;
  const inside = (p: Vec) => {
    const t = stemAt(p);
    return Math.hypot(p[0], p[1], p[2]) < cerebrumRadius(normalise(p), 0) * 1.04
      || Math.hypot((p[0] - bulb[0]) / bulbRadii[0], (p[1] - bulb[1]) / bulbRadii[1], (p[2] - bulb[2]) / bulbRadii[2]) < 1.06
      || (t >= 0 && t <= 1 && distance(p, lerp(STEM_TOP, STEM_BOTTOM, t)) < stemRadius(t) * 1.1);
  };
  model.groups.forEach((_, g) => {
    const neurons = data.nodes.flatMap((_, i) => (model.groupOf[i] === g && regionOfNode[i] >= 0 ? [position(i)] : []));
    const points = neurons.length ? neurons : own[g].map((filament) => filament.points[filament.points.length - 1]);
    if (!points.length) return;
    const centre: Vec = [0, 1, 2].map((c) => points.reduce((sum, p) => sum + p[c], 0) / points.length) as Vec;
    const out = normalise(add(centre, middle, -1));
    let anchor = centre;
    for (let step = 0; step < 40 && inside(anchor); step += 1) anchor = add(anchor, out, 0.1);
    anchors.set(add(anchor, out, 0.12), g * 3);
  });
  return { anchors, regions: null, network: pack(filaments), center: [-0.1, -0.5, 0] as Vec, radius: 3.4 };
}

/** Each region and the groups that own it (largest first). */
export function brainAnatomy(model: BrainModel): { regions: { name: string; groups: number[] }[] } {
  return { regions: territories(model).map((own, region) => ({ name: BRAIN_REGIONS[region], groups: [...own].sort((a, b) => b.nodes - a.nodes || a.group - b.group).map((owner) => owner.group) })) };
}

/**
 * The brain's folded surface: a triangle mesh, its normals taken from the unfolded surface (so its rim
 * is one clean silhouette) and, per vertex, how far it is from the floor of the nearest sulcus.
 */
export type BrainSurface = { positions: Float32Array; normals: Float32Array; indices: Uint32Array; sulci: Float32Array };
let shell: BrainSurface | null = null;
/** The brain's folded surface as a triangle mesh (cerebrum, cerebellum, brainstem), for its rim of light and traced sulci. */
export function brainShell(): BrainSurface {
  if (shell) return shell;
  const positions: number[] = [];
  const unfolded: number[] = [];
  const indices: number[] = [];
  const sulci: number[] = [];
  /** A closed ring grid of `rows` × `columns`: `at` gives each vertex's folded and unfolded place and its sulcus distance. */
  const grid = (rows: number, columns: number, at: (u: number, v: number) => [Vec, Vec, number]) => {
    const base = positions.length / 3;
    for (let i = 0; i <= rows; i += 1)
      for (let j = 0; j < columns; j += 1) {
        const [p, smooth, sulcus] = at(i / rows, j / columns);
        positions.push(...p);
        unfolded.push(...smooth);
        sulci.push(sulcus);
      }
    for (let i = 0; i < rows; i += 1)
      for (let j = 0; j < columns; j += 1) {
        const a = base + i * columns + j, b = base + i * columns + ((j + 1) % columns);
        indices.push(a, a + columns, b, b, a + columns, b + columns);
      }
  };
  // Poles front and back, so a ring of vertices runs exactly along the fissure.
  const around = (u: number, v: number): Vec => [Math.cos(Math.PI * u), Math.sin(Math.PI * u) * Math.cos(2 * Math.PI * v), Math.sin(Math.PI * u) * Math.sin(2 * Math.PI * v)];
  grid(220, 288, (u, v) => {
    const d = around(u, v);
    const r = cerebrumRadius(d);
    // Normals from the hemispheres without their fissure: the rim follows the outline alone.
    const smooth = cerebrumRadius(d, 0);
    return [cortexSurface(d), [d[0] * smooth, d[1] * smooth, d[2] * smooth], sulcusDistance([d[0] * r, d[1] * r, d[2] * r])];
  });
  const { c, r: radii } = CEREBELLUM;
  grid(64, 96, (u, v) => {
    const d = around(u, v);
    // The folia: 26 ridges around the cerebellum's side.
    const phase = (26 * Math.atan2(d[1], -d[0])) / (2 * Math.PI);
    const sulcus = (Math.abs(phase - Math.floor(phase) - 0.5) * 2 * Math.PI * 0.8) / 26;
    return [cerebellumSurface(d), [c[0] + d[0] * radii[0], c[1] + d[1] * radii[1], c[2] + d[2] * radii[2]], sulcus];
  });
  grid(24, 28, (u, v) => {
    const p = stemSurface(0.12 + 0.88 * u, -2 * Math.PI * v);
    return [p, p, 0.5];
  });
  // Area-weighted vertex normals of the unfolded surface.
  const normals = new Float32Array(unfolded.length);
  const corner = (v: number): Vec => [unfolded[v * 3], unfolded[v * 3 + 1], unfolded[v * 3 + 2]];
  for (let k = 0; k < indices.length; k += 3) {
    const [a, b, c3] = [corner(indices[k]), corner(indices[k + 1]), corner(indices[k + 2])];
    const u = add(c3, b, -1), w = add(a, b, -1);
    const n: Vec = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
    for (let j = 0; j < 3; j += 1) for (let axis = 0; axis < 3; axis += 1) normals[indices[k + j] * 3 + axis] += n[axis];
  }
  for (let v = 0; v < normals.length; v += 3) {
    const n = normalise([normals[v], normals[v + 1], normals[v + 2]]);
    normals.set(n, v);
  }
  shell = { positions: Float32Array.from(positions), normals, indices: Uint32Array.from(indices), sulci: Float32Array.from(sulci) };
  return shell;
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
  return { anchors, regions, network: null, center: [0, 0, 0] as Vec, radius: ring + Math.max(...radii, 0.5) };
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
  return { anchors, regions: null, network: null, center: [0, 0, 0] as Vec, radius: outer + 0.5 };
}

function placeAll(data: BrainData, model: BrainModel, layout: BrainLayout) {
  const { parents, times } = nodeIndex(data);
  const positions = new Float32Array(data.nodes.length * 3);
  const births = new Float32Array(data.nodes.length);
  const placed =
    layout === "anatomy" ? anatomy(data, model, times, parents, positions, births)
    : layout === "spheres" ? spheres(data, model, positions, births)
    : wheel(data, model, times, positions, births);
  const view = layout === "anatomy" ? { azimuth: 0.55, elevation: 0.22 } : layout === "spheres" ? { azimuth: 0, elevation: 0.62 } : { azimuth: 0, elevation: 0 };
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
  return { ...sub, positions, births, groups, parents };
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

/** A faint, non-interactive frame giving each layout its shape: the brain's folded cortex, sphere outlines, wheel rings. */
export function brainScaffold(layout: BrainLayout, model: BrainModel): Float32Array {
  const points: number[] = [];
  const r = stream(hash32(`scaffold:${layout}`));
  if (layout === "anatomy") {
    // The cortex's gyral crowns, the cerebellum's folia and the brainstem: the brain reads even with few records.
    for (let n = 0; n < 6500; ) {
      const p = cortexSurface(randomUnit(r));
      if (r() < crown(p) ** 3) {
        points.push(...p);
        n += 1;
      }
    }
    for (let n = 0; n < 1400; n += 1) points.push(...cerebellumSurface(randomUnit(r)));
    for (let n = 0; n < 350; n += 1) points.push(...stemSurface(0.12 + 0.88 * r(), 2 * Math.PI * r()));
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

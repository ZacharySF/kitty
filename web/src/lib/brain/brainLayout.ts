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
  /** Point size multiplier per node: anatomy draws its packed brain finer, so the lobes keep their colours. */
  scales: Float32Array;
  /** xyz per shown group: where its label floats. */
  anchors: Float32Array;
  /** Spheres only: xyz + radius per shown group, so hovering anywhere over a sphere finds it. */
  regions: Float32Array | null;
  /** Anatomy only: the nodes drawn as whiskers and eyes, which take the theme's ink rather than their group's colour. */
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

// ── Anatomy: a standing cat drawn only as its brain and vessels ───────────
// Axes: x points where the cat faces, y is up, z runs across its body (+z toward the home camera).
// One unit is about half a head. The cat stands on y = 0 in the pose of a standing cat seen from
// the side: head up and turned toward the viewer, back level, tail stretched out behind. There is
// no body: the brain sits in the head and vessels branch from the spinal cord into each region,
// so the cat's outline is wherever its vessels run.
const HEAD_AT: Vec = [3.3, 5.5, 0];
/** The head turns ~70° toward the viewer, so the side view shows both ears and eyes. */
const HEAD_YAW = 1.2;
const FACE: Vec = [Math.cos(HEAD_YAW), 0, Math.sin(HEAD_YAW)];
const ACROSS: Vec = [-Math.sin(HEAD_YAW), 0, Math.cos(HEAD_YAW)];
/** Head-local (forward, up, across) to cat space. */
const inHead = (f: number, u: number, s: number): Vec => [HEAD_AT[0] + f * FACE[0] + s * ACROSS[0], HEAD_AT[1] + u, HEAD_AT[2] + f * FACE[2] + s * ACROSS[2]];
// Brain-local axes (as the cortex is drawn): x across, y up, z forward.
const CEREBRUM = { rx: 0.95, ry: 0.7, rz: 1.12 };
const CEREBELLUM = { x: 0, y: -0.34, z: -0.94, rx: 0.56, ry: 0.29, rz: 0.3 };
const STEM_TOP: Vec = [0, -0.56, -0.8];
const toCat = (p: Vec): Vec => inHead(p[2] - 0.12, p[1] + 0.14, p[0]);
/** Centre of the cerebrum, in cat space. */
export const CAT_BRAIN: Vec = toCat([0, 0, 0]);
const distance = (a: Vec, b: Vec) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const lerp = (a: Vec, b: Vec, t: number): Vec => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

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
const arcAt = (path: Path, knot: number, steps = 48) => path.lengths[knot * steps] / path.total;

// The spinal cord leaves the brainstem, runs along the level back to the sacrum and out along the
// tail, which is held out behind the cat and lifts a little at its tip.
const CORD = curve([
  toCat(STEM_TOP), [2.55, 5.05, -0.22], [1.85, 5.15, 0], [0.6, 5.4, 0], [-0.4, 5.32, 0], [-1.35, 5.1, 0],
  [-2.25, 4.85, -0.03], [-3.2, 4.66, -0.06], [-4.2, 4.62, -0.1], [-5.0, 4.85, -0.12], [-5.5, 5.3, -0.1], [-5.62, 5.62, -0.06],
]);
const WITHERS = arcAt(CORD, 2);
const SACRUM = arcAt(CORD, 5);
const TAIL_ROOT = arcAt(CORD, 6);
/** A limb's nerve, from the cord out to the shoulder or hip and down to the floor, on the near (+z) or far side. */
const limb = (root: number, knots: Vec[], side: number) => ({ root, path: curve([along(CORD, root), ...knots.map(([x, y, z]) => [x, y, z * side] as Vec)], 24) });
// Fore: shoulder, elbow, wrist, paw; the far foreleg reaches back in mid-stride.
const FORELEGS = [
  limb(WITHERS * 0.8, [[2.75, 4.5, 0.38], [3.35, 3.85, 0.42], [3.5, 2.65, 0.42], [3.7, 0.8, 0.42], [3.82, 0.14, 0.42]], 1),
  limb(WITHERS * 0.8, [[2.6, 4.5, 0.38], [2.85, 3.85, 0.42], [2.85, 2.7, 0.42], [2.0, 0.85, 0.42], [1.7, 0.14, 0.42]], -1),
];
// Hind: hip, knee, hock (the backward-pointing heel), foot and the base of the toes.
const HINDLEGS = [
  limb(SACRUM * 0.93, [[-1.0, 4.3, 0.5], [-0.75, 2.8, 0.52], [-1.65, 1.6, 0.5], [-1.0, 0.55, 0.5], [-0.45, 0.12, 0.5]], 1),
  limb(SACRUM * 0.93, [[-0.8, 4.3, 0.5], [-0.35, 2.9, 0.52], [-1.0, 1.7, 0.5], [-0.75, 0.5, 0.5], [-0.5, 0.12, 0.5]], -1),
];
const WRIST = FORELEGS.map((leg) => arcAt(leg.path, 4, 24));
const HOCK = HINDLEGS.map((leg) => arcAt(leg.path, 3, 24));
/** Four toes fanning forward from the end of a leg. */
const toesOf = (leg: Path) => {
  const end = leg.points[leg.points.length - 1];
  return [0, 1, 2, 3].map((k) => {
    const spread = (k - 1.5) * 0.075;
    return curve([end, [end[0] + 0.14, end[1] - 0.04, end[2] + spread * 0.5], [end[0] + 0.3, 0.04, end[2] + spread]], 6);
  });
};
const CHEST = curve([along(CORD, 0.03), [3.0, 4.55, 0], [3.75, 3.9, 0], [3.78, 3.1, 0], [3.2, 2.72, 0]], 16);
const BELLY = curve([[3.2, 2.72, 0], [2.2, 3.02, 0], [1.0, 3.18, 0], [-0.1, 3.3, 0], [-0.75, 3.55, 0]], 16);

/** A point on an ellipsoid's surface, only where `keep` allows (given the unit direction). */
function onEllipsoid(c: Vec, radii: Vec, r: Random, keep: (u: Vec) => boolean = () => true): Vec {
  let u = randomUnit(r);
  for (let attempt = 0; attempt < 16 && !keep(u); attempt += 1) u = randomUnit(r);
  return [c[0] + u[0] * radii[0], c[1] + u[1] * radii[1], c[2] + u[2] * radii[2]];
}
/** A point on a tube of radius `radius(s)` around part of a path (a leg or the tail). */
function onTube(path: Path, from: number, to: number, radius: (s: number) => number, r: Random): Vec {
  const s = from + (to - from) * r();
  const centre = along(path, s);
  const ahead = along(path, Math.min(1, s + 0.01)), behind = along(path, Math.max(0, s - 0.01));
  const tangent = normalise([ahead[0] - behind[0], ahead[1] - behind[1], ahead[2] - behind[2]]);
  const d = randomUnit(r);
  const dot = d[0] * tangent[0] + d[1] * tangent[1] + d[2] * tangent[2];
  const out = normalise([d[0] - dot * tangent[0], d[1] - dot * tangent[1], d[2] - dot * tangent[2]]);
  const width = radius(s);
  return [centre[0] + out[0] * width, centre[1] + out[1] * width, centre[2] + out[2] * width];
}

/** One vessel: a polyline with its path distance from the brain and its width at every point. */
export type CatVessel = { region: number; group: number; level: number; points: Vec[]; dist: number[]; width: number[] };
/** Part of a path as a vessel; `base` is the path distance from the brain where the path starts. */
function section(path: Path, from: number, to: number, base: number, width: [number, number]): Omit<CatVessel, "region" | "group"> {
  const count = Math.max(2, Math.ceil(((to - from) * path.total) / 0.05) + 1);
  const points: Vec[] = [], dist: number[] = [], widths: number[] = [];
  for (let k = 0; k < count; k += 1) {
    const t = k / (count - 1);
    const s = from + (to - from) * t;
    points.push(along(path, s));
    dist.push(base + s * path.total);
    widths.push(width[0] + (width[1] - width[0]) * t);
  }
  return { level: 0, points, dist, width: widths };
}
const cordAt = (s: number) => s * CORD.total;
const cord = (from: number, to: number, width: [number, number]) => section(CORD, from, to, 0, width);
const legSection = (leg: { root: number; path: Path }, from: number, to: number, width: [number, number]) => section(leg.path, from, to, cordAt(leg.root), width);
const legEnd = (leg: { root: number; path: Path }) => cordAt(leg.root) + leg.path.total;

type Region = {
  name: string;
  /** Its share of the vessel nodes, relative to the other regions. */
  weight: number;
  /** Branches off the trunks even with no data, so an empty region still draws its part of the cat. */
  min: number;
  /** How far from its trunk a branch aims before turning to the nearest surface: short twigs on legs and tail, long fans over the torso. */
  reach: number;
  /** The fixed vessels the branches grow from; `nodes` lets the flank add ribs as it fills. */
  trunks: (nodes: number) => Omit<CatVessel, "region" | "group">[];
  /** A point on the region's outer surface: where branches end, so the outline emerges from them. */
  target: (r: Random) => Vec;
};
const side = (r: Random) => (r() < 0.5 ? 0 : 1);
/** The cat's regions in anatomical order, front to back: a group that spans several takes a run of them. */
const CAT: Region[] = [
  { name: "withers", weight: 0.8, min: 3, reach: 0.9,
    trunks: () => [cord(0, WITHERS, [0.085, 0.075]), ...[-1, 1].map((z) => section(curve([along(CORD, WITHERS * 0.9), [2.3, 4.75, 0.72 * z], [2.95, 4.15, 0.62 * z]], 12), 0, 1, cordAt(WITHERS * 0.9), [0.04, 0.025]))],
    target: (r) => onEllipsoid([2.15, 4.85, 0], [0.75, 0.55, 0.8], r, (u) => u[1] > -0.4) },
  { name: "chest", weight: 1.2, min: 3, reach: 1,
    trunks: () => [section(CHEST, 0, 1, cordAt(0.03), [0.05, 0.04])],
    target: (r) => onEllipsoid([3.45, 3.75, 0], [0.82, 1.0, 0.72], r, (u) => u[0] > -0.3) },
  { name: "forelegs", weight: 1, min: 2, reach: 0.3,
    trunks: () => FORELEGS.map((leg, k) => legSection(leg, 0, WRIST[k], [0.05, 0.04])),
    target: (r) => { const k = side(r); return onTube(FORELEGS[k].path, 0.2, WRIST[k], (s) => 0.24 - 0.1 * s, r); } },
  { name: "paws", weight: 0.3, min: 2, reach: 0.25,
    trunks: () => FORELEGS.flatMap((leg, k) => [legSection(leg, WRIST[k], 1, [0.04, 0.035]), ...toesOf(leg.path).map((toe) => section(toe, 0, 1, legEnd(leg), [0.03, 0.018]))]),
    target: (r) => { const k = side(r); return onTube(FORELEGS[k].path, WRIST[k], 1, () => 0.13, r); } },
  { name: "back", weight: 1, min: 3, reach: 0.9,
    trunks: () => [cord(WITHERS, SACRUM, [0.075, 0.07])],
    target: (r) => onEllipsoid([0.45, 5.15, 0], [1.45, 0.62, 0.85], r, (u) => u[1] > -0.2) },
  { name: "flank", weight: 2, min: 2, reach: 1.1,
    // Ribs: intercostal vessels arching down each side from the cord, more of them as the flank fills.
    trunks: (nodes) => {
      const count = Math.min(6, 3 + Math.floor(nodes / 400));
      return [-1, 1].flatMap((z) => Array.from({ length: count }, (_, k) => {
        const s = WITHERS + (SACRUM - WITHERS) * (0.12 + (0.76 * k) / (count - 1));
        const [x, y] = along(CORD, s);
        return section(curve([along(CORD, s), [x - 0.1, y - 0.3, 0.8 * z], [x - 0.25, 4.05, 0.98 * z], [x - 0.35, 3.4, 0.6 * z]], 10), 0, 1, cordAt(s), [0.035, 0.022]);
      }));
    },
    target: (r) => onEllipsoid([0.55, 4.25, 0], [1.55, 1.0, 0.98], r, (u) => Math.abs(u[2]) > 0.45) },
  { name: "belly", weight: 1, min: 3, reach: 0.9,
    trunks: () => [section(BELLY, 0, 1, cordAt(0.03) + CHEST.total, [0.045, 0.035])],
    target: (r) => onEllipsoid([1.15, 3.42, 0], [1.75, 0.38, 0.72], r, (u) => u[1] < 0.3) },
  { name: "rump", weight: 1.2, min: 2, reach: 1,
    trunks: () => [-1, 1].map((z) => section(curve([along(CORD, SACRUM * 1.03), [-1.75, 4.9, 0.42 * z], [-2.0, 4.15, 0.55 * z], [-1.75, 3.35, 0.45 * z]], 12), 0, 1, cordAt(SACRUM * 1.03), [0.045, 0.03])),
    target: (r) => onEllipsoid([-1.15, 4.25, 0], [0.82, 1.1, 0.82], r) },
  { name: "hindlegs", weight: 1.2, min: 2, reach: 0.35,
    trunks: () => HINDLEGS.map((leg, k) => legSection(leg, 0, HOCK[k] - 0.035, [0.055, 0.04])),
    target: (r) => { const k = side(r); return onTube(HINDLEGS[k].path, 0.15, HOCK[k] - 0.04, (s) => 0.36 - (0.2 * s) / HOCK[k], r); } },
  { name: "hock", weight: 0.2, min: 2, reach: 0.2,
    trunks: () => HINDLEGS.map((leg, k) => legSection(leg, HOCK[k] - 0.035, HOCK[k] + 0.05, [0.04, 0.035])),
    target: (r) => { const k = side(r); return onTube(HINDLEGS[k].path, HOCK[k] - 0.035, HOCK[k] + 0.05, () => 0.15, r); } },
  { name: "feet", weight: 0.4, min: 2, reach: 0.25,
    trunks: () => HINDLEGS.map((leg, k) => legSection(leg, HOCK[k] + 0.05, 1, [0.035, 0.03])),
    target: (r) => { const k = side(r); return onTube(HINDLEGS[k].path, HOCK[k] + 0.05, 1, () => 0.12, r); } },
  { name: "toes", weight: 0.2, min: 2, reach: 0.2,
    trunks: () => HINDLEGS.flatMap((leg) => toesOf(leg.path).map((toe) => section(toe, 0, 1, legEnd(leg), [0.03, 0.018]))),
    target: (r) => { const end = HINDLEGS[side(r)].path.points.at(-1)!; return onEllipsoid([end[0] + 0.2, 0.1, end[2]], [0.2, 0.07, 0.16], r); } },
  { name: "tail base", weight: 0.4, min: 2, reach: 0.3,
    trunks: () => [cord(SACRUM, TAIL_ROOT, [0.07, 0.06])],
    target: (r) => onTube(CORD, SACRUM, TAIL_ROOT, () => 0.24, r) },
  { name: "tail", weight: 0.7, min: 3, reach: 0.3,
    trunks: () => [cord(TAIL_ROOT, 1, [0.06, 0.03])],
    target: (r) => onTube(CORD, TAIL_ROOT, 1, (s) => 0.2 - (0.12 * (s - TAIL_ROOT)) / (1 - TAIL_ROOT), r) },
];
export const CAT_REGIONS = CAT.map((region) => region.name);
/** Share of records drawn as beads on their group's vessels; the rest form the brain, ears, eyes and whiskers. */
const RECORD_VESSELS = 0.06;
/** Brain points are drawn this much smaller: thousands share the small cortex, and full-size glows would add up to white. */
const BRAIN_POINT = 0.8;

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
 * Which groups own each region, and how many of their vessel nodes it holds. The groups are laid
 * end to end along the regions (front to back), each over a stretch as long as its share of the
 * nodes, so every region holds nodes in proportion to its room: a large group spans a run of
 * regions, small neighbours share one. A sliver of a group at a region's edge folds back into
 * the group's other regions.
 */
function territories(model: BrainModel): { group: number; nodes: number }[][] {
  const owners: { group: number; nodes: number }[][] = CAT.map(() => []);
  const nodes = model.groups.map((group) => group.edits + group.records * RECORD_VESSELS);
  const total = nodes.reduce((a, b) => a + b, 0);
  if (!total) {
    if (nodes.length) CAT.forEach((_, index) => owners[index].push({ group: 0, nodes: 0 }));
    return owners;
  }
  const capacity = CAT.reduce((sum, region) => sum + region.weight, 0);
  const spans: { region: number; overlap: number }[][] = nodes.map(() => []);
  let start = 0;
  nodes.forEach((count, g) => {
    const end = start + (count / total) * capacity;
    let edge = 0;
    CAT.forEach((region, index) => {
      const overlap = Math.min(end, edge + region.weight) - Math.max(start, edge);
      if (overlap > 1e-9 || (count === 0 && start >= edge && start < edge + region.weight)) spans[g].push({ region: index, overlap: Math.max(overlap, 1e-9) });
      edge += region.weight;
    });
    start = end;
  });
  spans.forEach((span, g) => {
    // Keep the group's slivers only where it is a region's main owner.
    const main = (index: number) => !spans.some((other, h) => h !== g && other.some((s) => s.region === index && s.overlap > span.find((own) => own.region === index)!.overlap));
    const kept = span.filter((s) => s.overlap >= 0.12 * CAT[s.region].weight || main(s.region));
    const use = kept.length ? kept : [span.reduce((a, b) => (b.overlap > a.overlap ? b : a))];
    const sum = use.reduce((a, s) => a + s.overlap, 0);
    for (const s of use) owners[s.region].push({ group: g, nodes: (nodes[g] * s.overlap) / sum });
  });
  // The region's main owner leads it (its trunks are drawn from that group).
  for (const own of owners) own.sort((a, b) => b.nodes - a.nodes || a.group - b.group);
  return owners;
}

const BRANCH_WIDTH = [0, 0.032, 0.022, 0.015];
/** Vessel nodes per unit of vessel length a region grows toward, so every region glows about as densely. */
const NODES_PER_UNIT = 40;
const MOST_TREES = 90;
/**
 * Branching trees off a region's trunks, more and deeper as the region holds more nodes: each
 * branch runs from a trunk to the region's surface and forks into finer twigs ending near it.
 * Each tree has its own seed, so adding data adds trees and never moves the existing ones.
 */
function grow(region: Region, nodes: number, trunks: Omit<CatVessel, "region" | "group">[], least: number) {
  const depth = nodes < 80 ? 1 : nodes < 1200 ? 2 : 3;
  const lengths = trunks.map((trunk) => trunk.dist[trunk.dist.length - 1] - trunk.dist[0]);
  const sum = lengths.reduce((a, b) => a + b, 0);
  const goal = nodes / NODES_PER_UNIT;
  let grown = sum;
  const trees: Omit<CatVessel, "region" | "group">[][] = [];
  for (let b = 0; b < MOST_TREES && (b < Math.max(least, region.min) || grown < goal); b += 1) {
    const r = stream(hash32(`vessel:${region.name}:${b}`));
    let roll = r() * sum, pick = 0;
    while (pick < trunks.length - 1 && (roll -= lengths[pick]) > 0) pick += 1;
    const trunk = trunks[pick];
    const at = Math.round((0.08 + 0.84 * r()) * (trunk.points.length - 1));
    const start = trunk.points[at];
    // The surface point nearest to where the branch aims: a twig hugging a leg, or a fan across the torso.
    const near = (aim: Vec, tries: number) => {
      let best = region.target(r);
      for (let k = 1; k < tries; k += 1) {
        const candidate = region.target(r);
        if (distance(candidate, aim) < distance(best, aim)) best = candidate;
      }
      return best;
    };
    const tree: Omit<CatVessel, "region" | "group">[] = [];
    const branch = (from: Vec, base: number, target: Vec, level: number) => {
      const span = distance(from, target);
      const bend = randomUnit(r);
      const mid: Vec = [0, 1, 2].map((c) => (from[c] + target[c]) / 2 + bend[c] * span * 0.16) as Vec;
      const path = curve([from, mid, target], Math.max(2, Math.ceil(span / 0.1)));
      const width = BRANCH_WIDTH[level];
      const vessel = { level, points: path.points, dist: path.lengths.map((length) => base + length), width: path.lengths.map((length) => width * (1 - (0.45 * length) / (path.total || 1))) };
      tree.push(vessel);
      grown += path.total;
      if (level >= depth) return;
      for (let child = 0; child < 2; child += 1) {
        const k = Math.round((0.35 + 0.5 * r()) * (path.points.length - 1));
        const offset = randomUnit(r);
        branch(path.points[k], vessel.dist[k], near([0, 1, 2].map((c) => target[c] + offset[c] * span * 0.5) as Vec, 3), level + 1);
      }
    };
    const aim = randomUnit(r);
    branch(start, trunk.dist[at], near([0, 1, 2].map((c) => start[c] + aim[c] * region.reach) as Vec, 6), 1);
    trees.push(tree);
  }
  return trees;
}

/**
 * The cat as vessels: each region's trunks and branching trees, each vessel owned by a group.
 * Depends only on the groups and their sizes, so the layout and the scaffold always agree.
 */
export function catAnatomy(model: BrainModel): { regions: { name: string; groups: number[] }[]; vessels: CatVessel[] } {
  const owners = territories(model);
  const vessels: CatVessel[] = [];
  CAT.forEach((region, index) => {
    const own = owners[index];
    const nodes = own.reduce((sum, owner) => sum + owner.nodes, 0);
    const lead = own[0]?.group ?? -1;
    const trunks = region.trunks(nodes);
    for (const trunk of trunks) vessels.push({ ...trunk, region: index, group: lead });
    // A shared region deals its trees to its groups by size, at least one each.
    const trees = grow(region, nodes, trunks, own.length);
    const deal = own.length ? apportion(own.map((owner) => owner.nodes), trees.length) : [trees.length];
    let next = 0;
    deal.forEach((count, k) => {
      for (const tree of trees.slice(next, next + count)) for (const vessel of tree) vessels.push({ ...vessel, region: index, group: own[k]?.group ?? -1 });
      next += count;
    });
  });
  return { regions: CAT.map((region, index) => ({ name: region.name, groups: owners[index].map((owner) => owner.group) })), vessels };
}

/**
 * Picks points on vessels by quantile of their path distance from the brain (0: nearest), with
 * thicker vessels holding more points. Returns the point, jittered within the vessel, and its distance.
 */
function vesselSampler(vessels: CatVessel[]) {
  const segments: { a: Vec; b: Vec; width: number; dist: number; weight: number }[] = [];
  for (const vessel of vessels)
    for (let k = 0; k < vessel.points.length - 1; k += 1) {
      const width = (vessel.width[k] + vessel.width[k + 1]) / 2;
      const length = distance(vessel.points[k], vessel.points[k + 1]);
      segments.push({ a: vessel.points[k], b: vessel.points[k + 1], width, dist: (vessel.dist[k] + vessel.dist[k + 1]) / 2, weight: length * (0.3 + width / 0.05) });
    }
  segments.sort((x, y) => x.dist - y.dist);
  const cumulative = new Float64Array(segments.length);
  let total = 0;
  segments.forEach((segment, k) => (cumulative[k] = total += segment.weight));
  return (q: number, r: Random): { p: Vec; dist: number } => {
    if (!segments.length) return { p: CAT_BRAIN, dist: 0 };
    const goal = clamp01(q) * total;
    let lo = 0, hi = segments.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cumulative[mid] < goal) lo = mid + 1;
      else hi = mid;
    }
    const { a, b, width, dist } = segments[lo];
    const p = lerp(a, b, r());
    const spread = width * 0.35;
    return { p: [p[0] + gauss(r) * spread, p[1] + gauss(r) * spread, p[2] + gauss(r) * spread], dist };
  };
}

/** Whiskers: three bright lines fanning out from each side of the muzzle. */
function whiskerPoint(r: Random): Vec {
  const across = r() < 0.5 ? -1 : 1;
  const row = Math.floor(r() * 3);
  const t = r();
  const p = inHead(1.12 + 0.3 * t, -0.5 - 0.07 * row + t * (0.14 - 0.14 * row) - 0.16 * t * t, across * (0.26 + 0.95 * t));
  return [p[0] + gauss(r) * 0.006, p[1] + gauss(r) * 0.006, p[2] + gauss(r) * 0.006];
}
/** Two small bright eyes just in front of the brain. */
function eyePoint(r: Random): Vec {
  const p = inHead(1.1, -0.18, r() < 0.5 ? -0.42 : 0.42);
  return [p[0] + gauss(r) * 0.03, p[1] + gauss(r) * 0.03, p[2] + gauss(r) * 0.03];
}
// Each ear is its veins: a front and a back edge meeting at the tip, and a midrib up its middle.
const EARS = [-1, 1].flatMap((s) => {
  const front = inHead(0.2, 0.74, 0.6 * s), back = inHead(-0.55, 0.66, 0.88 * s), tip = inHead(-0.25, 1.75, 1.15 * s), root = inHead(-0.2, 0.7, 0.74 * s);
  const bow = (a: Vec, b: Vec, out: number): Vec => { const m = lerp(a, b, 0.5); return [m[0] + ACROSS[0] * out * s, m[1], m[2] + ACROSS[2] * out * s]; };
  return [curve([front, bow(front, tip, 0.05), tip], 8), curve([back, bow(back, tip, 0.06), tip], 8), curve([root, lerp(root, tip, 0.8)], 8)];
});
const EAR_LENGTH = EARS.reduce((sum, ear) => sum + ear.total, 0);
function earPoint(r: Random): Vec {
  let roll = r() * EAR_LENGTH, pick = 0;
  while (pick < EARS.length - 1 && (roll -= EARS[pick].total) > 0) pick += 1;
  const p = along(EARS[pick], r());
  return [p[0] + gauss(r) * 0.012, p[1] + gauss(r) * 0.012, p[2] + gauss(r) * 0.012];
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
  return [side * 0.035 + d[0] * CEREBRUM.rx * depth, y < 0 ? y * 0.82 : y, d[2] * CEREBRUM.rz * depth];
}
/** A point on the folded cerebellum tucked behind the cerebrum, brain-local. */
function cerebellumPoint(r: Random): Vec {
  const d = randomUnit(r);
  const depth = 0.84 + 0.16 * r();
  const folia = 0.022 * Math.sin(Math.atan2(d[1], d[2]) * 24);
  return [CEREBELLUM.x + d[0] * CEREBELLUM.rx * depth, CEREBELLUM.y + d[1] * CEREBELLUM.ry * depth + folia, CEREBELLUM.z + d[2] * CEREBELLUM.rz * depth];
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
function anatomy(data: BrainData, model: BrainModel, times: Float64Array, out: Float32Array, births: Float32Array, scales: Float32Array) {
  const cat = catAnatomy(model);
  const lobeOf = lobes(model);
  const farthest = Math.max(1, ...cat.vessels.map((vessel) => vessel.dist[vessel.dist.length - 1]));
  const onVessels: number[][] = model.groups.map(() => []);
  const inked: number[] = [];
  data.nodes.forEach((node, i) => {
    const g = model.groupOf[i];
    if (node.kind === "edit") {
      onVessels[g].push(i);
      return;
    }
    const r = stream(hash32(node.id));
    const roll = r();
    let p: Vec;
    if (roll < 0.035) {
      p = whiskerPoint(r);
      births[i] = 0.2 + 0.06 * r();
      inked.push(i);
    } else if (roll < 0.045) {
      p = eyePoint(r);
      births[i] = 0.18 + 0.04 * r();
      inked.push(i);
    } else if (roll < 0.105) {
      p = earPoint(r);
      births[i] = 0.14 + 0.1 * r();
    } else if (roll < 0.105 + RECORD_VESSELS) {
      onVessels[g].push(i);
      return;
    } else {
      if (roll < 0.3) p = toCat(cerebellumPoint(r));
      else {
        // Each group owns a coloured lobe; draws are kept inside it and favour the ridges of the folds.
        const lobe = lobeOf[g];
        const spread = Math.min(Math.PI, Math.max(0.36, lobe.angle * 1.5));
        let d = capDirection(lobe.dir, spread, r);
        for (let attempt = 0; attempt < 12; attempt += 1) {
          if (lobeAt(d, lobeOf) === g && (gyri(d) + 1) / 2 >= r() * 0.8) break;
          d = capDirection(lobe.dir, spread, r);
        }
        p = toCat(cortexPoint(d, r));
      }
      // The brain lights first, from its centre out.
      births[i] = 0.02 + 0.16 * clamp01(distance(p, CAT_BRAIN) / 1.0);
      scales[i] = BRAIN_POINT;
    }
    out.set(p, i * 3);
  });
  // A group's edits (and a few of its records) glow along its vessels in time order: the newest
  // nearest the brain, the oldest out at the tips. They light up from the brain outward.
  onVessels.forEach((members, g) => {
    const sample = vesselSampler(cat.vessels.filter((vessel) => vessel.group === g));
    const rank = timeRank(members, times);
    for (const i of members) {
      const r = stream(hash32(data.nodes[i].id) ^ 0x5bd1e995);
      const age = 1 - (rank.get(i) ?? r());
      const { p, dist } = sample(age * 0.94 + r() * 0.06, r);
      out.set(p, i * 3);
      births[i] = clamp01(0.24 + (0.74 * dist) / farthest);
    }
  });
  // Labels float over each group's own region; a group without vessels is labelled off its lobe.
  const anchors = new Float32Array(model.groups.length * 3);
  model.groups.forEach((_, g) => {
    const points = cat.vessels.filter((vessel) => vessel.group === g && vessel.level > 0).flatMap((vessel) => vessel.points);
    const own = points.length ? points : cat.vessels.filter((vessel) => vessel.group === g).flatMap((vessel) => vessel.points);
    if (own.length) anchors.set([0, 1, 2].map((c) => own.reduce((sum, p) => sum + p[c], 0) / own.length), g * 3);
    else anchors.set(toCat(cortexPoint(lobeOf[g].dir, () => 0).map((value) => value * 1.5) as Vec), g * 3);
  });
  // Framed a little low, so the cat stands above the legend; the open View panel reframes it (scene inset).
  return { anchors, regions: null, whiskers: Int32Array.from(inked), center: [0, 2.9, 0] as Vec, radius: 5.4 };
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
  const scales = new Float32Array(data.nodes.length).fill(1);
  const placed =
    layout === "anatomy" ? anatomy(data, model, times, positions, births, scales)
    : layout === "spheres" ? spheres(data, model, positions, births)
    : wheel(data, model, times, positions, births);
  const view = layout === "anatomy" ? { azimuth: 0.15, elevation: 0.08 } : layout === "spheres" ? { azimuth: 0, elevation: 0.62 } : { azimuth: 0, elevation: 0 };
  const turnAxis: "y" | "z" = layout === "wheel" ? "z" : "y";
  return { positions, births, scales, parents, groups: Float32Array.from(model.groupOf), ...placed, turnAxis, view, lineAlpha: layout === "anatomy" ? 0.022 : 0.08 };
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
  const scales = new Float32Array(data.nodes.length).fill(1);
  members.forEach((i, j) => {
    positions.set(sub.positions.subarray(j * 3, j * 3 + 3), i * 3);
    births[i] = sub.births[j];
    groups[i] = sub.groups[j];
    scales[i] = sub.scales[j];
  });
  const parents = full.parents.map((parent, i) => (memberOf.has(i) && memberOf.has(parent) ? parent : -1));
  return { ...sub, positions, births, groups, scales, parents, whiskers: sub.whiskers.map((j) => members[j]) };
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

/** A faint, non-interactive frame giving each layout its shape: the cat's vessels and brain, sphere outlines, wheel rings. */
export function brainScaffold(layout: BrainLayout, model: BrainModel): Float32Array {
  const points: number[] = [];
  const r = stream(hash32(`scaffold:${layout}`));
  if (layout === "anatomy") {
    // Every vessel of the cat, even ones with no nodes yet, plus the brain, ears, eyes and whiskers: no skin.
    const sample = vesselSampler(catAnatomy(model).vessels);
    for (let n = 0; n < 6500; n += 1) points.push(...sample(r(), r).p);
    for (let n = 0; n < 1500; n += 1) points.push(...toCat(cortexPoint(randomUnit(r), r)));
    for (let n = 0; n < 250; n += 1) points.push(...toCat(cerebellumPoint(r)));
    for (let n = 0; n < 300; n += 1) points.push(...earPoint(r));
    for (let n = 0; n < 120; n += 1) points.push(...whiskerPoint(r));
    for (let n = 0; n < 40; n += 1) points.push(...eyePoint(r));
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

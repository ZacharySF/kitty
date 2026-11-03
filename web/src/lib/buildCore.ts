/**
 * The build (docs/platform-design.md Q8/Q9): pure helpers behind the live build store
 * (build.svelte.ts), kept free of Svelte and the DOM so Node tests can import them.
 * The server owns validation; these only shape what the client applies.
 */
import type { Problem } from "./api";

export type HistoryEntry = { id: string; saved_at: string; reason: string; source: string; name: string; valid: boolean; revision: string };
export type GraphPreference = {
  look: "2d" | "3d";
  layout: "tree" | "clusters" | "sphere" | "force";
  physics: { repulsion: number; spring: number; damping: number };
};
export type BuildTheme = { base: string; tokens: Record<string, string>; css: string };
/** Settings → Appearance (Q59), mirrored onto <html data-*> by appearance.ts. */
export type AppearancePreference = {
  title_font: "fraunces" | "plain";
  density: "compact" | "comfortable";
  motion: "full" | "reduced";
  motif: boolean;
  background: "nebula" | "petals";
};
export type BrainGrouping = "section" | "group" | "type";
/** The Brain's View panel (brain/brainSettings.ts); whether the panel is open stays per device. */
export type BrainPreference = {
  layout: "anatomy" | "spheres" | "wheel";
  group_by: BrainGrouping;
  labels: "off" | "sections" | "hover";
  turning: boolean;
  speed: number;
  lines: boolean;
  scaffold: boolean;
  point_size: number;
  background: "transparent" | "black";
  /** Picked colours per grouping and group key; absent keys follow the theme. */
  colors: Record<BrainGrouping, Record<string, string>>;
};
/** Quiet hours (Q60) in minutes of the local day. */
export type QuietPreference = { enabled: boolean; from: number; to: number; let_calendar: boolean; digest: boolean };
export type BuildUi = {
  graph: GraphPreference;
  atlas: { hidden: string[] };
  notes: { pen_only: boolean };
  terminal: { coding_agent: "omp" | "claude" | "codex" };
  appearance: AppearancePreference;
  home: { look: "brain" | "2d" };
  brain: BrainPreference;
  notifications: { quiet: QuietPreference };
  flashcards: { look: "tiles" | "graph" };
  study: { guide_view: "columns" | "rail"; plans_view: "timeline" | "days" };
};
export type Build = {
  schema: number;
  kitty_version: string;
  name: string;
  author: string;
  description: string;
  modules: string[];
  settings: Record<string, Record<string, unknown>>;
  theme: BuildTheme;
  ui: BuildUi;
};
/** What `GET /api/build` and every `build` push carry. */
export type BuildSnapshot = {
  revision: string;
  active_revision: string;
  build: Build;
  text: string;
  problems: Problem[];
  invalid: boolean;
  restart_required: boolean;
  safe: boolean;
};
export type BuildResponse = BuildSnapshot & { history: HistoryEntry[]; local_preferences_moved: boolean };

type DeepPartial<T> = { [K in keyof T]?: T[K] extends unknown[] ? T[K] : T[K] extends object ? DeepPartial<T[K]> : T[K] };
/** What `POST /api/build/preferences` accepts. */
export type PreferencePatch = {
  ui?: DeepPartial<BuildUi>;
  theme?: { base: string };
  name?: string;
  author?: string;
  description?: string;
};

/** The normalized build with every default (kitty-build.schema.json). */
export const DEFAULT_BUILD: Build = {
  schema: 1,
  kitty_version: "",
  name: "",
  author: "",
  description: "",
  modules: ["applyai", "workspace", "terminal"],
  settings: {},
  theme: { base: "kitty", tokens: {}, css: "" },
  ui: {
    graph: { look: "2d", layout: "tree", physics: { repulsion: 60, spring: 0.5, damping: 0.35 } },
    atlas: { hidden: [] },
    notes: { pen_only: false },
    terminal: { coding_agent: "omp" },
    appearance: { title_font: "fraunces", density: "compact", motion: "full", motif: true, background: "nebula" },
    home: { look: "brain" },
    brain: {
      layout: "anatomy", group_by: "section", labels: "hover", turning: true, speed: 1, lines: false,
      scaffold: true, point_size: 1, background: "transparent", colors: { section: {}, group: {}, type: {} },
    },
    notifications: { quiet: { enabled: false, from: 1320, to: 450, let_calendar: true, digest: true } },
    flashcards: { look: "tiles" },
    study: { guide_view: "columns", plans_view: "timeline" },
  },
};

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** `patch` merged into a copy of `target`: objects merge key by key; arrays and values (null too) replace. */
export function deepMerge<T>(target: T, patch: unknown): T {
  if (!isObject(target) || !isObject(patch)) return structuredClone(patch === undefined ? target : patch) as T;
  const result: Record<string, unknown> = { ...target };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    result[key] = isObject(value) && isObject(result[key]) ? deepMerge(result[key], value) : structuredClone(value);
  }
  return result as T;
}

/** Old per-browser preferences (before builds) and where they land in the build. */
export const LEGACY_KEYS = [
  "kitty.theme",
  "kitty.graph.look",
  "kitty.graph.default-layout",
  "kitty.atlas.physics",
  "kitty.graph.tree-default.v2",
  "kitty.atlas.hidden",
  "kitty.notes.pen-only",
  "kitty.coding-agent",
  "kitty.appearance",
  "kitty.home.look",
  "kitty.brain.view",
  "kitty.brain.colors",
  "kitty.notify.quiet",
  "kitty.flashcards.deck-look",
  "kitty.study.guide-view",
  "kitty.study.plan-view",
] as const;

type Reader = Pick<Storage, "getItem">;
const PHYSICS_RANGE = { repulsion: [0, 300], spring: [0, 1], damping: [0.05, 0.95] } as const;

function oneOf<T extends string, F extends T | undefined>(value: unknown, options: readonly T[], fallback: F): T | F {
  return options.includes(value as T) ? (value as T) : fallback;
}

function parse(raw: string | null): unknown {
  try { return raw === null ? undefined : JSON.parse(raw); } catch { return undefined; }
}

/**
 * This browser's legacy preferences as a patch for `POST /api/build/local`, and the keys
 * present (removed once moved). Values the build schema would refuse are left out.
 */
export function collectLegacy(storage: Reader): { patch: { ui?: Record<string, unknown>; theme?: { base: string } }; keys: string[] } {
  const read = (key: string) => { try { return storage.getItem(key); } catch { return null; } };
  const keys = LEGACY_KEYS.filter((key) => read(key) !== null);
  const ui: Record<string, Record<string, unknown>> = {};
  const patch: { ui?: Record<string, unknown>; theme?: { base: string } } = {};

  const theme = read("kitty.theme");
  if (theme && /^[a-z0-9][a-z0-9-]{0,39}$/.test(theme)) patch.theme = { base: theme };

  const graph: Record<string, unknown> = {};
  const look = read("kitty.graph.look");
  if (look === "2d" || look === "3d") graph.look = look;
  // Before tree-default.v2 the stored layout was an automatic Sphere, not a choice.
  const layout = read("kitty.graph.default-layout");
  if (read("kitty.graph.tree-default.v2") === "1" && ["tree", "clusters", "sphere", "force"].includes(layout ?? ""))
    graph.layout = layout;
  const physics = parse(read("kitty.atlas.physics"));
  if (isObject(physics)) {
    const values: Record<string, number> = {};
    for (const [key, [min, max]] of Object.entries(PHYSICS_RANGE)) {
      const value = physics[key];
      if (typeof value === "number" && Number.isFinite(value)) values[key] = Math.min(max, Math.max(min, value));
    }
    if (Object.keys(values).length) graph.physics = values;
  }
  if (Object.keys(graph).length) ui.graph = graph;

  const hidden = parse(read("kitty.atlas.hidden"));
  if (Array.isArray(hidden)) {
    const ids = [...new Set(hidden.filter((item): item is string => typeof item === "string" && /^[a-z][a-z0-9-]{0,39}$/.test(item)))];
    ui.atlas = { hidden: ids.slice(0, 100) };
  }

  const penOnly = read("kitty.notes.pen-only");
  if (penOnly === "true" || penOnly === "false") ui.notes = { pen_only: penOnly === "true" };

  const agent = read("kitty.coding-agent");
  if (agent === "omp" || agent === "claude" || agent === "codex") ui.terminal = { coding_agent: agent };

  const appearance = parse(read("kitty.appearance"));
  if (isObject(appearance)) {
    ui.appearance = {
      title_font: oneOf(appearance.titleFont, ["fraunces", "plain"] as const, "fraunces"),
      density: oneOf(appearance.density, ["compact", "comfortable"] as const, "compact"),
      motion: oneOf(appearance.motion, ["full", "reduced"] as const, "full"),
      motif: typeof appearance.motif === "boolean" ? appearance.motif : true,
      background: oneOf(appearance.background, ["nebula", "petals"] as const, "nebula"),
    };
  }

  // Home's choice used to share the graph look: an old "2d" stays 2D, "3d" becomes the Brain.
  const home = read("kitty.home.look") ?? (look === "2d" ? "2d" : null);
  if (home === "brain" || home === "2d") ui.home = { look: home };

  const brain: Record<string, unknown> = {};
  const view = parse(read("kitty.brain.view"));
  if (isObject(view)) {
    // `panel` (whether the View panel is open) stays on this device.
    const number = (value: unknown, min: number, max: number) =>
      typeof value === "number" && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : undefined;
    Object.assign(brain, Object.fromEntries(Object.entries({
      layout: oneOf(view.layout, ["anatomy", "spheres", "wheel"] as const, undefined),
      group_by: oneOf(view.groupBy, ["section", "group", "type"] as const, undefined),
      labels: oneOf(view.labels, ["off", "sections", "hover"] as const, undefined),
      turning: typeof view.turning === "boolean" ? view.turning : undefined,
      speed: number(view.speed, 0, 3),
      lines: typeof view.lines === "boolean" ? view.lines : undefined,
      scaffold: typeof view.scaffold === "boolean" ? view.scaffold : undefined,
      point_size: number(view.pointSize, 0.25, 3),
      background: oneOf(view.background, ["transparent", "black"] as const, undefined),
    }).filter(([, value]) => value !== undefined)));
  }
  const colours = parse(read("kitty.brain.colors"));
  if (isObject(colours)) {
    const picked: Record<string, Record<string, string>> = { section: {}, group: {}, type: {} };
    for (const by of Object.keys(picked))
      if (isObject(colours[by]))
        for (const [key, hex] of Object.entries(colours[by]))
          if (key.length <= 80 && typeof hex === "string" && /^#[0-9a-f]{6}$/i.test(hex)) picked[by][key] = hex.toLowerCase();
    brain.colors = picked;
  }
  if (Object.keys(brain).length) ui.brain = brain;

  const quiet = parse(read("kitty.notify.quiet"));
  if (isObject(quiet)) {
    const minute = (value: unknown, fallback: number) =>
      typeof value === "number" && Number.isInteger(value) && value >= 0 && value < 1440 ? value : fallback;
    ui.notifications = {
      quiet: {
        enabled: quiet.enabled === true,
        from: minute(quiet.from, 1320),
        to: minute(quiet.to, 450),
        let_calendar: typeof quiet.letCalendar === "boolean" ? quiet.letCalendar : true,
        digest: typeof quiet.digest === "boolean" ? quiet.digest : true,
      },
    };
  }

  const deckLook = read("kitty.flashcards.deck-look");
  if (deckLook === "tiles" || deckLook === "graph") ui.flashcards = { look: deckLook };
  const study: Record<string, unknown> = {};
  const guide = read("kitty.study.guide-view");
  if (guide === "columns" || guide === "rail") study.guide_view = guide;
  const plans = read("kitty.study.plan-view");
  if (plans === "timeline" || plans === "days") study.plans_view = plans;
  if (Object.keys(study).length) ui.study = study;

  if (Object.keys(ui).length) patch.ui = ui;
  return { patch, keys };
}

/** Bracket's semantic roles: tokens with these names override the base theme's roles. */
export const ROLE_TOKENS = [
  "void", "panel", "inset", "raised", "line", "lineHot", "ink", "mute", "dim",
  "structure", "signal", "go", "reference", "caution", "alternate", "danger",
] as const;
export type RoleToken = (typeof ROLE_TOKENS)[number];

/** The CSS custom property a non-role token sets. */
export function tokenProperty(name: string): string {
  const graph = /^graph([1-6])$/.exec(name);
  if (graph) return `--c${graph[1]}`;
  return name === "shadow" ? "--shadow-tint" : `--${name}`;
}

/**
 * How to apply `tokens` after `previous` (the properties the last build set): role
 * overrides for the base theme, properties to set, and stale ones to remove first.
 */
export function tokenPlan(tokens: Record<string, string>, previous: Iterable<string> = []): {
  roles: Partial<Record<RoleToken, string>>;
  set: Record<string, string>;
  remove: string[];
} {
  const roles: Partial<Record<RoleToken, string>> = {};
  const set: Record<string, string> = {};
  for (const [name, value] of Object.entries(tokens)) {
    if ((ROLE_TOKENS as readonly string[]).includes(name)) roles[name as RoleToken] = value;
    else set[tokenProperty(name)] = value;
  }
  return { roles, set, remove: [...previous].filter((name) => !(name in set)) };
}

/** Custom CSS as injected: confined to rika!'s app root, never the safe-mode control. */
export function scopedCss(css: string): string {
  return css.trim() ? `@scope (#app) {\n${css}\n}` : "";
}

export function hasCustomStyle(theme: Pick<BuildTheme, "tokens" | "css">): boolean {
  return Object.keys(theme.tokens).length > 0 || theme.css.trim() !== "";
}

/** Problems by 1-based line of a text with `lineCount` lines (out-of-range lines clamp). */
export function problemLines(problems: readonly Problem[], lineCount: number): Map<number, Problem[]> {
  const lines = new Map<number, Problem[]>();
  const last = Math.max(1, lineCount);
  for (const problem of problems) {
    const line = Math.min(last, Math.max(1, Math.trunc(problem.line) || 1));
    lines.set(line, [...(lines.get(line) ?? []), problem]);
  }
  return lines;
}

/** Character offset of the start of 1-based `line` (and `column`) in `text`, for moving the caret. */
export function lineOffset(text: string, line: number, column = 1): number {
  let offset = 0;
  for (let current = 1; current < line; current += 1) {
    const next = text.indexOf("\n", offset);
    if (next < 0) return text.length;
    offset = next + 1;
  }
  const end = text.indexOf("\n", offset);
  return Math.min(offset + Math.max(0, column - 1), end < 0 ? text.length : end);
}

/** `?safe` in a URL query. */
export function hasSafeParam(search: string): boolean {
  return new URLSearchParams(search).has("safe");
}

/** `href` with `?safe` added or removed, keeping the rest of the query and the hash. */
export function withSafeParam(href: string, safe: boolean): string {
  const url = new URL(href);
  if (safe) url.searchParams.set("safe", "");
  else url.searchParams.delete("safe");
  return url.href.replace(/([?&])safe=(?=&|#|$)/, "$1safe");
}

/** Safe mode: the window's own toggle wins; otherwise `?safe` or the server's `--safe`. */
export function resolveSafe({ url, server, toggle }: { url: boolean; server: boolean; toggle: boolean | null }): boolean {
  return toggle ?? (url || server);
}

/** Ctrl+Alt+Shift+S, by physical key so keyboard layouts and Shift agree. */
export function isSafeShortcut(event: Pick<KeyboardEvent, "ctrlKey" | "altKey" | "shiftKey" | "metaKey" | "code" | "key">): boolean {
  return event.ctrlKey && event.altKey && event.shiftKey && !event.metaKey && (event.code === "KeyS" || event.key.toLowerCase() === "s");
}

/** A build value as one short diff cell: long text (custom CSS) becomes a line count. */
export function describeValue(path: string, value: unknown): string {
  if (value === undefined) return "(not set)";
  if (typeof value === "string") {
    const lines = value === "" ? 0 : value.split("\n").length;
    if (path === "theme.css" || path.endsWith(".css")) return lines ? `${lines} line${lines === 1 ? "" : "s"} of CSS` : "no CSS";
    if (lines > 1 || value.length > 120) return `${lines} line${lines === 1 ? "" : "s"} (${value.length} characters)`;
    return JSON.stringify(value);
  }
  const text = JSON.stringify(value) ?? String(value);
  return text.length > 120 ? `${text.slice(0, 119)}…` : text;
}

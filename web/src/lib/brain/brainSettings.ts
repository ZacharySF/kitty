import type { BrainGroupBy, BrainLayout } from "./brainLayout";

export type BrainLabels = "off" | "sections" | "hover";
/** Transparent lets the theme's backdrop show through; Black is the original night-sky stage. */
export type BrainBackground = "transparent" | "black";
/** The Brain's View panel, remembered across sessions in localStorage. */
export type BrainView = {
  layout: BrainLayout;
  groupBy: BrainGroupBy;
  labels: BrainLabels;
  turning: boolean;
  /** Auto-turn speed multiplier, 0–3. */
  speed: number;
  lines: boolean;
  scaffold: boolean;
  /** Point size multiplier, 0.25–3. */
  pointSize: number;
  background: BrainBackground;
  panel: boolean;
};

export const BRAIN_VIEW_KEY = "kitty.brain.view";
export const BRAIN_LAYOUTS: { id: BrainLayout; label: string }[] = [
  { id: "anatomy", label: "Anatomy" }, { id: "spheres", label: "Spheres" }, { id: "wheel", label: "Wheel" },
];
export const BRAIN_GROUPINGS: { id: BrainGroupBy; label: string }[] = [
  { id: "section", label: "Section" }, { id: "group", label: "Group" }, { id: "type", label: "Type" },
];
export const BRAIN_LABELS: { id: BrainLabels; label: string }[] = [
  { id: "off", label: "Off" }, { id: "sections", label: "Sections" }, { id: "hover", label: "Hover" },
];
export const BRAIN_BACKGROUNDS: { id: BrainBackground; label: string }[] = [
  { id: "transparent", label: "Transparent" }, { id: "black", label: "Black" },
];
export const BRAIN_SPEED = { min: 0, max: 3, step: 0.05 };
export const BRAIN_POINT_SIZE = { min: 0.25, max: 3, step: 0.05 };
export const DEFAULT_BRAIN_VIEW: BrainView = {
  layout: "anatomy",
  groupBy: "section",
  labels: "hover",
  turning: true,
  speed: 1,
  lines: false,
  scaffold: true,
  pointSize: 1,
  background: "transparent",
  panel: true,
};

const oneOf = <T extends string>(options: { id: T }[], value: unknown, fallback: T): T =>
  options.some((option) => option.id === value) ? (value as T) : fallback;
const flag = (value: unknown, fallback: boolean) => (typeof value === "boolean" ? value : fallback);
const range = (value: unknown, limits: { min: number; max: number }, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? Math.min(limits.max, Math.max(limits.min, value)) : fallback;

/** The saved view; unknown or malformed fields fall back to their defaults. */
export function loadBrainView(): BrainView {
  const fallback = DEFAULT_BRAIN_VIEW;
  let saved: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(localStorage.getItem(BRAIN_VIEW_KEY) || "{}");
    if (parsed && typeof parsed === "object") saved = parsed;
  } catch { /* A damaged preference never prevents opening the brain. */ }
  return {
    layout: oneOf(BRAIN_LAYOUTS, saved.layout, fallback.layout),
    groupBy: oneOf(BRAIN_GROUPINGS, saved.groupBy, fallback.groupBy),
    labels: oneOf(BRAIN_LABELS, saved.labels, fallback.labels),
    turning: flag(saved.turning, fallback.turning),
    speed: range(saved.speed, BRAIN_SPEED, fallback.speed),
    lines: flag(saved.lines, fallback.lines),
    scaffold: flag(saved.scaffold, fallback.scaffold),
    pointSize: range(saved.pointSize, BRAIN_POINT_SIZE, fallback.pointSize),
    background: oneOf(BRAIN_BACKGROUNDS, saved.background, fallback.background),
    panel: flag(saved.panel, fallback.panel),
  };
}

/** Colours the user picked, per grouping and group key; anything absent follows the theme. */
export type BrainColours = Record<BrainGroupBy, Record<string, string>>;
export const BRAIN_COLOURS_KEY = "kitty.brain.colors";
const HEX = /^#[0-9a-f]{6}$/i;
export function loadBrainColours(): BrainColours {
  const colours: BrainColours = { section: {}, group: {}, type: {} };
  try {
    const parsed = JSON.parse(localStorage.getItem(BRAIN_COLOURS_KEY) || "{}");
    for (const by of BRAIN_GROUPINGS)
      for (const [key, hex] of Object.entries(parsed?.[by.id] ?? {}))
        if (typeof hex === "string" && HEX.test(hex)) colours[by.id][key] = hex.toLowerCase();
  } catch { /* Damaged colours fall back to the theme's. */ }
  return colours;
}
export function saveBrainColours(colours: BrainColours): void {
  try { localStorage.setItem(BRAIN_COLOURS_KEY, JSON.stringify(colours)); }
  catch { /* Private browsing: the colours still apply for this session. */ }
}

export function saveBrainView(view: BrainView): void {
  try { localStorage.setItem(BRAIN_VIEW_KEY, JSON.stringify(view)); }
  catch { /* Private browsing: the view still applies for this session. */ }
}

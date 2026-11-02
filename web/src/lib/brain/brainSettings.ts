import type { BrainGroupBy, BrainLayout } from "./brainLayout";
import type { BrainPreference } from "../buildCore";

export type BrainLabels = "off" | "sections" | "hover";
/** Transparent lets the theme's backdrop show through; Black is the original night-sky stage. */
export type BrainBackground = "transparent" | "black";
/** The Brain's View panel: kept in the build (`ui.brain`), except whether the panel is open. */
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

export const BRAIN_LAYOUTS: { id: BrainLayout; label: string }[] = [
  { id: "anatomy", label: "Brain" }, { id: "spheres", label: "Spheres" }, { id: "wheel", label: "Wheel" },
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
  panel: false,
};

const oneOf = <T extends string>(options: { id: T }[], value: unknown, fallback: T): T =>
  options.some((option) => option.id === value) ? (value as T) : fallback;
const flag = (value: unknown, fallback: boolean) => (typeof value === "boolean" ? value : fallback);
const range = (value: unknown, limits: { min: number; max: number }, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? Math.min(limits.max, Math.max(limits.min, value)) : fallback;

/** Whether the View panel is open: state of this device, not a build preference. */
export const BRAIN_PANEL_KEY = "kitty.brain.panel";
export function loadBrainPanel(): boolean {
  try { return localStorage.getItem(BRAIN_PANEL_KEY) === "open"; } catch { return DEFAULT_BRAIN_VIEW.panel; }
}
export function saveBrainPanel(open: boolean): void {
  try { localStorage.setItem(BRAIN_PANEL_KEY, open ? "open" : "closed"); }
  catch { /* Private browsing: the panel still toggles for this session. */ }
}

/**
 * The view a build's `ui.brain` (docs/platform-design.md Q8) describes, with this device's
 * panel state; unknown or malformed fields fall back to their defaults.
 */
export function brainView(saved: Partial<Record<keyof BrainPreference, unknown>> | undefined, panel = DEFAULT_BRAIN_VIEW.panel): BrainView {
  const fallback = DEFAULT_BRAIN_VIEW;
  const value = saved ?? {};
  return {
    layout: oneOf(BRAIN_LAYOUTS, value.layout, fallback.layout),
    groupBy: oneOf(BRAIN_GROUPINGS, value.group_by, fallback.groupBy),
    labels: oneOf(BRAIN_LABELS, value.labels, fallback.labels),
    turning: flag(value.turning, fallback.turning),
    speed: range(value.speed, BRAIN_SPEED, fallback.speed),
    lines: flag(value.lines, fallback.lines),
    scaffold: flag(value.scaffold, fallback.scaffold),
    pointSize: range(value.point_size, BRAIN_POINT_SIZE, fallback.pointSize),
    background: oneOf(BRAIN_BACKGROUNDS, value.background, fallback.background),
    panel,
  };
}

/** A view as the build stores it (`panel` stays on the device). */
export function brainPreference(view: BrainView): Omit<BrainPreference, "colors"> {
  return {
    layout: view.layout, group_by: view.groupBy, labels: view.labels, turning: view.turning, speed: view.speed,
    lines: view.lines, scaffold: view.scaffold, point_size: view.pointSize, background: view.background,
  };
}

/** Colours the user picked, per grouping and group key; anything absent follows the theme. */
export type BrainColours = Record<BrainGroupBy, Record<string, string>>;
const HEX = /^#[0-9a-f]{6}$/i;
/** The picked colours a build's `ui.brain.colors` holds; junk is dropped. */
export function brainColours(saved: unknown): BrainColours {
  const colours: BrainColours = { section: {}, group: {}, type: {} };
  const value = saved && typeof saved === "object" ? (saved as Record<string, unknown>) : {};
  for (const by of BRAIN_GROUPINGS) {
    const picked = value[by.id];
    if (!picked || typeof picked !== "object") continue;
    for (const [key, hex] of Object.entries(picked))
      if (typeof hex === "string" && HEX.test(hex)) colours[by.id][key] = hex.toLowerCase();
  }
  return colours;
}

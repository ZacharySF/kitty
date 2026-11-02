import type { ThemeId } from "./themes/catalog";
import type { GraphLook } from "./graph3d";
import type { AppearancePreference } from "./buildCore";
import { buildStore, setPreferences } from "./build.svelte";

/**
 * Q59 Settings → Appearance. The look choices without a home of their own, kept in the
 * build's `ui.appearance` (docs/platform-design.md Q8) and mirrored onto <html data-*> so
 * CSS applies them. The theme (`theme.base`) and the section graph look (`ui.graph.look`)
 * keep their own keys; presets set all three in one change.
 */
export type Appearance = {
  /** Fraunces page titles, or plain Inter. */
  titleFont: "fraunces" | "plain";
  /** Q11: compact 13px body, or comfortable 14px. */
  density: "compact" | "comfortable";
  motion: "full" | "reduced";
  /** The theme's motif layer (bullets, ticks, dividers, ornaments); off keeps its surfaces. */
  motif: boolean;
  /** Q16: the nebula, or the nebula with drifting petals. */
  background: "nebula" | "petals";
};

/** Dispatched on `window` after the appearance changes; `detail` is the Appearance. */
export const APPEARANCE_EVENT = "kitty:appearance";
export const DEFAULT_APPEARANCE: Appearance = {
  titleFont: "fraunces",
  density: "compact",
  motion: "full",
  motif: true,
  background: "nebula",
};

export type Preset = { id: "cozy" | "focused" | "minimal"; name: string; blurb: string; theme: ThemeId; look: GraphLook; appearance: Appearance };
export const PRESETS: readonly Preset[] = [
  {
    id: "cozy",
    name: "Cozy",
    blurb: "Blossoms and drifting petals, Fraunces titles, roomier text, 3D section graphs.",
    theme: "kitty",
    look: "3d",
    appearance: { titleFont: "fraunces", density: "comfortable", motion: "full", motif: true, background: "petals" },
  },
  {
    id: "focused",
    name: "Focused",
    blurb: "Dense text, reduced motion, flat 2D section graphs and no decoration.",
    theme: "kitty",
    look: "2d",
    appearance: { titleFont: "fraunces", density: "compact", motion: "reduced", motif: false, background: "nebula" },
  },
  {
    id: "minimal",
    name: "Minimal",
    blurb: "Neutral Harness chrome, plain titles, still frames, 2D section graphs.",
    theme: "harness",
    look: "2d",
    appearance: { titleFont: "plain", density: "compact", motion: "reduced", motif: false, background: "nebula" },
  },
];

/** The build's `ui.appearance` as the interface uses it. */
export function appearanceOf(preference: AppearancePreference): Appearance {
  return {
    titleFont: preference.title_font,
    density: preference.density,
    motion: preference.motion,
    motif: preference.motif,
    background: preference.background,
  };
}

/** An Appearance as the build stores it. */
export function appearancePreference(appearance: Appearance): AppearancePreference {
  return {
    title_font: appearance.titleFont,
    density: appearance.density,
    motion: appearance.motion,
    motif: appearance.motif,
    background: appearance.background,
  };
}

/** The appearance in effect (the active build's). */
export function savedAppearance(): Appearance {
  return appearanceOf(buildStore.build.ui.appearance);
}

let applied = "";
/** Mirrors an appearance onto <html data-*>; the build store calls it on every change. */
export function applyAppearance(appearance: Appearance): void {
  const key = JSON.stringify(appearance);
  if (key === applied) return;
  applied = key;
  const root = document.documentElement.dataset;
  root.titleFont = appearance.titleFont;
  root.density = appearance.density;
  root.motion = appearance.motion;
  root.motif = appearance.motif ? "on" : "off";
  root.background = appearance.background;
  window.dispatchEvent(new CustomEvent(APPEARANCE_EVENT, { detail: appearance }));
}

/** Saves a whole appearance to the build (applied in this window at once). */
export function setAppearance(appearance: Appearance): Promise<boolean> {
  return setPreferences({ ui: { appearance: appearancePreference(appearance) } });
}

/** A preset's theme, section graph look and appearance, as one build change. */
export function applyPreset(preset: Preset): Promise<boolean> {
  return setPreferences({
    theme: { base: preset.theme },
    ui: { graph: { look: preset.look }, appearance: appearancePreference(preset.appearance) },
  });
}

/** Reduced motion is the Appearance setting or the system preference. */
export function prefersReducedMotion(): boolean {
  return document.documentElement.dataset.motion === "reduced" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

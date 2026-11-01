import type { ThemeId } from "./themeCatalog";
import type { AtlasLook } from "./graphPreferences";

/**
 * Q59 Settings → Appearance. The look choices without a home of their own,
 * saved together in localStorage (`kitty.appearance`) and mirrored onto
 * <html data-*> so CSS applies them. The theme (`kitty.theme`) and the atlas
 * look (`kitty.graph.look`) keep their own keys; presets set those too.
 */
export type Appearance = {
  /** Fraunces page titles, or plain Space Grotesk. */
  titleFont: "fraunces" | "plain";
  /** Q11: compact 13px body, or comfortable 14px. */
  density: "compact" | "comfortable";
  motion: "full" | "reduced";
  /** Blossom accents in icons and the theme picker. */
  motif: boolean;
  /** Q16: the nebula, or the nebula with drifting petals. */
  background: "nebula" | "petals";
};

export const APPEARANCE_KEY = "kitty.appearance";
/** Dispatched on `window` after the appearance changes; `detail` is the Appearance. */
export const APPEARANCE_EVENT = "kitty:appearance";
export const DEFAULT_APPEARANCE: Appearance = {
  titleFont: "fraunces",
  density: "compact",
  motion: "full",
  motif: true,
  background: "nebula",
};

export type Preset = { id: "cozy" | "focused" | "minimal"; name: string; blurb: string; theme: ThemeId; look: AtlasLook; appearance: Appearance };
export const PRESETS: readonly Preset[] = [
  {
    id: "cozy",
    name: "Cozy",
    blurb: "Blossoms and drifting petals, Fraunces titles, roomier text, 3D atlas.",
    theme: "kitty",
    look: "3d",
    appearance: { titleFont: "fraunces", density: "comfortable", motion: "full", motif: true, background: "petals" },
  },
  {
    id: "focused",
    name: "Focused",
    blurb: "Dense text, reduced motion, a flat 2D map and no decoration.",
    theme: "kitty",
    look: "2d",
    appearance: { titleFont: "fraunces", density: "compact", motion: "reduced", motif: false, background: "nebula" },
  },
  {
    id: "minimal",
    name: "Minimal",
    blurb: "Neutral Harness chrome, plain titles, still frames, 2D map.",
    theme: "harness",
    look: "2d",
    appearance: { titleFont: "plain", density: "compact", motion: "reduced", motif: false, background: "nebula" },
  },
];

function pick<T extends string>(value: unknown, options: readonly T[], fallback: T): T {
  return options.includes(value as T) ? (value as T) : fallback;
}

/** Reads the saved appearance; anything missing or unknown falls back to the default. */
export function savedAppearance(): Appearance {
  let saved: Record<string, unknown> = {};
  try {
    const value: unknown = JSON.parse(localStorage.getItem(APPEARANCE_KEY) ?? "null");
    if (value && typeof value === "object") saved = value as Record<string, unknown>;
  } catch {
    // Unreadable storage keeps the defaults.
  }
  return {
    titleFont: pick(saved.titleFont, ["fraunces", "plain"], DEFAULT_APPEARANCE.titleFont),
    density: pick(saved.density, ["compact", "comfortable"], DEFAULT_APPEARANCE.density),
    motion: pick(saved.motion, ["full", "reduced"], DEFAULT_APPEARANCE.motion),
    motif: typeof saved.motif === "boolean" ? saved.motif : DEFAULT_APPEARANCE.motif,
    background: pick(saved.background, ["nebula", "petals"], DEFAULT_APPEARANCE.background),
  };
}

function applyAppearance(appearance: Appearance): void {
  const root = document.documentElement.dataset;
  root.titleFont = appearance.titleFont;
  root.density = appearance.density;
  root.motion = appearance.motion;
  root.motif = appearance.motif ? "on" : "off";
  root.background = appearance.background;
  window.dispatchEvent(new CustomEvent(APPEARANCE_EVENT, { detail: appearance }));
}

/** Applies and saves a whole appearance. */
export function setAppearance(appearance: Appearance): void {
  applyAppearance(appearance);
  try {
    localStorage.setItem(APPEARANCE_KEY, JSON.stringify(appearance));
  } catch {
    // Storage can be unavailable (private mode); the look still applies this session.
  }
}

/** Applies the saved appearance; call once before mounting the app. */
export function applySavedAppearance(): void {
  applyAppearance(savedAppearance());
}

/** Reduced motion is the Appearance setting or the system preference. */
export function prefersReducedMotion(): boolean {
  return document.documentElement.dataset.motion === "reduced" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

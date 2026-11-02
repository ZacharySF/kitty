import { applyTheme as applyBracketTheme } from "@nunera/bracket/theme";
import { DEFAULT_THEME, getTheme } from "./catalog";
import type { ThemeId, ThemeModule, ThemeTokens } from "./contract";
import { themeMotif } from "./motif.svelte";

/* Applying a theme: tokens synchronously, then its motif module lazily (contract.ts). */

/** Dispatched on `window` after a theme's tokens are applied; `detail` is the ThemeId. */
export const THEME_EVENT = "kitty:theme";
const STORAGE_KEY = "kitty.theme";

/**
 * Motif modules, one chunk each (its CSS and SVG come with it). Themes without an entry run
 * on the engine presets alone.
 */
const MODULES: Partial<Record<ThemeId, () => Promise<{ default: ThemeModule }>>> = {
  kitty: () => import("./kitty"),
  "liquid-glass": () => import("./liquid-glass"),
  scythe: () => import("./scythe"),
};

export function currentTheme(): ThemeId {
  return getTheme(document.documentElement.dataset.theme).id;
}

function savedTheme(): ThemeId {
  try {
    return getTheme(localStorage.getItem(STORAGE_KEY)).id;
  } catch {
    return DEFAULT_THEME;
  }
}

/** Defaults for palettes that predate the contract (tests add bare palettes). */
const DEFAULTS: Pick<ThemeTokens, "surface" | "focus" | "selection" | "motion" | "backdrop"> = {
  surface: { style: "glass", alpha: { panel: 62, inset: 50, raised: 72 }, radius: 14 },
  focus: "ring",
  selection: "wash",
  motion: "calm",
  backdrop: "nebula",
};

function applyTokens(theme: ThemeTokens): void {
  const root = document.documentElement;
  const { surface, focus, selection, motion, backdrop } = { ...DEFAULTS, ...theme };
  applyBracketTheme({ name: `kitty! · ${theme.label}`, ...theme.roles }, root, "comfortable");
  // Bracket derives contrast from opaque hexes first; translucency is layered after.
  for (const role of ["void", "panel", "inset", "raised"] as const) {
    root.style.setProperty(`--solid-${role}`, theme.roles[role]);
    if (role !== "void") root.style.setProperty(`--br-${role}`, `color-mix(in srgb, ${theme.roles[role]} ${surface.alpha[role]}%, transparent)`);
  }
  theme.graph.forEach((color, index) => root.style.setProperty(`--c${index + 1}`, color));
  root.style.setProperty("--shadow-tint", theme.shadow);
  root.style.setProperty("--border-fine", `color-mix(in srgb, ${theme.roles.line} 70%, transparent)`);
  root.style.setProperty("--border-colorful", `color-mix(in srgb, ${theme.roles.reference} 48%, ${theme.roles.line})`);
  root.style.setProperty("--border-active", theme.roles.lineHot);
  root.style.setProperty("--r-panel", `${surface.radius}px`);
  root.style.colorScheme = theme.scheme;
  Object.assign(root.dataset, {
    theme: theme.id,
    scheme: theme.scheme,
    themeSurface: surface.style,
    themeFocus: focus,
    themeSelection: selection,
    themeMotion: motion,
    themeBackdrop: backdrop,
  });
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme.roles.void);
}

let pending = 0;

/** Loads the theme's motif module (if any) and swaps it in, unless another theme won meanwhile. */
async function applyMotif(id: ThemeId): Promise<void> {
  const ticket = ++pending;
  const root = document.documentElement;
  const load = MODULES[id];
  let module: ThemeModule | null = null;
  if (load) {
    try {
      module = (await load()).default;
    } catch (error) {
      // A missing chunk (e.g. after an update) leaves the engine presets in place.
      console.warn(`kitty!: the ${id} theme motif did not load`, error);
    }
  }
  if (ticket !== pending) return;
  if (module) root.dataset.themeMotif = id;
  else delete root.dataset.themeMotif;
  themeMotif.set(module ? id : null, module);
}

function apply(theme: ThemeTokens): Promise<void> {
  applyTokens(theme);
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme.id }));
  return applyMotif(theme.id);
}

/**
 * Applies a theme instantly and persists it in localStorage (`kitty.theme`). The returned
 * promise settles once its motif module is in.
 */
export function setTheme(id: ThemeId): Promise<void> {
  const theme = getTheme(id);
  try {
    localStorage.setItem(STORAGE_KEY, theme.id);
  } catch {
    // Storage can be unavailable (private mode); the theme still applies.
  }
  return apply(theme);
}

/** Applies the persisted theme; await it before mounting so the first paint has its motif. */
export function applySavedTheme(): Promise<void> {
  return apply(getTheme(savedTheme()));
}

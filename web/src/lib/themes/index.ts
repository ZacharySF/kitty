import { applyTheme as applyBracketTheme } from "@nunera/bracket/theme";
import { scopedCss, tokenPlan, type BuildTheme } from "../buildCore";
import { getTheme } from "./catalog";
import type { ThemeId, ThemeModule, ThemeTokens } from "./contract";
import { themeMotif } from "./motif.svelte";

/*
 * Applying a look: the build's base theme (catalog.ts) with its tokens and custom CSS
 * (docs/platform-design.md Q9) synchronously, then the theme's motif module lazily
 * (contract.ts). The build (build.svelte.ts) is the only caller and the only place the
 * theme is stored; there is no localStorage copy.
 */

/** Dispatched on `window` after a look's tokens are applied; `detail` is the base ThemeId. */
export const THEME_EVENT = "kitty:theme";
/** The `<style>` holding the build's custom CSS (Q9). */
export const BUILD_CSS_ID = "kitty-build-css";

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
  applyBracketTheme({ name: `rika! · ${theme.label}`, ...theme.roles }, root, "comfortable");
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
      console.warn(`rika!: the ${id} theme motif did not load`, error);
    }
  }
  if (ticket !== pending) return;
  if (module) root.dataset.themeMotif = id;
  else delete root.dataset.themeMotif;
  themeMotif.set(module ? id : null, module);
}

/** Custom properties the last applied tokens set, removed when they disappear. */
let tokenProperties: string[] = [];
let motifTheme: ThemeId | null = null;
let motifReady: Promise<void> = Promise.resolve();

/**
 * Applies a build's look to this window (Q9): the base theme with role tokens overriding
 * its roles, the other tokens as custom properties, the custom CSS scoped to #app, then
 * the base theme's motif. Safe mode applies the base theme alone. The returned promise
 * settles once the motif module is in.
 */
export function applyBuildLook(look: BuildTheme, safe: boolean): Promise<void> {
  const root = document.documentElement;
  const plan = tokenPlan(safe ? {} : look.tokens, tokenProperties);
  // Stale properties go first: the base theme then restores any it also sets.
  for (const name of plan.remove) root.style.removeProperty(name);
  const base = getTheme(look.base);
  applyTokens({ ...base, roles: { ...base.roles, ...plan.roles } });
  for (const [name, value] of Object.entries(plan.set)) root.style.setProperty(name, value);
  tokenProperties = Object.keys(plan.set);

  const css = safe ? "" : scopedCss(look.css);
  let style = document.getElementById(BUILD_CSS_ID);
  if (!css) style?.remove();
  else {
    if (!style) {
      style = document.createElement("style");
      style.id = BUILD_CSS_ID;
      document.head.append(style);
    }
    style.textContent = css;
  }
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: base.id }));
  // Token-only changes keep the motif already in place.
  if (motifTheme !== base.id) {
    motifTheme = base.id;
    motifReady = applyMotif(base.id);
  }
  return motifReady;
}

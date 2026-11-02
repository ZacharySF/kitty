import type { Component } from "svelte";
import type { Theme } from "@nunera/bracket/theme";

/**
 * The theme contract (design Q11: a theme is a world, not a tint).
 *
 * A theme is two halves:
 *
 * 1. **Tokens** (`ThemeTokens`, listed in `catalog.ts`). Plain data, applied synchronously
 *    before the app mounts by `applyTheme()` in `index.ts`:
 *    - `roles` / `graph` / `shadow`: Bracket's 16 colour roles, the six data colours
 *      (`--c1..--c6`) and the overlay shadow tint;
 *    - `surface`: what a surface is made of (glass, paper, flat or frame), its fill
 *      opacity over the backdrop and its corner radius;
 *    - `focus`, `selection`, `motion`: which focus ring, selection fill and motion style
 *      the theme uses;
 *    - `backdrop`: the generic ground under every surface (nebula, grain or plain).
 *    The tokens become `--*` custom properties and `data-theme-*` attributes on `<html>`;
 *    `engine.css` (imported by main.ts right after styles.css) turns those into the shared
 *    presets, so a theme without a module is already more than a colour swap. A module
 *    may refine a preset by overriding its custom properties (`--surface-fill`, …).
 *
 * 2. **An optional motif module** (`ThemeModule`, `<id>/index.ts`), lazy-loaded when the
 *    theme is applied (`MODULES` in `index.ts`). It default-exports a `ThemeModule` and
 *    imports its own CSS, which must be scoped to `:root[data-theme="<id>"]` (Vite never
 *    unloads a loaded stylesheet; the scope is what swaps it out on a theme switch).
 *    Motif rules that decorate (bullets, ticks, dividers, cat marks) are further scoped to
 *    `:not([data-motif="off"])`, so Settings → Appearance → Theme motif can turn them off.
 *    Its parts, all optional:
 *    - CSS: surface recipe beyond the preset (rims, specular fills), motif marks
 *      (bullets, ticks, dividers, frame ornaments), focus ornaments for `.theme-focus`,
 *      ambient animation;
 *    - `Art`: background art drawn once behind every surface (`ThemeLayer.svelte`);
 *    - `EmptyArt`: the illustration in every `EmptyState` (default: the sleeping cat);
 *    - `LoaderArt`: the art in view-level `Loader`s (default: the cat's glowing eyes).
 *    Once loaded, `<html data-theme-motif="<id>">` is set.
 *
 * Hard rules:
 * - **Typography is global.** Tokens and modules never set `font-family` (Q11-B was
 *   rejected); Fraunces / Inter / JetBrains Mono keep one job each in every theme.
 * - **Motion yields.** Every animation and transition a theme adds runs on `--motion-ms`
 *   / `--motion-ease` or stops under `:root[data-motion="reduced"]` and
 *   `prefers-reduced-motion: reduce` (`engine.css` zeroes the tokens and the global rule in
 *   `styles.css` stops animations).
 * - **Contrast budget.** Surfaces keep at least `MIN_SURFACE_ALPHA`% fill, and backdrop art
 *   stays quiet enough that `ink`, `mute` and `dim` text, selected and pressed controls
 *   pass WCAG AA on every surface over the brightest backdrop pixel
 *   (tests/themes.browser.test.mjs checks each theme).
 * - **Original art only.** Motifs are drawn for kitty!, as CSS and SVG; never traced.
 */

export type ThemeId =
  | "harness"
  | "dracula"
  | "catppuccin-mocha"
  | "tokyo-night"
  | "nord"
  | "gruvbox"
  | "one-dark"
  | "kitty"
  | "scythe"
  | "liquid-glass";

export type Palette = Omit<Theme, "name">;

/** Glass: frosted, translucent, blurred. Paper: matte, opaque, grain and soft shadow, no border.
 *  Flat: opaque fill and a hairline. Frame: square corners, double hairline, flat ink fill. */
export const SURFACE_STYLES = ["glass", "paper", "flat", "frame"] as const;
export type SurfaceStyle = (typeof SURFACE_STYLES)[number];

/** Every focus style keeps at least a 2px visible ring; petal and bracket add corner
 *  ornaments drawn by `ThemeLayer`'s focus overlay, glow adds a soft halo. */
export const FOCUS_STYLES = ["ring", "glow", "petal", "bracket"] as const;
export type FocusStyle = (typeof FOCUS_STYLES)[number];

/** How selected options, tabs and current items read: a tinted wash, a rounded pill with an
 *  accent edge, a lit glass lens, or a flat edge bar. */
export const SELECTION_STYLES = ["wash", "pill", "lit", "bar"] as const;
export type SelectionStyle = (typeof SELECTION_STYLES)[number];

/** Calm 320ms ease-out, fluid 240ms spring, snappy 120ms, or none (instant). Reduce motion
 *  always wins and forces none. */
export const MOTION_STYLES = ["calm", "fluid", "snappy", "none"] as const;
export type MotionStyle = (typeof MOTION_STYLES)[number];

/** The generic ground under the surfaces; a module's `Art` draws on top of it. */
export const BACKDROPS = ["nebula", "grain", "plain"] as const;
export type Backdrop = (typeof BACKDROPS)[number];

/** Surfaces never go below this fill opacity (%), whatever the art behind them. */
export const MIN_SURFACE_ALPHA = 24;

export type Surface = {
  style: SurfaceStyle;
  /** Fill opacity (%) of the panel, inset and raised colours over the backdrop. */
  alpha: { panel: number; inset: number; raised: number };
  /** Panel corner radius in px (controls keep their own radii). */
  radius: number;
};

export type ThemeTokens = {
  id: ThemeId;
  label: string;
  scheme: "dark" | "light";
  /** Bracket's 16 semantic roles. */
  roles: Palette;
  /** Data colours (--c1..--c6): subjects, calendars, graph groups. Six distinct hues. */
  graph: readonly [string, string, string, string, string, string];
  /** Tint used for overlay and popover shadows. */
  shadow: string;
  surface: Surface;
  focus: FocusStyle;
  selection: SelectionStyle;
  motion: MotionStyle;
  backdrop: Backdrop;
};

/** What a lazily loaded theme module default-exports (its CSS comes in as a side effect). */
export type ThemeModule = {
  /** Background art, drawn once behind every surface. Must stay inside the contrast budget. */
  Art?: Component;
  /** Empty-state illustration (decorative; rendered aria-hidden). */
  EmptyArt?: Component;
  /** View-level loader art (decorative; the Loader keeps its caption and role). */
  LoaderArt?: Component;
  /** Window frame ornament above all content; must stay at the edges and pass the pointer through. */
  Frame?: Component;
};

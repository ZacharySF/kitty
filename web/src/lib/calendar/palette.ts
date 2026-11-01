import type { KittyTheme } from "../themeCatalog";

/** Relative luminance of a `#rrggbb` colour (WCAG 2). */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two `#rrggbb` colours. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Palette slot 1..6 (`--c1..--c6`) for the calendar at `index`. */
export const paletteSlot = (index: number) => (Math.max(0, index) % 6) + 1;

/**
 * Text colour for a filled block on palette slot `slot`: the theme's dark
 * void when it reads at least as well as ink, otherwise ink.
 */
export function blockText(theme: KittyTheme, slot: number): string {
  const fill = theme.graph[slot - 1];
  return contrast(fill, theme.roles.void) >= contrast(fill, theme.roles.ink)
    ? "var(--solid-void)"
    : "var(--ink)";
}

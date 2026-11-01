import { applyTheme } from "@nunera/bracket/theme";
import { DEFAULT_THEME, getTheme, type KittyTheme, type Palette, type ThemeId } from "./themeCatalog";

/** Dispatched on `window` after a theme is applied; `detail` is the ThemeId. */
export const THEME_EVENT = "kitty:theme";
const STORAGE_KEY = "kitty.theme";

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

/** Surface alpha: panels stay readable while the backdrop shows through. */
const GLASS: Partial<Record<keyof Palette, number>> = { panel: 62, inset: 50, raised: 72 };

function applyKittyTheme(theme: KittyTheme): void {
  const root = document.documentElement;
  applyTheme(
    { name: `kitty! · ${theme.label}`, ...theme.roles },
    root,
    "comfortable",
  );
  // Bracket derives contrast from opaque hexes first; translucency is layered after.
  for (const role of ["void", "panel", "inset", "raised"] as const) {
    root.style.setProperty(`--solid-${role}`, theme.roles[role]);
    const alpha = GLASS[role];
    if (alpha) root.style.setProperty(`--br-${role}`, `color-mix(in srgb, ${theme.roles[role]} ${alpha}%, transparent)`);
  }
  theme.graph.forEach((color, index) =>
    root.style.setProperty(`--c${index + 1}`, color),
  );
  root.style.setProperty("--shadow-tint", theme.shadow);
  root.style.setProperty("--border-fine", `color-mix(in srgb, ${theme.roles.line} 70%, transparent)`);
  root.style.setProperty("--border-colorful", `color-mix(in srgb, ${theme.roles.reference} 48%, ${theme.roles.line})`);
  root.style.setProperty("--border-active", theme.roles.lineHot);
  root.style.setProperty("--motion-fast", "140ms");
  root.style.colorScheme = theme.scheme;
  root.dataset.theme = theme.id;
  root.dataset.scheme = theme.scheme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme.roles.void);
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme.id }));
}

/** Applies a theme instantly and persists it in localStorage (`kitty.theme`). */
export function setTheme(id: ThemeId): void {
  const theme = getTheme(id);
  applyKittyTheme(theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme.id);
  } catch {
    // Storage can be unavailable (private mode); the theme still applies.
  }
}

/** Applies the persisted theme; call once before mounting the app. */
export function applySavedTheme(): void {
  applyKittyTheme(getTheme(savedTheme()));
}

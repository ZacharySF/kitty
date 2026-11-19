/**
 * The Themes module: which themes Settings → Appearance offers. The default theme and the
 * one in use are always there; the rest are the build's `ui.themes.installed`. Pure, so
 * Node tests can import it.
 */
import { DEFAULT_THEME, THEMES } from "./catalog.ts";

/** Every theme the module can add: all but the default. */
export const EXTRA_THEMES = THEMES.filter((theme) => theme.id !== DEFAULT_THEME);
/** What turning the module on starts with. */
export const STARTER_THEMES = ["aero", "liquid-glass", "blossom", "nord"];

/** The themes to offer, in the catalogue's order. */
export function shownThemes(installed: readonly string[], active: string) {
  return THEMES.filter((theme) => theme.id === DEFAULT_THEME || theme.id === active || installed.includes(theme.id));
}

/** `installed` with `id` added or taken out, in the catalogue's order; names this build does not know are kept. */
export function toggleTheme(installed: readonly string[], id: string): string[] {
  const next = installed.includes(id) ? installed.filter((item) => item !== id) : [...installed, id];
  const order = (item: string) => { const at = THEMES.findIndex((theme) => theme.id === item); return at < 0 ? THEMES.length : at; };
  return [...next].sort((a, b) => order(a) - order(b));
}

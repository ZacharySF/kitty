import { applyTheme, type Theme } from "@nunera/bracket/theme";

export type ThemeId =
  | "dracula"
  | "catppuccin-mocha"
  | "tokyo-night"
  | "nord"
  | "gruvbox"
  | "one-dark"
  | "kitty"
  | "scythe";

type Palette = Omit<Theme, "name">;

export type KittyTheme = {
  id: ThemeId;
  label: string;
  scheme: "dark" | "light";
  /** Bracket's 16 semantic roles. */
  roles: Palette;
  /** Graph node palette (--c1..--c6), coordinated with the semantic roles. */
  graph: readonly [string, string, string, string, string, string];
  /** Tint used for overlay and popover shadows. */
  shadow: string;
  /** Optional decorative mark for the picker preview. */
  motif?: "blossom";
};

/** Decorative ASCII only; render with aria-hidden, and only for Kitty. */
export const KITTY_BLOSSOM = " .- .-. -.\n(   .'.   )\n '-.( * ).-'\n    '-' ";

/**
 * Editor accents/backgrounds follow their upstream palettes; secondary UI text
 * is lifted where editor comment colors would be too dim for small controls.
 */
export const THEMES: readonly KittyTheme[] = [
  {
    // https://github.com/dracula/dracula-theme#color-palette-oss
    id: "dracula",
    label: "Dracula",
    scheme: "dark",
    roles: {
      void: "#282a36",
      panel: "#303341",
      inset: "#232530",
      raised: "#383b4c",
      line: "#44475a",
      lineHot: "#bd93f9",
      ink: "#f8f8f2",
      mute: "#c2c5d7",
      dim: "#a8aec9",
      structure: "#bd93f9",
      signal: "#ffb86c",
      go: "#50fa7b",
      reference: "#8be9fd",
      caution: "#f1fa8c",
      alternate: "#ff79c6",
      danger: "#ff7575",
    },
    graph: ["#bd93f9", "#8be9fd", "#ff79c6", "#50fa7b", "#f1fa8c", "#ffb86c"],
    shadow: "#14151c",
  },
  {
    // https://github.com/catppuccin/palette/blob/main/palette.json (Mocha)
    id: "catppuccin-mocha",
    label: "Catppuccin Mocha",
    scheme: "dark",
    roles: {
      void: "#1e1e2e",
      panel: "#242436",
      inset: "#181825",
      raised: "#313244",
      line: "#45475a",
      lineHot: "#cba6f7",
      ink: "#cdd6f4",
      mute: "#bac2de",
      dim: "#a6adc8",
      structure: "#cba6f7",
      signal: "#f5c2e7",
      go: "#a6e3a1",
      reference: "#89b4fa",
      caution: "#f9e2af",
      alternate: "#94e2d5",
      danger: "#f38ba8",
    },
    graph: ["#cba6f7", "#89b4fa", "#94e2d5", "#f5c2e7", "#fab387", "#a6e3a1"],
    shadow: "#11111b",
  },
  {
    // https://github.com/folke/tokyonight.nvim/tree/main/lua/tokyonight/colors
    id: "tokyo-night",
    label: "Tokyo Night",
    scheme: "dark",
    roles: {
      void: "#1a1b26",
      panel: "#202231",
      inset: "#16161e",
      raised: "#292e42",
      line: "#3b4261",
      lineHot: "#7aa2f7",
      ink: "#c0caf5",
      mute: "#a9b1d6",
      dim: "#969fc4",
      structure: "#7aa2f7",
      signal: "#bb9af7",
      go: "#9ece6a",
      reference: "#7dcfff",
      caution: "#e0af68",
      alternate: "#73daca",
      danger: "#f7768e",
    },
    graph: ["#7aa2f7", "#bb9af7", "#7dcfff", "#73daca", "#e0af68", "#f7768e"],
    shadow: "#0c0e14",
  },
  {
    // https://github.com/nordtheme/nord/blob/develop/src/nord.scss
    id: "nord",
    label: "Nord",
    scheme: "dark",
    roles: {
      void: "#2e3440",
      panel: "#353c4a",
      inset: "#292e39",
      raised: "#3b4252",
      line: "#4c566a",
      lineHot: "#88c0d0",
      ink: "#eceff4",
      mute: "#d8dee9",
      dim: "#b3bfd1",
      structure: "#88c0d0",
      signal: "#8fbcbb",
      go: "#a3be8c",
      reference: "#a4bed9",
      caution: "#ebcb8b",
      alternate: "#b48ead",
      danger: "#d4868d",
    },
    graph: ["#88c0d0", "#81a1c1", "#b48ead", "#a3be8c", "#ebcb8b", "#d08770"],
    shadow: "#1b2029",
  },
  {
    // https://github.com/morhetz/gruvbox/blob/master/colors/gruvbox.vim
    id: "gruvbox",
    label: "Gruvbox",
    scheme: "dark",
    roles: {
      void: "#282828",
      panel: "#32302f",
      inset: "#1d2021",
      raised: "#3c3836",
      line: "#665c54",
      lineHot: "#d5c4a1",
      ink: "#ebdbb2",
      mute: "#d5c4a1",
      dim: "#bdae93",
      structure: "#fabd2f",
      signal: "#8ec07c",
      go: "#b8bb26",
      reference: "#a7c1b6",
      caution: "#fabd2f",
      alternate: "#d3869b",
      danger: "#fb7968",
    },
    graph: ["#fabd2f", "#8ec07c", "#83a598", "#d3869b", "#fe8019", "#ebdbb2"],
    shadow: "#181a1b",
  },
  {
    // https://github.com/atom/one-dark-syntax/blob/master/styles/colors.less
    id: "one-dark",
    label: "One Dark",
    scheme: "dark",
    roles: {
      void: "#282c34",
      panel: "#2c313a",
      inset: "#21252b",
      raised: "#353b45",
      line: "#4b5363",
      lineHot: "#61afef",
      ink: "#d7dae0",
      mute: "#abb2bf",
      dim: "#9da5b4",
      structure: "#61afef",
      signal: "#c678dd",
      go: "#98c379",
      reference: "#56b6c2",
      caution: "#e5c07b",
      alternate: "#d19a66",
      danger: "#e06c75",
    },
    graph: ["#61afef", "#c678dd", "#56b6c2", "#98c379", "#e5c07b", "#e06c75"],
    shadow: "#17191f",
  },
  {
    id: "kitty",
    label: "Kitty",
    scheme: "dark",
    roles: {
      void: "#2a2028",
      panel: "#352832",
      inset: "#261d25",
      raised: "#43333e",
      line: "#785765",
      lineHot: "#d6a6b8",
      ink: "#f5e8ee",
      mute: "#d9bdca",
      dim: "#c7a9b8",
      structure: "#deb2c3",
      signal: "#e4bdcc",
      go: "#becbb7",
      reference: "#d3adbf",
      caution: "#e1c7ad",
      alternate: "#c6b5d5",
      danger: "#e7a7ae",
    },
    graph: ["#deb2c3", "#cba7c0", "#c6b5d5", "#b8c5d6", "#f5e8ee", "#e1c7ad"],
    shadow: "#181117",
    motif: "blossom",
  },
  {
    id: "scythe",
    label: "Scythe",
    scheme: "dark",
    roles: {
      void: "#11121b",
      panel: "#1a1b29",
      inset: "#151622",
      raised: "#252638",
      line: "#45475f",
      lineHot: "#9b96c9",
      ink: "#ecebf5",
      mute: "#b4b1c8",
      dim: "#9e9bb6",
      structure: "#b5a5dc",
      signal: "#abc2e5",
      go: "#a7c4bd",
      reference: "#95b3d4",
      caution: "#d3bb98",
      alternate: "#c397ac",
      danger: "#d294a6",
    },
    graph: ["#b5a5dc", "#95b3d4", "#ecebf5", "#9b96c9", "#b7c9e3", "#c397ac"],
    shadow: "#06060d",
  },
];

export const DEFAULT_THEME: ThemeId = "dracula";
/** Dispatched on `window` after a theme is applied; `detail` is the ThemeId. */
export const THEME_EVENT = "kitty:theme";
const STORAGE_KEY = "kitty.theme";

export function getTheme(id: string | null | undefined): KittyTheme {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[0];
}

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

function applyKittyTheme(theme: KittyTheme): void {
  const root = document.documentElement;
  applyTheme(
    { name: `kitty! · ${theme.label}`, ...theme.roles },
    root,
    "comfortable",
  );
  theme.graph.forEach((color, index) =>
    root.style.setProperty(`--c${index + 1}`, color),
  );
  root.style.setProperty("--shadow-tint", theme.shadow);
  root.style.setProperty("--border-fine", `color-mix(in srgb, ${theme.roles.structure} 34%, ${theme.roles.line})`);
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

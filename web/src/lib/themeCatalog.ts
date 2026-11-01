import type { Theme } from "@nunera/bracket/theme";

/* Theme data and lookup only: no DOM, so it loads in Node tests. Applying a theme lives in themes.ts. */
export type ThemeId =
  | "harness"
  | "dracula"
  | "catppuccin-mocha"
  | "tokyo-night"
  | "nord"
  | "gruvbox"
  | "one-dark"
  | "kitty"
  | "scythe";

export type Palette = Omit<Theme, "name">;

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
    // LearnHarness's neutral chrome (#141414 / #1c1c1c / #434343) with kitty's pink accents.
    id: "harness",
    label: "Harness",
    scheme: "dark",
    roles: {
      void: "#141414",
      panel: "#1c1c1c",
      inset: "#111111",
      raised: "#262626",
      line: "#434343",
      lineHot: "#8d8d8d",
      ink: "#e6e6e6",
      mute: "#b4b4b4",
      dim: "#8d8d8d",
      structure: "#deb2c3",
      signal: "#e4bdcc",
      go: "#a8d5b5",
      reference: "#9cc7f0",
      caution: "#e8c872",
      alternate: "#c6b5f0",
      danger: "#ee928c",
    },
    graph: ["#deb2c3", "#c9b3e6", "#b3bfe8", "#e8c4d3", "#d6c1ea", "#e6dce1"],
    shadow: "#000000",
  },
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
      void: "#1b151a",
      panel: "#241c22",
      inset: "#161115",
      raised: "#30252d",
      line: "#5e4655",
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

/** First launch, and any saved id that no longer exists, open on Kitty. */
export const DEFAULT_THEME: ThemeId = "kitty";

export function getTheme(id: string | null | undefined): KittyTheme {
  return THEMES.find((theme) => theme.id === id) ?? THEMES.find((theme) => theme.id === DEFAULT_THEME)!;
}

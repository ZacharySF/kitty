import type { ITheme } from "@xterm/xterm";

/** Bracket's semantic roles, as listed for each theme in themes.ts. */
export type ThemeRoles = {
  void: string; panel: string; inset: string; raised: string; line: string; lineHot: string;
  ink: string; mute: string; dim: string; structure: string; signal: string; go: string;
  reference: string; caution: string; alternate: string; danger: string;
};

/**
 * Q1: a transparent terminal (the page's glass shows through) drawn in theme
 * roles. kitty!'s herdr config uses the same Harness roles for its own chrome.
 */
export function terminalTheme(roles: ThemeRoles): ITheme {
  return {
    background: "#00000000",
    foreground: roles.ink,
    cursor: roles.structure,
    cursorAccent: roles.void,
    selectionBackground: roles.line,
    selectionForeground: roles.ink,
    black: roles.raised,
    red: roles.danger,
    green: roles.go,
    yellow: roles.caution,
    blue: roles.reference,
    magenta: roles.structure,
    cyan: roles.alternate,
    white: roles.mute,
    brightBlack: roles.dim,
    brightRed: roles.danger,
    brightGreen: roles.go,
    brightYellow: roles.caution,
    brightBlue: roles.reference,
    brightMagenta: roles.signal,
    brightCyan: roles.alternate,
    brightWhite: roles.ink,
  };
}

/** Q12: the upgrade stays on the page's own origin; only the scheme changes. */
export function terminalSocketUrl(
  location: { protocol: string; host: string },
  socket: string,
  token: string,
  cols: number,
  rows: number,
): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const query = new URLSearchParams({ token, cols: String(cols), rows: String(rows) });
  return `${scheme}//${location.host}${socket}?${query}`;
}

export type AgentState = "idle" | "working" | "blocked" | "done" | "unknown";
export type TerminalActivity = {
  running: boolean;
  workspaces: {
    id: string;
    label: string;
    status: AgentState;
    focused: boolean;
    /** `task` is the pane's terminal title; `since` the epoch ms its state began. */
    agents: { pane: string; name: string; status: AgentState; task: string; line: string; revision: number; since: number }[];
  }[];
};

/** GET /api/terminal/tern (kitty/web_tern.py): the installed Tern and kitty!'s own session. */
export type TernBlock = {
  id: number; title: string; cwd: string | null; program: string | null; cols: number | null; rows: number | null;
  focused: boolean; live: boolean; exited: number | null; working: boolean;
};
export type TernStatus = {
  install_url: string;
  installed: boolean;
  path: string | null;
  source: "settings" | "path" | null;
  override: string | null;
  version: string | null;
  problem: string | null;
  workspace: string;
  socket: string;
  running: boolean;
  windows: number;
  sessions: { id: number; name: string; shown: boolean; tabs: { id: number; number: number | null; name: string | null; shown: boolean; blocks: TernBlock[] }[] }[];
};

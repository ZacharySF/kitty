import type { ITheme } from "@xterm/xterm";

/** Bracket's semantic roles, as listed for each theme in themes.ts. */
export type ThemeRoles = {
  void: string; panel: string; inset: string; raised: string; line: string; lineHot: string;
  ink: string; mute: string; dim: string; structure: string; signal: string; go: string;
  reference: string; caution: string; alternate: string; danger: string;
};

/**
 * Q1: a transparent terminal (the page's glass shows through) drawn in theme
 * roles. rika!'s herdr config uses the same Harness roles for its own chrome.
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

/** GET /api/terminal/tern (kitty/web_tern.py): the installed Tern and rika!'s own session. */
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
  /** Recent pane starts and exits on rika!'s daemon, newest first. */
  activity: TernEvent[];
  /** Captures can be saved as quick notes (the Workspace module is on). */
  quick_notes: boolean;
};
export type TernEvent = { event: "pane_spawned" | "pane_exited"; pane: number; status: number | null; at: string };
/** A pane of rika!'s own Tern session, the only panes rika! runs, focuses or captures. */
export type TernPane = TernBlock & { tab: number | null; shown: boolean };

/** rika!'s session is named "kitty" (kitty/core/tern.py SESSION). */
export function ternPanes(status: TernStatus): TernPane[] {
  return status.sessions.filter((session) => session.name === "kitty")
    .flatMap((session) => session.tabs.flatMap((tab) => tab.blocks.map((block) => ({ ...block, tab: tab.number, shown: tab.shown }))));
}

/** The pane a command runs in: the chosen one while it exists, else the one on screen (Tern marks
 *  a focused block in every tab; only the shown tab's is visible), else the first. */
export function ternTarget(panes: TernPane[], chosen: number | null): TernPane | null {
  return panes.find((pane) => pane.id === chosen) ?? panes.find((pane) => pane.shown && pane.focused)
    ?? panes.find((pane) => pane.focused) ?? panes[0] ?? null;
}

export function ternPaneLabel(pane: TernBlock): string {
  return pane.title || pane.program?.split("/").pop() || `pane ${pane.id}`;
}

/** Live state of a pane: exited (with its status), working or idle. */
export function ternBlockState(block: TernBlock): { state: "exited" | "working" | "idle"; label: string } {
  if (block.exited !== null) return { state: "exited", label: `exited ${block.exited}` };
  return block.working ? { state: "working", label: "working" } : { state: "idle", label: "idle" };
}

export function ternEventLabel(event: TernEvent): string {
  if (event.event === "pane_spawned") return `pane ${event.pane} started`;
  return event.status === null ? `pane ${event.pane} exited` : `pane ${event.pane} exited ${event.status}`;
}

/** GET /api/terminal/tern/plugin (kitty/core/tern_plugin.py): the kitty Tern plugin in the user's Tern. */
export type TernPluginStatus = {
  available: boolean;
  version: string | null;
  dir: string;
  installed: boolean;
  installed_version: string | null;
  current: boolean;
  linked_elsewhere: string | null;
  tern_status: string | null;
  problems: string[];
  /** The build's settings.tern.plugin. */
  enabled: boolean;
};

/** What the plugin panel says and which action it offers. */
export function ternPluginState(plugin: TernPluginStatus): {
  label: string; action: "install" | "update" | null; problem: string | null;
} {
  const failed = plugin.tern_status?.startsWith("failed") ? `Tern reports: ${plugin.tern_status}` : null;
  const problem = plugin.problems[0] ?? failed;
  if (!plugin.available) return { label: "This rika! was built without the Tern plugin", action: null, problem };
  if (!plugin.enabled) return { label: "Turned off in this build (settings.tern.plugin)", action: null, problem };
  if (!plugin.installed) {
    const label = plugin.linked_elsewhere ? "Tern uses the plugin of another rika! workspace" : "Not installed in Tern";
    return { label, action: "install", problem };
  }
  if (!plugin.current) return { label: "Installed; this rika! ships a newer copy", action: "update", problem };
  return { label: plugin.version ? `Installed (${plugin.version})` : "Installed", action: null, problem };
}

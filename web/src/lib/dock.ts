/**
 * The dock (docs/terminal-dock-design.md Q3, Q8): the bottom panel of the window
 * holding kitty!'s terminal, herdr's agent cards (Q56), or nothing but a thin
 * handle. Its state and height are remembered per browser profile.
 */
export type DockMode = "terminal" | "activity" | "hidden";
export const DOCK_MODES: readonly DockMode[] = ["terminal", "activity", "hidden"];
/**
 * `shown` is what the handle brings back after hiding. `float` detaches the dock into a
 * floating bottom-right card (hidden: a small pill) that reserves no page space.
 */
export type DockState = { mode: DockMode; height: number | null; shown: Exclude<DockMode, "hidden">; float: boolean };

export const DOCK_STORAGE_KEY = "kitty.dock";
/** Roughly eight terminal rows plus the dock's own controls. */
export const DOCK_MIN_HEIGHT = 160;
/** The main panel above the dock never shrinks below this. */
export const MAIN_MIN_HEIGHT = 200;

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export function clampDockHeight(height: number, viewport: number): number {
  return Math.max(DOCK_MIN_HEIGHT, Math.min(Math.round(height), viewport - MAIN_MIN_HEIGHT));
}

/**
 * Q8: the first size is the space the dock fills in the current layout, the empty
 * area between the bottom of the main panel's content and the bottom of the window.
 */
export function firstDockHeight(viewport: number, panelBottom: number): number {
  return clampDockHeight(viewport - panelBottom, viewport);
}

export function readDock(storage: Storage): DockState {
  const fallback: DockState = { mode: "terminal", height: null, shown: "terminal", float: false };
  try {
    const saved: unknown = JSON.parse(storage.getItem(DOCK_STORAGE_KEY) ?? "null");
    if (!saved || typeof saved !== "object") return fallback;
    const fields = saved as Record<string, unknown>;
    const mode = DOCK_MODES.includes(fields.mode as DockMode) ? (fields.mode as DockMode) : fallback.mode;
    const height = fields.height;
    return {
      mode,
      height: typeof height === "number" && Number.isFinite(height) && height > 0 ? height : null,
      shown: mode !== "hidden" ? mode : fields.shown === "activity" ? "activity" : "terminal",
      float: fields.float === true,
    };
  } catch {
    return fallback;
  }
}

export function writeDock(storage: Storage, state: DockState): void {
  try {
    storage.setItem(DOCK_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage can be unavailable (private mode); the dock still works this session.
  }
}

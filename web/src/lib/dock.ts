/**
 * The dock (docs/terminal-dock-design.md Q3, Q8): a floating glass card in the
 * bottom-right corner holding kitty!'s terminal or herdr's agent cards (Q56);
 * hidden, it is a small pill. It never takes page space. Its state, size and
 * position are remembered per browser profile.
 */
export type DockMode = "terminal" | "activity" | "hidden";
export const DOCK_MODES: readonly DockMode[] = ["terminal", "activity", "hidden"];
export type DockSize = { width: number; height: number };
/** Distance of the card from the window's right and bottom edges. */
export type DockOffset = { right: number; bottom: number };
/**
 * `shown` is what the pill brings back after hiding. `size` is null until the card
 * is resized, so it follows the default for the current window.
 */
export type DockState = { mode: DockMode; shown: Exclude<DockMode, "hidden">; size: DockSize | null; offset: DockOffset };

export const DOCK_STORAGE_KEY = "kitty.dock";
/** Roughly eight terminal rows plus the dock's own controls. */
export const DOCK_MIN_HEIGHT = 160;
/** Room for the dock's tabs and actions. */
export const DOCK_MIN_WIDTH = 320;
/** The card keeps at least this much of the window around it. */
export const DOCK_MARGIN = 8;
const CORNER: DockOffset = { right: 16, bottom: 16 };

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

const within = (value: number, low: number, high: number) => Math.max(low, Math.min(Math.round(value), high));

export function defaultDockSize(viewportWidth: number, viewportHeight: number): DockSize {
  return { width: Math.min(720, Math.round(viewportWidth * 0.92)), height: Math.min(440, Math.round(viewportHeight * 0.6)) };
}

export function clampDockSize(size: DockSize, viewportWidth: number, viewportHeight: number): DockSize {
  return {
    width: within(size.width, DOCK_MIN_WIDTH, viewportWidth - 2 * DOCK_MARGIN),
    height: within(size.height, DOCK_MIN_HEIGHT, viewportHeight - 2 * DOCK_MARGIN),
  };
}

export function clampDockOffset(offset: DockOffset, size: DockSize, viewportWidth: number, viewportHeight: number): DockOffset {
  return {
    right: within(offset.right, DOCK_MARGIN, viewportWidth - size.width - DOCK_MARGIN),
    bottom: within(offset.bottom, DOCK_MARGIN, viewportHeight - size.height - DOCK_MARGIN),
  };
}

const positive = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value > 0;
const placed = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;

/**
 * Older builds also saved a docked `height` and a `float` flag; both are dropped,
 * so any saved state loads as the floating card.
 */
export function readDock(storage: Storage): DockState {
  const fallback: DockState = { mode: "terminal", shown: "terminal", size: null, offset: { ...CORNER } };
  try {
    const saved: unknown = JSON.parse(storage.getItem(DOCK_STORAGE_KEY) ?? "null");
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return fallback;
    const fields = saved as Record<string, unknown>;
    const mode = DOCK_MODES.includes(fields.mode as DockMode) ? (fields.mode as DockMode) : fallback.mode;
    const size = fields.size as Partial<DockSize> | null | undefined;
    const offset = fields.offset as Partial<DockOffset> | null | undefined;
    return {
      mode,
      shown: mode !== "hidden" ? mode : fields.shown === "activity" ? "activity" : "terminal",
      size: size && positive(size.width) && positive(size.height) ? { width: size.width, height: size.height } : null,
      offset: offset && placed(offset.right) && placed(offset.bottom) ? { right: offset.right, bottom: offset.bottom } : fallback.offset,
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

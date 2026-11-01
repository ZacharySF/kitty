/**
 * The dock (docs/terminal-dock-design.md Q3, Q8): kitty!'s terminal or herdr's
 * agent cards (Q56). By default it is docked to the bottom of the window at a fixed
 * height step (hidden: a thin handle); floated, it is a glass card in the
 * bottom-right corner (hidden: a small pill) that takes no page space. Its state,
 * height step, card size and position are remembered per browser profile.
 */
export type DockMode = "terminal" | "activity" | "hidden";
export const DOCK_MODES: readonly DockMode[] = ["terminal", "activity", "hidden"];
/** The docked height steps: small, medium, large. There is no dragged height. */
export type DockStep = "s" | "m" | "l";
export const DOCK_STEPS: readonly DockStep[] = ["s", "m", "l"];
const STEP_SHARE: Record<DockStep, number> = { s: 0.25, m: 0.4, l: 0.6 };
export type DockSize = { width: number; height: number };
/** Distance of the card from the window's right and bottom edges. */
export type DockOffset = { right: number; bottom: number };
/**
 * `shown` is what the handle or pill brings back after hiding. `float` shows the
 * corner card instead of the bottom panel. `size` is null until the card is
 * resized, so it follows the default for the current window.
 */
export type DockState = {
  mode: DockMode;
  shown: Exclude<DockMode, "hidden">;
  float: boolean;
  step: DockStep;
  size: DockSize | null;
  offset: DockOffset;
};

export const DOCK_STORAGE_KEY = "kitty.dock";
/** Roughly eight terminal rows plus the dock's own controls. */
export const DOCK_MIN_HEIGHT = 160;
/** The page above the docked panel never shrinks below this (the Brain home's floor). */
export const MAIN_MIN_HEIGHT = 320;
/** Room for the dock's tabs and actions. */
export const DOCK_MIN_WIDTH = 320;
/** The card keeps at least this much of the window around it. */
export const DOCK_MARGIN = 8;
const CORNER: DockOffset = { right: 16, bottom: 16 };

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

const within = (value: number, low: number, high: number) => Math.max(low, Math.min(Math.round(value), high));

/** The docked panel's height: a share of the window, keeping both it and the page usable. */
export function dockedHeight(step: DockStep, viewportHeight: number): number {
  return Math.max(DOCK_MIN_HEIGHT, Math.min(Math.round(viewportHeight * STEP_SHARE[step]), viewportHeight - MAIN_MIN_HEIGHT));
}

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
 * v0.3.22 saved no `float` (the card was the only form), so its state loads docked
 * with its card size and position kept for floating. Older builds' dragged docked
 * `height` is dropped.
 */
export function readDock(storage: Storage): DockState {
  const fallback: DockState = { mode: "terminal", shown: "terminal", float: false, step: "m", size: null, offset: { ...CORNER } };
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
      float: fields.float === true,
      step: DOCK_STEPS.includes(fields.step as DockStep) ? (fields.step as DockStep) : fallback.step,
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

/**
 * The dock (docs/terminal-dock-design.md Q3, Q8): rika!'s terminal or herdr's
 * agent cards (Q56). By default it is docked to the bottom of the window at a fixed
 * height step (hidden: a thin handle); floated, it is a glass card in the
 * bottom-right corner (hidden: a small pill) that takes no page space. Its state,
 * height step, card size and position live in the build (`ui.dock`, Q8).
 */
import type { DockOffset, DockSize, DockStep } from "./buildCore";

/** The docked height steps: small, medium, large. There is no dragged height. */
export const DOCK_STEPS: readonly DockStep[] = ["s", "m", "l"];
const STEP_SHARE: Record<DockStep, number> = { s: 0.25, m: 0.4, l: 0.6 };
/** Roughly eight terminal rows plus the dock's own controls. */
export const DOCK_MIN_HEIGHT = 160;
/** The page above the docked panel never shrinks below this (the Brain home's floor). */
export const MAIN_MIN_HEIGHT = 320;
/** Room for the dock's tabs and actions. */
export const DOCK_MIN_WIDTH = 320;
/** The card keeps at least this much of the window around it. */
export const DOCK_MARGIN = 8;

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

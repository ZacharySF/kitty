/**
 * The dock (docs/terminal-dock-design.md Q3, Q8): the bottom panel of the window
 * holding kitty!'s terminal, herdr's agent-activity strip, or nothing but a thin
 * handle. Its state and height live in the build (`ui.dock`, Q8).
 */
/** Roughly eight terminal rows plus the dock's own controls. */
export const DOCK_MIN_HEIGHT = 160;
/** The main panel above the dock never shrinks below this. */
export const MAIN_MIN_HEIGHT = 200;

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

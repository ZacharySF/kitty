/**
 * Launchers on top of the Math view (Ctrl K, the home Up next row): the request is left
 * in the session and the caller opens Math, which always mounts afresh and takes it.
 */
export type MathLaunch = "sprint" | "lesson" | "practice";
const KEY = "kitty.math.launch";

export function launchMath(mode: MathLaunch) {
  try { sessionStorage.setItem(KEY, mode); } catch { /* Math opens on its last mode. */ }
}

/** The pending launch, consumed. */
export function takeLaunch(): MathLaunch | null {
  try {
    const mode = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return mode === "sprint" || mode === "lesson" || mode === "practice" ? mode : null;
  } catch {
    return null;
  }
}

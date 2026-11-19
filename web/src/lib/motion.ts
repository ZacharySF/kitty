/**
 * The Animations module, as the base sees it: `<html data-anim="on">` and its script started
 * once its pack has loaded (ui.animations.on in the build; build.svelte.ts calls
 * `setAnimations`), and a tween for script-driven moves that falls back to the final value at once. The pack
 * (./animations) is imported only here and only when the module is on.
 */
type Pack = typeof import("./animations/index");
let pack: Pack | null = null;
let wanted = false;

const still = () => document.documentElement.dataset.motion === "reduced" || matchMedia("(prefers-reduced-motion: reduce)").matches;

export async function setAnimations(on: boolean): Promise<void> {
  wanted = on;
  if (on && !pack) {
    try {
      pack = await import("./animations/index");
    } catch (error) {
      // Not downloaded (or a stale chunk after an update): the app is simply still.
      console.warn("rika!: the Animations module did not load", error);
    }
  }
  const live = wanted && pack !== null;
  document.documentElement.dataset.anim = live ? "on" : "off";
  if (live) pack!.start();
  else pack?.end();
}

/** Whether moves are animated right now. */
export function animated(): boolean {
  return wanted && pack !== null && !still();
}

/** Runs `apply` from 0 to 1 over `ms` when animated, else once with 1. Returns a stop function. */
export function tween(ms: number, apply: (progress: number) => void): () => void {
  if (ms > 0 && animated() && pack) return pack.tween(ms, apply);
  apply(1);
  return () => {};
}

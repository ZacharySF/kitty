/**
 * The Animations module's payload (a pack: web/vite.config.ts): its stylesheet and the one
 * piece of script motion needs, a tween. The base reaches it only through ../motion.ts,
 * which loads it when the module is switched on, so a workspace without it loads nothing.
 *
 * The rules it follows (Apple's and Material's motion guidance, Chrome's motion guide):
 * short (80–260 ms), entrances ease out and are slower than exits, only `transform` and
 * `opacity` move (the compositor's, so nothing re-lays out), distances are a few pixels,
 * and all of it stops under Reduce motion (the setting or the system's).
 */
import "./animations.css";

/** Ease-out cubic: fast first, settling at the end. */
const ease = (t: number) => 1 - (1 - t) ** 3;

/**
 * Calls `apply` with values from 0 to 1 over `ms`, eased; returns a function that stops
 * it where it is. A second tween of the same thing should stop the first.
 */
export function tween(ms: number, apply: (progress: number) => void): () => void {
  const started = performance.now();
  let frame = 0;
  const step = (now: number) => {
    const progress = Math.min(1, (now - started) / ms);
    apply(ease(progress));
    if (progress < 1) frame = requestAnimationFrame(step);
  };
  frame = requestAnimationFrame(step);
  return () => cancelAnimationFrame(frame);
}

// How often frames go to kitty!. A mirrored tab sends a frame when it is switched on, then
// at most one every FRAME_GAP_MS while the page changes or scrolls, and one every
// HEARTBEAT_MS while it is still (so kitty! keeps it; kitty! drops a mirror after 150 s of
// silence). Screenshots across all tabs stay CAPTURE_GAP_MS apart: Chrome allows
// tabs.captureVisibleTab twice a second.

export const FRAME_GAP_MS = 3000;
export const HEARTBEAT_MS = 60_000;
export const CAPTURE_GAP_MS = 600;

/** One tab's frame pacing: changes coalesce into at most one frame per gap. */
export class Pacer {
  private last = -Infinity;
  private pending = false;

  private readonly gap: number;

  constructor(gap = FRAME_GAP_MS) {
    this.gap = gap;
  }

  /**
   * A change happened at `now`: the delay before the next frame, or null when a frame is
   * already scheduled (this change will be in it).
   */
  schedule(now: number): number | null {
    if (this.pending) return null;
    this.pending = true;
    return Math.max(0, this.last + this.gap - now);
  }

  /** A frame was taken at `now` (sent or not); changes after this schedule a new one. */
  ran(now: number): void {
    this.last = now;
    this.pending = false;
  }

  /** The scheduled frame was dropped (mirroring stopped). */
  cancel(): void {
    this.pending = false;
  }

  /** True when nothing was sent for a heartbeat. */
  quiet(now: number, heartbeat = HEARTBEAT_MS): boolean {
    return !this.pending && now - this.last >= heartbeat;
  }
}

/** Keeps screenshot calls across every tab at least `gap` apart. */
export class CaptureGate {
  private next = -Infinity;

  private readonly gap: number;

  constructor(gap = CAPTURE_GAP_MS) {
    this.gap = gap;
  }

  /** Reserve the next capture slot: how long to wait from `now` before capturing. */
  reserve(now: number): number {
    const at = Math.max(now, this.next);
    this.next = at + this.gap;
    return at - now;
  }
}

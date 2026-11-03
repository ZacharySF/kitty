/**
 * Hold Space in the Rika bar to talk (docs/rika-design.md Q3 A). Pure, so the timing is
 * testable without a browser:
 *
 * - a tap (released before HOLD_MS) types a space;
 * - holding past HOLD_MS starts listening; releasing sends what was said;
 * - holding past LATCH_MS turns it into a brain dump (Q7 A) that keeps listening after
 *   Space is released; the next press of Space ends the dump and makes the plan.
 *
 * Nothing is drawn for any of this (no keycap or hint, Q5); the breathing bar is the signal.
 */
export const HOLD_MS = 300;
/** Longer than any spoken command: past this the hold is a ramble, so it becomes a dump. */
export const LATCH_MS = 8000;

export type HoldEvents = {
  /** Held past HOLD_MS: start listening for a command. */
  start(): void;
  /** Held past LATCH_MS: keep listening as a brain dump. */
  latch(): void;
  /** Released (or pressed again after a latch): stop and send. */
  stop(): void;
  /** Released before HOLD_MS: type a space. */
  tap(): void;
};
type Timers = { set: (run: () => void, ms: number) => unknown; clear: (handle: unknown) => void };

const browserTimers: Timers = { set: (run, ms) => setTimeout(run, ms), clear: (handle) => clearTimeout(handle as number) };

export class SpaceHold {
  state: "idle" | "pressed" | "holding" | "latched" | "ending" = "idle";
  private timer: unknown = null;
  private latchTimer: unknown = null;
  private readonly on: HoldEvents;
  private readonly timers: Timers;
  private readonly holdMs: number;
  private readonly latchMs: number;

  constructor(on: HoldEvents, timers: Timers = browserTimers, holdMs = HOLD_MS, latchMs = LATCH_MS) {
    this.on = on;
    this.timers = timers;
    this.holdMs = holdMs;
    this.latchMs = latchMs;
  }

  /** Space went down; the caller prevents the key's default (a tap types its space on release). */
  down(repeat: boolean) {
    if (this.state === "latched" && !repeat) {
      this.state = "ending";
      this.on.stop();
      return;
    }
    if (this.state !== "idle" || repeat) return;
    this.state = "pressed";
    this.timer = this.timers.set(() => {
      this.timer = null;
      this.state = "holding";
      this.on.start();
      this.latchTimer = this.timers.set(() => {
        this.latchTimer = null;
        if (this.state !== "holding") return;
        this.state = "latched";
        this.on.latch();
      }, this.latchMs - this.holdMs);
    }, this.holdMs);
  }

  up() {
    if (this.state === "pressed") {
      this.clear();
      this.state = "idle";
      this.on.tap();
    } else if (this.state === "holding") {
      this.clear();
      this.state = "idle";
      this.on.stop();
    } else if (this.state === "ending") {
      this.state = "idle";
    }
    // A latched dump keeps listening after Space is released.
  }

  /** A dump started some other way (the drawer's Brain dump, Ctrl K): the next Space ends it. */
  adopt() {
    if (this.state === "idle") this.state = "latched";
  }

  /** Forget the hold without a callback (Escape cancelled, or listening ended elsewhere). */
  reset() {
    this.clear();
    this.state = "idle";
  }

  private clear() {
    if (this.timer !== null) this.timers.clear(this.timer);
    if (this.latchTimer !== null) this.timers.clear(this.latchTimer);
    this.timer = this.latchTimer = null;
  }
}

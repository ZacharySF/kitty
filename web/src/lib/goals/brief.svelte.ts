import { rika } from "../rika/session.svelte";
import { loadBrief, markBriefSeen, runBrief } from "./api";
import type { Brief } from "./goals";

/**
 * Today's morning brief (docs/rika-design.md Q15 A), shared by the Rika bar's
 * "Morning brief · N" chip and the home widget. It loads when the first of them mounts,
 * follows `{type:'brief'}` stream events and re-reads when the window regains focus.
 */
class BriefState {
  brief = $state<Brief | null>(null);
  loaded = $state(false);
  error = $state("");
  busy = $state(false);
  /** The bar chip's popover. */
  open = $state(false);
  private users = 0;
  private stop: () => void = () => {};

  /** Returns the release; the last release stops listening. */
  start(): () => void {
    if (this.users++ === 0) {
      void this.load();
      const unsubscribe = rika.subscribe((event) => {
        // The sweep publishes `{type:'brief', brief}` with the brief it wrote.
        if (event.type === "brief" && "brief" in event) this.brief = (event.brief as Brief | null) ?? null;
      });
      const focus = () => void this.load();
      window.addEventListener("focus", focus);
      this.stop = () => {
        unsubscribe();
        window.removeEventListener("focus", focus);
      };
    }
    let released = false;
    return () => {
      if (released) return;
      released = true;
      if (--this.users === 0) this.stop();
    };
  }

  async load() {
    try {
      this.brief = await loadBrief();
      this.error = "";
    } catch (cause) {
      this.error = (cause as Error).message;
    } finally {
      this.loaded = true;
    }
  }

  /** Opening the brief anywhere marks it seen. */
  async seen() {
    if (!this.brief || this.brief.seen) return;
    try {
      this.brief = await markBriefSeen();
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  /** Sweeps now (POST /api/rika/brief/run). */
  async run() {
    if (this.busy) return;
    this.busy = true;
    try {
      this.brief = await runBrief();
      this.error = "";
    } catch (cause) {
      this.error = (cause as Error).message;
    } finally {
      this.busy = false;
    }
  }

  /** Ctrl K "Morning brief": opens the bar's popover, sweeping first when today has none. */
  async show() {
    if (!this.brief) await this.run();
    if (this.brief) this.open = true;
  }
}

export const morningBrief = new BriefState();

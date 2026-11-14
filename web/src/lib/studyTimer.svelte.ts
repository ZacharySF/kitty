import { api } from "./api";

export type Timer = {
  revision: number;
  status: "idle" | "running" | "paused";
  phase: "priming" | "focus" | "short_break";
  phase_id: string | null;
  session_id: string | null;
  completed_at: string | null;
  completed_phase_id: string | null;
  completed_phase: "priming" | "focus" | "short_break" | null;
  deadline_at: string | null;
  remaining_seconds: number;
  duration_seconds?: number;
  cycle?: number;
  notes?: string;
};
export type TimerResult = {
  timer: Timer;
  server_now: string;
  settings?: { sound: boolean; notifications: boolean };
};

const CACHE = "kitty-study-focus-bar";

/**
 * The study (Pomodoro) timer as the server keeps it, read once for everything that shows
 * it: the top bar and home's Focus widget share this state, one 2 s poll and one 250 ms
 * clock, running only while something uses it (`use`).
 */
class StudyTimer {
  result = $state<TimerResult | null>(null);
  /** Counts answers from the server, so a watcher can tell a fresh one from the cache. */
  loads = $state(0);
  now = $state(Date.now());
  offset = $state(0);
  busy = $state(false);
  offline = $state(false);
  error = $state("");
  #loading = false;
  #users = 0;
  #release: (() => void) | null = null;

  remaining = $derived.by(() => {
    const timer = this.result?.timer;
    return timer?.status === "running" && timer.deadline_at
      ? Math.max(0, Math.ceil((Date.parse(timer.deadline_at) - this.now - this.offset) / 1000))
      : Math.ceil(timer?.remaining_seconds || 0);
  });
  clock = $derived(`${Math.floor(this.remaining / 60).toString().padStart(2, "0")}:${(this.remaining % 60).toString().padStart(2, "0")}`);
  phase = $derived(this.result?.timer.phase === "priming" ? "Priming" : this.result?.timer.phase === "focus" ? "Focus" : "Break");

  async load() {
    if (this.#loading) return;
    this.#loading = true;
    try {
      const result = await api<TimerResult>("/study/timer");
      this.result = result;
      this.offset = Date.parse(result.server_now) - Date.now();
      this.offline = false;
      this.loads += 1;
      try { localStorage.setItem(CACHE, JSON.stringify(result)); } catch { /* Server remains authoritative. */ }
    } catch (cause) {
      this.offline = true;
      this.error = (cause as Error).message;
    } finally {
      this.#loading = false;
    }
  }

  async act(action: string) {
    if (!this.result || this.busy || this.offline) return;
    if (action === "stop" && !confirm("Discard unfinished focus time? Completed sessions remain saved.")) return;
    // The click is the gesture that lets the top bar's sound play.
    window.dispatchEvent(new CustomEvent("kitty-study-unlock-sound"));
    this.busy = true;
    this.error = "";
    try {
      await api("/study/timer", { action, revision: this.result.timer.revision, request_id: crypto.randomUUID() });
      await this.load();
      window.dispatchEvent(new CustomEvent("kitty-study-changed"));
    } catch (cause) {
      this.error = (cause as Error).message;
      await this.load();
    } finally {
      this.busy = false;
    }
  }

  /** Keeps the timer fresh while the caller is on screen; returns the release. */
  use(): () => void {
    if (!this.#users++) this.#release = this.#begin();
    return () => {
      if (--this.#users) return;
      this.#release?.();
      this.#release = null;
    };
  }

  #begin() {
    try {
      const cached = localStorage.getItem(CACHE);
      if (cached && !this.result) {
        this.result = JSON.parse(cached);
        this.offline = true;
      }
    } catch { /* Ignore a malformed cache. */ }
    void this.load();
    const tick = window.setInterval(() => {
      this.now = Date.now();
      if (this.result?.timer.status === "running" && this.remaining === 0) void this.load();
    }, 250);
    const refresh = window.setInterval(() => { if (!this.busy) void this.load(); }, 2000);
    const wake = () => { if (document.visibilityState === "visible") void this.load(); };
    const changed = () => void this.load();
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", changed);
    window.addEventListener("online", changed);
    window.addEventListener("kitty-study-changed", changed);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(refresh);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", changed);
      window.removeEventListener("online", changed);
      window.removeEventListener("kitty-study-changed", changed);
    };
  }
}

export const studyTimer = new StudyTimer();

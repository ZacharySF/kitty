/**
 * A mental-math sprint run in the browser (design Q2). The backend deals the problems
 * from a seed; answers are checked here so the next problem appears at once.
 * Typed (Zetamac): a right answer is taken the instant it is typed, wrong ones cost
 * nothing, and the score is the number right. Multiple choice: +1 right, −1 wrong.
 */
export type SprintMode = "zetamac" | "mc";
export type SprintProblem = { q: string; a: number; choices?: number[] };
export type Sprint = {
  mode: SprintMode;
  duration_s: number;
  problems: SprintProblem[];
  index: number;
  right: number;
  wrong: number;
  started_at: number;
  finished_at: number | null;
};
export type SprintResult = { score: number; wrong: number; elapsed_ms: number };

export function startSprint(mode: SprintMode, duration_s: number, problems: SprintProblem[], now: number): Sprint {
  if (!problems.length) throw new Error("A sprint needs problems");
  return { mode, duration_s, problems, index: 0, right: 0, wrong: 0, started_at: now, finished_at: null };
}

/** The problem on screen; a fast run wraps around the dealt list. */
export function current(run: Sprint): SprintProblem {
  return run.problems[run.index % run.problems.length];
}

export function remainingSeconds(run: Sprint, now: number): number {
  return Math.max(0, run.duration_s - (now - run.started_at) / 1000);
}

const open = (run: Sprint, now: number) => run.finished_at === null && remainingSeconds(run, now) > 0;

/** Typed mode: true when `text` is the answer, which counts it and moves on. */
export function typed(run: Sprint, text: string, now: number): boolean {
  const value = text.trim().replace(/[−–]/g, "-");
  if (!open(run, now) || !/^-?\d+$/.test(value) || Number(value) !== current(run).a) return false;
  run.right += 1;
  run.index += 1;
  return true;
}

/** Multiple choice: grades `value` and moves on; null once the time is up. */
export function choose(run: Sprint, value: number, now: number): "right" | "wrong" | null {
  if (!open(run, now)) return null;
  const right = value === current(run).a;
  if (right) run.right += 1;
  else run.wrong += 1;
  run.index += 1;
  return right ? "right" : "wrong";
}

export function score(run: Sprint): number {
  return run.mode === "mc" ? run.right - run.wrong : run.right;
}

/** Ends the run (time up or stopped early) and returns what is recorded. */
export function finish(run: Sprint, now: number): SprintResult {
  run.finished_at ??= Math.min(now, run.started_at + run.duration_s * 1000);
  return { score: score(run), wrong: run.wrong, elapsed_ms: run.finished_at - run.started_at };
}

/** "1:42": whole seconds, rounded up so 0:00 means the time is really up. */
export function clock(seconds: number): string {
  const whole = Math.ceil(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function perMinute(right: number, duration_s: number): number {
  return duration_s > 0 ? (right * 60) / duration_s : 0;
}

/** The latest score against the run before it and the best so far. */
export function trend(scores: number[]): { best: number; last: number; change: number; newBest: boolean } {
  if (!scores.length) return { best: 0, last: 0, change: 0, newBest: false };
  const last = scores[scores.length - 1];
  const earlier = scores.slice(0, -1);
  return {
    best: Math.max(...scores),
    last,
    change: earlier.length ? last - earlier[earlier.length - 1] : 0,
    newBest: earlier.length > 0 && last > Math.max(...earlier),
  };
}

import type { SprintMode } from "./sprint";

/** Math settings, remembered per browser (the right drawer's Settings). */
export type Operation = "add" | "sub" | "mul" | "div";
export type MathSettings = {
  mode: SprintMode;
  duration_s: number;
  ops: Operation[];
  /** Start a Study focus cycle on the Math subject when a sprint or lesson starts. */
  focus: boolean;
};

/** Zetamac's lengths; 120 s is its default. */
export const DURATIONS = [30, 60, 120, 300, 600];
export const OPERATIONS: { op: Operation; sign: string; range: string }[] = [
  { op: "add", sign: "+", range: "(2–100) + (2–100)" },
  { op: "sub", sign: "−", range: "reversed +" },
  { op: "mul", sign: "×", range: "(2–12) × (2–100)" },
  { op: "div", sign: "÷", range: "reversed ×" },
];
export const DEFAULT_SETTINGS: MathSettings = { mode: "zetamac", duration_s: 120, ops: ["add", "sub", "mul", "div"], focus: false };
const KEY = "kitty.math.settings";

export function loadSettings(): MathSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
    const ops = Array.isArray(saved.ops) ? OPERATIONS.map((item) => item.op).filter((op) => saved.ops.includes(op)) : [];
    return {
      mode: saved.mode === "mc" ? "mc" : "zetamac",
      duration_s: DURATIONS.includes(saved.duration_s) ? saved.duration_s : DEFAULT_SETTINGS.duration_s,
      ops: ops.length ? ops : DEFAULT_SETTINGS.ops,
      focus: saved.focus === true,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(settings: MathSettings) {
  try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch { /* The settings still apply in this session. */ }
}

import { api } from "../api";

/**
 * Math inside a Study focus cycle (design Q5: practice can run inside a focus phase and
 * tag the session with a Math subject). The Study timer is the single source: Math only
 * reads it, and can start a cycle on the Math subject, creating that subject once.
 */
export type FocusTimer = {
  revision: number;
  status: "idle" | "running" | "paused";
  phase: "priming" | "focus" | "short_break";
  subject_id: number | null;
  deadline_at: string | null;
  remaining_seconds: number;
};
export type Subject = { id: number; name: string; color: string };
type TimerState = { timer: FocusTimer; server_now: string; settings: { timezone: string } };

export const MATH_SUBJECT = "Math";
const MATH_COLOR = "#5b8def";

export async function readFocus(): Promise<{ timer: FocusTimer; offset: number; subject: Subject | null }> {
  const state = await api<TimerState>("/study/timer");
  const offset = Date.parse(state.server_now) - Date.now();
  if (state.timer.status === "idle" || state.timer.subject_id === null) return { timer: state.timer, offset, subject: null };
  const { subjects } = await api<{ subjects: Subject[] }>("/study");
  return { timer: state.timer, offset, subject: subjects.find((item) => item.id === state.timer.subject_id) ?? null };
}

/** Starts a Study cycle (priming, then focus) tagged Math. */
export async function startMathFocus(): Promise<void> {
  const [state, { subjects }] = await Promise.all([api<TimerState>("/study/timer"), api<{ subjects: Subject[] }>("/study")]);
  if (state.timer.status !== "idle") return;
  const subject = subjects.find((item) => item.name.toLowerCase() === MATH_SUBJECT.toLowerCase())
    ?? (await api<{ item: Subject }>("/study/subjects", { action: "save", name: MATH_SUBJECT, color: MATH_COLOR })).item;
  window.dispatchEvent(new CustomEvent("kitty-study-unlock-sound"));
  await api("/study/timer", {
    action: "start",
    revision: state.timer.revision,
    request_id: crypto.randomUUID(),
    subject_id: subject.id,
    timezone: state.settings.timezone,
  });
  window.dispatchEvent(new CustomEvent("kitty-study-changed"));
}

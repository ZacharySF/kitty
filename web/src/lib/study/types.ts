/** Shapes returned by `GET /api/study`, shared by the Study hub and its sections. */
export type Item = {
  id: number;
  revision: number;
  name?: string;
  title?: string;
  color?: string;
  subject_id?: number | null;
  project_id?: number | null;
  period?: string;
  minutes?: number;
  priority?: string;
  due_date?: string | null;
  done?: boolean;
  checklist?: { id: string; text: string; done: boolean }[];
  exam_date?: string;
  timezone?: string;
  notes?: string;
  exam_id?: number | null;
  start?: string;
  end?: string;
};
export type Session = {
  id: number;
  revision: number;
  source: string;
  subject_id: number | null;
  project_id: number | null;
  target_id: number | null;
  started_at: string;
  ended_at: string;
  timezone: string;
  duration_seconds: number;
  notes: string;
  distractions: string;
  period_seconds: number;
};
export type Phase = "priming" | "focus" | "short_break";
export type Timer = {
  revision: number;
  status: "idle" | "running" | "paused";
  phase: Phase;
  phase_id: string | null;
  session_id: string | null;
  remaining_seconds: number;
  duration_seconds: number;
  deadline_at: string | null;
  subject_id: number | null;
  project_id: number | null;
  target_id: number | null;
  timezone: string;
  notes: string;
  distractions: string;
  cycle: number;
};
export type Settings = {
  revision: number;
  timezone: string;
  focus_seconds: number;
  short_break_seconds: number;
  sound: boolean;
  notifications: boolean;
};
/** One local day of completed study, split by subject (largest first). */
export type StudyDay = { date: string; seconds: number; subjects: { id: number | null; seconds: number }[] };
export type SubjectTotal = { id: number | null; name: string; color: string; seconds: number };
export type Stats = {
  timezone: string;
  today: string;
  week_start: string;
  today_seconds: number;
  week_seconds: number;
  total_seconds: number;
  month_seconds: number;
  daily_streak: number;
  weekly_streak: number;
  session_count: number;
  days: StudyDay[];
  subjects: SubjectTotal[];
  goals: { id: number; seconds: number; goal_seconds: number }[];
  history: {
    period: "week" | "month";
    start: string;
    end: string;
    previous: string;
    next: string;
    seconds: number;
    session_count: number;
    days: StudyDay[];
    subjects: SubjectTotal[];
  };
};
export type GuideState = { checked: boolean[]; responses: string[]; reflection: string; next_review: string };
/** `subject_id` is the session's subject; `return_step` is the spaced return's rung on the ladder. */
export type Guide = {
  id: string; revision: number; created_at: string; ended_at: string | null;
  subject_id: number | null; return_step: number; state: GuideState;
};
export type Workspace = {
  timer: Timer;
  settings: Settings;
  server_now: string;
  stats: Stats;
  subjects: Item[];
  projects: Item[];
  targets: Item[];
  tasks: Item[];
  exams: Item[];
  plans: Item[];
  sessions: Session[];
  guide: Guide | null;
  guides: Guide[];
};
export type Collection = "subjects" | "projects" | "targets" | "tasks" | "exams";

export const minutesLabel = (seconds: number) => `${Math.round(seconds / 60)} min`;
export const phaseLabel = (phase: string) => phase === "priming" ? "Priming" : phase === "focus" ? "Focus" : "Break";
/** Fallback tint for unassigned study, matching the backend's default subject colour. */
export const UNASSIGNED_COLOR = "var(--muted)";

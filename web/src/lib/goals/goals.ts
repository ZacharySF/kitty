/**
 * Goals (docs/rika-design.md Q14 A) and the morning brief (Q15 A): the wire shapes served
 * by kitty.web_goals and the pure helpers the Goals view, the home widget and the Rika
 * bar's brief chip share.
 */
export type Id = number | string;
export type GoalStatus = "active" | "paused" | "done" | "dropped";
export type MeasureAuto = null | "applications" | "study_minutes" | "tasks_done" | "math_mastered";
export type Measure = { id: string; label: string; target: number; unit: string; current: number; auto: MeasureAuto; computed: boolean };
export type LinkKind = "note" | "block" | "task" | "job" | "study_session" | "math_topic" | "opportunity" | "event" | "mail";
export type GoalLink = { kind: LinkKind; ref: string; title: string; detail: string; view: string | null; missing: boolean };
export type MatchKind = "job" | "opportunity" | "mail" | "event";
export type Match = {
  id: Id;
  goal_id: Id;
  goal_title: string;
  kind: MatchKind;
  ref: string;
  title: string;
  detail: string;
  score: number;
  urgent: boolean;
  due_at: string | null;
  classified_by: "keywords" | "model";
  status: "new" | "linked" | "dismissed";
  view: string | null;
  created_at: string;
};
export type Goal = {
  id: Id;
  title: string;
  why: string;
  target_date: string | null;
  status: GoalStatus;
  measures: Measure[];
  progress: number;
  days_left: number | null;
  links_count: number;
  suggestions_count: number;
  revision: number;
  created_at: string;
  updated_at: string;
  /** Detail only. */
  links?: GoalLink[];
  suggestions?: Match[];
};
export type Linkable = { kind: LinkKind; ref: string; title: string; detail: string };
export type Brief = {
  day: string;
  created_at: string;
  seen: boolean;
  items: Match[];
  mail: { drafted: number; scheduled: number; sent: number };
  notes: string[];
  message_id: string | null;
};

export const STATUSES: { value: GoalStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "done", label: "Done" },
  { value: "dropped", label: "Dropped" },
];
export const statusLabel = (status: GoalStatus) => STATUSES.find((item) => item.value === status)?.label ?? status;

export const KIND_LABEL: Record<LinkKind, string> = {
  note: "Note",
  block: "Block",
  task: "Task",
  job: "Job",
  study_session: "Study",
  math_topic: "Math",
  opportunity: "Opportunity",
  event: "Calendar",
  mail: "Mail",
};
export const KIND_TONE: Record<LinkKind, string> = {
  note: "var(--c3)",
  block: "var(--c3)",
  task: "var(--c1)",
  job: "var(--c4)",
  study_session: "var(--c3)",
  math_topic: "var(--c3)",
  opportunity: "var(--c5)",
  event: "var(--c1)",
  mail: "var(--c2)",
};
/** What an auto measure counts (Measure.auto); a manual measure is "". */
export const AUTO_CHOICES: { value: Exclude<MeasureAuto, null>; label: string; unit: string }[] = [
  { value: "applications", label: "Applications sent", unit: "" },
  { value: "study_minutes", label: "Study minutes", unit: "min" },
  { value: "tasks_done", label: "Tasks done", unit: "" },
  { value: "math_mastered", label: "Math topics mastered", unit: "" },
];
export const autoLabel = (auto: MeasureAuto) => AUTO_CHOICES.find((item) => item.value === auto)?.label ?? "";

const clamp = (value: number) => Math.max(0, Math.min(1, value));
export const measureFraction = (measure: Pick<Measure, "current" | "target">) =>
  measure.target > 0 && Number.isFinite(measure.current) ? clamp(measure.current / measure.target) : 0;
export const percent = (fraction: number) => Math.round(clamp(Number.isFinite(fraction) ? fraction : 0) * 100);

/** 18 → "18", 2.5 → "2.5", 1/3 → "0.3". */
export function amount(value: number): string {
  if (!Number.isFinite(value)) return "0";
  return String(Math.round(value * 10) / 10);
}

/** The target with its unit: "40", "60 min", "100%". */
export function measureTarget(measure: Pick<Measure, "target" | "unit">): string {
  const unit = measure.unit.trim();
  return `${amount(measure.target)}${!unit ? "" : unit === "%" ? "%" : ` ${unit}`}`;
}

/** "18 / 40", "33 / 60 min", "64 / 100%". */
export const measureText = (measure: Pick<Measure, "current" | "target" | "unit">) => `${amount(measure.current)} / ${measureTarget(measure)}`;

/** "133 days left", "due today", "2 days overdue"; "" without a target date. */
export function daysLeftLabel(days: number | null): string {
  if (days === null || !Number.isFinite(days)) return "";
  if (days === 0) return "due today";
  const count = Math.abs(days);
  return `${count} day${count === 1 ? "" : "s"} ${days > 0 ? "left" : "overdue"}`;
}

/** "Mar 15, 2027" for a local YYYY-MM-DD day. */
export function dayLabel(day: string, locale?: string): string {
  const date = new Date(`${day}T00:00`);
  return Number.isNaN(date.getTime()) ? day : date.toLocaleDateString(locale, { month: "short", day: "numeric", year: "numeric" });
}

/** The target chip: "by Mar 15, 2027 · 133 days left"; "" without a target date. */
export function targetLabel(goal: Pick<Goal, "target_date" | "days_left">, locale?: string): string {
  if (!goal.target_date) return "";
  const left = daysLeftLabel(goal.days_left);
  return `by ${dayLabel(goal.target_date, locale)}${left ? ` · ${left}` : ""}`;
}

const localDay = (value: string) => {
  const day = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00`) : new Date(value);
  day.setHours(0, 0, 0, 0);
  return day.getTime();
};

/**
 * An active goal with a target date and measures is on track while its progress keeps up
 * with the time gone since it was set (a little slack); otherwise behind. Null when there
 * is nothing to compare.
 */
export function pace(goal: Pick<Goal, "status" | "target_date" | "created_at" | "progress" | "measures">, now = new Date()): "on track" | "behind" | null {
  if (goal.status !== "active" || !goal.target_date || !goal.measures.length) return null;
  const start = localDay(goal.created_at);
  const end = localDay(goal.target_date);
  const today = localDay(now.toISOString());
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  const elapsed = end > start ? clamp((today - start) / (end - start)) : today >= end ? 1 : 0;
  return goal.progress + 0.05 >= elapsed || elapsed === 0 ? "on track" : "behind";
}

/** "91% fit" when a model classified the match; a keyword match's score is no fit, so it says so. */
export const fitLabel = (match: Pick<Match, "score" | "classified_by">) =>
  match.classified_by === "model" ? `${percent(match.score)}% fit` : "keyword match";

/** A match's second line: "posted 2 h ago · 91% fit · Summer internship". */
export function matchLine(match: Pick<Match, "detail" | "score" | "classified_by" | "goal_title">, withGoal = true): string {
  return [match.detail, fitLabel(match), withGoal ? match.goal_title : ""].filter(Boolean).join(" · ");
}

/** "2 replies drafted · 1 scheduled"; "" when the brief touched no mail. */
export function mailLine(mail: Brief["mail"] | null | undefined): string {
  if (!mail) return "";
  return [
    mail.drafted ? `${mail.drafted} ${mail.drafted === 1 ? "reply" : "replies"} drafted` : "",
    mail.scheduled ? `${mail.scheduled} scheduled` : "",
    mail.sent ? `${mail.sent} sent` : "",
  ].filter(Boolean).join(" · ");
}

export const linkKey = (item: { kind: string; ref: string }) => `${item.kind}:${item.ref}`;

/** `#goals/12` names goal 12; anything else names none. */
export function goalFromHash(hash: string): string | null {
  const match = /^#goals\/([^/?#]+)/.exec(hash);
  return match ? decodeURIComponent(match[1]) : null;
}
/** The deep link that opens one goal in the Goals view. */
export const goalHash = (id: Id) => `#goals/${encodeURIComponent(String(id))}`;

/** One row of the measures editor; numbers stay text while they are typed. */
export type MeasureDraft = { id?: string; label: string; target: string; unit: string; current: string; auto: MeasureAuto };
export type MeasureInput = { id?: string; label: string; target: number; unit: string; current?: number; auto: MeasureAuto };

export const emptyMeasure = (): MeasureDraft => ({ label: "", target: "", unit: "", current: "0", auto: null });
export const measureDraft = (measure: Measure): MeasureDraft => ({
  id: measure.id,
  label: measure.label,
  target: amount(measure.target),
  unit: measure.unit,
  current: amount(measure.current),
  auto: measure.auto,
});

/**
 * The measures a create or update sends, or the first problem. Blank rows are dropped;
 * an auto measure's current comes from data, so it is never sent.
 */
export function measureInputs(drafts: MeasureDraft[]): { measures: MeasureInput[]; error: string } {
  const measures: MeasureInput[] = [];
  for (const draft of drafts) {
    const label = draft.label.trim();
    if (!label && !draft.target.trim()) continue;
    if (!label) return { measures, error: "Every measure needs a name." };
    const target = Number(draft.target);
    if (!draft.target.trim() || !Number.isFinite(target) || target <= 0) return { measures, error: `“${label}” needs a target above 0.` };
    const current = Number(draft.current || "0");
    if (!draft.auto && (!Number.isFinite(current) || current < 0)) return { measures, error: `“${label}” needs a current amount of 0 or more.` };
    measures.push({
      ...(draft.id ? { id: draft.id } : {}),
      label,
      target,
      unit: draft.unit.trim(),
      ...(draft.auto ? {} : { current }),
      auto: draft.auto,
    });
  }
  return { measures, error: "" };
}

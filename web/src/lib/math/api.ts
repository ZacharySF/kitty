import type { Topic, TopicState } from "./graph";
import type { SprintMode, SprintProblem } from "./sprint";

/** Shapes of `/api/math/*` (see docs/GUIDE.md, Math). Identifiers are opaque. */
export type Id = string | number;
export type AnswerKind = "number" | "fraction" | "choice" | "expression";
export type Problem = {
  problem_id: Id;
  prompt_md: string;
  answer_kind: AnswerKind;
  choices?: string[];
  tolerance?: number;
  purpose?: "lesson" | "review";
  variables?: string[];
};
export type Verdict = {
  /** null: the checker could not decide (or read the answer); nothing was marked. */
  correct: boolean | null;
  kind?: "equal" | "mistake" | "different" | "undecided";
  feedback?: string;
  canonical?: string;
  revealed?: boolean;
  mastery?: { state: TopicState; progress?: number; due_at?: string | null };
};
export type Lesson = {
  topic_id: string;
  title: string;
  example: { prompt_md: string; steps: string[] };
  /** One instance, step by step: item k blanks step `blank_step` (the `[[blank]]` token); later steps are left out. */
  guided: { problem_id: Id; prompt_md: string; steps: string[]; blank_step: number }[];
  solo_count: number;
  stop_after_misses: number;
};
export type Next = { kind: "lesson" | "review"; topic_id: string; title: string; why: string };
export type SprintRecord = { id: Id; mode: SprintMode; duration_s: number; score: number; wrong: number; elapsed_ms: number; finished_at: string };
export type SprintHistory = { sprints: SprintRecord[]; streak_days: number; best: Record<SprintMode, number> };
export type SprintDeal = { sprint_id: Id; problems: SprintProblem[] };
export type { Topic };

/** The blank in a guided step. */
export const BLANK = "[[blank]]";

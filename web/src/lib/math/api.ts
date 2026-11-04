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
/** An FSRS card: a textbook exercise's or an own-book entry's schedule. Ratings are 1 Again … 4 Easy. */
export type Card = { due_at: string; due: boolean; reviews: number; lapses: number; last_rating: Rating };
export type Rating = 1 | 2 | 3 | 4;
/** Grinstead & Snell's bundled edition (GNU FDL). */
export type TextbookSource = { title: string; short: string; version: string; licence: string; url: string };
export type TextbookSummary = {
  id: string;
  label: string;
  section: string;
  section_title: string;
  topics: string[];
  /** "number": rika! authored a checked answer; "self": you check your own working and rate it. */
  graded: "number" | "self";
  program: boolean;
  figure: boolean;
  starred: boolean;
  card: Card | null;
};
export type TextbookExercise = TextbookSummary & { text_md: string; part: string | null; source: TextbookSource };
export type TextbookList = { source: TextbookSource; exercises: TextbookSummary[] };
export type TextbookProblem = { problem_id: Id; exercise_id: string; prompt_md: string; answer_kind: "number" | "fraction"; sigfigs?: number; tolerance?: number };
export type TextbookVerdict = Omit<Verdict, "mastery"> & { tries_left?: number; card: Card };
/** An own-book problem: a reference (book, section, number) and your answer, never the book's text. */
export type BookEntry = {
  id: Id;
  book: string;
  section: string;
  problem: string;
  answer: string;
  confidence: Rating;
  topic_id: string | null;
  topic_title: string | null;
  created_at: string;
  updated_at: string;
  card: Card;
};
export type BookList = { problems: BookEntry[]; books: string[]; due: number };
export type MixedItem =
  | { kind: "topic"; topic_id: string; title: string; strategy: string; due_at: string; early: boolean; why: string }
  | { kind: "textbook"; exercise_id: string; label: string; topic_id: string; title: string; strategy: string; due_at: string; graded: "number" | "self" }
  | { kind: "book"; entry_id: Id; label: string; topic_id: string | null; strategy: string; due_at: string };
export type Mixed = { default: boolean; mastered: number; needed: number; why: string; held_back: number; items: MixedItem[] };
export type { Topic };

/** The blank in a guided step. */
export const BLANK = "[[blank]]";

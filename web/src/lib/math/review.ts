import type { BookEntry, Mixed, MixedItem, Rating } from "./api";

/**
 * Spaced review beyond generated problems (design steps 7 and 9): Grinstead & Snell
 * exercises, your own books' problems, and the mixed review that interleaves them with
 * due topics so no two problems in a row share a solution method.
 */
export const RATINGS: { n: Rating; label: string; hint: string }[] = [
  { n: 1, label: "Again", hint: "couldn't do it" },
  { n: 2, label: "Hard", hint: "got there with effort" },
  { n: 3, label: "Good", hint: "solid" },
  { n: 4, label: "Easy", hint: "effortless" },
];
/** An own-book entry's first rating. */
export const CONFIDENCE: { n: Rating; label: string }[] = [
  { n: 1, label: "Couldn't do it" },
  { n: 2, label: "Shaky" },
  { n: 3, label: "Confident" },
  { n: 4, label: "Certain" },
];
/** The backend's own-book limits. */
const LIMITS = { book: 120, section: 60, problem: 40, answer: 200 };
const CONTROL = /[\u0000-\u001f\u007f]/;

export type PracticeOrder = "mixed" | "next";
export const PRACTICE_KEY = "kitty.math.practice";

export function dueIn(at: string, now: number): string {
  const days = Math.ceil((Date.parse(at) - now) / 86_400_000);
  return days <= 0 ? "due now" : days === 1 ? "in 1 day" : `in ${days} days`;
}

/** Mixed review is open once enough topics are mastered; then an explicit choice wins over the backend's default. */
export function practiceOrder(stored: string | null, mixed: Pick<Mixed, "default" | "mastered" | "needed"> | null): PracticeOrder {
  if (!mixed || mixed.mastered < mixed.needed) return "next";
  if (stored === "mixed" || stored === "next") return stored;
  return mixed.default ? "mixed" : "next";
}

export function mixedLocked(mixed: Pick<Mixed, "mastered" | "needed">): string {
  return `Mixed review opens once ${mixed.needed} topics are mastered (${mixed.mastered} so far)`;
}

const KIND = { textbook: "Textbook", book: "Own book" } as const;
/** "Review · restrict sample space": what the item is and how it is solved. */
export function itemEyebrow(item: Pick<MixedItem, "kind" | "strategy"> & { early?: boolean }): string {
  const kind = item.kind === "topic" ? (item.early ? "Early review" : "Review") : KIND[item.kind];
  return `${kind} · ${item.strategy.replace(/-/g, " ")}`;
}

export function entryLabel(entry: Pick<BookEntry, "book" | "section" | "problem">): string {
  return [entry.book, entry.section, `#${entry.problem}`].filter(Boolean).join(" · ");
}

/** The first thing the backend would reject in an own-book entry, or "". */
export function entryProblem(fields: Pick<BookEntry, "book" | "section" | "problem" | "answer">): string {
  const [book, section, problem, answer] = [fields.book, fields.section, fields.problem, fields.answer].map((value) => value.trim());
  if (!book) return "Name the book.";
  if (!problem) return "Give the problem's number.";
  if ([book, section, problem, answer].some((value) => CONTROL.test(value))) return "No line breaks or control characters.";
  if (book.length > LIMITS.book) return `The book's name is at most ${LIMITS.book} characters.`;
  if (section.length > LIMITS.section) return `The section is at most ${LIMITS.section} characters.`;
  if (problem.length > LIMITS.problem) return `The problem's number is at most ${LIMITS.problem} characters.`;
  if (answer.length > LIMITS.answer) return `Your answer is at most ${LIMITS.answer} characters: it's a reference, not the working.`;
  return "";
}

/** Books you have logged before that match what is typed, most used first. */
export function bookSuggestions(books: string[], typed: string, limit = 4): string[] {
  const needle = typed.trim().toLowerCase();
  return books.filter((book) => book.toLowerCase() !== needle && book.toLowerCase().includes(needle)).slice(0, limit);
}

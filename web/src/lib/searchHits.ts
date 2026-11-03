/**
 * `GET /api/search` (the full-text index, kitty.core.search): its hits, what each one is
 * called in the palette, and where opening it goes. Pure, so node tests cover it.
 */
import type { View } from "./workspace";

/** `[text, is_hit]` runs: the server marks the matched words; the UI wraps them in `<mark>`. */
export type Segment = [string, boolean];
export type SearchHit = {
  kind: string;
  id: number | string;
  title: string;
  title_marks: Segment[];
  snippet: Segment[];
  view: View;
  match: "word" | "substring";
};
export type SearchResult = { query: string; hits: SearchHit[]; total: number; truncated: boolean };

const KINDS: Record<string, { label: string; icon: string }> = {
  note_page: { label: "Note", icon: "notes" },
  document: { label: "Document", icon: "notes" },
  quick_note: { label: "Quick note", icon: "quick-notes" },
  tag: { label: "Tag", icon: "favorites" },
  study_subject: { label: "Subject", icon: "study" },
  study_project: { label: "Project", icon: "study" },
  study_task: { label: "Study task", icon: "study" },
  calendar_event: { label: "Event", icon: "calendar" },
  calendar_task: { label: "Task", icon: "calendar" },
  flashcard_deck: { label: "Deck", icon: "flashcards" },
  flashcard_note: { label: "Card", icon: "flashcards" },
  job: { label: "Job", icon: "jobs" },
};

export function searchPath(text: string, limit: number): string {
  return `/search?q=${encodeURIComponent(text)}&limit=${limit}`;
}

export function hitLabel(hit: SearchHit): string {
  return KINDS[hit.kind]?.label ?? "Result";
}

export function hitIcon(hit: SearchHit): string {
  return KINDS[hit.kind]?.icon ?? "notes";
}

/** The plain text of marked runs (what the option is announced as). */
export function plain(segments: Segment[]): string {
  return segments.map(([text]) => text).join("");
}

/** Snippet runs with whitespace collapsed, for one line under the title. */
export function oneLine(segments: Segment[]): Segment[] {
  return segments.map(([text, hit]) => [text.replace(/\s+/g, " "), hit] as Segment).filter(([text]) => text.length > 0);
}

/**
 * Where a hit opens: `job` for the job drawer, `hash` for a deep link (a note page, a tag's
 * settings page), `view` otherwise; `storage` is a session key the target view reads on
 * mount (a flashcard note).
 */
export function hitTarget(hit: SearchHit):
  | { job: number }
  | { hash: string }
  | { view: View; storage?: [string, string] } {
  if (hit.kind === "job") return { job: Number(hit.id) };
  if (hit.kind === "note_page") return { hash: `#notes/${hit.id}` };
  if (hit.kind === "tag") return { hash: "#settings/tags" };
  if (hit.kind === "flashcard_note") return { view: "flashcards", storage: ["kitty.flashcards.open-note", String(hit.id)] };
  return { view: hit.view };
}

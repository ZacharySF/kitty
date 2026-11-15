import type { HomeWidgetId } from "./layout";

/** Each home widget's icon (icons.ts), for the lists that name widgets. */
export const WIDGET_ICONS: Record<HomeWidgetId, string> = {
  agenda: "today", note: "quick-notes", focus: "timer", next: "next", agents: "terminal", session: "pulse",
  goals: "goals", week: "week", cards: "flashcards", pipeline: "applications", clock: "clock",
};

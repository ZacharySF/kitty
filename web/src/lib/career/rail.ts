import type { View } from "../workspace";

/** `GET /api/career/summary`: each count is the size of the list its rail link opens. */
export type CareerCounts = {
  new: number;
  queued: number;
  saved: number;
  active: number;
  interviews: number;
  reminders: number;
  emails: number;
};
export type RailCount = keyof CareerCounts;
/**
 * One place on the Career rail. `count` names its live count; `urgent` counts ask for you
 * (new jobs, interviews, due reminders, waiting emails): an accent pill, and a numbered dot
 * on the collapsed strip, where the others show a plain dot. `stage` asks Track to open on
 * that column (careerFocus).
 */
export type RailPlace = { key: string; view: View; label: string; icon: string; count?: RailCount; urgent?: boolean; stage?: string };

/** The rail's groups, top to bottom (Q12 C). */
export const RAIL_GROUPS: { label: string; places: RailPlace[] }[] = [
  { label: "", places: [{ key: "overview", view: "career", label: "Overview", icon: "goals" }] },
  {
    label: "Find",
    places: [
      { key: "new", view: "inbox", label: "New jobs", icon: "inbox", count: "new", urgent: true },
      { key: "all", view: "jobs", label: "All jobs", icon: "jobs" },
      { key: "queue", view: "queue", label: "Queue", icon: "queue", count: "queued" },
      { key: "saved", view: "favorites", label: "Saved", icon: "favorites", count: "saved" },
    ],
  },
  {
    label: "Track",
    places: [
      { key: "applied", view: "pipeline", label: "Applied", icon: "applications", count: "active" },
      { key: "interviews", view: "pipeline", label: "Interviews", icon: "calendar", count: "interviews", urgent: true, stage: "interview" },
      { key: "reminders", view: "reminders", label: "Reminders", icon: "clock", count: "reminders", urgent: true },
    ],
  },
  {
    label: "You",
    places: [
      { key: "resumes", view: "workshop", label: "Resumes", icon: "resumes" },
      { key: "profile", view: "profile", label: "Profile", icon: "settings" },
    ],
  },
];
/** The quieter links under the rail: the rest of Career, one press away. */
export const RAIL_MORE: RailPlace[] = [
  { key: "mail", view: "mail", label: "Email updates", icon: "mail", count: "emails", urgent: true },
  { key: "applications", view: "applications", label: "Application list", icon: "applications" },
  { key: "library", view: "resumes", label: "Resume files", icon: "resumes" },
  { key: "sources", view: "sources", label: "Job sources", icon: "sources" },
  { key: "finder", view: "finder", label: "Find companies", icon: "jobs" },
  { key: "triage", view: "triage", label: "Rank jobs", icon: "sources" },
  { key: "reports", view: "reports", label: "Reports", icon: "reports" },
  { key: "questions", view: "questions", label: "Questions", icon: "questions" },
];

const PLACES = [...RAIL_GROUPS.flatMap((group) => group.places), ...RAIL_MORE];
/** Every view the rail stands beside: Career's built-in pages. */
export const RAIL_VIEWS: ReadonlySet<string> = new Set(PLACES.map((place) => place.view));

/** The place a view marks as current: the first place that opens it (Track is Applied, not Interviews). */
export function currentPlace(view: string): string | null {
  return PLACES.find((place) => place.view === view)?.key ?? null;
}

/** How a place's count reads: nothing without one, else the number and whether it asks for you. */
export function placeCount(place: RailPlace, counts: CareerCounts | null): { value: number; urgent: boolean } | null {
  if (!place.count || !counts) return null;
  const value = counts[place.count];
  return { value, urgent: Boolean(place.urgent) && value > 0 };
}

/** The collapsed strip's mark: a numbered dot for an urgent count, a plain dot for any other, none at zero. */
export function stripDot(place: RailPlace, counts: CareerCounts | null): "number" | "dot" | null {
  const count = placeCount(place, counts);
  if (!count || count.value === 0) return null;
  return count.urgent ? "number" : "dot";
}

/** A count as the rail prints it: 1,284 and, past 999 on the strip, 999+. */
export function countLabel(value: number, short = false): string {
  return short && value > 999 ? "999+" : value.toLocaleString("en-US");
}

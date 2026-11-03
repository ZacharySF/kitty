import type { ModuleId } from "./api";

/** `module` is the owner; core views (null) are always available. */
export const navigation = [
  { id: "today", label: "Atlas", icon: "atlas", module: null },
  { id: "calendar", label: "Calendar", icon: "calendar", module: "workspace" },
  { id: "opportunities", label: "Opportunities & projects", icon: "opportunities", module: "workspace" },
  { id: "herdr", label: "herdr", icon: "terminal", module: "terminal" },
  { id: "tern", label: "Tern", icon: "terminal", module: "terminal" },
  { id: "study", label: "Study", icon: "study", module: "workspace" },
  { id: "math", label: "Math", icon: "math", module: "workspace" },
  { id: "learnharness", label: "LearnHarness", icon: "study", module: "workspace" },
  { id: "flashcards", label: "Flashcards", icon: "flashcards", module: "workspace" },
  { id: "notes", label: "Notes & canvas", icon: "notes", module: "workspace" },
  { id: "quick-notes", label: "Quick notes", icon: "quick-notes", module: "workspace" },
  { id: "documents", label: "Documents & PDF", icon: "notes", module: "workspace" },
  { id: "schema", label: "Mind maps", icon: "schema", module: "workspace" },
  { id: "ai", label: "AI settings", icon: "ai", module: null },
  { id: "inbox", label: "New jobs", icon: "inbox", module: "applyai" },
  { id: "queue", label: "Prepare applications", icon: "queue", module: "applyai" },
  { id: "jobs", label: "Find jobs", icon: "jobs", module: "applyai" },
  { id: "pipeline", label: "My applications", icon: "applications", module: "applyai" },
  { id: "applications", label: "Application list", icon: "applications", module: "applyai" },
  { id: "favorites", label: "Saved jobs", icon: "favorites", module: "applyai" },
  { id: "reminders", label: "Reminders", icon: "today", module: "applyai" },
  { id: "sources", label: "Job sources", icon: "sources", module: "applyai" },
  { id: "resumes", label: "My resumes", icon: "resumes", module: "applyai" },
  { id: "settings", label: "Settings", icon: "settings", module: null },
  { id: "profile", label: "My profile", icon: "settings", module: "applyai" },
  { id: "finder", label: "Find companies", icon: "jobs", module: "applyai" },
  { id: "reports", label: "Reports & exports", icon: "reports", module: "applyai" },
  { id: "mail", label: "Email updates", icon: "mail", module: "applyai" },
  { id: "triage", label: "Rank jobs", icon: "sources", module: "applyai" },
  { id: "questions", label: "Application questions", icon: "questions", module: "applyai" },
] as const satisfies readonly { id: string; label: string; icon: string; module: ModuleId | null }[];
export type View = (typeof navigation)[number]["id"];

type Enabled = readonly string[];
export function moduleOf(view: View): ModuleId | null {
  return navigation.find(item => item.id === view)?.module ?? null;
}
/** True when the view is core or its owning module is active in this process. */
export function viewEnabled(view: View, enabled: Enabled): boolean {
  const owner = moduleOf(view);
  return owner === null || enabled.includes(owner);
}
export function isView(value: string): value is View {
  return navigation.some(item => item.id === value);
}
export function visibleNavigation(enabled: Enabled) {
  return navigation.filter(item => viewEnabled(item.id, enabled));
}

/** `tone` is the group's graph colour, used to mark its tabs. */
export const navigationGroups: { label: string; shortcut: string; tone: string; views: View[] }[] = [
  {
    label: "Plan",
    shortcut: "Ctrl + Alt",
    tone: "var(--c1)",
    views: ["today", "calendar", "opportunities", "reminders", "herdr", "tern"],
  },
  { label: "Study", shortcut: "Ctrl + X", tone: "var(--c3)", views: ["study", "math", "learnharness", "flashcards", "notes", "quick-notes", "schema", "documents"] },
  {
    label: "Job search",
    shortcut: "Ctrl + C",
    tone: "var(--c4)",
    views: [
      "jobs",
      "inbox",
      "favorites",
      "queue",
      "pipeline",
      "applications",
      "resumes",
      "sources",
      "finder",
      "mail",
      "triage",
      "questions",
      "reports",
    ],
  },
  { label: "Settings", shortcut: "Ctrl + V", tone: "var(--c6)", views: ["settings", "profile", "ai"] },
];
/**
 * Groups with their enabled views. A group that would only offer the atlas home
 * is dropped: with every module off the header shows just Settings.
 */
export function visibleGroups(enabled: Enabled) {
  return navigationGroups
    .map(group => ({ ...group, views: group.views.filter(view => viewEnabled(view, enabled)) }))
    .filter(group => group.views.some(view => view !== "today"));
}
/**
 * Related sections, drawn as edges between section hubs on the atlas home.
 * Single source of truth for "what connects to what"; pairs are undirected.
 */
export const navigationRelations: [View, View][] = [
  ["herdr", "tern"],
  ["calendar", "reminders"],
  ["calendar", "study"],
  ["opportunities", "calendar"],
  ["opportunities", "study"],
  ["study", "flashcards"],
  ["study", "learnharness"],
  ["study", "math"],
  ["math", "flashcards"],
  ["study", "notes"],
  ["study", "schema"],
  ["flashcards", "notes"],
  ["notes", "schema"],
  ["notes", "quick-notes"],
  ["notes", "documents"],
  ["schema", "jobs"],
  ["jobs", "inbox"],
  ["jobs", "favorites"],
  ["jobs", "pipeline"],
  ["jobs", "sources"],
  ["jobs", "triage"],
  ["favorites", "queue"],
  ["queue", "pipeline"],
  ["queue", "resumes"],
  ["queue", "questions"],
  ["pipeline", "resumes"],
  ["pipeline", "applications"],
  ["pipeline", "reminders"],
  ["pipeline", "mail"],
  ["applications", "reports"],
  ["sources", "finder"],
  ["resumes", "profile"],
  ["questions", "profile"],
  ["ai", "triage"],
  ["ai", "notes"],
  ["ai", "flashcards"],
];
type ShortcutGroup = { title: string; module?: ModuleId; shortcuts: string[][] };
const shortcutGroups: ShortcutGroup[] = [
  {
    title: "Navigate",
    shortcuts: [
      ["rika!", "Return to the main schema"],
      ["Ctrl + Alt / Ctrl + X / Ctrl + C / Ctrl + V", "Plan / Study / Job search / Settings (outside text editing)"],
      ["Ctrl / ⌘ + K", "Search commands, sections, and jobs"],
      [
        "Calendar / Study / Notes / Mind maps / AI",
        "Pick a node on the atlas, Sections on the stage, or Ctrl / ⌘ + K",
      ],
      ["?", "Show keyboard help"],
      ["F / Esc", "Toggle focused panel fullscreen / exit (outside text editing)"],
      ["Ctrl + Shift + F", "Toggle panel fullscreen inside terminal views"],
      ["Escape", "Close dialog or details; leave a text field"],
    ],
  },
  {
    title: "Atlas home",
    shortcuts: [
      ["/", "Open Sections and find a section"],
      ["Enter in section search", "Show the first match on the map; Enter again opens it"],
      ["]", "Hide or show the side widgets"],
      ["Drag / scroll", "Turn / zoom the Brain, or pan / zoom the 2D map"],
      ["Click a Brain group", "Open that group on its own; Escape returns to all"],
      ["Ctrl + ← / →", "Rotate the visible 3D schema left / right (outside editors and dialogs)"],
      ["Click a node", "Open that section, item, or job"],
    ],
  },
  {
    title: "Study",
    module: "workspace",
    shortcuts: [
      ["Space / p", "Start, pause, or resume the study timer"],
      ["s / r", "Stop / reset the study timer"],
      ["b", "Skip the current break"],
      ["[ / ]", "Open or close the subjects and guide drawers"],
    ],
  },
  {
    title: "Math",
    module: "workspace",
    shortcuts: [
      ["[ / ]", "Open or close the topic graph and history drawers"],
      ["Type the answer", "In a sprint it is taken the instant it is right, no Enter"],
      ["1–4", "Pick a choice in a multiple-choice sprint or problem"],
      ["Enter", "Check the answer; again for the next problem"],
      ["R", "Restart a finished sprint"],
      ["Ctrl / ⌘ + K", "Mental math sprint · Next math lesson, from any page"],
    ],
  },
  {
    title: "Flashcards",
    module: "workspace",
    shortcuts: [
      ["n", "Quick-add a card to the deck in view; Ctrl / ⌘ + K → New card does it from any page"],
      ["Tab / Enter in the add row", "Move to the back / save and start the next card"],
      ["Enter / Ctrl + Enter · Esc", "Save the quick-add card · close it"],
    ],
  },
  {
    title: "Browse & select",
    module: "applyai",
    shortcuts: [
      ["Ctrl / ⌘ + F", "Search jobs; company-only in Inbox"],
      ["/", "Search roles and companies"],
      ["j / ↓ / ↑", "Move between job rows"],
      ["Enter", "Open the focused job"],
      ["Space", "Mark or unmark a job"],
      ["Shift + click checkbox", "Select a range on this page"],
      ["0–9", "Choose a configured saved view"],
      ["c", "Focus the track filter"],
      ["v", "Toggle favorites filter"],
      ["w in New jobs", "Toggle 14-day / all-time window"],
      ["r", "Refresh the current view"],
    ],
  },
  {
    title: "Job decisions",
    module: "applyai",
    shortcuts: [
      ["Right-click", "Confirm deletion of a job or its selected group; submitted applications stay protected"],
      ["*", "Star selection; unstar if all are already starred"],
      ["g", "Add a track to marked or focused jobs"],
      ["t", "Choose a tier"],
      ["x", "Skip; in Prepare applications, mark not eligible"],
      ["a", "Add a posting; in Prepare applications, record submission"],
      ["o", "Open employer posting"],
      ["k", "Choose queue state"],
      ["s", "Choose application status; in Prepare applications, skip"],
      ["u", "Undo latest status transition"],
      ["d", "Set deadline"],
      ["n", "Set next action; in Prepare applications, next row"],
      ["p in Prepare applications", "Previous row"],
      ["l in Prepare applications", "Move to later"],
      ["R", "Choose resume"],
      ["e", "Edit notes"],
      ["q", "Log a screening question"],
    ],
  },
  {
    title: "Application preparation",
    module: "applyai",
    shortcuts: [
      ["Shift + F", "Review autofill for marked, opened or focused jobs"],
      ["w", "Stop browser worker; New jobs uses its time-window key"],
    ],
  },
  {
    title: "My applications",
    module: "applyai",
    shortcuts: [
      ["↑ ↓ ← →", "Move between application cards"],
      ["Enter / s / u / n / o", "Open / status / undo / next action / posting"],
      ["Shift + ← → / ↑ ↓", "Move the card to the next stage / track"],
      ["v / Shift + G", "Review mail proposals"],
      ["r", "Refresh pipeline"],
    ],
  },
  {
    title: "My resumes",
    module: "applyai",
    shortcuts: [
      ["t / Shift + T", "Next / previous track"],
      ["g", "Generate candidate"],
      ["v", "Review selected version"],
      ["o / Enter", "Open selected PDF"],
      ["p", "Review before promotion"],
      ["x", "Review before rejection"],
      ["b", "Rollback controls"],
    ],
  },
  {
    title: "Profile & sources",
    module: "applyai",
    shortcuts: [
      [
        "e / w / r / n in My profile",
        "Edit configuration / saved answer / refresh / resumes",
      ],
      ["j / k / ↑ / ↓ in Sources", "Choose source"],
      ["e / s / Enter in Sources", "Enable-disable / run selected source"],
      ["d / r in Sources", "Discover / refresh health"],
    ],
  },
  {
    title: "Find companies",
    module: "applyai",
    shortcuts: [
      ["j / k / ↑ / ↓", "Choose proposal"],
      ["a / Enter", "Review approval"],
      ["r / x", "Review rejection"],
      ["Shift + R", "Confirm finder run"],
      ["u", "Refresh proposals"],
    ],
  },
];
/** Pairs of related sections whose both ends are available. */
export function visibleRelations(enabled: Enabled): [View, View][] {
  return navigationRelations.filter(([a, b]) => viewEnabled(a, enabled) && viewEnabled(b, enabled));
}
const shortNames: Partial<Record<View, string>> = {
  calendar: "Calendar", study: "Study", notes: "Notes", schema: "Mind maps", ai: "AI",
};
/** Shortcut help for the active modules; navigation lines list only reachable targets. */
export function shortcutsFor(enabled: Enabled): ShortcutGroup[] {
  const groups = visibleGroups(enabled);
  const searches = enabled.includes("applyai") ? "commands, sections, and jobs" : "commands and sections";
  const targets = (Object.keys(shortNames) as View[]).filter(view => viewEnabled(view, enabled)).map(view => shortNames[view]);
  return shortcutGroups
    .filter(group => !group.module || enabled.includes(group.module))
    .map(group => {
      if (group.title !== "Navigate") return group;
      return {
        ...group,
        shortcuts: group.shortcuts.flatMap(row => {
          if (row[0].startsWith("Ctrl + Alt /"))
            return groups.length
              ? [[groups.map(item => item.shortcut).join(" / "), `${groups.map(item => item.label).join(" / ")} (outside text editing)`]]
              : [];
          if (row[0].startsWith("Ctrl / ⌘ + K")) return [[row[0], `Search ${searches}`]];
          if (row[0].startsWith("Calendar /"))
            return [[targets.join(" / "), row[1]]];
          return [row];
        }),
      };
    });
}

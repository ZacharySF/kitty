/**
 * Templates: Student, Job search, Builder and Minimal. Each names the modules it uses and
 * a home arrangement of home's own widgets. The first-run question's answers are the same
 * four (`use`), so an answer preselects its template's modules. Applying a template only
 * arranges home (`ui.home` in the build); the modules it lacks are offered one by one.
 * Pure, so Node tests can import it.
 */
import type { ModuleId } from "../api";
import { HOME_WIDGETS, type HomeLayout, type HomeWidgetId } from "./layout.ts";

export type TemplateId = "student" | "job-search" | "builder" | "minimal";
export interface Template {
  id: TemplateId;
  name: string;
  /** The answer to "What will you use rika! for?" that picks it. */
  use: string;
  modules: ModuleId[];
  home: { columns: HomeWidgetId[][]; hidden: HomeWidgetId[] };
}

export const TEMPLATES: Template[] = [
  { id: "student", name: "Student", use: "Studying", modules: ["workspace", "math"],
    home: { columns: [["next", "focus", "note"], ["agenda"]], hidden: ["agents", "session"] } },
  { id: "job-search", name: "Job search", use: "Finding a job", modules: ["applyai", "workspace"],
    home: { columns: [["agenda", "next"], ["note"]], hidden: ["agents", "focus", "session"] } },
  { id: "builder", name: "Builder", use: "Building with agents", modules: ["terminal", "workspace", "brain"],
    home: { columns: [["agents", "session", "note"], ["agenda", "next"]], hidden: ["focus"] } },
  { id: "minimal", name: "Minimal", use: "Notes and a calendar", modules: ["workspace"],
    home: { columns: [["agenda", "note"]], hidden: ["next", "agents", "focus", "session"] } },
];

/** The modules the pressed answers want, each once. */
export function wantedModules(answers: readonly string[]): ModuleId[] {
  return [...new Set(TEMPLATES.filter((template) => answers.includes(template.id)).flatMap((template) => template.modules))];
}

export type Signals = { enabled: readonly string[]; counts?: { jobs: number; applications: number } };

/**
 * The template Rika offers: the one whose modules best match what is on (each one on
 * counts for it, each one off against), job records tipping a tie toward Job search.
 */
export function suggestTemplate({ enabled, counts }: Signals): Template {
  const records = (counts?.jobs ?? 0) + (counts?.applications ?? 0);
  const score = (template: Template) =>
    template.modules.reduce((sum, id) => sum + (enabled.includes(id) ? 1 : -1), 0)
    + (template.id === "job-search" && enabled.includes("applyai") && records ? 1 : 0);
  return TEMPLATES.reduce((best, template) => (score(template) > score(best) ? template : best));
}

const list = (words: string[]) => (words.length < 3 ? words.join(" and ") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`);
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** Rika's one-line case for a template: what it saw, and what the layout leads with. */
export function suggestionLine(template: Template, { enabled, counts }: Signals): string {
  const first = template.home.columns[0].map((id) => HOME_WIDGETS.find((widget) => widget.id === id)!.title);
  const layout = `The ${template.name} layout puts ${list(first)} first.`;
  if (template.id === "job-search" && enabled.includes("applyai") && counts && counts.jobs + counts.applications)
    return `You have ${plural(counts.applications, "application")} and ${plural(counts.jobs, "job")}. ${layout}`;
  return layout;
}

/** The template's modules that are not in the saved selection, in its own order. */
export function missingModules(template: Template, configured: readonly string[]): ModuleId[] {
  return template.modules.filter((id) => !configured.includes(id));
}

/** "It also uses Math, which is off." */
export function missingLine(labels: string[]): string {
  return labels.length ? `It also uses ${list(labels)}, which ${labels.length === 1 ? "is" : "are"} off.` : "";
}

/** What applying writes to the build: the arrangement, and that the offer was made. */
export function arrangement(template: Template): { ui: { home: HomeLayout & { template_offered: true } } } {
  return { ui: { home: { columns: template.home.columns.map((column) => [...column]), hidden: [...template.home.hidden], template_offered: true } } };
}

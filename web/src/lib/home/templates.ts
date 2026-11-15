/**
 * Templates: a named home arrangement and the modules it uses. The first four are also the
 * answers to the first-run question (`use`), so an answer preselects its template's
 * modules; the rest are the library behind "Other templates". Applying a template only
 * arranges home (`ui.home` in the build); the modules it lacks are offered one by one.
 * Pure, so Node tests can import it.
 */
import type { ModuleId } from "../api";
import { HOME_WIDGETS, type HomeLayout, type HomeWidgetId } from "./layout.ts";

export type TemplateId =
  | "student" | "job-search" | "builder" | "minimal"
  | "exam-week" | "stem" | "memorise" | "interview" | "internship" | "agent-ops" | "founder"
  | "planner" | "deep-work" | "writer" | "researcher" | "goals-first" | "calm" | "everything";
export interface Template {
  id: TemplateId;
  name: string;
  /** The answer to "What will you use rika! for?" that picks it; none for a library-only template. */
  use?: string;
  modules: ModuleId[];
  home: { columns: HomeWidgetId[][]; hidden: HomeWidgetId[] };
}

/** Hidden is every widget that would otherwise join home by itself and is not placed. */
function template(id: TemplateId, name: string, modules: ModuleId[], columns: HomeWidgetId[][], use?: string): Template {
  const placed = new Set<string>(columns.flat());
  const hidden = HOME_WIDGETS.filter((widget) => !("library" in widget) && !placed.has(widget.id)).map((widget) => widget.id);
  return { id, name, ...(use ? { use } : {}), modules, home: { columns, hidden } };
}

export const TEMPLATES: Template[] = [
  template("student", "Student", ["workspace", "math"], [["next", "focus", "note"], ["agenda"]], "Studying"),
  template("job-search", "Job search", ["applyai", "workspace"], [["agenda", "next"], ["note"]], "Finding a job"),
  template("builder", "Builder", ["terminal", "workspace", "brain"], [["agents", "session", "note"], ["agenda", "next"]], "Building with agents"),
  template("minimal", "Minimal", ["workspace"], [["agenda", "note"]], "Notes and a calendar"),
  template("exam-week", "Exam week", ["workspace"], [["week", "cards", "focus"], ["next", "goals"]]),
  template("stem", "Math and science", ["workspace", "math"], [["next", "focus", "cards"], ["agenda", "goals"]]),
  template("memorise", "Memorising", ["workspace"], [["cards", "focus"], ["agenda", "note"]]),
  template("interview", "Interview prep", ["applyai", "workspace"], [["pipeline", "next"], ["agenda", "cards", "note"]]),
  template("internship", "Internship hunt", ["applyai", "workspace"], [["pipeline", "goals"], ["next", "week"]]),
  template("agent-ops", "Agent ops", ["terminal"], [["agents", "session"], ["goals", "clock"]]),
  template("founder", "Founder", ["terminal", "workspace"], [["goals", "agents", "note"], ["week", "next"]]),
  template("planner", "Planner", ["workspace"], [["week", "agenda"], ["next", "goals"]]),
  template("deep-work", "Deep work", ["workspace"], [["focus", "goals"], ["agenda"]]),
  template("writer", "Writer", ["workspace"], [["note", "focus"], ["goals", "week"]]),
  template("researcher", "Researcher", ["workspace", "brain"], [["note", "goals"], ["agenda", "cards"]]),
  template("goals-first", "Goal setter", ["workspace"], [["goals", "clock"], ["next", "note"]]),
  template("calm", "Calm", ["workspace"], [["clock", "agenda"]]),
  template("everything", "Everything", ["workspace", "applyai", "terminal"],
    [["agenda", "week", "note", "focus", "cards"], ["next", "goals", "pipeline", "agents", "session"]]),
];
/** The first-run question's answers. */
export const USES = TEMPLATES.filter((item) => item.use);

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
  // Rika offers one of the four plain answers; the rest of the library is for browsing.
  return USES.reduce((best, template) => (score(template) > score(best) ? template : best));
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

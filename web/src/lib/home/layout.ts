/**
 * Home's saved arrangement (the build's `ui.home.columns` and `ui.home.hidden`): which of
 * home's widgets are drawn, in which column and order. Pure, so Node tests can import it.
 */
import type { ModuleId } from "../api";

export const HOME_WIDGETS = [
  { id: "agenda", title: "Today", modules: ["workspace"], column: 0 },
  { id: "note", title: "Quick note", modules: ["workspace"], column: 0 },
  { id: "focus", title: "Focus", modules: ["workspace"], column: 0 },
  { id: "next", title: "Up next", modules: ["workspace", "applyai"], column: 1 },
  { id: "agents", title: "Agents", modules: ["terminal"], column: 1 },
  { id: "session", title: "Agent session", modules: ["terminal"], column: 1 },
  { id: "goals", title: "Goals", modules: [], column: 1, library: true },
  { id: "week", title: "This week", modules: ["workspace"], column: 0, library: true },
  { id: "cards", title: "Flashcards", modules: ["workspace"], column: 0, library: true },
  { id: "pipeline", title: "Applications", modules: ["applyai"], column: 1, library: true },
  { id: "clock", title: "Clock", modules: [], column: 0, library: true },
] as const satisfies readonly Widget[];
/**
 * `modules`: the widget draws when any of them is on (none: always). `library`: it waits
 * in edit mode's library until it is added or a template places it, so home stays as it
 * was for everyone who has not asked for it.
 */
type Widget = { id: string; title: string; modules: readonly ModuleId[]; column: number; library?: boolean };
const drawable = (widget: Widget, enabled: readonly string[]) => !widget.modules.length || widget.modules.some((module) => enabled.includes(module));
export type HomeWidgetId = (typeof HOME_WIDGETS)[number]["id"];
export type HomeLayout = { columns: string[][]; hidden: string[] };


/**
 * The columns to draw. A saved id this build does not know, or whose modules are all off,
 * keeps its place in the file and is skipped; a widget the file neither places nor hides
 * joins its usual column, so a new widget shows for people with a saved arrangement.
 */
export function resolveHome(saved: HomeLayout, enabled: readonly string[]): HomeWidgetId[][] {
  const drawn = (id: string) => HOME_WIDGETS.find((widget) => widget.id === id && drawable(widget, enabled))?.id;
  const columns = saved.columns.map((column) => column.flatMap((id) => (saved.hidden.includes(id) ? [] : drawn(id) ?? [])));
  const placed = new Set<string>([...saved.columns.flat(), ...saved.hidden]);
  for (const widget of HOME_WIDGETS) {
    if (placed.has(widget.id) || "library" in widget || !drawn(widget.id)) continue;
    while (columns.length <= widget.column) columns.push([]);
    columns[widget.column].push(widget.id);
  }
  return columns.filter((column) => column.length);
}

/** Edit mode arranges two columns: beside Rika's panel a third never fits (a saved third still draws, wrapped). */
export const MAX_COLUMNS = 2;

/**
 * Edit mode's one move: `id` leaves wherever it is and lands in `column` just before
 * `before` (at the end without it). A column index one past the last starts a new column,
 * up to MAX_COLUMNS.
 * Emptied columns stay until the arrangement is saved, so indexes hold during a drag.
 */
export function placeWidget(columns: HomeWidgetId[][], id: HomeWidgetId, column: number, before?: HomeWidgetId): HomeWidgetId[][] {
  const next = columns.map((items) => items.filter((item) => item !== id));
  while (next.length <= Math.min(column, MAX_COLUMNS - 1)) next.push([]);
  const target = next[Math.min(column, MAX_COLUMNS - 1)];
  const at = before && before !== id ? target.indexOf(before) : -1;
  target.splice(at < 0 ? target.length : at, 0, id);
  return next;
}

/** The widgets that can be drawn and are not on home: edit mode's library. */
export function spareWidgets(columns: HomeWidgetId[][], enabled: readonly string[]) {
  const placed = new Set<string>(columns.flat());
  return HOME_WIDGETS.filter((widget) => !placed.has(widget.id) && drawable(widget, enabled));
}

/**
 * What Done writes to the build: the edited columns, and as hidden every drawable widget
 * left off (plus what was hidden before and still is). A widget whose module is off is
 * neither, so it joins its usual column when the module comes on.
 */
export function savedHome(columns: HomeWidgetId[][], previous: HomeLayout, enabled: readonly string[]): HomeLayout {
  const placed = new Set<string>(columns.flat());
  // A library widget left off needs no entry: it never joins home by itself.
  const hidden = new Set([...previous.hidden, ...spareWidgets(columns, enabled).filter((widget) => !("library" in widget)).map((widget) => widget.id)]);
  return { columns: columns.filter((column) => column.length), hidden: [...hidden].filter((id) => !placed.has(id)) };
}

/** A column's share of the board: between these, in steps of 0.05 (1 is an even share). */
export const WIDTH_LIMITS = { min: 0.6, max: 1.4, step: 0.05 };
/** Rika's share of home's width when it is set by hand; 0 keeps the usual width. */
export const RIKA_LIMITS = { min: 0.2, max: 0.45, step: 0.01 };

const snap = (value: number, { min, max, step }: { min: number; max: number; step: number }) =>
  Math.round(Math.min(max, Math.max(min, value)) / step) * step;

/**
 * Two columns' shares from where the splitter between them sits (`fraction` of the board's
 * width, 0 to 1): they always add up to 2, so an even split is [1, 1].
 */
export function splitWidths(fraction: number): [number, number] {
  const first = Number(snap(fraction * 2, WIDTH_LIMITS).toFixed(2));
  return [first, Number((2 - first).toFixed(2))];
}

/** The saved shares as one per drawn column: a missing, extra or out-of-range entry is an even share. */
export function columnShares(saved: readonly number[], count: number): number[] {
  return Array.from({ length: count }, (_, index) => {
    const value = saved[index];
    return typeof value === "number" && value >= 0.5 && value <= 1.5 ? value : 1;
  });
}

/** Rika's share from where its splitter sits (`fraction` of home's width taken by Rika). */
export function rikaShare(fraction: number): number {
  return Number(snap(fraction, RIKA_LIMITS).toFixed(2));
}

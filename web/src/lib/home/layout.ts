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
] as const satisfies readonly { id: string; title: string; modules: readonly ModuleId[]; column: number }[];
export type HomeWidgetId = (typeof HOME_WIDGETS)[number]["id"];
export type HomeLayout = { columns: string[][]; hidden: string[] };

/**
 * The columns to draw. A saved id this build does not know, or whose modules are all off,
 * keeps its place in the file and is skipped; a widget the file neither places nor hides
 * joins its usual column, so a new widget shows for people with a saved arrangement.
 */
export function resolveHome(saved: HomeLayout, enabled: readonly string[]): HomeWidgetId[][] {
  const drawn = (id: string) => HOME_WIDGETS.find((widget) => widget.id === id && widget.modules.some((module) => enabled.includes(module)))?.id;
  const columns = saved.columns.map((column) => column.flatMap((id) => (saved.hidden.includes(id) ? [] : drawn(id) ?? [])));
  const placed = new Set<string>([...saved.columns.flat(), ...saved.hidden]);
  for (const widget of HOME_WIDGETS) {
    if (placed.has(widget.id) || !drawn(widget.id)) continue;
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
  return HOME_WIDGETS.filter((widget) => !placed.has(widget.id) && widget.modules.some((module) => enabled.includes(module)));
}

/**
 * What Done writes to the build: the edited columns, and as hidden every drawable widget
 * left off (plus what was hidden before and still is). A widget whose module is off is
 * neither, so it joins its usual column when the module comes on.
 */
export function savedHome(columns: HomeWidgetId[][], previous: HomeLayout, enabled: readonly string[]): HomeLayout {
  const placed = new Set<string>(columns.flat());
  const hidden = new Set([...previous.hidden, ...spareWidgets(columns, enabled).map((widget) => widget.id)]);
  return { columns: columns.filter((column) => column.length), hidden: [...hidden].filter((id) => !placed.has(id)) };
}

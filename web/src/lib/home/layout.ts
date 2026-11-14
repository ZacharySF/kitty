/**
 * Home's saved arrangement (the build's `ui.home.columns` and `ui.home.hidden`): which of
 * home's widgets are drawn, in which column and order. Pure, so Node tests can import it.
 */
import type { ModuleId } from "../api";

export const HOME_WIDGETS = [
  { id: "agenda", title: "Today", modules: ["workspace"], column: 0 },
  { id: "note", title: "Quick note", modules: ["workspace"], column: 0 },
  { id: "next", title: "Up next", modules: ["workspace", "applyai"], column: 1 },
  { id: "agents", title: "Agents", modules: ["terminal"], column: 1 },
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

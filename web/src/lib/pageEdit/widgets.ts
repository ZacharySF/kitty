/**
 * Page edit mode: the build's `ui.pages.widgets` entries, one per home widget placed on a
 * page, as `<view>|<widget>|<x>,<y>,<w>` (px from the view panel's top-left corner, on the
 * 8px grid). A page holds at most one of each widget. Pure, for Node tests.
 */

export type PageWidget = { view: string; widget: string; x: number; y: number; w: number };

/** kitty-build.schema.json's pattern for one entry. */
export const WIDGET_PATTERN = /^[a-z][a-z0-9-]{0,39}[|][a-z][a-z0-9-]{0,39}[|][0-9]{1,5},[0-9]{1,5},[0-9]{3,4}$/;
export const MAX_WIDGETS = 200;
export const WIDGET_WIDTH = { min: 240, max: 1200, initial: 360 };
const GRID = 8;
const LIMIT = 99992;

const onGrid = (value: number, min: number, max: number) => Math.max(min, Math.min(max, Math.round(value / GRID) * GRID)) || 0;

/** One entry read; null for anything the schema would refuse. A width out of range is brought into it. */
export function parseWidget(entry: string): PageWidget | null {
  if (!WIDGET_PATTERN.test(entry)) return null;
  const [view, widget, place] = entry.split("|");
  const [x, y, w] = place.split(",").map(Number);
  return { view, widget, x, y, w: Math.max(WIDGET_WIDTH.min, Math.min(WIDGET_WIDTH.max, w)) };
}

export function formatWidget({ view, widget, x, y, w }: PageWidget): string {
  return `${view}|${widget}|${onGrid(x, 0, LIMIT)},${onGrid(y, 0, LIMIT)},${onGrid(w, WIDGET_WIDTH.min, WIDGET_WIDTH.max)}`;
}

/** The widgets of one view, in the order they were placed. */
export function widgetsFor(entries: readonly string[], view: string): PageWidget[] {
  return entries.map(parseWidget).filter((item): item is PageWidget => item !== null && item.view === view);
}

/** `entries` without one page's widget. */
export function removeWidget(entries: readonly string[], view: string, widget: string): string[] {
  return entries.filter((entry) => !entry.startsWith(`${view}|${widget}|`));
}

/** `entries` without a page's widgets. */
export function withoutViewWidgets(entries: readonly string[], view: string): string[] {
  return entries.filter((entry) => !entry.startsWith(`${view}|`));
}

/**
 * `entries` with `widget` on `view` at x, y (in place of the one already there). An entry
 * the schema would refuse is not written; past the build's limit the oldest entries go.
 */
export function addWidget(entries: readonly string[], view: string, widget: string, x: number, y: number, w = WIDGET_WIDTH.initial): string[] {
  const rest = removeWidget(entries, view, widget);
  const entry = formatWidget({ view, widget, x, y, w });
  return WIDGET_PATTERN.test(entry) ? [...rest, entry].slice(-MAX_WIDGETS) : rest;
}

/** One placed widget changed where it stands in the list; `entries` as they are when it is not there. */
function change(entries: readonly string[], view: string, widget: string, next: (item: PageWidget) => PageWidget): string[] {
  return entries.map((entry) => {
    const item = entry.startsWith(`${view}|${widget}|`) ? parseWidget(entry) : null;
    return item ? formatWidget(next(item)) : entry;
  });
}

export function moveWidget(entries: readonly string[], view: string, widget: string, x: number, y: number): string[] {
  return change(entries, view, widget, (item) => ({ ...item, x, y }));
}

export function resizeWidget(entries: readonly string[], view: string, widget: string, w: number): string[] {
  return change(entries, view, widget, (item) => ({ ...item, w }));
}

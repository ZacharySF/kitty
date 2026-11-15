/**
 * Page edit mode: the build's `ui.pages.moves` entries, one per moved block, as
 * `<view>|<dx>,<dy>,<w>,<h>|<path>` (px; an empty w or h is unchanged). Pure, for Node tests.
 */

export type Move = { view: string; dx: number; dy: number; w: number | null; h: number | null; path: string };

/** kitty-build.schema.json's pattern for one entry. */
export const MOVE_PATTERN = /^[a-z][a-z0-9-]{0,39}[|][-]?[0-9]{1,5},[-]?[0-9]{1,5},[0-9]{0,5},[0-9]{0,5}[|][ -~]{1,600}$/;
export const MAX_MOVES = 400;
export const SNAP = 8;
/** The smallest a block can be dragged to. */
export const MIN_SIZE = 40;
const LIMIT = 99999;

const offset = (value: number) => Math.max(-LIMIT, Math.min(LIMIT, Math.round(value))) || 0;
const size = (value: number | null) => (value === null ? null : Math.max(MIN_SIZE, Math.min(LIMIT, Math.round(value))));

/** `value` on the 8px grid, or as it is (Alt held). */
export function snap(value: number, free = false): number {
  return free ? Math.round(value) : Math.round(value / SNAP) * SNAP;
}

/** One entry read; null for anything the schema would refuse. */
export function parseMove(entry: string): Move | null {
  if (!MOVE_PATTERN.test(entry)) return null;
  const first = entry.indexOf("|");
  const second = entry.indexOf("|", first + 1);
  const [dx, dy, w, h] = entry.slice(first + 1, second).split(",");
  return { view: entry.slice(0, first), dx: Number(dx), dy: Number(dy), w: w ? Number(w) : null, h: h ? Number(h) : null, path: entry.slice(second + 1) };
}

export function formatMove(move: Move): string {
  return `${move.view}|${offset(move.dx)},${offset(move.dy)},${size(move.w) ?? ""},${size(move.h) ?? ""}|${move.path}`;
}

/** A move that changes nothing. */
export function isIdentity(move: Move): boolean {
  return offset(move.dx) === 0 && offset(move.dy) === 0 && move.w === null && move.h === null;
}

/** The moves of one view. */
export function movesFor(entries: readonly string[], view: string): Move[] {
  return entries.map(parseMove).filter((move): move is Move => move !== null && move.view === view);
}

const names = (entry: string, view: string, path: string) => entry.startsWith(`${view}|`) && entry.endsWith(`|${path}`) && parseMove(entry)?.path === path;

/** The move saved for one block, or one that changes nothing. */
export function moveOf(entries: readonly string[], view: string, path: string): Move {
  const entry = entries.find((item) => names(item, view, path));
  return (entry && parseMove(entry)) || { view, dx: 0, dy: 0, w: null, h: null, path };
}

/** `entries` without one block's move. */
export function withoutMove(entries: readonly string[], view: string, path: string): string[] {
  return entries.filter((entry) => !names(entry, view, path));
}

/** `entries` without a view's moves. */
export function withoutView(entries: readonly string[], view: string): string[] {
  return entries.filter((entry) => !entry.startsWith(`${view}|`));
}

/**
 * `entries` with `move` in place of its block's earlier one (dropped when it changes nothing,
 * or when the schema would refuse it). Past the build's limit the oldest entries go.
 */
export function withMove(entries: readonly string[], move: Move): string[] {
  const rest = withoutMove(entries, move.view, move.path);
  const entry = formatMove(move);
  if (isIdentity(move) || !MOVE_PATTERN.test(entry)) return rest;
  return [...rest, entry].slice(-MAX_MOVES);
}

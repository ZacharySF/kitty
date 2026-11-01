/** Date helpers for exam plans and spaced returns. Dates are `YYYY-MM-DD` strings. */
import type { Guide, Item } from "./types";

/** Days until the next spaced return for each rung (mirrors `study.RETURN_LADDER`). */
export const RETURN_LADDER = [1, 3, 7, 16, 35];
/** Planned minutes past which a day counts as heavy. */
export const HEAVY_MINUTES = 90;

const DAY = 86_400_000;
const noon = (date: string) => Date.parse(`${date}T12:00:00Z`);
export const addDays = (date: string, days: number) => new Date(noon(date) + days * DAY).toISOString().slice(0, 10);
export const daysBetween = (from: string, to: string) => Math.round((noon(to) - noon(from)) / DAY);
export const formatDay = (date: string, options: Intl.DateTimeFormatOptions) =>
  new Date(noon(date)).toLocaleDateString(undefined, { timeZone: "UTC", ...options });

function wallClock(instant: number, timeZone: string) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(instant).map((part) => [part.type, part.value]));
  return Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute), Number(parts.second));
}
/** The local date of an instant in `timeZone`. */
export const localDay = (iso: string, timeZone: string) => new Date(wallClock(Date.parse(iso), timeZone)).toISOString().slice(0, 10);
export const localTime = (iso: string, timeZone: string) => new Date(wallClock(Date.parse(iso), timeZone)).toISOString().slice(11, 16);
/** Moves an instant by whole days, keeping its wall-clock time in `timeZone` across DST changes. */
export function shiftDays(iso: string, timeZone: string, days: number) {
  const before = Date.parse(iso);
  const moved = before + days * DAY;
  const offset = (instant: number) => wallClock(instant, timeZone) - instant;
  return new Date(moved - (offset(moved) - offset(before))).toISOString();
}

export const blockMinutes = (block: Item) => Math.round((Date.parse(block.end!) - Date.parse(block.start!)) / 60_000);
export const blockDay = (block: Item) => localDay(block.start!, block.timezone || "UTC");

/** What a spaced return is about: the session's gist, its outcome, or its date. */
export const returnTitle = (guide: Guide) =>
  guide.state.responses[4] || guide.state.responses[0] || `Session of ${new Date(guide.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}`;

/** Calendar date arithmetic and layout: pure functions of their arguments, no component state. */
import type { DayLoad } from "./plan.ts";

/** Noon UTC on `YYYY-MM-DD`: day arithmetic on it never crosses a date line. */
export const dateAt = (day: string) => new Date(`${day}T12:00:00Z`);

/** `day` moved by `count` calendar days. */
export function addDays(day: string, count: number) {
  const d = dateAt(day);
  d.setUTCDate(d.getUTCDate() + count);
  return d.toISOString().slice(0, 10);
}

/** The Monday of `day`'s week. */
export function weekStart(day: string) {
  return addDays(day, -((dateAt(day).getUTCDay() + 6) % 7));
}

/** The wall date, time, hour and minute of the instant `value` in `zone`. */
export function wallParts(value: string, zone: string) {
  const values = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const get = (key: string) => values.find((p) => p.type === key)?.value || "";
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    time: `${get("hour")}:${get("minute")}`,
    hour: Number(get("hour")),
    minute: Number(get("minute")),
  };
}

/** The `<input>` value for an instant: `YYYY-MM-DDTHH:MM` wall time in `zone`, or the date itself when all-day. */
export function localInput(value: string, allDay: boolean, zone: string) {
  if (allDay) return value;
  const p = wallParts(value, zone);
  return `${p.date}T${p.time}`;
}

/** Wall time "YYYY-MM-DDTHH:MM" moved by `minutes`. */
export const shiftWall = (wall: string, minutes: number) =>
  new Date(Date.parse(`${wall}:00Z`) + minutes * 60000).toISOString().slice(0, 16);

/** The wall time of half-hour slot `halfHour` counted from `day` (48 slots a day; later slots run into the next days). */
export function slot(day: string, halfHour: number) {
  const offset = halfHour % 48;
  return `${addDays(day, Math.floor(halfHour / 48))}T${String(Math.floor(offset / 2)).padStart(2, "0")}:${offset % 2 ? "30" : "00"}`;
}

/** A timed item placed on one day: minutes since midnight, and its lane among overlapping items. */
export type TimedPlacement<T> = { event: T; start: number; end: number; column: number; columns: number };

/**
 * Lays out the timed items shown on `day` (in `zone`) side by side: items that overlap share a
 * group, each takes the first free lane, and every item in a group spans `columns` lanes. Items
 * starting before or ending after `day` are clipped to it; anything shorter than 30 minutes still
 * occupies 30 so a label fits.
 */
export function placeTimed<T extends { start: string; end: string }>(items: T[], day: string, zone: string): TimedPlacement<T>[] {
  const placements = items
    .map((event) => {
      const start = wallParts(event.start, zone);
      const end = wallParts(event.end, zone);
      return {
        event,
        start: start.date < day ? 0 : start.hour * 60 + start.minute,
        end: end.date > day ? 1440 : end.hour * 60 + end.minute,
        column: 0,
        columns: 1,
      };
    })
    .sort((a, b) => a.start - b.start || b.end - a.end);
  let group: TimedPlacement<T>[] = [];
  let laneEnds: number[] = [];
  let groupEnd = 0;
  function finishGroup() {
    for (const placement of group) placement.columns = laneEnds.length;
    group = [];
    laneEnds = [];
  }
  for (const placement of placements) {
    if (placement.start >= groupEnd) finishGroup();
    let column = laneEnds.findIndex((end) => end <= placement.start);
    if (column === -1) column = laneEnds.length;
    placement.column = column;
    const visibleEnd = Math.min(1440, Math.max(placement.end, placement.start + 30));
    laneEnds[column] = visibleEnd;
    groupEnd = group.length ? Math.max(groupEnd, visibleEnd) : visibleEnd;
    group.push(placement);
  }
  finishGroup();
  return placements;
}

/** Event counts and booked minutes per local date in `zone`; timed events split across midnight, all-day ones book no time. */
export function dayLoad(events: { start: string; end: string; all_day: boolean }[], zone: string): DayLoad {
  const result: DayLoad = {};
  const add = (day: string, minutes: number, count: number) => {
    const entry = (result[day] ??= { minutes: 0, events: 0 });
    entry.minutes += minutes;
    entry.events += count;
  };
  for (const event of events) {
    if (event.all_day) { add(event.start, 0, 1); continue; }
    const start = wallParts(event.start, zone);
    const end = wallParts(event.end, zone);
    const startMinute = start.hour * 60 + start.minute;
    const endMinute = end.hour * 60 + end.minute;
    if (start.date === end.date) { add(start.date, endMinute - startMinute, 1); continue; }
    add(start.date, 1440 - startMinute, 1);
    for (let day = addDays(start.date, 1); day < end.date; day = addDays(day, 1)) add(day, 1440, 0);
    if (endMinute) add(end.date, endMinute, 0);
  }
  return result;
}

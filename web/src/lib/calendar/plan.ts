/** Booked minutes and event count per `YYYY-MM-DD`. */
export type DayLoad = Record<string, { minutes: number; events: number }>;

/** Day plan timeline bounds, minutes since midnight. */
export const PLAN_START = 8 * 60;
export const PLAN_END = 21 * 60;
/** Minutes a planned task blocks out; tasks carry no duration of their own. */
export const PLAN_LENGTH = 30;

/**
 * Start minute of the first gap of `length` minutes at or after `from`
 * (rounded up to a quarter hour) that avoids every busy [start, end) range
 * and ends by `dayEnd`; null when the day is full.
 */
export function nextFreeGap(busy: [number, number][], from: number, length: number, dayEnd = PLAN_END): number | null {
  let cursor = Math.ceil(from / 15) * 15;
  for (const [start, end] of [...busy].sort((a, b) => a[0] - b[0])) {
    if (end <= cursor) continue;
    if (start - cursor >= length) break;
    cursor = Math.max(cursor, Math.ceil(end / 15) * 15);
  }
  return cursor + length <= dayEnd ? cursor : null;
}

/** ISO-8601 week number of a `YYYY-MM-DD` date. */
export function isoWeek(day: string): number {
  const date = new Date(`${day}T12:00:00Z`);
  const thursday = new Date(date);
  thursday.setUTCDate(date.getUTCDate() + 3 - ((date.getUTCDay() + 6) % 7));
  const jan4 = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 4, 12));
  return 1 + Math.round(((thursday.getTime() - jan4.getTime()) / 864e5 - 3 + ((jan4.getUTCDay() + 6) % 7)) / 7);
}

/** "2h 30m" style duration. */
export function duration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  return hours && rest ? `${hours}h ${rest}m` : hours ? `${hours}h` : `${rest}m`;
}

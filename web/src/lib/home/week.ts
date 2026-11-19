/**
 * Home's This week widget: the seven days from today with how many events start on each,
 * and the event that comes next. Pure, so Node tests can import it.
 */
export type WeekEvent = { title: string; start: string; all_day?: boolean };

/** Local midnight of the day that holds `at`. */
export function midnight(at: number): number {
  const day = new Date(at);
  day.setHours(0, 0, 0, 0);
  return day.getTime();
}

/** When an event starts: a bare date (an all-day event) is that day's local midnight. */
const instant = (value: string) => (/^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00`).getTime() : Date.parse(value));

/** Seven calendar days from `start` (a local midnight), each with the events that start on it. */
export function weekDays<T extends WeekEvent>(events: readonly T[], start: number): { at: number; events: T[] }[] {
  const first = new Date(start);
  // Calendar days, not 24-hour steps: a clock change makes one day of the week 23 or 25 hours long.
  const days = Array.from({ length: 7 }, (_, index) => ({ at: new Date(first.getFullYear(), first.getMonth(), first.getDate() + index).getTime(), events: [] as T[] }));
  for (const event of events) days.find((item) => item.at === midnight(instant(event.start)))?.events.push(event);
  return days;
}

/** The events that have not started at `now` (an all-day one starts at its day's midnight), soonest first, at most `limit`. */
export function upcoming<T extends WeekEvent>(events: readonly T[], now: number, limit: number): (T & { at: number })[] {
  return events
    .map((event) => ({ ...event, at: instant(event.start) }))
    .filter((event) => event.at > now)
    .sort((a, b) => a.at - b.at)
    .slice(0, limit);
}

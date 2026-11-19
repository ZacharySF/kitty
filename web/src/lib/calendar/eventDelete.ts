/** Deleting an event straight from the grid: which one the keys mean, and what is sent. */
import type { Cal, CalendarEvent } from "./types";

/** One drawn event: a series shows many, each with its own occurrence. */
export const eventKey = (event: Pick<CalendarEvent, "id" | "occurrence">) => `${event.id} ${event.occurrence}`;

/** Ids of the calendars imported from an outside account; the server refuses changes to their events. */
export const lockedCalendars = (calendars: Pick<Cal, "id" | "read_only">[]) =>
  new Set(calendars.filter((cal) => cal.read_only).map((cal) => cal.id));

/**
 * The event Delete or Backspace removes: the one with keyboard focus, else the selected one.
 * Null when there is none, it is no longer shown, or its calendar is read-only.
 */
export function deleteTarget<E extends Pick<CalendarEvent, "id" | "occurrence" | "calendar_id">>(
  events: E[], locked: ReadonlySet<string>, focused: string | undefined, selected: string | null,
): E | null {
  const key = focused ?? selected;
  const event = key ? events.find((item) => eventKey(item) === key) : undefined;
  return event && !locked.has(event.calendar_id) ? event : null;
}

/** The edit that deletes an event: all of a single event, only this occurrence of a repeating one. */
export const deleteBody = (event: Pick<CalendarEvent, "revision" | "occurrence" | "rrule">) => ({
  revision: event.revision,
  scope: event.rrule ? "this" : "all",
  occurrence: event.occurrence,
  changes: {},
  delete: true,
});

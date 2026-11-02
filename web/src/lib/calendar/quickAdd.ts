import { parseQuickEvent } from "./quickParse";

/** Where a quick add starts before anything is typed: the selected day, or a clicked slot or range. */
export type QuickSlot = {
  /** `YYYY-MM-DD` or wall time `YYYY-MM-DDTHH:MM`. */
  start: string;
  /** Exclusive end in the same form, when a slot or range was picked. */
  end?: string;
};

export type QuickKind = "event" | "task";

export type QuickPlan = {
  title: string;
  /** `YYYY-MM-DD`. */
  day: string;
  /** Minutes since midnight; null for an all-day event or a date-only task. */
  start: number | null;
  /** Minutes since `day`'s midnight (may pass 1440); null when untimed. */
  end: number | null;
  /** Exclusive end day for an untimed multi-day range. */
  endDay: string;
  where: string;
  rrule: string;
};

const minutesOf = (wall: string) => Number(wall.slice(11, 13)) * 60 + Number(wall.slice(14, 16));
const dayAfter = (day: string) => {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
};

/** The typed phrase wins; the slot fills whatever it leaves out (an hour when only a start is known). */
export function planQuick(text: string, today: string, slot: QuickSlot): QuickPlan {
  const parsed = parseQuickEvent(text, today);
  const timedSlot = slot.start.length > 10;
  const slotLength = timedSlot && slot.end && slot.end.length > 10
    ? (Date.parse(`${slot.end}:00Z`) - Date.parse(`${slot.start}:00Z`)) / 60000
    : 60;
  const day = parsed.date ?? slot.start.slice(0, 10);
  const start = parsed.start ?? (timedSlot ? minutesOf(slot.start) : null);
  const end = start === null ? null
    : parsed.end !== null && parsed.end > start ? parsed.end
    : start + (parsed.minutes ?? (parsed.start === null ? slotLength : 60));
  const endDay = start === null && !parsed.date && slot.end && slot.end.length === 10 ? slot.end : dayAfter(day);
  return { title: parsed.title, day, start, end, endDay, where: parsed.where, rrule: parsed.rrule };
}

/** "HH:MM" for minutes since midnight, wrapping past midnight. */
export const clock = (minutes: number) =>
  `${String(Math.floor(minutes / 60) % 24).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;

/** Wall time `YYYY-MM-DDTHH:MM` that is `minutes` after `day`'s midnight. */
export const wallAt = (day: string, minutes: number) =>
  new Date(Date.parse(`${day}T00:00:00Z`) + minutes * 60000).toISOString().slice(0, 16);

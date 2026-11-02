/**
 * Notifications (docs/frontend-redesign-design.md Q60): every reminder source becomes a
 * notice that toasts once, lands in the notification center, and is held during quiet
 * hours until a morning digest hands it over. Delivery state is remembered per browser
 * profile, so a reload never re-toasts what you have already seen.
 */
export type Source = "calendar" | "job";
export type CalendarReminder = { event_id: string; occurrence: string; minutes: number; title: string; due: string };
export type JobReminder = { id: number; kind: string; due_at: string; note: string | null; job_id: number | null; label: string };
export type Target =
  | { kind: "calendar"; event_id: string; occurrence: string; minutes: number }
  | { kind: "job"; id: number; job_id: number | null };
export type Notice = { key: string; source: Source; title: string; body: string; due: string; target: Target };

/** Quiet hours as minutes of the local day; `from === to` is an empty window. */
export type QuietHours = { enabled: boolean; from: number; to: number; letCalendar: boolean; digest: boolean };
export const DEFAULT_QUIET: QuietHours = { enabled: false, from: 22 * 60, to: 7 * 60 + 30, letCalendar: true, digest: true };
export const LEDGER_KEY = "kitty.notify.ledger";
/** Whether due reminders also raise a system notification. */
export const SYSTEM_KEY = "kitty.notify.system";
/** Fired on `window` when quiet hours change, so the running notifier re-checks at once. */
export const QUIET_EVENT = "kitty:quiet-hours";
export const SOURCE_LABEL: Record<Source, string> = { calendar: "Calendar", job: "Job search" };

/**
 * What has happened to each notice key. `snoozed` maps a key to the ISO time it returns;
 * `held` keeps quiet-hours order for the digest.
 */
export type Ledger = { read: string[]; delivered: string[]; held: string[]; snoozed: Record<string, string> };
export const emptyLedger = (): Ledger => ({ read: [], delivered: [], held: [], snoozed: {} });
/** Seen keys kept per list; the oldest drop off first. */
const MEMORY = 400;

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export function clock(minutes: number): string {
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export const minuteOfDay = (date: Date) => date.getHours() * 60 + date.getMinutes();

/** Length of the quiet window in minutes. */
export const quietSpan = (quiet: QuietHours) => (quiet.to - quiet.from + 1440) % 1440;

export function inQuietHours(quiet: QuietHours, minute: number): boolean {
  if (!quiet.enabled || quiet.from === quiet.to) return false;
  return quiet.from < quiet.to ? minute >= quiet.from && minute < quiet.to : minute >= quiet.from || minute < quiet.to;
}

/** True when quiet hours keep this notice back instead of toasting it. */
export const holds = (quiet: QuietHours, notice: Notice, minute: number) =>
  inQuietHours(quiet, minute) && !(notice.source === "calendar" && quiet.letCalendar);

function offset(minutes: number): string {
  if (minutes === 0) return "Starting now";
  if (minutes % 1440 === 0) return `${minutes / 1440} day${minutes === 1440 ? "" : "s"} before`;
  if (minutes % 60 === 0) return `${minutes / 60} h before`;
  return `${minutes} min before`;
}

export function calendarNotice(item: CalendarReminder): Notice {
  return {
    key: `calendar:${item.event_id}:${item.occurrence}:${item.minutes}`,
    source: "calendar",
    title: item.title,
    body: offset(item.minutes),
    due: item.due,
    target: { kind: "calendar", event_id: item.event_id, occurrence: item.occurrence, minutes: item.minutes },
  };
}

const KIND: Record<string, string> = { followup: "Follow-up", deadline: "Deadline", opens: "Applications open", custom: "Reminder" };

/** Job-search reminders are keyed by due date too, so one that comes back after a snooze toasts again. */
export function jobNotice(item: JobReminder): Notice {
  return {
    key: `job:${item.id}:${item.due_at}`,
    source: "job",
    title: item.label,
    body: KIND[item.kind] ?? "Reminder",
    due: item.due_at,
    target: { kind: "job", id: item.id, job_id: item.job_id },
  };
}

/** "2 calendar · 1 job search" */
export function digestLine(notices: Notice[]): string {
  return (["calendar", "job"] as const)
    .map(source => [source, notices.filter(n => n.source === source).length] as const)
    .filter(([, count]) => count)
    .map(([source, count]) => `${count} ${SOURCE_LABEL[source].toLowerCase()}`)
    .join(" · ");
}

export type Step = {
  ledger: Ledger;
  /** Notices to toast now. */
  toasts: Notice[];
  /** Held notices handed over together when quiet hours end, or null. */
  digest: Notice[] | null;
  /** Due notices the center lists: everything but what is snoozed. */
  visible: Notice[];
};

/** One delivery pass over the currently due notices. */
export function step(ledger: Ledger, notices: Notice[], quiet: QuietHours, now: Date): Step {
  const minute = minuteOfDay(now);
  const snoozed: Record<string, string> = {};
  const woke = new Set<string>();
  for (const [key, until] of Object.entries(ledger.snoozed)) {
    if (Date.parse(until) > now.getTime()) snoozed[key] = until;
    else woke.add(key);
  }
  const delivered = new Set(ledger.delivered.filter(key => !woke.has(key)));
  const visible = notices.filter(n => !(n.key in snoozed));
  const due = new Set(visible.map(n => n.key));
  let held = ledger.held.filter(key => due.has(key));
  const toasts: Notice[] = [];
  for (const notice of visible) {
    if (delivered.has(notice.key) || held.includes(notice.key)) continue;
    if (holds(quiet, notice, minute)) held.push(notice.key);
    else {
      toasts.push(notice);
      delivered.add(notice.key);
    }
  }
  let digest: Notice[] | null = null;
  if (held.length && !inQuietHours(quiet, minute)) {
    const released = visible.filter(n => held.includes(n.key));
    for (const key of held) delivered.add(key);
    if (quiet.digest && released.length) digest = released;
    held = [];
  }
  return {
    ledger: { read: ledger.read.slice(-MEMORY), delivered: [...delivered].slice(-MEMORY), held, snoozed },
    toasts,
    digest,
    visible,
  };
}

const strings = (value: unknown) => (Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);

export function readLedger(storage: Storage): Ledger {
  try {
    const saved = JSON.parse(storage.getItem(LEDGER_KEY) ?? "null") as Record<string, unknown> | null;
    if (!saved || typeof saved !== "object") return emptyLedger();
    const snoozed = saved.snoozed && typeof saved.snoozed === "object" ? saved.snoozed as Record<string, unknown> : {};
    return {
      read: strings(saved.read),
      delivered: strings(saved.delivered),
      held: strings(saved.held),
      snoozed: Object.fromEntries(Object.entries(snoozed).filter((entry): entry is [string, string] => typeof entry[1] === "string")),
    };
  } catch {
    return emptyLedger();
  }
}

export function writeLedger(storage: Storage, ledger: Ledger): void {
  storage.setItem(LEDGER_KEY, JSON.stringify(ledger));
}

/** Quiet hours as the build's `ui.notifications.quiet` describes them; junk takes the defaults. */
export function quietOf(saved: unknown): QuietHours {
  const value = saved && typeof saved === "object" ? (saved as Record<string, unknown>) : {};
  const minute = (item: unknown, fallback: number) =>
    Number.isInteger(item) && (item as number) >= 0 && (item as number) < 1440 ? (item as number) : fallback;
  const flag = (item: unknown, fallback: boolean) => (typeof item === "boolean" ? item : fallback);
  return {
    enabled: flag(value.enabled, DEFAULT_QUIET.enabled),
    from: minute(value.from, DEFAULT_QUIET.from),
    to: minute(value.to, DEFAULT_QUIET.to),
    letCalendar: flag(value.let_calendar, DEFAULT_QUIET.letCalendar),
    digest: flag(value.digest, DEFAULT_QUIET.digest),
  };
}

/** Quiet hours as the build stores them (docs/platform-design.md Q8). */
export function quietPreference(quiet: QuietHours): { enabled: boolean; from: number; to: number; let_calendar: boolean; digest: boolean } {
  return { enabled: quiet.enabled, from: quiet.from, to: quiet.to, let_calendar: quiet.letCalendar, digest: quiet.digest };
}

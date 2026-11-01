import type { KittyTheme } from "../themeCatalog";

/** Relative luminance of a `#rrggbb` colour (WCAG 2). */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two `#rrggbb` colours. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Theme palette slots a calendar or event can store; each is `--c1..--c6`, so it recolours with the theme. */
export const PALETTE = ["c1", "c2", "c3", "c4", "c5", "c6"] as const;
/** A study subject; its colour is Study's, the single source for every subject-linked item. */
export type Subject = { id: number; name: string; color: string };
export type Paint = { color: string; subject: Subject | null };
type Linked = { subject_id?: number | null };
type PaintedCalendar = Linked & { id: string; color: string };

export const isSlot = (value: string | null | undefined) => !!value && /^c[1-6]$/.test(value);
/** CSS colour of a stored colour: a palette slot follows the theme; a hex from before slots (or Google) renders as is. */
export const cssColor = (stored: string) => (isSlot(stored) ? `var(--${stored})` : stored);
/** Palette slot for the calendar after `count` existing ones. */
export const nextSlot = (count: number) => `c${(Math.max(0, count) % 6) + 1}`;

/** The study subject an item links to; a deleted subject counts as unlinked. */
export const subjectOf = (item: Linked, subjects: Subject[]) =>
  item.subject_id == null ? null : (subjects.find((s) => s.id === item.subject_id) ?? null);

/** A calendar's colour: its subject's when linked, otherwise its own. */
export function calendarPaint(calendar: PaintedCalendar, subjects: Subject[]): Paint {
  const subject = subjectOf(calendar, subjects);
  return { color: subject ? subject.color : cssColor(calendar.color), subject };
}

/**
 * An event's colour: its own subject, else its calendar's subject (both locked to
 * the subject colour), else its picked palette slot, else its calendar's colour.
 */
export function itemPaint(
  item: Linked & { calendar_id: string; color?: string | null },
  calendars: PaintedCalendar[],
  subjects: Subject[],
): Paint {
  const own = subjectOf(item, subjects);
  if (own) return { color: own.color, subject: own };
  const calendar = calendars.find((c) => c.id === item.calendar_id);
  const inherited = calendar ? calendarPaint(calendar, subjects) : { color: "var(--c1)", subject: null };
  if (inherited.subject || !item.color) return inherited;
  return { color: cssColor(item.color), subject: null };
}

/**
 * Text colour for a filled block on `fill` (a `var(--cN)` slot or a `#rrggbb`
 * subject/legacy colour): the theme's dark void when it reads at least as well as ink.
 */
export function blockText(theme: KittyTheme, fill: string): string {
  const slot = /^var\(--c([1-6])\)$/.exec(fill);
  const hex = slot ? theme.graph[Number(slot[1]) - 1] : fill;
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return "var(--ink)";
  return contrast(hex, theme.roles.void) >= contrast(hex, theme.roles.ink) ? "var(--solid-void)" : "var(--ink)";
}

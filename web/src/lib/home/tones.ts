/**
 * Colour-coding home's widgets with the theme's own six data colours (`--c1`..`--c6`: the
 * ones calendars, subjects and map groups use), so a coded widget always belongs to the look
 * it sits in: six greys from frost to graphite on Black, the theme's hues elsewhere. The
 * build keeps `ui.home.tones`, one `widget:tone` entry per coded widget. Pure, so Node tests
 * can import it.
 */
export const TONES = [1, 2, 3, 4, 5, 6].map((step) => ({ id: `c${step}`, label: `Colour ${step}`, color: `var(--c${step})` }));
export type ToneId = string;

/** The colour of a widget's tone, or null when it has none (or one this build does not know). */
export function toneColor(tones: readonly string[], widget: string): string | null {
  const entry = tones.find((item) => item.startsWith(`${widget}:`));
  return TONES.find((tone) => tone.id === entry?.slice(widget.length + 1))?.color ?? null;
}

/** `tones` with `widget` given `tone`, or cleared (null); other widgets' entries are kept as they are. */
export function withTone(tones: readonly string[], widget: string, tone: string | null): string[] {
  const rest = tones.filter((item) => !item.startsWith(`${widget}:`));
  return tone ? [...rest, `${widget}:${tone}`] : rest;
}

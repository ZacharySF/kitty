/**
 * Colour-coding home's widgets: a short palette of quiet tones (greys, slates and darks that
 * sit on the black theme like tinted glass) and the build's `ui.home.tones` list, one
 * `widget:tone` entry per coded widget. Pure, so Node tests can import it.
 */
export const TONES = [
  { id: "graphite", label: "Graphite", color: "#8e8e93" },
  { id: "slate", label: "Slate", color: "#7c8aa0" },
  { id: "steel", label: "Steel", color: "#5b7fa8" },
  { id: "moss", label: "Moss", color: "#74917f" },
  { id: "sand", label: "Sand", color: "#a3927c" },
  { id: "plum", label: "Plum", color: "#8c7da3" },
  { id: "wine", label: "Wine", color: "#a0747f" },
  { id: "ink", label: "Ink", color: "#3a3a3f" },
] as const;
export type ToneId = (typeof TONES)[number]["id"];

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

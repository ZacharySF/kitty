const HEX = /^#[0-9a-f]{6}$/i;

/**
 * Pulls every graph color halfway toward the first (theme accent) color, so
 * nodes read as one family — like LearnHarness's graph — while each group keeps
 * a distinguishable tint. Inputs that are not #rrggbb are returned unchanged.
 */
export function harmonize(palette: readonly string[], pull = 0.5): string[] {
  const [base] = palette;
  if (!base || !palette.every((color) => HEX.test(color))) return [...palette];
  const channels = (hex: string) => [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16));
  const accent = channels(base);
  return palette.map((color, index) => index === 0 ? color : `#${channels(color)
    .map((value, channel) => Math.round(value + (accent[channel] - value) * pull).toString(16).padStart(2, "0"))
    .join("")}`);
}

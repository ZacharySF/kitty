/**
 * What Kitty rolls each time it loads: which still is behind the page, how it is framed and
 * which ink the big type takes. One seed per page load; pure, so Node tests can check that a
 * seed always gives the same roll and that rolls differ.
 */
export const STILLS = ["platform", "corridor", "beams"] as const;
export type Still = (typeof STILLS)[number];
/** The two inks of the sleeve: signal orange and tube yellow. */
export const INKS = ["#ff5a1f", "#ffd60a"] as const;
export type Roll = {
  still: Still;
  /** Mirrored left to right. */
  flipped: boolean;
  /** Where the frame sits over the still, in percent (object-position). */
  x: number;
  y: number;
  /** The ink of the vertical word. */
  ink: (typeof INKS)[number];
  /** Which edge the vertical word runs down. */
  edge: "left" | "right";
};

/** mulberry32: small, fast and the same in every engine. */
export function generator(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function roll(seed: number): Roll {
  const next = generator(seed);
  const pick = <T>(items: readonly T[]): T => items[Math.floor(next() * items.length)];
  return {
    still: pick(STILLS),
    flipped: next() < 0.5,
    x: Math.round(20 + next() * 60),
    y: Math.round(30 + next() * 40),
    ink: pick(INKS),
    edge: next() < 0.75 ? "right" : "left",
  };
}

/** The camera's date stamp, as compact film cameras burnt it in: '26 11 15. */
export function stamp(date: Date): string {
  const two = (value: number) => String(value).padStart(2, "0");
  return `'${two(date.getFullYear() % 100)} ${two(date.getMonth() + 1)} ${two(date.getDate())}`;
}

/** The word down the edge: the page you are on, from its address (`#quick-notes/3` → QUICK NOTES). */
export function word(hash: string): string {
  const name = hash.replace(/^#/, "").split(/[/?]/)[0].replace(/-/g, " ").trim();
  return (name === "today" || !name ? "rika!" : name).toUpperCase();
}

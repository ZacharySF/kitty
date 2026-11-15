/**
 * What Kitty rolls each time it loads: where the light falls, which scraps of text drift
 * behind the glass, what the departure board says. One seed per page load; pure, so Node
 * tests can check that a seed always gives the same scene and that scenes differ.
 */
export type Streak = { top: number; left: number; width: number; height: number; tint: "cyan" | "white" | "amber"; alpha: number };
export type Scrap = { text: string; top: number; left: number; size: number; alpha: number; turned: boolean };
export type Scene = { streaks: Streak[]; panes: number[]; scraps: Scrap[]; board: string[]; glyph: string; ticker: string };

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

const FACES = ["=^.^=", "(=^.^=)", ">^.^<", "=^-^=", "( o.o )", "/\\_/\\", "(=^w^=)", ":3", "~(=^.^)"];
const WORDS = ["soft club", "last train", "blue hour", "after rain", "platform 3", "window seat", "low battery", "dial tone", "night bus", "vending light",
  "glass corridor", "no signal", "wet neon", "mint air", "quiet car", "escalator up", "lost and found", "arrivals", "do not lean", "mind the gap"];
const LINES = ["KITTY LINE", "SOFT CLUB", "LAST TRAIN", "BLUE HOUR", "NIGHT BUS", "GLASS LOOP", "MINT EXPRESS", "AFTER RAIN"];
const STATES = ["ON TIME", "BOARDING", "DELAYED", "NAPPING", "ARRIVED", "DREAMING", "GATE OPEN"];
const TEXTURES = ["░▒▓█▓▒░", "▁▂▃▅▆▇▆▅▃▂▁", ":::::::::", "· · · · ·", "<<<    >>>", "+--+--+--+", "||| | || |||| | ||", "∿∿∿∿∿", "/////", "[        ]", "* . * . *"];
const GLYPHS = [">> ", ":: ", "// ", "=^ ", "~ ", "✦ ", "[ ] ", "<3 ", "░ ", "+ "];

export function scene(seed: number): Scene {
  const next = generator(seed);
  const pick = <T>(items: readonly T[]): T => items[Math.floor(next() * items.length)];
  const between = (low: number, high: number) => low + next() * (high - low);
  const whole = (low: number, high: number) => Math.floor(between(low, high + 1));
  const clock = () => `${String(whole(0, 23)).padStart(2, "0")}:${String(whole(0, 59)).padStart(2, "0")}`;
  const hex = () => `0x${whole(4096, 65535).toString(16).toUpperCase()}`;
  const bar = () => { const filled = whole(1, 9); return `[${"#".repeat(filled)}${"-".repeat(10 - filled)}] ${filled * 10}%`; };
  const made = [
    () => pick(FACES), () => pick(TEXTURES), () => pick(WORDS), () => `${pick(WORDS)} ${pick(FACES)}`, () => `${clock()} ${pick(LINES).toLowerCase()}`,
    hex, bar, () => `gate ${whole(1, 42)} >>`, () => `${between(10, 60).toFixed(3)}N ${between(100, 160).toFixed(3)}E`, () => `now playing: ${pick(WORDS).replace(/ /g, "_")}.wav`,
    () => `${pick(FACES)} ${pick(FACES)} ${pick(FACES)}`, () => `404 ${pick(["nap", "sun", "exit", "signal"])} not found`,
  ];
  const streaks: Streak[] = Array.from({ length: whole(5, 9) }, () => ({
    top: between(4, 94), left: between(-10, 70), width: between(18, 70), height: between(1, 7),
    tint: pick(["cyan", "cyan", "white", "amber"] as const), alpha: between(0.05, 0.16),
  }));
  const panes = Array.from({ length: whole(4, 8) }, () => between(3, 97)).sort((a, b) => a - b);
  const scraps: Scrap[] = Array.from({ length: whole(9, 15) }, () => ({
    text: pick(made)(), top: between(3, 93), left: between(1, 88), size: whole(11, 16), alpha: between(0.09, 0.2), turned: next() < 0.18,
  }));
  const rows = Array.from({ length: whole(3, 5) }, () => `| ${clock()}  ${pick(LINES).padEnd(12)}  ${pick(STATES).padEnd(9)} |`);
  const rule = `+${"-".repeat(rows[0].length - 2)}+`;
  return {
    streaks, panes, scraps, board: [rule, ...rows, rule], glyph: pick(GLYPHS),
    ticker: Array.from({ length: 6 }, () => pick(made)()).join("   ✦   "),
  };
}

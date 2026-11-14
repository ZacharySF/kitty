/**
 * Agent usage (kitty/web_usage.py): the shapes /api/usage answers, how the numbers are
 * written, and the geometry of the two charts. Pure, so Node tests can import it.
 */
export type UsageSource = "rika" | "ai" | "claude" | "codex" | "omp";
export type UsageSums = { input: number; cache_read: number; cache_write: number; output: number; cost_usd: number };
export type UsageDay = { day: string; sources: Record<UsageSource, UsageSums> };
export type UsageKind = { kind: "omp" | "claude" | "codex"; label: string; enabled: boolean };
export type CostSource = "reported" | "priced" | "none";

/** One coding-agent session's totals; `key` is opaque. */
export type SessionUsage = {
  key: string;
  agent: "omp" | "claude" | "codex";
  label: string;
  cwd_name: string | null;
  first_at: string | null;
  last_at: string | null;
  model: string | null;
  context_tokens: number | null;
  context_window: number | null;
  context_pct: number | null;
  requests: number;
  input: number;
  cache_read: number;
  cache_write: number;
  output: number;
  /** Input, cache writes and output: everything that is not a cache read. */
  new_tokens: number;
  tool_calls: number;
  files_changed: number;
  cost_usd: number | null;
  cost_source: CostSource;
};
export type UsageStep = { t: string; input: number; output: number; cache: number };
export type SessionDetail = SessionUsage & {
  models: { model: string; role: "main" | "subagent" | "aux"; input: number; cache_read: number; cache_write: number; output: number; cost_usd: number | null; cost_source: CostSource }[];
  series: UsageStep[];
};
export type UsageOverview = {
  scanning: boolean;
  skipped: number;
  kinds: UsageKind[];
  days: UsageDay[];
  total: UsageSums;
  models: ({ source: UsageSource; model: string } & UsageSums)[];
  sessions: SessionUsage[];
};

/** The sources of a day, bottom of the bar first, each a lightness step of the ink. */
export const SOURCES: { id: UsageSource; label: string; ink: number }[] = [
  { id: "omp", label: "omp", ink: 100 },
  { id: "claude", label: "Claude Code", ink: 72 },
  { id: "codex", label: "Codex", ink: 50 },
  { id: "rika", label: "Rika", ink: 34 },
  { id: "ai", label: "AI requests", ink: 22 },
];

export const newTokens = (sums: Pick<UsageSums, "input" | "cache_write" | "output">) => sums.input + sums.cache_write + sums.output;
export const allTokens = (sums: Pick<UsageSums, "input" | "cache_read" | "cache_write" | "output">) => newTokens(sums) + sums.cache_read;

/** A token count in three figures at most: 940, 1.2k, 18k, 3.9M. */
export function tokens(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "0";
  let scaled = value;
  let unit = 0;
  // 999.6k would round to "1000k": that is 1M.
  while (unit < 3 && Math.round(scaled) >= 1000) {
    scaled /= 1000;
    unit += 1;
  }
  if (!unit) return String(Math.round(scaled));
  return `${scaled < 9.95 ? scaled.toFixed(1).replace(/\.0$/, "") : Math.round(scaled)}${"kMB"[unit - 1]}`;
}

/** Dollars; an estimate at list price is marked "≈". No cost is no text. */
export function money(usd: number | null | undefined, source: CostSource = "reported"): string {
  if (typeof usd !== "number" || !Number.isFinite(usd) || source === "none") return "";
  const mark = source === "priced" ? "≈" : "";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `under $0.01`;
  return `${mark}$${usd.toFixed(2)}`;
}

const plural = (count: number, word: string) => `${tokens(count)} ${word}${count === 1 ? "" : "s"}`;

/** A live agent card's one line: context, new tokens, cache reads apart, tool calls, files changed, cost. */
export function usageLine(usage: SessionUsage): string {
  return [
    usage.context_pct == null ? "" : `${usage.context_pct}% context`,
    `${tokens(usage.new_tokens)} new`,
    usage.cache_read ? `${tokens(usage.cache_read)} cached` : "",
    plural(usage.tool_calls, "tool"),
    plural(usage.files_changed, "file"),
    money(usage.cost_usd, usage.cost_source),
  ].filter(Boolean).join(" · ");
}

/** A round number at or above `value` for the top of an axis: 1, 2 or 5 times a power of ten. */
export function axisTop(value: number): number {
  if (!(value > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 5, 10].map((step) => step * power).find((top) => top >= value)!;
}

export type Bar = { day: string; total: number; parts: { source: UsageSource; y: number; height: number; value: number }[] };

/**
 * The stacked bars of a run of days in a plot `height` tall: every source's tokens as a
 * part, the first of SOURCES at the bottom, scaled to a round axis top.
 */
export function stackBars(days: UsageDay[], height: number): { top: number; bars: Bar[] } {
  const totals = days.map((day) => SOURCES.reduce((sum, source) => sum + allTokens(day.sources[source.id]), 0));
  const top = axisTop(Math.max(0, ...totals));
  const bars = days.map((day, index) => {
    let base = height;
    const parts = SOURCES.flatMap((source) => {
      const value = allTokens(day.sources[source.id]);
      if (!value) return [];
      const size = (value / top) * height;
      base -= size;
      return [{ source: source.id, y: base, height: size, value }];
    });
    return { day: day.day, total: totals[index], parts };
  });
  return { top, bars };
}

/** "Nov 10" for an ISO day, without a time zone's help. */
export function dayName(day: string): string {
  const [, month, date] = day.split("-").map(Number);
  return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][month - 1] ?? ""} ${date}`;
}

/** What a day's bar says under the chart. */
export function dayReadout(day: UsageDay): string {
  const parts = SOURCES.map((source) => ({ source, value: allTokens(day.sources[source.id]) })).filter((part) => part.value);
  const cost = SOURCES.reduce((sum, source) => sum + day.sources[source.id].cost_usd, 0);
  if (!parts.length && !cost) return `${dayName(day.day)} · nothing`;
  return [dayName(day.day), `${tokens(parts.reduce((sum, part) => sum + part.value, 0))} tokens`, money(cost),
    parts.map((part) => `${part.source.label} ${tokens(part.value)}`).join(", ")].filter(Boolean).join(" · ");
}

/**
 * A session's sparkline: new tokens (input and output) per five minutes as polyline
 * points in a `width` by `height` box, a pixel clear of its edges. Fewer than two steps
 * draw no line.
 */
export function sparkPoints(series: UsageStep[], width: number, height: number): string {
  if (series.length < 2) return "";
  const values = series.map((step) => step.input + step.output);
  const top = Math.max(1, ...values);
  return values
    .map((value, index) => `${((index / (values.length - 1)) * (width - 2) + 1).toFixed(1)},${(height - 1 - (value / top) * (height - 2)).toFixed(1)}`)
    .join(" ");
}

const PIN = "rika.usage.pinned";
/** The session home's widget is pinned to (Settings → AI → Usage → Pin), or null to follow the latest. */
export function pinnedSession(storage: Pick<Storage, "getItem"> | undefined = globalThis.localStorage): string | null {
  const key = storage?.getItem(PIN) ?? "";
  return /^[0-9a-f]{16}$/.test(key) ? key : null;
}
export function pinSession(key: string | null, storage: Pick<Storage, "setItem" | "removeItem"> | undefined = globalThis.localStorage): void {
  if (key) storage?.setItem(PIN, key);
  else storage?.removeItem(PIN);
}

/**
 * Wire types and pure helpers for Settings → Rika (docs/rika-design.md Q13 A, Q16 B,
 * Q18 A): routing memory, email auto-send and Rika's memory. Served by
 * kitty.web_rika (routes, memory) and kitty.web_rika_mail.
 */
import type { RikaDraft } from "../rika";

export type RouteTier = "direct" | "fast" | "standard" | "deep";
export type Route = {
  signature: string;
  label: string;
  tier: RouteTier;
  path: string[];
  uses: number;
  escalations: number;
  avg_tokens: number;
  avg_cost_usd: number;
  shortcut: { template: string; uses: number; active: boolean } | null;
  example: string;
  updated_at: string;
};
export type TrendDay = { day: string; requests: number; avg_cost_usd: number };
export type RoutesState = {
  routes: Route[];
  summary: { today_requests: number; today_tokens: number; today_cost_usd: number; cached_share: number; trend: TrendDay[] };
};

export type AutoKind = "scheduling" | "confirmation" | "thanks";
export type MailState = {
  mailbox: {
    connected: boolean;
    demo: boolean;
    account: string | null;
    setup: string | null;
    can_delay: false;
    hold_seconds: number;
    hold_choices: number[];
    last_check: string | null;
  };
  kinds: { kind: AutoKind; label: string; hint: string; auto: boolean; sent: number }[];
  drafts: RikaDraft[];
};

export type Preference = { id: number; text: string; source: "stated" | "added"; created_at: string; updated_at: string };
export type MemorySource = { source: string; label: string; items: number; refreshed_at: string | null };
export type MemoryHit = { source: string; ref: string; title: string; snippet: string; view: string | null };
export type MemoryState = {
  preferences: Preference[];
  index: { sources: MemorySource[]; total: number };
  results: MemoryHit[];
  notes_shared: boolean;
};

/** Average tokens in a table cell: 640, 1.2k, 12k. */
export function compactTokens(tokens: number): string {
  if (tokens < 1000) return String(Math.round(tokens));
  const thousands = tokens / 1000;
  return `${thousands < 10 ? Number(thousands.toFixed(1)) : Math.round(thousands)}k`;
}

/**
 * The 14-day sparkline of average cost per request, drawn from zero to the dearest day
 * inside a `width` × `height` box (4 px of headroom, 2 px of floor). Days without
 * requests are gaps; a single day draws a level line.
 */
export function trendPath(days: TrendDay[], width: number, height: number): string {
  const points = days.map((day, index) => ({ ...day, index })).filter((day) => day.requests > 0);
  if (!points.length) return "";
  const max = Math.max(...points.map((day) => day.avg_cost_usd));
  const y = (cost: number) => Number((height - 2 - (max > 0 ? cost / max : 0) * (height - 6)).toFixed(1));
  if (points.length === 1) return `M0,${y(points[0].avg_cost_usd)} L${width},${y(points[0].avg_cost_usd)}`;
  const x = (index: number) => Number(((index / Math.max(1, days.length - 1)) * width).toFixed(1));
  return points.map((day, at) => `${at ? "L" : "M"}${x(day.index)},${y(day.avg_cost_usd)}`).join(" ");
}

/** The first and last days with requests, for "$0.0061 → $0.0019". */
export function trendRange(days: TrendDay[]): { from: number; to: number } | null {
  const active = days.filter((day) => day.requests > 0);
  return active.length ? { from: active[0].avg_cost_usd, to: active.at(-1)!.avg_cost_usd } : null;
}

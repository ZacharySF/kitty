/**
 * Rika's wire types (docs/rika-design.md; served by kitty.web_rika) and the pure
 * helpers the bar, its toasts and the history drawer share.
 */
import type { AgentNow } from "../agentSkills";

export type Id = number | string;
export type Tier = "fast" | "standard" | "deep";
export type AgentStatus = "working" | "blocked" | "idle" | "done";

export type RikaMessage = {
  id: Id;
  conversation_id?: Id;
  role: "user" | "rika";
  text: string;
  tier?: Tier;
  model?: string;
  cost_usd?: number | null;
  /** On a rika reply: the user message it answers. */
  reply_to?: Id | null;
  /** A reply paused for an approval is pending until its turn ends. */
  status?: "pending" | "done" | "failed";
  created_at: string;
};
export type RikaAction = {
  id: Id;
  /** The rika reply whose turn ran it. */
  message_id?: Id | null;
  tool: string;
  summary: string;
  reversible: boolean;
  status: "done" | "undone" | "failed";
  created_at?: string;
};
export type RikaApproval = {
  id: Id;
  message_id?: Id | null;
  tool: string;
  summary: string;
  detail: string;
  cost_usd?: number | null;
  status?: "pending" | "approved" | "rejected" | "expired";
  created_at?: string;
};
export type RikaAgent = {
  id: string;
  kind: string;
  name: string;
  status: AgentStatus;
  task: string;
  line: string;
  /** Epoch ms the current state began. */
  since: number;
  where: { herdr_pane?: string; tern_pane?: string };
  /** What it uses now (agent skills); null while "Show agent skills and activity" is off for its kind. */
  now?: AgentNow | null;
};

export type Entry =
  | ({ kind: "message" } & RikaMessage)
  | ({ kind: "action" } & RikaAction)
  | ({ kind: "approval" } & RikaApproval);

export type StreamEvent =
  | { type: "token"; message_id: Id; text: string; reply_to?: Id | null }
  | { type: "message"; message: RikaMessage }
  | { type: "action"; action: RikaAction }
  | { type: "approval"; approval: RikaApproval }
  | { type: "tier"; message_id: Id; tier: Tier; model: string; reply_to?: Id | null }
  | { type: "agents"; agents: RikaAgent[] }
  /** A client action: Rika opened a view (also recorded as an `open_view` action). */
  | { type: "navigate"; view: string; params?: Record<string, unknown> };

export type History = { items: Entry[]; has_more?: boolean; next_before?: string | null };

/** How many agents get a chip each before the bar shows one count instead. */
export const CHIP_LIMIT = 4;
const URGENCY: AgentStatus[] = ["blocked", "working", "idle", "done"];

export type Chips = { kind: "agents"; agents: RikaAgent[] } | { kind: "count"; count: number; status: AgentStatus };

/** Q19 B: a chip per running agent; past four, one count in the most urgent state. */
export function agentChips(agents: RikaAgent[], limit = CHIP_LIMIT): Chips {
  if (agents.length <= limit) return { kind: "agents", agents };
  const status = URGENCY.find((state) => agents.some((agent) => agent.status === state)) ?? "idle";
  return { kind: "count", count: agents.length, status };
}

/** The stream stays on the page's own origin; only the scheme changes. */
export function rikaSocketUrl(location: { protocol: string; host: string }): string {
  return `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/rika/stream`;
}

export const entryKey = (entry: Pick<Entry, "kind" | "id">) => `${entry.kind}:${entry.id}`;
const stamp = (entry: Entry) => {
  const time = Date.parse(entry.created_at ?? "");
  return Number.isNaN(time) ? Number.POSITIVE_INFINITY : time;
};

/**
 * Merges history pages and live events oldest first. A later copy of an entry replaces
 * the earlier one in place, so a streamed reply keeps its spot when its final text lands.
 */
export function mergeEntries(current: Entry[], incoming: Entry[]): Entry[] {
  const byKey = new Map(current.map((entry) => [entryKey(entry), entry]));
  for (const entry of incoming) {
    const previous = byKey.get(entryKey(entry));
    byKey.set(entryKey(entry), previous ? { ...previous, ...entry, created_at: previous.created_at ?? entry.created_at } as Entry : entry);
  }
  return [...byKey.values()]
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => stamp(a.entry) - stamp(b.entry) || a.index - b.index)
    .map(({ entry }) => entry);
}

/** An approval's cost estimate; no estimate draws no cost line. */
export function costLabel(usd: number | null | undefined): string {
  if (typeof usd !== "number" || !Number.isFinite(usd)) return "";
  if (usd === 0) return "free";
  if (usd < 0.01) return "under $0.01";
  return `$${usd.toFixed(2)}`;
}

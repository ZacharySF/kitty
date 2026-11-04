/**
 * Rika's wire types (docs/rika-design.md; served by kitty.web_rika) and the pure
 * helpers the bar, its toasts and the history drawer share.
 */
import type { AgentNow } from "../agentSkills";
import type { Guarantee } from "../agents/agents";

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
  /** How a request reached Rika: typed, spoken (Q6 C) or a brain dump's transcript (Q7 A). */
  source?: "typed" | "voice" | "dump";
  /** Q13: what the reply cost and the tool path it took; null on user messages. */
  usage?: RikaUsage | null;
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
  /** The mediation guarantee of its pane's newest launch (Settings → Agents); absent from older servers. */
  guarantee?: Guarantee;
};

/** Q7 A: the six areas a brain dump's plan is grouped by, in order. */
export const PLAN_AREAS = ["notes", "tasks", "calendar", "cards", "jobs", "agents"] as const;
export type PlanArea = (typeof PLAN_AREAS)[number];
export type PlanItem = {
  id: string;
  area: PlanArea;
  title: string;
  detail: string;
  /** The words of the dump the item came from. */
  source: string;
  /** Tool calls it makes, e.g. two cards. */
  count: number;
  /** It acts outside the app (starting a worker); approving the plan approves it. */
  outward: boolean;
  /** At most one item per plan asks one question with two or three choices. */
  question: { text: string; choices: string[] } | null;
  choice: number | null;
  picked?: boolean | null;
  result: { status: "done" | "failed" | "skipped"; summary: string; links: string[] } | null;
};
export type RikaPlan = {
  id: Id;
  /** The rika reply that carries it. */
  message_id: Id;
  /** The brain dump it plans. */
  request_id: Id;
  items: PlanItem[];
  /** Seconds of talk. */
  duration: number;
  /** Q8 A: the dump note keeping the transcript and links to what the plan made. */
  note: { page_id: number; title: string } | null;
  status: "pending" | "running" | "applied" | "dismissed" | "failed";
  created_at: string;
  decided_at?: string | null;
};

export type VoiceMode = "command" | "dump";
export type Segment = { start: number; end: number; text: string };

export type Entry =
  | ({ kind: "message" } & RikaMessage)
  | ({ kind: "action" } & RikaAction)
  | ({ kind: "approval" } & RikaApproval)
  | ({ kind: "plan" } & RikaPlan)
  | RikaDraft;

type VoiceEvent = { session: string; mode: VoiceMode };
export type StreamEvent =
  | { type: "token"; message_id: Id; text: string; reply_to?: Id | null }
  | { type: "message"; message: RikaMessage }
  | { type: "action"; action: RikaAction }
  | { type: "approval"; approval: RikaApproval }
  | { type: "plan"; plan: RikaPlan }
  | { type: "tier"; message_id: Id; tier: Tier; model: string; reply_to?: Id | null }
  | { type: "agents"; agents: RikaAgent[] }
  /** A client action: Rika opened a view (also recorded as an `open_view` action). */
  | { type: "navigate"; view: string; params?: Record<string, unknown> }
  /** On-device speech to text, answering this page only (kitty.core.rika.voice). */
  | ({ type: "voice.state"; state: "loading" | "listening" | "finishing" | "cancelled" } & VoiceEvent)
  | ({ type: "voice.partial"; text: string } & VoiceEvent)
  | ({ type: "voice.segment"; index: number } & Segment & VoiceEvent)
  | ({ type: "voice.final"; text: string; segments: Segment[]; duration: number } & VoiceEvent)
  | ({ type: "voice.error"; message: string } & VoiceEvent)
  /** The Whisper model downloading on first use. */
  | { type: "voice.model"; model: string; state: "downloading" | "ready" | "failed"; received: number; total: number; message?: string }
  /** Q16: a reply draft changed (drafted, edited, scheduled, sent, cancelled, failed). */
  | { type: "mail"; mail: RikaDraft };

/** Plan items by area, in the areas' order; empty areas are left out. */
export function planGroups(items: PlanItem[]): { area: PlanArea; items: PlanItem[] }[] {
  return PLAN_AREAS.map((area) => ({ area, items: items.filter((item) => item.area === area) })).filter((group) => group.items.length);
}

/** The plan's approve button: all of it, or only the ticked items. */
export function approveLabel(picked: number, total: number): string {
  return picked === total ? `Approve all ${total}` : `Approve ${picked} of ${total}`;
}

/** m:ss (h:mm:ss past an hour) of talk. */
export function talkClock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor(whole / 60) % 60).padStart(2, "0");
  const ss = String(whole % 60).padStart(2, "0");
  return whole >= 3600 ? `${Math.floor(whole / 3600)}:${mm}:${ss}` : `${Math.floor(whole / 60)}:${ss}`;
}

/** "42% of 145 MB" for the voice model download. */
export function downloadLabel(received: number, total: number): string {
  const percent = total > 0 ? Math.min(100, Math.floor((received / total) * 100)) : 0;
  return `${percent}% of ${Math.round(total / 1_000_000)} MB`;
}

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

/** Q13 A: a reply's tokens and tool path, recorded by the routing memory. */
export type RikaUsage = {
  tokens_in: number | null;
  tokens_cached: number | null;
  tokens_out: number | null;
  path: string[];
  signature: string;
  remembered: boolean;
  shortcut: boolean;
};

export type MailStatus = "draft" | "scheduled" | "sent" | "cancelled" | "discarded" | "failed";
/** Q16 B: a reply Rika drafted; history and the stream carry it as an entry of kind `mail`. */
export type RikaDraft = {
  kind: "mail";
  id: string;
  mail_id?: string | null;
  message_id?: Id | null;
  thread_id?: string | null;
  to: string;
  subject: string;
  body: string;
  reply_kind: "scheduling" | "confirmation" | "thanks" | "question" | "personal" | "automated";
  status: MailStatus;
  auto: boolean;
  send_after: string | null;
  sent_at: string | null;
  error: string | null;
  original?: { sender: string; sender_email: string; subject: string; received_at: string; snippet: string } | null;
  created_at?: string;
  updated_at?: string;
};

/** Readout dollars: sub-cent requests keep four places ("$0.0002"); nothing spent is "$0". */
export function usd(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  if (value === 0) return "$0";
  if (value < 0.0001) return "under $0.0001";
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4).replace(/(\.\d\d\d*?)0+$/, "$1")}`;
}

export type Readout = { tier: Tier | "direct"; label: string; detail: string };
const count = (value: number) => value.toLocaleString("en");

/**
 * Q13 A: the line under a reply. A deterministic or shortcut reply ran its tools with no
 * model: "Direct · <path> · 0 tokens · $0"; the demo's scripted replies say they are
 * scripted. The morning brief has no readout (its cost, if any, shows as before). A model
 * reply: its tier, the remembered tool path when the routing memory chose it, tokens in
 * (cached), out and the reported cost.
 */
export function usageReadout(message: Pick<RikaMessage, "tier" | "model" | "cost_usd" | "usage">): Readout | null {
  const usage = message.usage;
  if (!usage || message.model === "brief") return null;
  if (message.model === "demo") return { tier: "direct", label: "Demo", detail: "Scripted demo reply · 0 tokens · $0" };
  const path = usage.path.join(" → ");
  if (message.model === "deterministic" || message.model === "shortcut" || usage.shortcut)
    return { tier: "direct", label: "Direct", detail: [path, "0 tokens", "$0"].filter(Boolean).join(" · ") };
  const tier = message.tier ?? "fast";
  const parts = [
    usage.remembered && path ? `remembered path ${path}` : "",
    usage.tokens_in == null ? "" : `${count(usage.tokens_in)} in${usage.tokens_cached ? ` (${count(usage.tokens_cached)} cached)` : ""}`,
    usage.tokens_out == null ? "" : `${count(usage.tokens_out)} out`,
    usd(message.cost_usd),
  ];
  return { tier, label: tier[0].toUpperCase() + tier.slice(1), detail: parts.filter(Boolean).join(" · ") };
}

/** Whole seconds left before a held send goes out; 0 once it is not held. */
export function holdLeft(draft: Pick<RikaDraft, "status" | "send_after">, now: number): number {
  const at = Date.parse(draft.send_after ?? "");
  if (draft.status !== "scheduled" || Number.isNaN(at)) return 0;
  return Math.max(0, Math.ceil((at - now) / 1000));
}
export const holdClock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

/** The kind line of a mail item: auto-send kinds say so. */
export function mailLine(draft: Pick<RikaDraft, "auto" | "status" | "reply_kind">): string {
  if (!draft.auto) return `Reply · ${draft.reply_kind}`;
  return `${draft.status === "sent" ? "Auto-sent" : "Auto-send"} · ${draft.reply_kind}`;
}

/**
 * Agent mediation (kitty.web_agents, kitty.core.agent_sessions): the shapes Settings → Agents,
 * the guarantee badges and the agent-start notice read.
 */

/** What a launch guarantees (paper §13's kernel matrix): every route closed, journaled API only, or nothing. */
export type Guarantee = "full" | "attribution" | "none";
export const GUARANTEES: readonly Guarantee[] = ["full", "attribution", "none"];
/** kitty.core.agent_sessions.GUARANTEE_LABELS: the herdr tab labels use the same words. */
export const GUARANTEE_LABELS: Record<Guarantee, string> = { full: "Full", attribution: "Attribution only", none: "Not mediated" };
/** Tooltips: what each level means, in a line. */
export const GUARANTEE_HINTS: Record<Guarantee, string> = {
  full: "Every route to rika!'s data goes through its API, and every access is journaled",
  attribution: "API accesses are journaled and writes carry the session; other routes may be open",
  none: "No launch exchange: no token, nothing journaled",
};

/** A value from the API as a guarantee; anything unknown is "none". */
export const guaranteeOf = (value: unknown): Guarantee => (value === "full" || value === "attribution" ? value : "none");

export type SessionState = "pending" | "confined" | "unconfined" | "refused" | "expired" | "ended";
export const STATE_LABELS: Record<SessionState, string> = {
  pending: "Starting", confined: "Confined", unconfined: "Unconfined", refused: "Refused", expired: "Expired", ended: "Ended",
};
/** States whose agent may still be running: End applies to them. */
export const isLive = (state: SessionState) => state === "pending" || state === "confined" || state === "unconfined";

export type HostGuarantee = { kernel: string; abi: number; guarantee: Guarantee; closed: string[]; open: string[] };

export type AgentSession = {
  id: string;
  kind: string;
  name: string;
  host: "herdr" | "tern" | "direct";
  pane: string | null;
  cwd: string;
  state: SessionState;
  guarantee: Guarantee;
  abi: number | null;
  pid: number | null;
  message: string | null;
  policy_version: number | null;
  created_at: string;
  confined_at: string | null;
  ended_at: string | null;
  report: Record<string, unknown> | null;
  /** It holds (or held) an agent-session token. */
  token: boolean;
};
/** One row of GET /api/agents' `sessions`. */
export type SessionListing = AgentSession & { accesses: number; held: boolean };

export type JournalRow = {
  id: number;
  tool: string;
  op: "read" | "write";
  purpose: string;
  block_id: string | null;
  seq: number;
  decision: "allow" | "deny";
  mode: "enforce" | "observe";
  policy_version: number;
  at: string;
};
export type PlantedChange = { path: string; change: "created" | "changed" | "removed" };
/** GET /api/agents/session?id=&before= (and the hold/end responses). */
export type SessionDetail = { session: AgentSession; journal: JournalRow[]; next: number | null; held: boolean; planted: PlantedChange[] };

export type Retention = {
  floor: number | null;
  horizon_seq: number;
  reasons: Record<string, number>;
  cap: number;
  over_cap: { reason: string; seq: number }[];
  journal_rows: number;
};

/** GET /api/agents. */
export type AgentsOverview = {
  host: HostGuarantee;
  sessions: SessionListing[];
  policy: { version: number; mode: "enforce" | "observe"; source: string };
  versions: { version: number; created_at: string | null; mode: "enforce" | "observe" }[];
  retention: Retention;
  purposes: Record<string, string>;
  tools: Record<string, { op: "read" | "write"; scope: string }>;
};

export type Flip = {
  id: number;
  session: string;
  agent: string;
  name: string;
  tool: string;
  op: "read" | "write";
  purpose: string;
  block_id: string | null;
  seq: number;
  at: string;
  mode: "enforce" | "observe";
  policy_version: number;
  recorded: "allow" | "deny";
  draft: "allow" | "deny";
};
/** POST /api/agents/policy/simulate. */
export type Simulation = {
  replayed: number;
  flips: Flip[];
  counts: { allow_to_deny: number; deny_to_allow: number };
  total: number;
  truncated: boolean;
  sessions: string[];
  /** The draft's mode would act on its decisions (enforce), or only journal them (observe). */
  enforced: boolean;
};

/** `mediation` on POST /api/terminal/agents and /api/terminal/tern/agent. */
export type Mediation = { session: string; state: SessionState | null; guarantee: Guarantee; message: string };

/** Apply is offered only for the exact source a simulation last replayed, and only when it changes the policy in force. */
export const canApply = (source: string, simulated: string | null, active: string) => simulated !== null && simulated === source && source !== active;

/** The next page of a journal joined to what is shown (newest first, never a row twice). */
export function moreJournal(shown: JournalRow[], older: JournalRow[]): JournalRow[] {
  const seen = new Set(shown.map((row) => row.id));
  return [...shown, ...older.filter((row) => !seen.has(row.id))];
}

/** "8 replayed · 2 allow → deny · 1 deny → allow". */
export function flipSummary(simulation: Pick<Simulation, "replayed" | "counts">): string {
  const { allow_to_deny: denied, deny_to_allow: allowed } = simulation.counts;
  const parts = [`${simulation.replayed.toLocaleString("en")} replayed`];
  if (denied) parts.push(`${denied.toLocaleString("en")} allow → deny`);
  if (allowed) parts.push(`${allowed.toLocaleString("en")} deny → allow`);
  if (!denied && !allowed) parts.push("no decision changes");
  return parts.join(" · ");
}

/** The first 8 characters of a block or session ID (the full one is the tooltip). */
export const shortId = (id: string | null) => (id ? id.slice(0, 8) : "");

/** `path` relative to the session's folder when it lies inside it (the full path is the tooltip). */
export const within = (path: string, folder: string) => {
  const base = folder.endsWith("/") ? folder : `${folder}/`;
  return path.startsWith(base) ? path.slice(base.length) : path;
};

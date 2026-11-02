export interface Job {
  id: number;
  company: string;
  title: string;
  location: string | null;
  is_remote: boolean;
  tier: string | null;
  tracks: string[];
  url: string;
  eligibility: string;
  eligibility_reason: string | null;
  queue_state: string;
  status: string | null;
  favorite: boolean;
  posted_at: string | null;
  deadline_at: string | null;
  fill_state: string | null;
  is_active: boolean;
  closed_at: string | null;
  freshness: string;
  verification_status: string;
  verification_reason: string | null;
  verification_checked_at: string | null;
}
export interface JobDetail extends Job {
  description: string | null;
  my_notes: string | null;
  tier_reason: string | null;
  fill_blocker: string | null;
  resume: { id?: number; label: string; file?: string } | null;
  application: {
    status: string;
    history: {
      id: number;
      from_status?: string | null;
      source?: string;
      state?: string;
      undoes_event_id?: number | null;
      to_status?: string;
      status?: string;
      occurred_at: string;
      evidence?: string | null;
    }[];
  } | null;
}
export interface Resume {
  id: number;
  label: string;
  track: string | null;
  available: boolean;
  is_default: boolean;
}
export interface ResumeVersion {
  id: string;
  track: string;
  status: string;
  is_default: boolean;
  created_at: string;
}
export interface Source {
  name: string;
  enabled: boolean;
  status: string;
  last_run: string | null;
  error: string | null;
  jobs_seen: number | null;
}
export interface Discovery {
  running: boolean;
  error: string | null;
  result: string | null;
}
export type ModuleId = "applyai" | "workspace" | "terminal";
export interface ModuleInfo {
  id: ModuleId;
  label: string;
  description: string;
}
export interface ModulesState {
  /** Active for this process. Never changes until kitty restarts. */
  enabled: ModuleId[];
  /** Saved selection; applies on the next restart. */
  configured: ModuleId[];
  available: ModuleInfo[];
  restart_required: boolean;
  /** Present when the pending modules.yaml cannot be read; the running app is unaffected. */
  error?: string;
}
export interface Bootstrap {
  demo: boolean;
  modules: ModulesState;
  tracks?: { name: string; label: string; weekly_target: number }[];
  counts?: {
    jobs: number;
    queued: number;
    applications: number;
    favorites: number;
  };
  weekly_total?: number;
  streak_days?: number;
  today?: string;
}

export interface TaskRecord {
  id: string;
  kind: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  finished_at: string | null;
  result: unknown;
  error: string | null;
}

/** A located problem in a build file (line and column are 1-based). */
export interface Problem {
  message: string;
  line: number;
  column: number;
  path: string;
  severity: "error" | "warning";
}

/** A refused request: the HTTP status, and the build file's problems when it has any. */
export class ApiError extends Error {
  status: number;
  problems: Problem[];
  constructor(message: string, status: number, problems: Problem[] = []) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

/** One workspace section in `GET /api/brain`; `group` is its navigation group (Plan, Study, Job search). */
export interface BrainSection {
  id: string;
  label: string;
  group: string;
  records: number;
  edits: number;
}
/** A record (a thing that exists) or an edit (one change to it); every edit has `at`. */
export interface BrainNode {
  id: string;
  kind: "record" | "edit";
  section: string;
  type: string;
  label: string;
  at: string | null;
  parent: string | null;
}
export interface BrainData {
  generated_at: string;
  sections: BrainSection[];
  nodes: BrainNode[];
  total: { records: number; edits: number };
  truncated: boolean;
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Kitty-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await response
    .json()
    .catch(() => ({ error: `Request failed (${response.status})` }));
  if (!response.ok)
    throw new ApiError(data.error || `Request failed (${response.status})`, response.status, Array.isArray(data.problems) ? data.problems : []);
  return data as T;
}

export function safeUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function dateLabel(value: string | null): string {
  if (!value) return "Not provided";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

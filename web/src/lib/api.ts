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
  /** Also true while an add-on module install, update or removal waits for a restart. */
  restart_required: boolean;
  /** Present when the pending modules.yaml cannot be read; the running app is unaffected. */
  error?: string;
  /** Add-on modules installed from GitHub releases. */
  external: ExternalModules;
}
/** An add-on module installed from a GitHub repository's latest release. */
export interface ExternalModule {
  id: string;
  name: string;
  version: string;
  /** `owner/repo` */
  repo: string;
  description: string;
  license: string;
  kind: "python" | "process";
  installed_at: string | null;
  sha256: string | null;
  status: "active" | "restart-to-install" | "restart-to-update" | "restart-to-remove" | "failed";
  /** The version running in this process; null when it is not loaded. */
  active_version: string | null;
  /** A plain sentence from the backend, e.g. "Restart rika! to finish installing." */
  message: string;
  /** Why loading failed, when `status` is "failed". */
  error?: string;
}
/** A page an active add-on module adds to the navigation; fixed until rika! restarts. */
export interface ExternalView {
  module: string;
  version: string;
  id: string;
  label: string;
  /** A free icon name; unknown names draw a generic icon. */
  icon: string;
  group: "plan" | "study" | "jobs" | "settings";
  /** Placed first in its group. */
  first: boolean;
  ui: "iframe" | "element";
  /** Element views: the same-origin script that defines `tag`. */
  script?: string;
  tag?: string;
}
export interface ExternalModules {
  installed: ExternalModule[];
  /** Only modules active in this process. */
  views: ExternalView[];
  token: { configured: boolean };
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

// The per-launch UI secret (src/kitty/web_auth.py; docs/platform-design.md, "UI secret"):
// every /api request and WebSocket upgrade carries it. The desktop shell injects it into
// each rika! window before page scripts run; in a browser the page trades the one-time
// `?code=` of the link kitty-web printed for it and keeps it in this tab's sessionStorage.
// A header and not a cookie: a 127.0.0.1 cookie would reach every port on 127.0.0.1.
export const SECRET_HEADER = "X-Kitty-Secret";
const SECRET_STORAGE = "kitty.ui-secret";

declare global {
  interface Window {
    __KITTY_UI_SECRET__?: string;
  }
}

let session: Promise<string | null> | undefined;

function storedSecret(): string | null {
  try {
    return sessionStorage.getItem(SECRET_STORAGE);
  } catch {
    return null;
  }
}

async function establishSession(): Promise<string | null> {
  if (typeof window === "undefined" || typeof location === "undefined") return null;
  const injected = window.__KITTY_UI_SECRET__;
  if (typeof injected === "string" && injected) return injected;
  const url = new URL(location.href);
  const code = url.searchParams.get("code");
  if (code === null) return storedSecret();
  // The code works once; it leaves the address bar (and so history and reloads) at once.
  url.searchParams.delete("code");
  history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
  try {
    const response = await fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Kitty-Request": "1" },
      body: JSON.stringify({ code }),
      cache: "no-store",
    });
    const data = response.ok ? await response.json() : null;
    if (typeof data?.secret === "string") {
      try {
        sessionStorage.setItem(SECRET_STORAGE, data.secret);
      } catch {
        /* Storage may be unavailable; the secret still serves this page. */
      }
      return data.secret;
    }
  } catch {
    /* A failed exchange leaves the stored secret, if any. */
  }
  return storedSecret();
}

/** This window's UI secret, or null when it has none (then rika! answers 401). */
export function uiSecret(): Promise<string | null> {
  session ??= establishSession();
  return session;
}

/** The WebSocket subprotocols that carry the UI secret on an upgrade. */
export async function socketProtocols(): Promise<string[]> {
  const secret = await uiSecret();
  return secret ? ["kitty", `kitty-secret.${secret}`] : [];
}

/** A raw `fetch` of `/api${path}` with the UI secret, for bodies read as blobs. */
export async function apiFetch(
  path: string,
  init: Omit<RequestInit, "headers"> & { headers?: Record<string, string> } = {},
): Promise<Response> {
  const secret = await uiSecret();
  const headers = { ...init.headers, ...(secret ? { [SECRET_HEADER]: secret } : {}) };
  return fetch(`/api${path}`, { ...init, headers });
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await apiFetch(path, {
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

/**
 * A single-use link to `GET /api${path}` for navigations that cannot carry a header (a PDF
 * in a new window, a download): valid for one minute, once, for exactly this path.
 */
export async function apiLink(path: string): Promise<string> {
  return (await api<{ url: string }>("/session/link", { path: `/api${path}` })).url;
}

/** Open `GET /api${path}` in a new window, as a plain link to it did. */
export async function openApi(path: string): Promise<void> {
  window.open(await apiLink(path), "_blank", "noopener,noreferrer");
}

/** Download `GET /api${path}`, as an `<a href download>` to it did. */
export async function downloadApi(path: string, filename = ""): Promise<void> {
  const link = document.createElement("a");
  link.href = await apiLink(path);
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}

/** An object URL for an image under /api (an `<img>` cannot carry the secret's header). */
export async function apiObjectUrl(path: string): Promise<string> {
  const response = await apiFetch(path, { cache: "no-store" });
  if (!response.ok) throw new ApiError(`Request failed (${response.status})`, response.status);
  return URL.createObjectURL(await response.blob());
}

/**
 * `onclick` for an `<a href="/api/…">` (a download, or a PDF with `target="_blank"`): the
 * navigation cannot carry the secret's header, so it follows a single-use link instead.
 */
export function followApiLink(event: MouseEvent): void {
  const link = event.currentTarget as HTMLAnchorElement;
  const url = new URL(link.href, location.href);
  if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return;
  event.preventDefault();
  const path = `${url.pathname.slice("/api".length)}${url.search}`;
  const follow = link.target === "_blank" ? openApi(path) : downloadApi(path, link.getAttribute("download") ?? "");
  follow.catch(() => {
    /* As a failed plain download: nothing opens. */
  });
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

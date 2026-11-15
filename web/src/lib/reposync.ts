// Settings → Devices → GitHub backup: a push-only copy of the notes in a private GitHub
// repository the owner names. Unlike device sync, this copy leaves the owner's devices.
import { api } from "./api.ts";
import { relativeTime } from "./devices.ts";

export type RunResult = "pushed" | "unchanged" | "offline" | "refused" | "rejected" | "error";
export interface BackupRun {
  at: string;
  commit: string | null;
  pages: number;
  result: RunResult;
  error: string | null;
}
export interface BackupState {
  /** False in the demo workspace; `reason` says why. */
  enabled: boolean;
  reason: string | null;
  /** "owner/name", or null until the owner connects one. */
  repo: string | null;
  /** What GitHub last said: true private, false public, null not known yet. */
  private: boolean | null;
  paused: boolean;
  /** Why rika! stopped by itself (the repository became public, the push was rejected). */
  problem: string | null;
  running: boolean;
  /** The last run that reached GitHub, and how many pages the copy held then. */
  synced_at: string | null;
  pages: number;
  last_run: BackupRun | null;
  next_run: string | null;
}

/** The status line of a connected backup: "Synced 4 minutes ago · 12 pages". */
export function backupStatus(state: BackupState, now: number = Date.now()): string {
  if (state.running) return "Syncing…";
  if (state.problem) return state.problem;
  if (state.paused) return "Paused";
  const failed = state.last_run && (state.last_run.result === "offline" || state.last_run.result === "error") ? state.last_run : null;
  const synced = state.synced_at
    ? `Synced ${relativeTime(state.synced_at, now)} · ${state.pages} ${state.pages === 1 ? "page" : "pages"}`
    : "Not synced yet";
  return failed ? `${synced} · last try failed: ${failed.error ?? "unknown error"}` : synced;
}

/** True when the status line reports something wrong. */
export function backupBad(state: BackupState): boolean {
  return !state.running && (!!state.problem || state.last_run?.result === "offline" || state.last_run?.result === "error");
}

export const loadBackup = () => api<BackupState>("/reposync");
export const connectBackup = (repo: string) => api<BackupState>("/reposync/repo", { repo: repo.trim() });
export const backupNow = () => api<BackupState>("/reposync/now", {});
export const pauseBackup = (paused: boolean) => api<BackupState>("/reposync/pause", { paused });
export const disconnectBackup = () => api<BackupState>("/reposync/disconnect", {});

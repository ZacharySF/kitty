// Settings → Backup: one archive of the whole workspace (kitty.core.backup), previewed and
// restored into a new folder. History (earlier page versions) and Sync (paired devices) are
// different things and are named apart on the page.
import { api } from "./api.ts";

export interface Excluded { category: string; reason: string }
export interface FileTotals { count: number; bytes: number }
export interface LastBackup { path: string; bytes: number; created_at: string; counts: Record<string, number>; files: FileTotals }
export interface BackupState {
  /** The suggested folder, outside the workspace (~/rika-backups). */
  folder: string;
  /** This workspace's folder; a restore never writes it. */
  root: string;
  last: LastBackup | null;
}
export interface Made extends LastBackup { excluded: Excluded[]; missing: string[] }
export interface Preview {
  archive: string;
  format: number;
  app_version: string | null;
  created_at: string | null;
  counts: Record<string, number>;
  files: FileTotals;
  excluded: Excluded[];
  missing: string[];
  warnings: string[];
  /** Why a restore would be refused; empty when it can go ahead. */
  problems: string[];
  can_restore: boolean;
  into: string | null;
}
export interface Restored {
  path: string;
  counts: Record<string, number>;
  files: FileTotals;
  warnings: string[];
  /** The desktop command that opens the copy, and the one for the website and CLI. */
  open: string;
  open_cli: string;
}

/** "12.4 MB" */
export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(1)} ${units[unit]}`;
}

/** "17 Nov 2026, 14:02" in the reader's locale. */
export function stamp(value: string): string {
  const time = Date.parse(value);
  return Number.isNaN(time) ? value : new Date(time).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const loadBackup = () => api<BackupState>("/backup");
export const createBackup = (folder: string) => api<Made>("/backup/create", { folder: folder.trim() });
export const previewBackup = (archive: string, into: string) =>
  api<Preview>("/backup/preview", { archive: archive.trim(), into: into.trim() || null });
export const restoreBackup = (archive: string, into: string) =>
  api<Restored>("/backup/restore", { archive: archive.trim(), into: into.trim() });

/**
 * The module picker's rules (first run and Settings → Modules share ModulePicker.svelte):
 * which modules are on before anything is touched, what a selection downloads and how the
 * total reads. Pure, so Node tests can import it; the live state is modulePicker.svelte.ts.
 */
import type { ModuleId, ModuleInfo, PackInfo, PacksState } from "./api";

/** A download size: "1.6 MB", "275 MB", "1.2 GB". */
export function megabytes(bytes: number | null): string {
  if (bytes === null) return "";
  if (!bytes) return "0 MB";
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  const value = Math.max(0.1, bytes / 1e6);
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} MB`;
}

export const percent = (progress: { received: number; total: number }) =>
  progress.total ? Math.min(100, Math.round((progress.received / progress.total) * 100)) : 0;

/** Here already: carried by this build or installed for it. */
export const packReady = (pack: PackInfo | undefined) => pack?.state === "built-in" || pack?.state === "installed";

/** The module's packs as the server lists them (none until the list answered). */
export function packsOf(item: ModuleInfo, packs: PacksState | null): PackInfo[] {
  return item.packs.flatMap((id) => packs?.packs.find((pack) => pack.id === id) ?? []);
}

/** Nothing to download: no pack, or every pack is here. */
export function moduleReady(item: ModuleInfo, packs: PacksState | null): boolean {
  return item.packs.every((id) => packReady(packs?.packs.find((pack) => pack.id === id)));
}

/** Can be turned on: ready, or this release's signed list offers each missing pack. */
export function moduleObtainable(item: ModuleInfo, packs: PacksState | null): boolean {
  return item.packs.every((id) => {
    const pack = packs?.packs.find((entry) => entry.id === id);
    return packReady(pack) || ["available", "outdated", "downloading"].includes(pack?.state ?? "");
  });
}

/** The packs a module would download, in order: missing ones this release's list offers. */
export function downloadsOf(item: ModuleInfo, packs: PacksState | null): PackInfo[] {
  return packsOf(item, packs).filter((pack) => ["available", "outdated", "downloading"].includes(pack.state));
}

/**
 * After an update: the packs an older version left (a pack is tied to one build) that are
 * still in use and so download again: the saved modules' and those no module owns (Themes,
 * Animations). A module that is off gets its pack again when it is turned on.
 */
export function renewals(available: ModuleInfo[], configured: readonly ModuleId[], packs: PacksState | null): PackInfo[] {
  const owned = new Set(available.flatMap((item) => item.packs));
  const wanted = new Set(available.filter((item) => configured.includes(item.id)).flatMap((item) => item.packs));
  return (packs?.packs ?? []).filter((pack) => pack.state === "outdated" && (wanted.has(pack.id) || !owned.has(pack.id)));
}

/**
 * The selection before and after answers and switches: a module is on when the user set
 * it so; otherwise when `base` has it (first run: every module without a download) or an
 * answer wants it and it can be had. Canonical order, as the server keeps it.
 */
export function preselect(
  available: ModuleInfo[], packs: PacksState | null, base: readonly ModuleId[], wanted: readonly ModuleId[],
  overrides: Partial<Record<ModuleId, boolean>> = {},
): ModuleId[] {
  return available
    .filter((item) => overrides[item.id] ?? (base.includes(item.id) || (wanted.includes(item.id) && moduleObtainable(item, packs))))
    .map((item) => item.id);
}

/** First run's base: every module that needs no download. */
export function withoutDownload(available: ModuleInfo[], packs: PacksState | null): ModuleId[] {
  return available.filter((item) => moduleReady(item, packs)).map((item) => item.id);
}

export interface Totals {
  on: number;
  of: number;
  /** Packs the selection would download, each once. */
  downloads: PackInfo[];
  /** Their summed size; `unsized` counts those whose size the list does not give. */
  bytes: number;
  unsized: number;
}

export function totals(available: ModuleInfo[], selection: readonly ModuleId[], packs: PacksState | null): Totals {
  const downloads: PackInfo[] = [];
  for (const item of available)
    if (selection.includes(item.id))
      for (const pack of downloadsOf(item, packs)) if (!downloads.includes(pack)) downloads.push(pack);
  return {
    on: selection.length,
    of: available.length,
    downloads,
    bytes: downloads.reduce((sum, pack) => sum + (pack.size ?? 0), 0),
    unsized: downloads.filter((pack) => pack.size === null).length,
  };
}

/** "3 of 7 on, 31.2 MB to download". */
export function totalLine(total: Totals): string {
  const on = `${total.on} of ${total.of} on`;
  if (!total.downloads.length) return `${on}, nothing to download`;
  if (!total.unsized) return `${on}, ${megabytes(total.bytes)} to download`;
  const rest = total.unsized === 1 ? "1 download" : `${total.unsized} downloads`;
  return `${on}, ${total.bytes ? `${megabytes(total.bytes)} plus ` : ""}${rest}`;
}

export const sameSelection = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((id) => b.includes(id));

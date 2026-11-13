import type { ModuleId } from "./api";

/**
 * Modules active in this process, taken from bootstrap. Saving a new selection
 * never changes this: it only takes effect after kitty restarts. `missing` lists the
 * packs (downloads) those modules need that this process lacks.
 */
export const activeModules = $state<{ enabled: ModuleId[]; missing: string[] }>({ enabled: [], missing: [] });

const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((id, index) => id === b[index]);

export function setActiveModules(enabled: ModuleId[], missing: string[] = []) {
  if (!same(enabled, activeModules.enabled)) activeModules.enabled = [...enabled];
  if (!same(missing, activeModules.missing)) activeModules.missing = [...missing];
}

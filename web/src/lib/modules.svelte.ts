import type { ExternalView, ModuleId } from "./api";

/**
 * Modules active in this process, taken from bootstrap: the built-ins and the add-on
 * views. Saving a new selection or installing an add-on never changes this: it only
 * takes effect after kitty restarts. `missing` lists the packs (downloads) those
 * modules need that this process lacks.
 */
export const activeModules = $state<{ enabled: ModuleId[]; views: ExternalView[]; missing: string[] }>({ enabled: [], views: [], missing: [] });

const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((id, index) => id === b[index]);

export function setActiveModules(enabled: ModuleId[], views: ExternalView[] = [], missing: string[] = []) {
  if (!same(enabled, activeModules.enabled)) activeModules.enabled = [...enabled];
  if (JSON.stringify(views) !== JSON.stringify(activeModules.views)) activeModules.views = [...views];
  if (!same(missing, activeModules.missing)) activeModules.missing = [...missing];
}

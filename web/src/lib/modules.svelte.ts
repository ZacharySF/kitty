import type { ModuleId } from "./api";

/**
 * Modules active in this process, taken from bootstrap. Saving a new selection
 * never changes this: it only takes effect after kitty restarts.
 */
export const activeModules = $state<{ enabled: ModuleId[] }>({ enabled: [] });

export function setActiveModules(enabled: ModuleId[]) {
  const next = [...enabled];
  const current = activeModules.enabled;
  if (next.length !== current.length || next.some((id, index) => id !== current[index]))
    activeModules.enabled = next;
}

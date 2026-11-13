import type { ExternalView, ModuleId } from "./api";

/**
 * Modules active in this process, taken from bootstrap: the built-ins and the add-on
 * views. Saving a new selection or installing an add-on never changes this: it only
 * takes effect after kitty restarts.
 */
export const activeModules = $state<{ enabled: ModuleId[]; views: ExternalView[] }>({ enabled: [], views: [] });

export function setActiveModules(enabled: ModuleId[], views: ExternalView[] = []) {
  const next = [...enabled];
  const current = activeModules.enabled;
  if (next.length !== current.length || next.some((id, index) => id !== current[index]))
    activeModules.enabled = next;
  if (JSON.stringify(views) !== JSON.stringify(activeModules.views)) activeModules.views = [...views];
}

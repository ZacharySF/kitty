import type { ThemeId, ThemeModule } from "./contract";

/** The active theme's motif module, reactive for ThemeLayer, EmptyState and Loader. */
class ThemeMotif {
  id = $state<ThemeId | null>(null);
  module = $state.raw<ThemeModule | null>(null);

  set(id: ThemeId | null, module: ThemeModule | null): void {
    this.id = id;
    this.module = module;
  }
}

export const themeMotif = new ThemeMotif();

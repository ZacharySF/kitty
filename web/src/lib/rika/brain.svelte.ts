import { api } from "../api";

/**
 * What Rika thinks with on this computer (`/api/rika/brain`, kitty.core.rika.brain), shared
 * by Settings → AI → Rika's brain and the thinking chip beside Rika's field.
 */
export type Tier = "fast" | "standard" | "deep";
export type Thinking = "off" | "low" | "medium" | "high" | "max";
export type Brain = {
  provider: "openrouter" | "omp";
  models: Record<Tier, string>;
  thinking: Thinking;
  thinking_levels: Thinking[];
  defaults: Record<Tier, string>;
  omp_installed: boolean;
};

export const THINKING_LABELS: Record<Thinking, string> = {
  off: "Off", low: "Low", medium: "Medium", high: "High", max: "Max",
};

class BrainState {
  current = $state<Brain | null>(null);
  #loading: Promise<Brain> | null = null;

  /** The saved choice, fetched once per page (`refresh` reads it again). */
  load(refresh = false): Promise<Brain> {
    if (!this.#loading || refresh) {
      this.#loading = api<Brain>("/rika/brain").then((found) => (this.current = found));
      this.#loading.catch(() => (this.#loading = null));
    }
    return this.#loading;
  }

  async save(change: Partial<Pick<Brain, "provider" | "models" | "thinking">>): Promise<Brain> {
    const base = this.current ?? (await this.load());
    const next = await api<Brain>("/rika/brain", {
      provider: change.provider ?? base.provider,
      models: change.models ?? base.models,
      thinking: change.thinking ?? base.thinking,
    });
    this.current = next;
    return next;
  }
}

export const brain = new BrainState();

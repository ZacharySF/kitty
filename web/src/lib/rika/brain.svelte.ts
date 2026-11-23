import { api } from "../api";
import type { Catalog } from "./models";

/**
 * What Rika thinks with on this computer (`/api/rika/brain`, kitty.core.rika.brain), shared
 * by Settings → AI → Rika's brain and the Model and Thinking chips beside Rika's field, and
 * the models that brain can use (`/api/rika/models`).
 */
export type Tier = "fast" | "standard" | "deep";
export type Thinking = "off" | "low" | "medium" | "high" | "max";
export type Brain = {
  provider: "openrouter" | "omp";
  models: Record<Tier, string>;
  /** Rika's pinned model on every tier; "" is Auto (the tier models). */
  model: string;
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
  catalog = $state<Catalog | null>(null);
  #loading: Promise<Brain> | null = null;
  #listing: Promise<Catalog> | null = null;

  /** The saved choice, fetched once per page (`refresh` reads it again). */
  load(refresh = false): Promise<Brain> {
    if (!this.#loading || refresh) {
      this.#loading = api<Brain>("/rika/brain").then((found) => (this.current = found));
      this.#loading.catch(() => (this.#loading = null));
    }
    return this.#loading;
  }

  /** The brain's models, fetched once per provider (`refresh` reads them again). */
  models(refresh = false): Promise<Catalog> {
    if (!this.#listing || refresh) {
      this.#listing = api<Catalog>("/rika/models").then((found) => (this.catalog = found));
      this.#listing.catch(() => (this.#listing = null));
    }
    return this.#listing;
  }

  async save(change: Partial<Pick<Brain, "provider" | "models" | "model" | "thinking">>): Promise<Brain> {
    const base = this.current ?? (await this.load());
    const provider = change.provider ?? base.provider;
    const next = await api<Brain>("/rika/brain", {
      provider,
      models: change.models ?? base.models,
      // Another provider's catalog never holds the pinned model: the server drops it.
      ...(change.model !== undefined ? { model: change.model } : provider === base.provider ? { model: base.model } : {}),
      thinking: change.thinking ?? base.thinking,
    });
    this.current = next;
    // Another provider lists other models; other tier models share other levels (Auto's).
    if (this.catalog && (next.provider !== base.provider || change.models)) void this.models(true).catch(() => {});
    return next;
  }
}

export const brain = new BrainState();

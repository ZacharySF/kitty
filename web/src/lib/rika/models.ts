import type { Thinking } from "./brain.svelte";

/**
 * The models Rika's brain can use (`GET /api/rika/models`, kitty.core.rika.brain.catalog)
 * and the choices the Model and Thinking chips beside her field offer from them.
 */
export type CatalogModel = { id: string; label: string; thinking: Thinking[] };
export type Catalog = { provider: "openrouter" | "omp"; models: CatalogModel[]; auto: Thinking[] };
export type ModelOption = { value: string; label: string; short: string; hint: string };

/** A model's name as its chip shows it: "Anthropic: Claude Opus 5.5" reads "Opus 5.5". */
export function shortModelLabel(label: string): string {
  const named = label.includes(": ") ? label.slice(label.indexOf(": ") + 2) : label;
  return named.replace(/^Claude\s+(?=\S)/, "");
}

/** Auto first, then every model of the catalog; a pinned model the catalog no longer lists stays choosable. */
export function modelOptions(catalog: Catalog | null, pinned: string): ModelOption[] {
  const options: ModelOption[] = [{ value: "", label: "Auto", short: "Auto", hint: "by request" }];
  for (const model of catalog?.models ?? []) {
    options.push({ value: model.id, label: model.label, short: shortModelLabel(model.label), hint: model.id });
  }
  if (pinned && !catalog?.models.some((model) => model.id === pinned)) {
    options.push({ value: pinned, label: pinned, short: pinned.slice(pinned.lastIndexOf("/") + 1), hint: "not listed now" });
  }
  return options;
}

/** The levels the chosen model accepts; Auto's are what its tier models share; an unknown model may take any. */
export function thinkingChoices(catalog: Catalog | null, model: string, levels: Thinking[]): Thinking[] {
  if (!catalog) return levels;
  if (!model) return catalog.auto.length ? catalog.auto : levels;
  return catalog.models.find((entry) => entry.id === model)?.thinking ?? levels;
}

/** `level` if it is among `choices`, else the nearest one, the lower on a tie. */
export function nearestThinking(level: Thinking, choices: Thinking[]): Thinking {
  if (choices.includes(level) || !choices.length) return level;
  const order: Thinking[] = ["off", "low", "medium", "high", "max"];
  const at = order.indexOf(level);
  let best = choices[0];
  for (const choice of choices) {
    const distance = Math.abs(order.indexOf(choice) - at);
    const bestDistance = Math.abs(order.indexOf(best) - at);
    if (distance < bestDistance || (distance === bestDistance && order.indexOf(choice) < order.indexOf(best))) best = choice;
  }
  return best;
}

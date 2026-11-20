import { api } from "../api";
import type { CareerCounts } from "./rail";

/**
 * The Career rail's live counts, shared by every pane: the last answer stays on screen while
 * the next loads, so moving between Career pages never blanks them. Read when a Career page
 * draws its rail (and after its changes); never on a timer.
 */
export const careerCounts = $state<{ value: CareerCounts | null }>({ value: null });

let pending: Promise<void> | null = null;

export function refreshCareerCounts(): Promise<void> {
  pending ??= api<{ counts: CareerCounts }>("/career/summary")
    .then((answer) => { careerCounts.value = answer.counts; })
    .catch(() => { /* The rail keeps its last counts; the page reports its own errors. */ })
    .finally(() => { pending = null; });
  return pending;
}

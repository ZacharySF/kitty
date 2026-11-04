// Proposed effects (src/kitty/core/effects.py): what agents and connected apps ask you to
// approve. Approving binds to the exact digest the server showed; executing re-checks every
// precondition, so a target that changed in between is refused rather than acted on. A kind
// with no executor (submit_application) is only ever a recorded decision.
import { api } from "./api.ts";
import type { Actor } from "./live.ts";

export type EffectState = "proposed" | "approved" | "denied" | "executed" | "refused" | "expired";
export interface EffectEvent {
  state: EffectState;
  actor: Actor;
  digest: string | null;
  detail: { reason?: string; changed?: string[]; options?: Record<string, unknown> } | null;
  at: string;
  expires_at: string | null;
}
export interface Effect {
  id: string;
  kind: string;
  title: string;
  payload: Record<string, unknown>;
  preconditions: Record<string, unknown>;
  preview: Record<string, unknown> | null;
  digest: string;
  actor: Actor;
  state: EffectState;
  created_at: string;
  expires_at: string;
  approval_expires_at: string | null;
  /** False for a proposal-only kind: approving it records your decision and nothing runs. */
  executable: boolean;
  /** What you do yourself for a proposal-only kind. */
  handoff: string | null;
  events: EffectEvent[];
}
export interface EffectsState {
  pending: Effect[];
  recent: Effect[];
}
export interface Decision extends EffectsState {
  effect: Effect;
  notice?: string | null;
  result?: unknown;
}

/** Fired after any decision so every open list refreshes. */
export const EFFECTS_EVENT = "kitty-effects-changed";

export function loadEffects(): Promise<EffectsState> {
  return api<EffectsState>("/effects");
}

function announce<T extends EffectsState>(next: T): T {
  window.dispatchEvent(new CustomEvent(EFFECTS_EVENT, { detail: next }));
  return next;
}

/** Approve this exact digest; an executable effect then runs at once (re-checked again). */
export async function approveEffect(effect: Effect): Promise<Decision> {
  const approved = await api<Decision>("/effects/approve", { id: effect.id, digest: effect.digest });
  if (!effect.executable) return announce(approved);
  return announce(await api<Decision>("/effects/execute", { id: effect.id, digest: effect.digest }));
}

export async function denyEffect(effect: Effect): Promise<Decision> {
  return announce(await api<Decision>("/effects/deny", { id: effect.id }));
}

/** Preview fields worth a line of their own, in this order; the rest stays in Details. */
const SHOWN = ["action", "company", "title", "apply_url", "page", "type", "text", "reason", "child_count"] as const;
const LABELS: Record<string, string> = {
  action: "Does", company: "Company", title: "Role", apply_url: "Application", page: "Page",
  type: "Block type", text: "Text", reason: "Why", child_count: "Children",
};

export function previewLines(effect: Effect): { label: string; value: string }[] {
  const preview = effect.preview ?? {};
  return SHOWN.filter((key) => preview[key] !== undefined && preview[key] !== null && preview[key] !== "")
    .map((key) => ({ label: LABELS[key], value: String(preview[key]) }));
}

/** One precondition's value as text: the value the effect was proposed against. */
export function conditionText(value: unknown): string {
  if (value === null || value === undefined) return "none";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

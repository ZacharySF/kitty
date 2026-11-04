// Who made a block edit (src/kitty/core/blocks/actor.py), as History and the undo plan show it:
// the person, an agent session (told apart by the client running it), a connected app, rika!
// itself or a paired device. Each look is an icon (icons.ts) and a theme role, so it follows every
// theme; ActorName.svelte mixes text in that role toward ink to stay AA everywhere.
import type { Actor } from "./live";

export type ActorLookId = "user" | "agent" | "omp" | "claude" | "codex" | "rika" | "client" | "system" | "peer";
export type ActorLook = { id: ActorLookId; icon: string; role: `--br-${string}`; label: string };

export const ACTOR_LOOKS: Record<ActorLookId, ActorLook> = {
  user: { id: "user", icon: "actor-user", role: "--br-structure", label: "You" },
  agent: { id: "agent", icon: "actor-agent", role: "--br-signal", label: "Agent" },
  omp: { id: "omp", icon: "actor-omp", role: "--br-signal", label: "omp agent" },
  claude: { id: "claude", icon: "actor-claude", role: "--br-caution", label: "Claude agent" },
  codex: { id: "codex", icon: "actor-codex", role: "--br-reference", label: "Codex agent" },
  rika: { id: "rika", icon: "actor-rika", role: "--br-alternate", label: "rika! assistant" },
  client: { id: "client", icon: "actor-client", role: "--br-go", label: "Connected app" },
  system: { id: "system", icon: "actor-system", role: "--br-dim", label: "rika! system" },
  peer: { id: "peer", icon: "actor-peer", role: "--br-mute", label: "Paired device" },
};

/** An agent's sub-kind from the name its MCP client (or rika!) gave it. */
function agentLook(actor: Actor): ActorLook {
  const name = (actor.name ?? "").toLowerCase();
  if (name.startsWith("rika") || actor.session?.startsWith("rika:")) return ACTOR_LOOKS.rika;
  if (name.includes("claude")) return ACTOR_LOOKS.claude;
  if (name.includes("codex")) return ACTOR_LOOKS.codex;
  if (/\bomp\b|oh-my-pi/.test(name)) return ACTOR_LOOKS.omp;
  return ACTOR_LOOKS.agent;
}

export function actorLook(actor: Actor | null | undefined): ActorLook {
  if (!actor) return ACTOR_LOOKS.user;
  return actor.kind === "agent" ? agentLook(actor) : ACTOR_LOOKS[actor.kind] ?? ACTOR_LOOKS.user;
}

/** The inline style that gives an `.actor` element its colour. */
export const actorStyle = (actor: Actor | null | undefined) => `--actor: var(${actorLook(actor).role})`;

export function actorName(actor: Actor): string {
  if (actor.kind === "user") return "You";
  if (actor.kind === "agent") return `Agent · ${actor.name ?? "unnamed"}`;
  if (actor.kind === "client") return `${actor.name ?? "Connected app"} (connected app)`;
  if (actor.kind === "system") return `rika! · ${actor.name ?? "system"}`;
  return actor.name ?? "Paired device";
}

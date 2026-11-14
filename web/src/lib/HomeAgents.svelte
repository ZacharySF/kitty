<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "./api";
  import HomeWidget from "./HomeWidget.svelte";
  import type { AgentState, TerminalActivity } from "./terminal";
  import type { View } from "./workspace";

  /**
   * Q20: what each herdr workspace agent is doing now, how long it has been in
   * that state (since this widget first saw it) and Approve for a blocked one,
   * which opens the herdr session where the prompt waits. Reading never starts herdr.
   */
  /** `placeholder`: home's edit mode, where the widget shows its title even with no session running. */
  let { id, onview, placeholder = false }: { id: string; onview: (view: View) => unknown; placeholder?: boolean } = $props();
  const POLL_MS = 3000;
  let activity = $state<TerminalActivity | null>(null);
  let now = $state(Date.now());
  /** When each workspace was first seen in its current state. */
  let since = $state(new Map<string, { status: AgentState; at: number }>());

  const spaces = $derived(activity?.running ? activity.workspaces : []);
  const working = $derived(spaces.filter((space) => space.status === "working").length);
  const blocked = $derived(spaces.filter((space) => space.status === "blocked").length);

  async function load() {
    try {
      const next = await api<TerminalActivity>("/terminal/activity");
      const at = Date.now();
      since = new Map(
        next.workspaces.map((space) => {
          const seen = since.get(space.id);
          return [space.id, seen?.status === space.status ? seen : { status: space.status, at }];
        }),
      );
      activity = next;
      now = at;
    } catch {
      /* Quiet on home: the widget stays hidden until herdr answers; the herdr view says why. */
    }
  }
  function elapsed(spaceId: string) {
    const seconds = Math.max(0, Math.floor((now - (since.get(spaceId)?.at ?? now)) / 1000));
    return seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m` : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }
  function doing(space: TerminalActivity["workspaces"][number]) {
    return space.agents.length ? space.agents.map((agent) => `${agent.name} ${agent.status}`).join(" · ") : `no agent · ${space.status}`;
  }

  onMount(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  });
</script>

<!-- Design review: no status chatter on home. The widget shows only while herdr has workspaces. -->
{#if spaces.length}<HomeWidget {id} title="Agents" eyebrow={`${working} working · ${blocked} ${blocked === 1 ? "needs" : "need"} you`}>
    <ul>
      {#each spaces as space (space.id)}
        <li data-state={space.status} class:focused={space.focused}>
          <span class="dot" aria-hidden="true"></span>
          <span class="what">
            <strong>{space.label}</strong>
            <small>{doing(space)}</small>
          </span>
          {#if space.status === "blocked"}
            <button class="pill primary" aria-label={`Approve ${space.label} in herdr`} onclick={() => onview("herdr")}>Approve</button>
          {:else}
            <span class="time">{space.status === "done" ? "done" : elapsed(space.id)}</span>
          {/if}
        </li>
      {/each}
    </ul>
</HomeWidget>{:else if placeholder}<HomeWidget {id} title="Agents" eyebrow="Shows while a session runs" />{/if}

<style>
  ul {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    --state: var(--subtle);
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    padding: 6px 4px;
    border-radius: 8px;
  }
  li[data-state="working"] {
    --state: var(--c4);
  }
  li[data-state="blocked"] {
    --state: var(--warning);
    background: color-mix(in srgb, var(--warning) 8%, transparent);
  }
  li[data-state="done"] {
    --state: var(--success);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--state);
    color: var(--state);
  }
  [data-state="working"] .dot {
    animation: pulse 1.6s ease-out infinite;
  }
  @keyframes pulse {
    0% {
      box-shadow: 0 0 0 0 color-mix(in srgb, currentColor 60%, transparent);
    }
    100% {
      box-shadow: 0 0 0 7px transparent;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    [data-state="working"] .dot {
      animation: none;
    }
  }
  .what {
    display: grid;
    min-width: 0;
  }
  strong {
    font: 400 17px/1.3 var(--font-ui);
  }
  .focused strong {
    color: var(--violet);
  }
  small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--muted);
    font-size: 14px;
  }
  .time {
    color: var(--muted);
    font-size: 14px;
    font-variant-numeric: tabular-nums;
  }
</style>

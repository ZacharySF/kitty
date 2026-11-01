<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "./api";
  import TernPanes from "./TernPanes.svelte";
  import type { TerminalActivity } from "./terminal";

  /** Q3: herdr's own agent states per workspace. Reading never starts herdr. */
  const POLL_MS = 3000;
  let activity = $state<TerminalActivity | null>(null);
  let error = $state("");

  async function load() {
    try {
      activity = await api<TerminalActivity>("/terminal/activity");
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }

  onMount(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  });
</script>

<div class="activity" role="region" aria-label="Agent activity">
  {#if error}
    <p class="activity-note" role="alert">{error}</p>
  {:else if !activity}
    <p class="activity-note" role="status">Reading herdr's agent states…</p>
  {:else if !activity.running}
    <p class="activity-note" role="status">kitty!'s herdr session is not running. Open the terminal to start it.</p>
  {:else if !activity.workspaces.length}
    <p class="activity-note" role="status">No herdr workspaces yet.</p>
  {:else}
    <ul aria-label="herdr agent activity">
      {#each activity.workspaces as space (space.id)}
        <li class="workspace" class:focused={space.focused}>
          <span class="name">{space.label}</span>
          <span class="state" data-state={space.status}>{space.status}</span>
          {#each space.agents as agent (agent.pane)}
            <span class="agent"><span>{agent.name}</span> <span class="state" data-state={agent.status}>{agent.status}</span></span>
          {/each}
        </li>
      {/each}
    </ul>
  {/if}
  <TernPanes />
</div>

<style>
  .activity {
    height: 100%;
    overflow: auto;
    padding: 8px 4px;
  }
  ul {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .workspace {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 6px 12px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 40%, transparent);
    font: 11px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .workspace.focused { border-color: color-mix(in srgb, var(--ink) 30%, transparent); }
  .name { color: var(--ink); font-weight: 600; }
  .agent { color: var(--muted); }
  .state { color: var(--muted); }
  .state::before {
    content: "";
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-right: 5px;
    border-radius: 999px;
    background: currentColor;
    vertical-align: 1px;
  }
  .state[data-state="working"] { color: var(--br-reference, #9cc7f0); }
  .state[data-state="blocked"] { color: var(--br-caution, #e8c872); }
  .state[data-state="done"] { color: var(--br-go, #a8d5b5); }
  .state[data-state="idle"] { color: var(--muted); }
  .state[data-state="unknown"] { color: var(--br-dim, #8d8d8d); }
  .activity-note {
    margin: 0;
    color: var(--muted);
    font: 11px var(--font-mono);
    letter-spacing: 0.06em;
  }
</style>

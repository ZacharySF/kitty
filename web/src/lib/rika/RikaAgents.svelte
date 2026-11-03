<script lang="ts">
  import { onMount } from "svelte";
  import { rika } from "./session.svelte";

  /**
   * Q19 A: the history drawer's Agents section, one live card per worker agent Rika sees
   * in herdr and Tern: state, task, last output line, how long it has been in that state,
   * and Open window, which focuses its Tern window.
   */
  let now = $state(Date.now());
  const clock = (ms: number) => {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(seconds / 60);
    return minutes >= 60
      ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
      : `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
  };
  onMount(() => {
    const tick = window.setInterval(() => (now = Date.now()), 1000);
    return () => window.clearInterval(tick);
  });
</script>

<section class="agents" aria-label="Agents">
  {#if !rika.agents.length}
    <p class="note" role="status">No worker agents are running. Ask Rika to start one.</p>
  {:else}
    {#each rika.agents as agent (agent.id)}
      <article class="rika-agent" data-state={agent.status} aria-label={`${agent.name} · ${agent.status}`}>
        <header>
          <strong>{agent.name}</strong>
          <span class="where">{agent.where.tern_pane ? `tern · ${agent.where.tern_pane}` : agent.where.herdr_pane ? `herdr · ${agent.where.herdr_pane}` : agent.kind}</span>
          <span class="rika-chip" data-state={agent.status}><i class="rika-dot"></i>{agent.status}</span>
        </header>
        <p class="task">{agent.task || "No task yet"}</p>
        <footer>
          <code title={agent.line}>{agent.line || "No output yet"}</code>
          {#if agent.status === "done"}<span class="off" aria-hidden="true">—</span>{:else}<time aria-label={`${agent.status} for ${clock(now - agent.since)}`}>{clock(now - agent.since)}</time>{/if}
        </footer>
        <div class="acts"><button class="pill" class:primary={agent.status === "blocked"} onclick={() => rika.open(agent)}>Open window</button></div>
      </article>
    {/each}
  {/if}
</section>

<style>
  .agents {
    display: grid;
    gap: 10px;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 12.5px;
  }
  .rika-agent {
    display: grid;
    gap: 8px;
    min-width: 0;
    padding: 11px 12px 10px;
    border: 1px solid var(--hairline);
    border-top: 2px solid color-mix(in srgb, var(--tone) 70%, transparent);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-raised) 55%, transparent);
  }
  .rika-agent[data-state="blocked"] {
    border-color: color-mix(in srgb, var(--tone) 45%, var(--hairline));
    box-shadow: 0 0 22px color-mix(in srgb, var(--tone) 12%, transparent);
  }
  header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 1px 8px;
    align-items: center;
  }
  strong {
    font: 600 14px var(--font-ui);
  }
  header .rika-chip {
    grid-row: 1 / 3;
    grid-column: 2;
  }
  .where {
    color: var(--subtle);
    font: 10px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .task {
    margin: 0;
    font-size: 12.5px;
    line-height: 1.35;
  }
  footer {
    display: flex;
    gap: 8px;
    align-items: baseline;
  }
  code {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    color: var(--muted);
    font: 10.5px var(--font-mono);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  time,
  .off {
    font: 500 11px var(--font-mono);
  }
  .off {
    color: var(--subtle);
  }
  .acts {
    display: flex;
    justify-content: end;
  }
  .acts .pill {
    min-height: 26px;
    padding: 3px 11px;
    font-size: 10.5px;
  }
</style>

<script lang="ts">
  import { onMount } from "svelte";
  import AgentSkillList from "../AgentSkillList.svelte";
  import AgentSkillsSwitch from "../AgentSkillsSwitch.svelte";
  import type { AgentKind } from "../agentSkills";
  import { agentSkills } from "../agentSkills.svelte";
  import NowChip from "../NowChip.svelte";
  import GuaranteeBadge from "../agents/GuaranteeBadge.svelte";
  import { rika } from "./session.svelte";

  /**
   * Q19 A: the history drawer's Agents section, one live card per worker agent Rika sees
   * in herdr and Tern: state, task, last output line, how long it has been in that state,
   * and Open window, which focuses its Tern window. Agent skills (docs/agent-skills-design.md
   * Q1 D) add a "now: skill · tool" chip and a Skills (N) button that lists the agent's
   * skills here in the drawer, with a link to the All agent skills view.
   */
  let now = $state(Date.now());
  /** The agent whose Skills list the section shows instead of the cards. */
  let showing = $state<string | null>(null);
  const chosen = $derived(rika.agents.find((agent) => agent.id === showing));
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
  $effect(() => {
    if (rika.agents.length) void agentSkills.load();
  });
  function allSkills(kind: string) {
    agentSkills.focus = (["omp", "claude", "codex"].includes(kind) ? kind : "omp") as AgentKind;
    showing = null;
    rika.closeDrawer();
    rika.navigate("agent-skills");
  }
</script>

<section class="agents" aria-label="Agents">
  {#if chosen}
    {@const entry = agentSkills.kind(chosen.kind)}
    <div class="skills" aria-label={`${chosen.name} agent skills`}>
      <div class="skills-head">
        <button class="pill" onclick={() => (showing = null)}>← Agents</button>
        <strong>{chosen.name} · agent skills</strong>
        {#if entry?.enabled}<span class="count">{entry.skills.length}</span>{/if}
      </div>
      {#if chosen.now}<NowChip now={chosen.now} />{/if}
      {#if agentSkills.error}<p class="note" role="alert">{agentSkills.error}</p>{/if}
      {#if entry}
        {#if entry.enabled}
          <AgentSkillList kind={entry.kind} skills={entry.skills} used={chosen.now?.skill ? [chosen.now.skill] : []} canOpen={agentSkills.catalog?.can_open ?? false} />
          {#if agentSkills.status}<p class="note" role="status">{agentSkills.status}</p>{/if}
        {:else}
          <AgentSkillsSwitch {entry} />
        {/if}
      {:else if !agentSkills.error}
        <p class="note">{["omp", "claude", "codex"].includes(chosen.kind) ? "Reading the agent's skills…" : "rika! does not know this agent's skills."}</p>
      {/if}
      <button class="pill all" onclick={() => allSkills(chosen.kind)}>All agent skills →</button>
    </div>
  {:else if !rika.agents.length}
    <p class="note" role="status">No worker agents are running. Ask Rika to start one.</p>
  {:else}
    <div class="top"><button class="pill" onclick={() => allSkills(rika.agents[0].kind)}>All agent skills →</button></div>
    {#each rika.agents as agent (agent.id)}
      {@const entry = agentSkills.kind(agent.kind)}
      <article class="rika-agent" data-state={agent.status} aria-label={`${agent.name} · ${agent.status}`}>
        <header>
          <strong>{agent.name}</strong>
          <span class="sub"><span class="where">{agent.where.tern_pane ? `tern · ${agent.where.tern_pane}` : agent.where.herdr_pane ? `herdr · ${agent.where.herdr_pane}` : agent.kind}</span
            >{#if agent.guarantee}<GuaranteeBadge guarantee={agent.guarantee} />{/if}</span>
          <span class="rika-chip" data-state={agent.status}><i class="rika-dot"></i>{agent.status}</span>
        </header>
        {#if agent.now}<div class="now-row"><NowChip now={agent.now} /></div>{/if}
        <p class="task">{agent.task || "No task yet"}</p>
        <footer>
          <code title={agent.line}>{agent.line || "No output yet"}</code>
          {#if agent.status === "done"}<span class="off" aria-hidden="true">—</span>{:else}<time aria-label={`${agent.status} for ${clock(now - agent.since)}`}>{clock(now - agent.since)}</time>{/if}
        </footer>
        <div class="acts">
          <button class="pill" aria-label={`${agent.name}'s agent skills${entry?.enabled ? `, ${entry.skills.length}` : ""}`} onclick={() => (showing = agent.id)}
            >Skills{#if entry?.enabled}<i>{entry.skills.length}</i>{/if}</button>
          <button class="pill" class:primary={agent.status === "blocked"} onclick={() => rika.open(agent)}>Open window</button>
        </div>
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
  .sub {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 8px;
    min-width: 0;
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
    gap: 6px;
    justify-content: end;
  }
  .pill {
    min-height: 26px;
    padding: 3px 11px;
    font-size: 10.5px;
  }
  .pill i {
    margin-left: 5px;
    padding: 0 5px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 16%, transparent);
    font-family: var(--font-mono);
    font-style: normal;
  }
  .now-row {
    display: flex;
    min-width: 0;
  }
  .top {
    display: flex;
    justify-content: end;
  }
  .skills {
    display: grid;
    gap: 8px;
    justify-items: start;
  }
  .skills > :global(*) {
    max-width: 100%;
  }
  .skills > :global(.skill-list),
  .skills > :global(.opt-in) {
    width: 100%;
  }
  .skills-head {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
  }
  .skills-head strong {
    font: 600 13px var(--font-ui);
  }
  .count {
    margin-left: auto;
    color: var(--subtle);
    font: 10px var(--font-mono);
  }
  .all {
    justify-self: end;
  }
</style>

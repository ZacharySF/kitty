<script lang="ts">
  import { onMount } from "svelte";
  import AgentSkillList from "./AgentSkillList.svelte";
  import AgentSkillsSwitch from "./AgentSkillsSwitch.svelte";
  import { AGENT_KINDS, type AgentKind } from "./agentSkills";
  import { agentSkills } from "./agentSkills.svelte";
  import NowChip from "./NowChip.svelte";
  import { rika } from "./rika/session.svelte";
  import Select from "./ui/Select.svelte";

  /**
   * All agent skills (docs/agent-skills-design.md Q1 D, Q2 B): every coding agent kind's
   * skills, MCP tools and plugins, names only, whether or not an agent is running. The
   * running agents sit in a strip above with what each uses now. A Plan view beside Tern,
   * linked from each agent card's Skills list.
   */
  onMount(() => void agentSkills.load(true));
  const entry = $derived(agentSkills.kind(agentSkills.focus));
  const options = $derived(
    AGENT_KINDS.map((kind) => {
      const item = agentSkills.kind(kind);
      return { value: kind, label: item?.label ?? kind, count: item?.enabled ? item.skills.length : undefined };
    }),
  );
  const used = $derived(rika.agents.filter((agent) => agent.kind === agentSkills.focus).map((agent) => agent.now?.skill ?? "").filter(Boolean));
</script>

<section class="agent-skills" aria-label="All agent skills">
  <header>
    <h1>All agent skills</h1>
    <p>The agent skills, MCP tools and plugins each coding agent can use on this machine, and what the running agents use now.
      Names only; rika! never edits an agent's own configuration.</p>
  </header>

  {#if rika.agents.length}
    <div class="running" role="list" aria-label="Running agents">
      {#each rika.agents as agent (agent.id)}
        <div class="run" role="listitem" data-state={agent.status}>
          <i class="dot" aria-hidden="true"></i><strong>{agent.name}</strong><span class="mono">{agent.kind}</span>
          {#if agent.now}<NowChip now={agent.now} />{/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if agentSkills.error}<p class="error" role="alert">{agentSkills.error}</p>{/if}
  {#if !agentSkills.catalog}
    {#if !agentSkills.error}<p class="mono" role="status">Reading the agents' folders…</p>{/if}
  {:else if entry}
    <div class="kindbar">
      <Select label="Agent kind" kind="segments" value={agentSkills.focus} {options} onchange={(value) => (agentSkills.focus = value as AgentKind)} />
      <span class="mono">Read from the folders {entry.label} loads · names only</span>
    </div>
    <div class="panel"><AgentSkillsSwitch {entry} /></div>
    {#if entry.enabled}
      <div class="cols">
        <section class="panel" aria-label={`${entry.label} agent skills`}>
          <h2>Agent skills <small>{entry.skills.length}</small></h2>
          <AgentSkillList kind={entry.kind} skills={entry.skills} {used} canOpen={agentSkills.catalog.can_open} />
        </section>
        <section class="panel narrow" aria-label={`${entry.label} tools and plugins`}>
          <h2>Tools (MCP) <small>{entry.mcp.length}</small></h2>
          {#each entry.mcp as server (server.name)}
            <p class="item"><span>{server.name}</span>{#if !server.enabled}<small>off</small>{/if}</p>
          {:else}<p class="empty">No MCP servers configured.</p>{/each}
          <h2>Plugins <small>{entry.plugins.length}</small></h2>
          {#each entry.plugins as plugin (plugin.name)}
            <p class="item"><span>{plugin.name}</span>{#if !plugin.enabled}<small>off</small>{/if}</p>
          {:else}<p class="empty">No plugins.</p>{/each}
        </section>
      </div>
      <p class="mono status" role="status">{agentSkills.status || (agentSkills.catalog.can_open ? "Open or copy a skill's SKILL.md" : "Copy a skill's SKILL.md path; Open file needs the rika! desktop app")}</p>
    {/if}
  {/if}
</section>

<style>
  .agent-skills {
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
    min-width: 0;
    max-width: 1040px;
    margin: 0 auto;
  }
  h1 {
    margin: 0;
    font-size: 1.6rem;
  }
  header p {
    max-width: 70ch;
    margin: 0.3rem 0 0;
    color: var(--muted);
    line-height: 1.5;
  }
  .running {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .run {
    --tone: var(--muted);
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-width: 0;
    padding: 5px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-raised) 55%, transparent);
  }
  .run[data-state="working"] {
    --tone: var(--accent);
  }
  .run[data-state="blocked"] {
    --tone: var(--warning);
  }
  .run[data-state="done"] {
    --tone: var(--success);
  }
  .run strong {
    font: 600 12px var(--font-ui);
  }
  .dot {
    width: 7px;
    height: 7px;
    flex: 0 0 auto;
    border-radius: 999px;
    background: var(--tone);
  }
  .kindbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }
  .mono {
    margin: 0;
    color: var(--subtle);
    font: 10.5px var(--font-mono);
    letter-spacing: 0.06em;
  }
  .cols {
    display: grid;
    grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
    gap: 14px;
  }
  .panel {
    min-width: 0;
    padding: 12px 14px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-panel, 12px);
    background: color-mix(in srgb, var(--solid-panel) 40%, transparent);
  }
  h2 {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 12px 0 4px;
    color: var(--muted);
    font: 600 var(--fs-label, 12px) / 1.3 var(--font-ui);
  }
  h2:first-child {
    margin-top: 0;
  }
  h2 small {
    margin-left: auto;
    color: var(--subtle);
    font: 10px var(--font-mono);
  }
  .item {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 4px 0 8px;
  }
  .item span {
    font: 500 12px var(--font-mono);
    overflow-wrap: anywhere;
  }
  .item small {
    color: var(--subtle);
    font: 10px var(--font-mono);
    text-transform: uppercase;
  }
  .empty {
    margin: 4px 0;
    color: var(--subtle);
    font-size: 11.5px;
  }
  .error {
    margin: 0;
    color: var(--br-danger, var(--danger, #e5484d));
  }
  @media (max-width: 760px) {
    .cols {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>

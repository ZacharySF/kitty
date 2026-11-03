<script lang="ts">
  import type { AgentKind, AgentSkill } from "./agentSkills";
  import { agentSkills } from "./agentSkills.svelte";

  /**
   * One agent kind's skills (docs/agent-skills-design.md Q2 B, Q5): name and description,
   * a "now" tag on the ones running agents use, and Open file / Copy path per row. Open
   * file uses the desktop app's opener; a plain website offers Copy path only.
   */
  let { kind, skills, used = [], canOpen }: { kind: AgentKind; skills: AgentSkill[]; used?: string[]; canOpen: boolean } = $props();
</script>

{#if skills.length}
  <ul class="skill-list">
    {#each skills as skill (skill.name)}
      <li data-skill={skill.name} class:used={used.includes(skill.name)}>
        <span class="name">{skill.name}{#if used.includes(skill.name)}<em>now</em>{/if}</span>
        <span class="desc" title={skill.description}>{skill.description || "No description"}</span>
        <span class="end">
          {#if canOpen}<button type="button" class="row-button" aria-label={`Open ${skill.name}'s SKILL.md`} onclick={() => agentSkills.open(kind, skill.path)}>Open file</button>{/if}
          <button type="button" class="row-button" aria-label={`Copy the path of ${skill.name}'s SKILL.md`} title={skill.path} onclick={() => agentSkills.copy(skill.path)}>Copy path</button>
        </span>
      </li>
    {/each}
  </ul>
{:else}
  <p class="empty">No agent skills found in the folders this agent loads.</p>
{/if}

<style>
  .skill-list {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 2px 10px;
    padding: 7px 2px;
    border-top: 1px solid var(--hairline);
  }
  li:first-child {
    border-top: 0;
  }
  .name {
    color: var(--ink);
    font: 500 12px var(--font-mono);
    overflow-wrap: anywhere;
  }
  em {
    margin-left: 8px;
    padding: 0 6px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--violet) 22%, transparent);
    color: var(--ink);
    font: 500 9px/1.6 var(--font-mono);
    font-style: normal;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    vertical-align: 1px;
  }
  .desc {
    grid-column: 1;
    min-width: 0;
    overflow: hidden;
    color: var(--muted);
    font-size: 11.5px;
    line-height: 1.35;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .end {
    grid-column: 2;
    grid-row: 1 / 3;
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  :global(:root body) .row-button {
    min-height: 24px;
    height: auto;
    margin: 0;
    padding: 2px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 40%, transparent);
    color: var(--ink);
    font: 500 10.5px var(--font-ui);
    cursor: pointer;
  }
  :global(:root body) .row-button:hover {
    border-color: var(--br-lineHot);
    background: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .empty {
    margin: 4px 0;
    color: var(--subtle);
    font-size: 11.5px;
  }
</style>

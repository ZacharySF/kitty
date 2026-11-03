<script lang="ts">
  import type { KindCatalog } from "./agentSkills";
  import { agentSkills } from "./agentSkills.svelte";
  import { rika } from "./rika/session.svelte";

  /**
   * "Show agent skills and activity" for one agent kind (docs/agent-skills-design.md Q4 C):
   * off by default; until it is on, rika! opens none of that agent's files. Turning it on
   * or off reloads the running agents, so their now chips follow at once.
   */
  let { entry }: { entry: KindCatalog } = $props();
  async function flip() {
    await agentSkills.setEnabled(entry.kind, !entry.enabled);
    await rika.loadAgents();
  }
</script>

<div class="opt-in">
  <span>
    <strong>Show agent skills and activity</strong>
    <small>{entry.enabled
      ? `rika! reads ${entry.label}'s skill, MCP and plugin names and what it uses now. Nothing is stored or synced.`
      : `Off: rika! opens none of ${entry.label}'s files. Names only when on; never credentials.`}</small>
  </span>
  <button type="button" class="switch" role="switch" aria-checked={entry.enabled} aria-label={`Show ${entry.label} skills and activity`}
    onclick={flip}><i></i></button>
</div>

<style>
  .opt-in {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 0;
  }
  span {
    display: grid;
    gap: 1px;
  }
  strong {
    color: var(--ink);
    font: 500 13px var(--font-ui);
  }
  small {
    color: var(--muted);
    font-size: 11.5px;
    line-height: 1.4;
  }
  :global(:root body) .switch {
    position: relative;
    flex-shrink: 0;
    width: 34px;
    height: 20px;
    min-height: 0;
    margin: 0;
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    cursor: pointer;
    transition: background-color 160ms;
  }
  .switch i {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 14px;
    height: 14px;
    border-radius: 999px;
    background: var(--muted);
    transition: transform 160ms, background-color 160ms;
  }
  :global(:root body) .switch[aria-checked="true"] {
    background: color-mix(in srgb, var(--accent) 45%, transparent);
  }
  .switch[aria-checked="true"] i {
    transform: translateX(14px);
    background: var(--ink);
  }
  .switch:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .switch,
    .switch i {
      transition: none;
    }
  }
</style>

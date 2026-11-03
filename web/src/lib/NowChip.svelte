<script lang="ts">
  import { nowTitle, type AgentNow } from "./agentSkills";

  /**
   * The now chip (docs/agent-skills-design.md Q1): "now: lavish · read" on a live agent
   * card. The tooltip carries omp's intent and where the answer came from; "now: —" when
   * nothing answered yet.
   */
  let { now }: { now: AgentNow } = $props();
</script>

<span class="now-chip" class:off={!now.skill && !now.tool} title={nowTitle(now)} data-now
  >now: {#if now.skill || now.tool}{#if now.skill}<b>{now.skill}</b>{/if}{#if now.skill && now.tool}{" · "}{/if}{now.tool ?? ""}{:else}—{/if}</span
>

<style>
  .now-chip {
    display: inline-block;
    box-sizing: border-box;
    max-width: 100%;
    min-width: 0;
    overflow: hidden;
    padding: 1px 8px;
    border: 1px solid color-mix(in srgb, var(--violet) 45%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--violet) 12%, transparent);
    color: var(--ink);
    font: 500 10px/1.7 var(--font-mono);
    letter-spacing: 0.02em;
    text-overflow: ellipsis;
    white-space: pre;
  }
  b {
    color: var(--violet);
    font-weight: 600;
  }
  .off {
    border-color: var(--hairline);
    background: transparent;
    color: var(--subtle);
  }
</style>

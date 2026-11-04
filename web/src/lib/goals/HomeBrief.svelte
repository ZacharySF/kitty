<script lang="ts">
  import HomeWidget from "../HomeWidget.svelte";
  import BriefItems from "./BriefItems.svelte";
  import { morningBrief } from "./brief.svelte";

  /**
   * Q15 A: today's morning brief on home, at the top of the left rail: the first few
   * things that moved toward your goals overnight, one line each, so the rail's other
   * widgets stay in reach. The rest (and mail and notes) wait in the Rika bar's Morning
   * brief and on Goals. Absent until today's sweep has run.
   */
  let { id }: { id: string } = $props();
  const SHOWN = 3;
  $effect(() => morningBrief.start());
  const brief = $derived(morningBrief.brief);
  const time = $derived(brief ? new Date(brief.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : "");
</script>

{#if brief}
  <HomeWidget {id} title="Morning brief" eyebrow={time}>
    {#if brief.items.length}
      <BriefItems items={brief.items} limit={SHOWN} compact onopen={() => void morningBrief.seen()} />
      {#if brief.items.length > SHOWN}<a class="more" href="#goals">{brief.items.length - SHOWN} more in Goals</a>{/if}
    {:else}
      <p class="note">Nothing new for your goals today.</p>
    {/if}
  </HomeWidget>
{/if}

<style>
  .note {
    margin: 0;
    color: var(--subtle);
    font-size: 11px;
  }
  .more {
    display: inline-block;
    margin-top: 6px;
    color: var(--accent);
    font-size: 11.5px;
    text-decoration: none;
  }
  .more:hover {
    color: var(--ink);
  }
</style>

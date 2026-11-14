<script lang="ts">
  import HomeWidget from "./HomeWidget.svelte";
  import { api } from "./api";
  import type { View } from "./workspace";

  /** Applications on home: how many sit at each stage. */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();
  const STAGES = [["applied", "Applied"], ["oa", "Assessment"], ["interview", "Interview"], ["offer", "Offer"]] as const;
  let counts = $state<Record<string, number> | null>(null);
  let error = $state("");
  $effect(() => {
    void revision;
    api<{ columns: Record<string, unknown[]> }>("/pipeline")
      .then((result) => { counts = Object.fromEntries(Object.entries(result.columns).map(([stage, cards]) => [stage, cards.length])); error = ""; })
      .catch((cause) => (error = (cause as Error).message));
  });
</script>

<HomeWidget {id} title="Applications">
  {#snippet tail()}<button type="button" class="text-button" onclick={() => onview("pipeline")}>Open</button>{/snippet}
  {#if error}<p class="note" role="alert">{error}</p>
  {:else if counts}
    <dl>
      {#each STAGES as [stage, label] (stage)}
        <div><dd>{counts[stage] ?? 0}</dd><dt>{label}</dt></div>
      {/each}
    </dl>
  {/if}
</HomeWidget>

<style>
  dl {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin: 0;
  }
  dd {
    margin: 0;
    font: 600 28px/1.1 var(--font-ui);
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }
  dt,
  .note {
    color: var(--muted);
    font-size: 14px;
  }
  dt {
    margin-top: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>

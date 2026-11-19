<script lang="ts">
  import HomeWidget from "./HomeWidget.svelte";
  import { api } from "./api";
  import type { View } from "./workspace";

  /**
   * Applications on home: how many are active, large, beside a slim bar per stage as tall as
   * its share of the busiest stage, each stage in its own tile colour. With none, one line
   * and the way to find some.
   */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();
  const STAGES = [["applied", "Applied", 4], ["oa", "Assessment", 2], ["interview", "Interview", 5], ["offer", "Offer", 3]] as const;
  let counts = $state<Record<string, number> | null>(null);
  let error = $state("");
  $effect(() => {
    void revision;
    api<{ columns: Record<string, unknown[]> }>("/pipeline")
      .then((result) => { counts = Object.fromEntries(Object.entries(result.columns).map(([stage, cards]) => [stage, cards.length])); error = ""; })
      .catch((cause) => (error = (cause as Error).message));
  });
  const total = $derived(counts ? STAGES.reduce((sum, [stage]) => sum + (counts![stage] ?? 0), 0) : 0);
  const most = $derived(counts ? Math.max(1, ...STAGES.map(([stage]) => counts![stage] ?? 0)) : 1);
</script>

<HomeWidget {id} title="Applications" icon="applications" hero tint="var(--tile-4)" opens="applications" onopen={() => onview("pipeline")} art={total ? chart : undefined}>
  {#if error}<p class="note" role="alert">{error}</p>
  {:else if counts && !total}
    <p class="empty"><span class="note">No applications yet.</span><button type="button" class="pill" onclick={() => onview("jobs")}>Find jobs</button></p>
  {:else if counts}
    <p class="total"><strong class="figure">{total}</strong><span>active</span></p>
  {/if}
</HomeWidget>

{#snippet chart()}
  <dl>
    {#each STAGES as [stage, label, tile] (stage)}
      {@const count = counts?.[stage] ?? 0}
      <div style:--shade={`var(--tile-${tile})`}>
        <dd>{count}</dd>
        <span class="bar" aria-hidden="true">{#if count}<i data-stage={stage} style:height={`${(count / most) * 100}%`}></i>{/if}</span>
        <dt>{label}</dt>
      </div>
    {/each}
  </dl>
{/snippet}

<style>
  .total {
    display: grid;
    margin: 0;
    color: var(--muted);
    font-size: 15px;
  }
  dl {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    width: 100%;
    height: 100%;
    min-height: 150px;
    margin: 0;
  }
  /* Beside the figure, the chart takes the larger share of the tile. */
  @container (min-width: 360px) {
    dl {
      width: min(62cqi, 300px);
    }
  }
  dl div {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto;
    justify-items: center;
    gap: 8px;
    min-width: 0;
  }
  dd {
    margin: 0;
    font: 600 15px/1.2 var(--font-ui);
    font-variant-numeric: tabular-nums;
  }
  /* A slim track, rounded at both ends, filled from the foot. */
  .bar {
    display: flex;
    align-items: flex-end;
    width: 26px;
    min-height: 0;
    border-radius: var(--r-control);
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    overflow: hidden;
  }
  .bar i {
    width: 100%;
    border-radius: var(--r-control);
    background: var(--shade);
  }
  dt,
  .note {
    color: var(--muted);
    font-size: 14px;
  }
  /* A name a little wider than its column stays whole, centred over the gaps beside it. */
  dt {
    display: flex;
    justify-content: center;
    font-size: var(--fs-label);
    white-space: nowrap;
  }
  /* In a narrower tile (Rika in the middle) the stage names step down a size so they never
     touch; narrower still, a name that does not fit its column is cut short. */
  @container (max-width: 459.98px) {
    dt {
      font-size: 12px;
    }
  }
  @container (max-width: 419.98px) {
    dt {
      display: block;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }
  .empty {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 0;
  }
</style>

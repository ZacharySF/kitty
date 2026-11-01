<script lang="ts">
  import { api } from "../../api";
  import DueForecast from "./DueForecast.svelte";
  import MaturityDonut from "./MaturityDonut.svelte";
  import QueueCounts from "./QueueCounts.svelte";
  import RetentionCurve from "./RetentionCurve.svelte";
  import ReviewHeatmap from "./ReviewHeatmap.svelte";
  import type { MemoryStats } from "./types";

  /** Memory stats (Q41 A, B, C, F, G) for the selected deck scope; reloads with `revision`. */
  let { deckId, revision }: { deckId: number; revision: number } = $props();

  let stats = $state<MemoryStats | null>(null);
  let error = $state("");
  let request = 0;
  $effect(() => {
    revision;
    const query = new URLSearchParams({ timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" });
    if (deckId) query.set("deck_id", String(deckId));
    const current = ++request;
    error = "";
    api<MemoryStats>(`/flashcards/stats?${query}`).then(
      (result) => { if (current === request) stats = result; },
      (cause) => { if (current === request) error = cause instanceof Error ? cause.message : String(cause); },
    );
  });
</script>

{#if error}<p class="error" role="alert">{error}</p>{/if}
{#if stats}
  <div class="memory-stats">
    <QueueCounts queue={stats.queue} />
    <DueForecast forecast={stats.forecast} />
    <RetentionCurve retention={stats.retention} />
    <MaturityDonut maturity={stats.maturity} />
    <ReviewHeatmap heatmap={stats.heatmap} streak={stats.streak} />
  </div>
{:else if !error}<p role="status">Loading memory stats…</p>{/if}

<style>
  /* Queue, forecast and retention share the top row; the heatmap takes two thirds of the second. */
  .memory-stats {
    display: grid;
    grid-template-columns: 1fr;
    gap: 12px;
    margin: 1rem 0;
  }
  @media (min-width: 1000px) {
    .memory-stats {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .memory-stats > :global(:last-child) {
      grid-column: span 2;
    }
  }
  .error {
    color: var(--warning);
  }
</style>

<script lang="ts">
  import HomeWidget from "./HomeWidget.svelte";
  import { api } from "./api";
  import type { View } from "./workspace";

  /** Flashcards on home: how many are due, and one button to review them. */
  let { id, revision, onview }: { id: string; revision: number; onview: (view: View) => unknown } = $props();
  let due = $state<number | null>(null);
  let error = $state("");
  $effect(() => {
    void revision;
    api<{ stats: { due: number } }>("/flashcards")
      .then((result) => { due = result.stats.due; error = ""; })
      .catch((cause) => (error = (cause as Error).message));
  });
</script>

<HomeWidget {id} title="Flashcards" icon="flashcards" hero tint="var(--tile-3)" opens="flashcards" onopen={() => onview("flashcards")}>
  {#if error}<p class="note" role="alert">{error}</p>
  {:else if due !== null}
    <p class="big"><strong class="figure">{due}</strong><span>{due === 1 ? "card due" : "cards due"}</span></p>
  {/if}
</HomeWidget>

<style>
  p {
    margin: 0;
  }
  .big {
    display: grid;
  }
  .big span,
  .note {
    color: var(--muted);
    font-size: 15px;
  }
</style>

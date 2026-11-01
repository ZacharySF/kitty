<script lang="ts">
  import { returnTitle } from "./planning";
  import type { Guide, Item } from "./types";

  /** Spaced returns due today or earlier, then saved reflections; each reopens its guide. */
  let { guides, subjects, today, onopen }: { guides: Guide[]; subjects: Item[]; today: string; onopen: (guide: Guide) => void } = $props();

  const saved = $derived(guides.filter((guide) => guide.state.next_review || guide.state.reflection));
  const due = $derived(saved.filter((guide) => guide.state.next_review && guide.state.next_review <= today)
    .sort((a, b) => a.state.next_review.localeCompare(b.state.next_review)));
</script>

<div class="returns">
  <span class="eyebrow">Spaced returns due · {due.length}</span>
  {#if due.length}
    <ul class="chips">
      {#each due as guide (guide.id)}
        <li><button type="button" class="chip" onclick={() => onopen(guide)} title={`Due ${guide.state.next_review}`}><span class="dot" style:background={subjects.find((item) => item.id === guide.subject_id)?.color || "var(--accent)"}></span>{returnTitle(guide)}</button></li>
      {/each}
    </ul>
  {:else}
    <p class="muted">Nothing due. Set a next review in the guide to plan a return.</p>
  {/if}
  {#if saved.length}
    <details>
      <summary>Saved reflections · {saved.length}</summary>
      {#each saved as guide (guide.id)}
        <article>
          <h4>{new Date(guide.created_at).toLocaleString()}</h4>
          {#if guide.state.next_review}<p>Next review: {guide.state.next_review}{guide.state.next_review <= today ? " · due" : ""}</p>{/if}
          <p class="preserve">{guide.state.reflection || "No reflection yet."}</p>
          <button type="button" class="chip" onclick={() => onopen(guide)}>Open guide & reflection</button>
        </article>
      {/each}
    </details>
  {/if}
</div>

<style>
  .returns {
    display: grid;
    gap: 0.45rem;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .chips li {
    max-width: 100%;
  }
  .chip {
    max-width: 100%;
    overflow: hidden;
    padding: 3px 10px;
    border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--hairline));
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chips .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .chip:hover {
    background: var(--wash);
  }
  .dot {
    flex: none;
    width: 7px;
    height: 7px;
    border-radius: 999px;
  }
  article {
    display: grid;
    gap: 0.25rem;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--hairline);
  }
  article .chip {
    justify-self: start;
  }
  h4,
  p {
    margin: 0;
    font-size: 12.5px;
  }
  h4 {
    font: 600 12.5px var(--heading);
  }
</style>

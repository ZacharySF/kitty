<script lang="ts">
  import { returnTitle } from "./planning";
  import type { Guide, Item } from "./types";

  /**
   * `due`: spaced returns due today or earlier, as chips (nothing when none are due).
   * `saved`: every saved reflection. Each reopens its guide.
   */
  let { guides, subjects, today, show, onopen }: {
    guides: Guide[]; subjects: Item[]; today: string; show: "due" | "saved"; onopen: (guide: Guide) => void;
  } = $props();

  const saved = $derived(guides.filter((guide) => guide.state.next_review || guide.state.reflection));
  const due = $derived(saved.filter((guide) => guide.state.next_review && guide.state.next_review <= today)
    .sort((a, b) => a.state.next_review.localeCompare(b.state.next_review)));
</script>

{#if show === "due" && due.length}
  <div class="returns">
    <div class="head"><span class="eyebrow">Spaced returns due</span><span class="count">{due.length}</span></div>
    <ul class="chips">
      {#each due as guide (guide.id)}
        <li><button type="button" class="chip" onclick={() => onopen(guide)} title={`Due ${guide.state.next_review}`}><span class="dot" style:background={subjects.find((item) => item.id === guide.subject_id)?.color || "var(--accent)"}></span>{returnTitle(guide)}</button></li>
      {/each}
    </ul>
  </div>
{:else if show === "saved" && saved.length}
  <section class="saved" aria-label="Saved reflections">
    <div class="head"><span class="eyebrow">Saved reflections</span><span class="count">{saved.length}</span></div>
    {#each saved as guide (guide.id)}
      <article>
        <button type="button" class="title" onclick={() => onopen(guide)}>{returnTitle(guide)}</button>
        <span class="when">{new Date(guide.created_at).toLocaleDateString()}{#if guide.state.next_review} · review {guide.state.next_review}{/if}</span>
        {#if guide.state.reflection}<p class="preserve">{guide.state.reflection}</p>{/if}
      </article>
    {/each}
  </section>
{/if}

<style>
  .returns,
  .saved {
    display: grid;
    gap: 8px;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px;
  }
  .count {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
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
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;
    height: 24px;
    overflow: hidden;
    padding: 0 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
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
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 2px 12px;
    padding: 6px 0;
    border-top: 1px solid var(--hairline);
  }
  .title {
    justify-self: start;
    padding: 0;
    border: 0;
    background: none;
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
    text-align: left;
  }
  .title:hover {
    text-decoration: underline;
  }
  .when {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  p {
    grid-column: 1 / -1;
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
</style>

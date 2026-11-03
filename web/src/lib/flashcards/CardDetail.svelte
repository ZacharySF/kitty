<script lang="ts" module>
  /** Card state colours shared by the browser list and the detail chip. */
  export const STATE_COLOR: Record<string, string> = {
    new: "var(--c4)",
    learning: "var(--warning)",
    relearning: "var(--error)",
    review: "var(--success)",
    suspended: "var(--subtle)",
    leech: "var(--error)",
  };
  export const isBuried = (until: string | null) => !!until && new Date(until) > new Date();
</script>

<script lang="ts">
  import { api } from "../api";
  import { GRADES } from "./GradeKeys.svelte";
  import { typesetHtml } from "../math/tex";

  type Detail = {
    id: number;
    note_id: number;
    ordinal: number;
    deck: string;
    state: string;
    due: string;
    suspended: number;
    buried_until: string | null;
    leech: number;
    lapses: number;
    reviews: number;
    revision: number;
    front: string;
    back: string;
    memory: { stability: number; difficulty: number; retrievability: number } | null;
  };
  type Review = { id: number; rating: number; reviewed_at: string; mode: string; undone: number };

  /**
   * Card browser detail pane (Q42): a flippable preview, FSRS memory-state meters,
   * the last reviews and card actions. Refetches when `revision` changes.
   */
  let {
    cardId,
    revision,
    busy,
    onaction,
    noteActions,
  }: {
    cardId: number;
    revision: number;
    busy: boolean;
    onaction: (card: { id: number; revision: number }, action: "suspend" | "bury", value: boolean) => void;
    noteActions: import("svelte").Snippet;
  } = $props();

  let card = $state<Detail | null>(null);
  let reviews = $state<Review[]>([]);
  let flipped = $state(false);
  let error = $state("");
  let request = 0;
  let shown = 0;
  $effect(() => {
    revision;
    const id = cardId;
    const current = ++request;
    if (id !== shown) flipped = false;
    shown = id;
    error = "";
    Promise.all([
      api<{ card: Detail }>(`/flashcards/card?id=${id}`),
      api<{ reviews: Review[] }>(`/flashcards/history?card_id=${id}`),
    ]).then(
      ([detail, history]) => {
        if (current !== request) return;
        card = detail.card;
        reviews = history.reviews.filter((review) => !review.undone).slice(0, 12).reverse();
      },
      (cause) => { if (current === request) error = cause instanceof Error ? cause.message : String(cause); },
    );
  });

  let shownState = $derived(card ? (card.suspended ? "suspended" : card.leech ? "leech" : card.state) : "");
  let buried = $derived(isBuried(card?.buried_until ?? null));
  const days = (value: number) => `${value < 10 ? value.toFixed(1) : Math.round(value)} d`;
  let meters = $derived.by(() => {
    const memory = card?.memory;
    return [
      { name: "Stability", text: memory ? days(memory.stability) : "—", value: memory ? Math.min(100, (Math.log(memory.stability + 1) / Math.log(366)) * 100) : 0, tone: "violet" },
      { name: "Difficulty", text: memory ? `${memory.difficulty.toFixed(1)} of 10` : "—", value: memory ? memory.difficulty * 10 : 0, tone: "warning" },
      { name: "Recall now", text: memory ? `${Math.round(memory.retrievability * 100)}%` : "—", value: memory ? memory.retrievability * 100 : 0, tone: memory && memory.retrievability < 0.85 ? "error" : "success" },
    ];
  });
  const when = (iso: string) => new Date(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
</script>

<section class="detail" aria-label="Card details">
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if card}
    <header>
      <span class="eyebrow">{card.deck}</span>
      <span class="chip" style:--s={STATE_COLOR[shownState]}>{shownState}</span>
      {#if buried}<span class="chip" style:--s="var(--subtle)">buried</span>{/if}
    </header>
    <button class="face" class:flipped onclick={() => (flipped = !flipped)} aria-label="Flip card preview">
      <span class="side">{flipped ? "Back" : "Front"} · click to flip</span>
      <span class="text" class:question={!flipped}>{@html typesetHtml(flipped ? card.back : card.front)}</span>
    </button>
    <div class="memory">
      {#each meters as meter}
        <div
          role="meter"
          aria-label={meter.name}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(meter.value)}
          aria-valuetext={card.memory ? meter.text : "not studied yet"}
        >
          <span class="name">{meter.name}</span>
          <b>{meter.text}</b>
          <span class="track"><span style:width={`${meter.value}%`} style:background={`var(--${meter.tone})`}></span></span>
        </div>
      {/each}
    </div>
    <div class="history">
      <span class="eyebrow">{reviews.length ? `Last ${reviews.length} ${reviews.length === 1 ? "review" : "reviews"}` : "No reviews yet"}</span>
      {#if reviews.length}
        <ul aria-label="Last reviews">
          {#each reviews as review (review.id)}
            {@const grade = GRADES[review.rating - 1]}
            <li style:background={grade.color} title={`${when(review.reviewed_at)} · ${grade.label}`} aria-label={`${when(review.reviewed_at)}: ${grade.label}${review.mode === "regular" ? "" : ` (${review.mode})`}`}></li>
          {/each}
        </ul>
      {/if}
      <small>{card.reviews} {card.reviews === 1 ? "review" : "reviews"} · {card.lapses} {card.lapses === 1 ? "lapse" : "lapses"}{card.state === "new" ? "" : ` · due ${when(card.due)}`}</small>
    </div>
    <div class="actions">
      <button onclick={() => card && onaction(card, "suspend", !card.suspended)} disabled={busy}>{card.suspended ? "Unsuspend" : "Suspend"}</button>
      <button onclick={() => card && onaction(card, "bury", !buried)} disabled={busy}>{buried ? "Unbury" : "Bury today"}</button>
      {@render noteActions()}
    </div>
  {:else if !error}<p role="status">Loading card…</p>{/if}
</section>

<style>
  .detail {
    display: grid;
    align-content: start;
    gap: 12px;
    min-width: 0;
    padding: 14px 16px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  header .eyebrow {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    color: var(--violet);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chip {
    padding: 0 7px;
    border: 1px solid color-mix(in srgb, var(--s) 45%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--s) 12%, transparent);
    color: var(--s);
    font: 500 9px/16px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .face {
    display: grid;
    gap: 8px;
    min-height: 110px;
    padding: 14px 16px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-raised) 80%, transparent);
    color: var(--ink);
    text-align: left;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }
  .face.flipped {
    background: color-mix(in srgb, var(--violet) 8%, var(--solid-raised));
  }
  .face:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .side {
    color: var(--subtle);
    font: 9.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .text {
    font-size: 14px;
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  .text.question {
    font: 600 17px/1.3 var(--font-ui);
  }
  .text :global(img) {
    max-width: 100%;
    max-height: 220px;
  }
  .text :global(hr) {
    border: 0;
    border-top: 1px solid var(--hairline);
  }
  .memory {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
  }
  .memory > div {
    display: grid;
    gap: 3px;
  }
  .name {
    color: var(--subtle);
    font: 9px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .memory b {
    font: 600 15px var(--font-mono);
  }
  .track {
    height: 3px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
    overflow: hidden;
  }
  .track span {
    display: block;
    height: 100%;
  }
  .history {
    display: grid;
    gap: 5px;
  }
  .history .eyebrow {
    color: var(--subtle);
  }
  .history ul {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .history li {
    width: 14px;
    height: 14px;
    border-radius: 4px;
  }
  .history small {
    color: var(--muted);
    font-size: 11.5px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .actions :global(button) {
    min-height: 28px;
    padding: 4px 12px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel) 60%, transparent);
    color: var(--ink);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .actions :global(button:hover:not(:disabled)) {
    border-color: var(--violet);
  }
  .actions :global(button.danger) {
    color: var(--error);
  }
  .error {
    color: var(--warning);
  }
  @media (prefers-reduced-motion: reduce) {
    .face {
      transition: none;
    }
  }
</style>

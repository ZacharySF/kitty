<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { api } from "../api";
  import type { BookEntry, Rating } from "./api";
  import Ratings from "./Ratings.svelte";
  import { dueIn, entryLabel, entryProblem } from "./review";

  /**
   * Review one of your own books' problems: rika! holds only the reference and your
   * answer, so you rework it from the book, optionally update the answer, and rate it.
   */
  let {
    entry,
    eyebrow = "Own book",
    compact = false,
    nextLabel = "Next",
    onreviewed,
    onnext,
    oncancel,
  }: {
    entry: BookEntry;
    eyebrow?: string;
    /** The drawer's inline copy: no card frame, no 1–4 keys. */
    compact?: boolean;
    nextLabel?: string;
    onreviewed?: (entry: BookEntry) => void;
    onnext?: () => void;
    oncancel?: () => void;
  } = $props();

  let answer = $state("");
  let busy = $state(false);
  let error = $state("");
  let done = $state<BookEntry | null>(null);

  async function rate(rating: Rating) {
    if (busy || done) return;
    const changed = answer.trim() && answer.trim() !== entry.answer ? answer.trim() : undefined;
    const problem = changed ? entryProblem({ ...entry, answer: changed }) : "";
    if (problem) return void (error = problem);
    busy = true;
    error = "";
    try {
      done = await api<BookEntry>(`/math/books/${encodeURIComponent(String(entry.id))}/ratings`, changed === undefined ? { rating } : { rating, answer: changed });
      onreviewed?.(done);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="review" class:compact data-book-review={String(entry.id)}>
  {#if !compact}<span class="eyebrow">{eyebrow}</span>{/if}
  <strong class="ref">{entryLabel(entry)}</strong>
  {#if entry.topic_title}<span class="topic">{entry.topic_title}</span>{/if}
  <p class="previous" data-previous>
    {#if entry.answer}Your answer last time: <b>{entry.answer}</b>{:else}No answer logged last time.{/if}
  </p>
  {#if done}
    <p class="due" data-next-review>Reviewed · next {dueIn(done.card.due_at, Date.now())}</p>
    {#if onnext}<Button size="sm" variant="solid" intent="primary" onclick={onnext}>{nextLabel}</Button>{/if}
  {:else}
    <p class="hint">Rework it from your book, then rate how it went.</p>
    <label class="field">
      <span>Update my answer</span>
      <input bind:value={answer} placeholder={entry.answer || "Optional, e.g. 3/8"} autocomplete="off" spellcheck="false" maxlength="200" />
    </label>
    <Ratings keys={!compact} {busy} onrate={rate} />
    {#if oncancel}<Button size="sm" onclick={oncancel}>Cancel</Button>{/if}
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</div>

<style>
  .review {
    display: grid;
    justify-items: center;
    gap: 10px;
    width: min(100%, 520px);
    box-sizing: border-box;
    padding: 26px 30px;
    border: 1px solid var(--border);
    border-radius: var(--br-radius-lg, 0.8rem);
    background: var(--surface);
    text-align: center;
  }
  .review.compact {
    justify-items: stretch;
    width: 100%;
    padding: 6px 0 2px 16px;
    border: 0;
    background: none;
    text-align: left;
  }
  .compact > :global(.br-btn) {
    justify-self: start;
  }
  .compact .ref,
  .compact .topic {
    display: none;
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .ref {
    color: var(--ink);
    font: 600 16px var(--font-ui);
  }
  .topic {
    padding: 2px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 500 11px var(--font-ui);
  }
  p {
    margin: 0;
    color: var(--muted);
    font-size: 12.5px;
  }
  .previous {
    color: var(--ink);
    font-size: 13.5px;
  }
  .previous b {
    font: 600 14px var(--font-mono);
  }
  .field {
    display: grid;
    gap: 4px;
    width: 100%;
    color: var(--muted);
    font-size: 11.5px;
    text-align: left;
  }
  .error {
    color: var(--warning);
  }
</style>

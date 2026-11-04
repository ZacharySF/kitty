<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { api } from "../api";
  import type { BookEntry, BookList, Id } from "./api";
  import BookForm, { type BookFields } from "./BookForm.svelte";
  import BookReview from "./BookReview.svelte";
  import type { Topic } from "./graph";
  import { dueIn, entryLabel } from "./review";

  /**
   * The Books drawer (design step 9): log problems you worked from your own books and
   * review them on the same spaced schedule; due ones first. Each is a reference (book,
   * section, number) with your answer; rika! never stores the book's text.
   */
  let { books, topics, error = "", onchange }: { books: BookList | null; topics: Topic[]; error?: string; onchange: () => Promise<void> } = $props();

  let open = $state<{ id: Id; what: "review" | "edit" | "delete" } | null>(null);
  let failure = $state("");
  let busy = $state(false);
  const path = (id: Id) => `/math/books/${encodeURIComponent(String(id))}`;
  const opened = (entry: BookEntry, what: "review" | "edit" | "delete") => open?.id === entry.id && open.what === what;

  function toggle(entry: BookEntry, what: "review" | "edit" | "delete") {
    open = opened(entry, what) ? null : { id: entry.id, what };
    failure = "";
  }

  // Saved means the form can clear at once; the list reloads behind it (its errors show above the list).
  async function add(fields: BookFields) {
    await api<BookEntry>("/math/books", {
      book: fields.book, problem: fields.problem, confidence: fields.confidence, section: fields.section, answer: fields.answer, topic_id: fields.topic_id || null,
    });
    void onchange();
  }
  async function edit(entry: BookEntry, fields: BookFields) {
    await api<BookEntry>(path(entry.id), { book: fields.book, section: fields.section, problem: fields.problem, answer: fields.answer, topic_id: fields.topic_id || null });
    open = null;
    void onchange();
  }
  async function remove(entry: BookEntry) {
    if (busy) return;
    busy = true;
    try {
      await api(`${path(entry.id)}/delete`, {});
      open = null;
      await onchange();
    } catch (cause) {
      failure = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<h4>Books</h4>
<p class="lead">Problems from your own books, on the same spaced reviews.</p>
<BookForm label="Log a book problem" saveLabel="Log it" books={books?.books ?? []} {topics} onsave={add} />
{#if error}<p class="note" role="alert">{error}</p>{/if}
{#if failure}<p class="note" role="alert">{failure}</p>{/if}
{#if books?.problems.length}
  <div class="head">
    <span class="eyebrow">Logged · {books.problems.length}</span>
    {#if books.due}<span class="due-count">{books.due} due</span>{/if}
  </div>
  <ul class="entries" aria-label="Logged book problems">
    {#each books.problems as entry (entry.id)}
      <li data-entry={String(entry.id)} data-due={entry.card.due || undefined}>
        <div class="row">
          <span class="mark" class:due={entry.card.due} aria-hidden="true"></span>
          <span class="what">
            <strong>{entryLabel(entry)}</strong>
            <small>{#if entry.card.due}<b class="due-tag">Due</b>{:else}Next {dueIn(entry.card.due_at, Date.now())}{/if}{#if entry.topic_title}{" · "}{entry.topic_title}{/if}{#if entry.answer}{" · "}{entry.answer}{/if}</small>
          </span>
        </div>
        <div class="actions">
          <Button size="sm" variant={entry.card.due ? "solid" : undefined} intent={entry.card.due ? "primary" : undefined} aria-pressed={opened(entry, "review")}
            onclick={() => toggle(entry, "review")}>Review</Button>
          <Button size="sm" aria-pressed={opened(entry, "edit")} onclick={() => toggle(entry, "edit")}>Edit</Button>
          <Button size="sm" aria-pressed={opened(entry, "delete")} onclick={() => toggle(entry, "delete")}>Delete</Button>
        </div>
        {#if opened(entry, "review")}
          <BookReview {entry} compact onreviewed={() => { open = null; void onchange(); }} oncancel={() => (open = null)} />
        {:else if opened(entry, "edit")}
          <BookForm label={`Edit ${entryLabel(entry)}`} saveLabel="Save" confidence={false} books={books.books} {topics}
            initial={{ book: entry.book, section: entry.section, problem: entry.problem, answer: entry.answer, topic_id: entry.topic_id ?? "" }}
            onsave={(fields) => edit(entry, fields)} oncancel={() => (open = null)} />
        {:else if opened(entry, "delete")}
          <div class="confirm" role="group" aria-label="Confirm delete">
            <span>Delete this entry and its reviews?</span>
            <Button size="sm" onclick={() => (open = null)}>Keep</Button>
            <Button size="sm" variant="solid" intent="danger" disabled={busy} onclick={() => remove(entry)}>Delete it</Button>
          </div>
        {/if}
      </li>
    {/each}
  </ul>
{:else if books}
  <p class="note">Nothing logged yet.</p>
{:else if !error}
  <p class="note" role="status">Reading your books…</p>
{/if}

<style>
  h4 {
    margin: 0;
    font-size: 14px;
  }
  .lead,
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 6px;
    border-top: 1px solid var(--hairline);
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .due-count {
    color: var(--warning);
    font: 500 11px var(--font-mono);
  }
  .entries {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .entries li {
    display: grid;
    gap: 6px;
    padding: 9px 0;
  }
  .entries li + li {
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  .row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }
  .mark {
    flex: none;
    width: 8px;
    height: 8px;
    margin-top: 5px;
    border: 1.5px solid var(--border);
    border-radius: 50%;
  }
  .mark.due {
    border-color: var(--warning);
    background: var(--warning);
  }
  .what {
    display: grid;
    min-width: 0;
  }
  .what strong {
    overflow-wrap: anywhere;
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
  }
  .what small {
    overflow-wrap: anywhere;
    color: var(--muted);
    font-size: 11px;
  }
  .due-tag {
    color: var(--warning);
    font-weight: 600;
  }
  .actions {
    display: flex;
    gap: 4px;
    padding-left: 16px;
  }
  .entries li > :global(.book-form) {
    padding-left: 16px;
  }
  .confirm {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding-left: 16px;
    color: var(--ink);
    font-size: 12px;
  }
</style>

<script lang="ts" module>
  import type { Rating } from "./api";

  export type BookFields = { book: string; section: string; problem: string; answer: string; confidence: Rating; topic_id: string };
</script>

<script lang="ts">
  import { Button } from "@nunera/bracket";
  import Select from "../ui/Select.svelte";
  import { strandLabel, type Topic } from "./graph";
  import { bookSuggestions, CONFIDENCE, entryProblem } from "./review";

  /**
   * Log (or edit) one of your own books' problems: the reference, your answer, how sure
   * you were, and the topic it practises. Only the reference is kept, never the text.
   */
  let {
    initial,
    books,
    topics,
    confidence: asks = true,
    label,
    saveLabel,
    onsave,
    oncancel,
  }: {
    initial?: Partial<BookFields>;
    /** Books logged before, most used first. */
    books: string[];
    topics: Topic[];
    /** A new entry asks how sure you were (its first rating); an edit doesn't. */
    confidence?: boolean;
    label: string;
    saveLabel: string;
    /** Throws the backend's message on failure; the form keeps its fields. */
    onsave: (fields: BookFields) => Promise<void>;
    oncancel?: () => void;
  } = $props();
  const uid = $props.id();

  // The form starts from `initial` once; it is a draft from then on.
  const start = (() => initial)();
  let book = $state(start?.book ?? "");
  let section = $state(start?.section ?? "");
  let problem = $state(start?.problem ?? "");
  let answer = $state(start?.answer ?? "");
  let confidence = $state<Rating | null>(start?.confidence ?? null);
  let topic = $state(start?.topic_id ?? "");
  let error = $state("");
  let busy = $state(false);
  let bookFocused = $state(false);
  const suggestions = $derived(bookFocused || book ? bookSuggestions(books, book) : []);
  const options = $derived([{ value: "", label: "No topic" }, ...topics.map((item) => ({ value: item.id, label: item.title, hint: strandLabel(item.strand) }))]);
  const ready = $derived(!!book.trim() && !!problem.trim() && (!asks || confidence !== null));

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (busy || !ready) return;
    const fields = { book: book.trim(), section: section.trim(), problem: problem.trim(), answer: answer.trim(), confidence: confidence ?? 3, topic_id: topic };
    error = entryProblem(fields);
    if (error) return;
    busy = true;
    try {
      await onsave(fields);
      if (!start) [book, section, problem, answer, confidence, topic] = ["", "", "", "", null, ""];
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<form class="book-form" aria-label={label} onsubmit={save}>
  <p class="ref-only">Reference only — don't paste the problem's text.</p>
  <label class="field">
    <span>Book</span>
    <input bind:value={book} placeholder="Title, e.g. Blitzstein & Hwang" autocomplete="off" spellcheck="false" maxlength="120"
      onfocus={() => (bookFocused = true)} onblur={() => setTimeout(() => (bookFocused = false), 150)} />
  </label>
  {#if suggestions.length}
    <div class="suggest" role="group" aria-label="Books you've logged">
      {#each suggestions as name (name)}<button type="button" onclick={() => (book = name)}>{name}</button>{/each}
    </div>
  {/if}
  <div class="pair">
    <label class="field"><span>Section</span><input bind:value={section} placeholder="e.g. 2.3" autocomplete="off" maxlength="60" /></label>
    <label class="field"><span>Problem</span><input bind:value={problem} placeholder="e.g. 14" autocomplete="off" maxlength="40" /></label>
  </div>
  <label class="field">
    <span>My answer</span>
    <input bind:value={answer} placeholder="Just your answer, e.g. 3/8" autocomplete="off" spellcheck="false" maxlength="200" />
  </label>
  {#if asks}
    <div class="field">
      <span id={`${uid}-confidence`}>How sure were you?</span>
      <div class="confidence" role="radiogroup" aria-labelledby={`${uid}-confidence`}>
        {#each CONFIDENCE as item (item.n)}
          <button type="button" role="radio" aria-checked={confidence === item.n} class={`c${item.n}`} onclick={() => (confidence = item.n)}>
            <kbd>{item.n}</kbd>{item.label}
          </button>
        {/each}
      </div>
    </div>
  {/if}
  <div class="field">
    <span>Topic</span>
    <Select kind="search" label="Topic" options={options} value={topic} placeholder="No topic" onchange={(value) => (topic = value)} />
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div class="foot">
    {#if oncancel}<Button size="sm" onclick={oncancel}>Cancel</Button>{/if}
    <Button size="sm" type="submit" variant="solid" intent="primary" disabled={busy || !ready}>{saveLabel}</Button>
  </div>
</form>

<style>
  .book-form {
    display: grid;
    gap: 9px;
  }
  .ref-only {
    margin: 0;
    color: var(--muted);
    font-size: 11.5px;
  }
  .field {
    display: grid;
    gap: 4px;
    min-width: 0;
    color: var(--muted);
    font-size: 11.5px;
  }
  .field input {
    min-width: 0;
  }
  .pair {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .suggest {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: -3px;
  }
  .suggest button {
    max-width: 100%;
    padding: 2px 9px;
    overflow: hidden;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: none;
    color: var(--muted);
    font: 500 11px var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
  }
  .suggest button:hover {
    border-color: var(--accent);
    color: var(--ink);
  }
  .confidence {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 5px;
  }
  .confidence button {
    --g: var(--success);
    display: flex;
    align-items: center;
    gap: 6px;
    height: var(--control-h, 28px);
    padding: 0 8px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: none;
    color: var(--ink);
    font: 500 12px var(--font-ui);
    cursor: pointer;
  }
  .confidence .c1 {
    --g: var(--error);
  }
  .confidence .c2 {
    --g: var(--warning);
  }
  .confidence .c4 {
    --g: var(--c4, var(--accent));
  }
  .confidence button[aria-checked="true"] {
    border-color: var(--g);
    background: color-mix(in srgb, var(--g) 16%, transparent);
  }
  kbd {
    color: var(--subtle);
    font: 500 10px var(--font-mono);
  }
  .error {
    margin: 0;
    color: var(--warning);
    font-size: 12px;
  }
  .foot {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
  }
</style>

<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api } from "../api";
  import Icon from "../Icon.svelte";
  import {
    DUPLICATE_WARNING, addCard, checkCard, loadFronts, normalizeFront, preselectDeck, rememberedDeck,
    type DeckOption, type TypeOption,
  } from "./newCard";

  /**
   * Quick add (Q66): a small front/back popover over whatever page is open, from N on
   * Flashcards or Ctrl K "New card". Enter or Ctrl+Enter saves and readies the next card;
   * Esc closes. `deck` is the deck in view, if any; otherwise the last-used deck is chosen.
   */
  let { deck = null, onclose }: { deck?: number | null; onclose: () => void } = $props();

  let dialog: HTMLDialogElement;
  let frontField = $state<HTMLTextAreaElement>();
  let backField = $state<HTMLTextAreaElement>();
  let decks = $state<DeckOption[]>([]);
  let types = $state<TypeOption[]>([]);
  let deckId = $state(0);
  let front = $state("");
  let back = $state("");
  let fronts = $state<string[]>([]);
  let problem = $state("");
  let added = $state("");
  let confirmed = $state("");
  let busy = $state(false);
  const live = $derived(checkCard({ front, back }, fronts));
  const deckName = $derived(decks.find((item) => item.id === deckId)?.name ?? "");

  onMount(() => {
    dialog.showModal();
    frontField?.focus();
    void (async () => {
      try {
        const overview = await api<{ decks: DeckOption[]; types: TypeOption[] }>("/flashcards");
        decks = overview.decks;
        types = overview.types;
        deckId = preselectDeck(decks, deck, rememberedDeck());
        if (!deckId) problem = "Create a deck in Flashcards before adding cards.";
      } catch (cause) { problem = (cause as Error).message; }
    })();
  });
  $effect(() => {
    const id = deckId;
    let current = true;
    fronts = [];
    loadFronts(api, id).then((items) => { if (current) fronts = items; }, (cause) => { if (current) problem = cause.message; });
    return () => { current = false; };
  });

  async function save() {
    if (busy) return;
    busy = true;
    problem = "";
    try {
      const draft = { front, back };
      const result = await addCard(api, { deckId, types, draft, fronts, allowDuplicate: confirmed === normalizeFront(front) });
      if (!result.saved) {
        if (result.check.problem) {
          problem = result.check.problem;
          (result.check.field === "back" ? backField : frontField)?.focus();
        } else confirmed = normalizeFront(front);
        return;
      }
      fronts = [...fronts, draft.front];
      added = `Added to ${deckName}.`;
      front = back = confirmed = "";
      window.dispatchEvent(new CustomEvent("kitty-flashcards-changed"));
      await tick();
      frontField?.focus();
    } catch (cause) {
      problem = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  function key(event: KeyboardEvent) {
    if (event.key !== "Enter" || event.shiftKey || event.altKey || event.isComposing) return;
    event.preventDefault();
    // Enter in the front with no back yet moves on to the back, like Tab.
    if (!event.ctrlKey && !event.metaKey && event.target === frontField && !back.trim() && front.trim()) backField?.focus();
    else void save();
  }
  function edited() {
    problem = "";
    added = "";
  }
</script>

<dialog
  bind:this={dialog}
  class="quick-card"
  aria-label="Quick add card"
  oncancel={onclose}
  onclick={(event) => { if (event.target === dialog) onclose(); }}
>
  <!-- svelte-ignore a11y_no_static_element_interactions (Enter is handled for the fields inside) -->
  <div class="body" onkeydown={key}>
    <header>
      <span class="mark" aria-hidden="true"><Icon name="flashcards" size={16} /></span>
      <span class="title">New card</span>
      <label class="deck"><span class="sr-only">Deck</span><select
          bind:value={deckId}
          onchange={() => { confirmed = ""; edited(); }}
          disabled={busy || !decks.length}
        >{#each decks as item (item.id)}{#if !item.archived || item.id === deckId}<option value={item.id}>{item.name}</option>{/if}{/each}</select></label>
    </header>
    <label class="face"><span>Front</span><textarea
        bind:this={frontField}
        bind:value={front}
        oninput={() => { confirmed = ""; edited(); }}
        rows="2"
        placeholder="Question or cue…"
      ></textarea></label>
    <label class="face"><span>Back</span><textarea
        bind:this={backField}
        bind:value={back}
        oninput={edited}
        rows="2"
        placeholder="Answer…"
      ></textarea></label>
    <footer>
      {#if problem}<p class="problem" role="alert">{problem}</p>
      {:else if live.duplicate}<p class="warning" role="status">{DUPLICATE_WARNING}</p>
      {:else if added}<p class="added" role="status">{added}</p>
      {:else}<p class="keys"><kbd>Enter</kbd> save · <kbd>Shift Enter</kbd> new line · <kbd>Esc</kbd> close</p>{/if}
    </footer>
  </div>
</dialog>

<style>
  .quick-card {
    width: min(440px, calc(100vw - 32px));
    margin: 14vh auto auto;
    padding: 0;
    overflow: hidden;
    border: 1px solid color-mix(in srgb, var(--ink) 10%, transparent);
    border-radius: 16px;
    color: var(--ink);
    background: color-mix(in srgb, var(--solid-panel, var(--br-panel)) 82%, transparent);
    backdrop-filter: blur(36px) saturate(170%);
    box-shadow: var(--overlay-shadow);
  }
  .quick-card::backdrop {
    background: color-mix(in srgb, var(--shadow-tint) 22%, transparent);
  }
  .body {
    display: grid;
    gap: 10px;
    padding: 14px 16px 12px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .mark {
    display: inline-grid;
    place-items: center;
    color: var(--violet);
  }
  .title {
    font: 600 14px var(--font-ui);
  }
  .deck {
    margin-left: auto;
    min-width: 0;
  }
  .deck select {
    max-width: 220px;
    padding: 4px 8px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--ink);
    background: color-mix(in srgb, var(--ink) 5%, transparent);
    font: 500 12px var(--font-ui);
  }
  .face {
    display: grid;
    gap: 4px;
  }
  .face span {
    color: var(--subtle);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  textarea {
    width: 100%;
    box-sizing: border-box;
    min-height: 2.8rem;
    padding: 8px 10px;
    resize: vertical;
    border: 1px solid var(--hairline);
    border-radius: 10px;
    color: var(--ink);
    background: color-mix(in srgb, var(--surface, var(--br-panel)) 70%, transparent);
    font: 14px var(--font-ui);
  }
  /* Beats the frame's glass field rule (styles.css) so the focused face lights up. */
  :global(:root body) .quick-card textarea:focus-visible {
    border-color: var(--violet);
    outline: none;
  }
  footer p {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
  }
  .keys { color: var(--subtle); }
  .keys kbd { font: 10.5px var(--font-mono); }
  .problem { color: var(--error); }
  .warning { color: var(--warning); }
  .added { color: var(--success); }
</style>

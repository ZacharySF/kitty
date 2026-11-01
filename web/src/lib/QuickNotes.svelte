<script lang="ts">
  import { onMount } from "svelte";
  import QuickNoteCard from "./QuickNoteCard.svelte";
  import { listQuickNotes, popOutQuickNote, type QuickNote } from "./quickNotes";
  import { onBlockChange } from "./live";

  let notes = $state<QuickNote[]>([]);
  let drafts = $state<{ key: number; id: number | null }[]>([]);
  let query = $state("");
  let loading = $state(true);
  let error = $state("");
  let nextDraft = 1;

  const owned = $derived(new Set(drafts.map(draft => draft.id).filter((id): id is number => id !== null)));
  const needle = $derived(query.trim().toLocaleLowerCase());
  const visible = $derived(notes.filter(note => !owned.has(note.id) && (!needle || note.body.toLocaleLowerCase().includes(needle))));

  async function refresh() {
    try {
      notes = await listQuickNotes();
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  function add() {
    query = "";
    drafts = [{ key: nextDraft++, id: null }, ...drafts];
  }
  function saved(note: QuickNote, draftKey?: number) {
    if (draftKey !== undefined) drafts = drafts.map(draft => draft.key === draftKey ? { ...draft, id: note.id } : draft);
    const index = notes.findIndex(item => item.id === note.id);
    const next = index < 0 ? [note, ...notes] : notes.map(item => item.id === note.id ? note : item);
    notes = next.sort((a, b) => Number(b.pinned) - Number(a.pinned));
  }
  function deleted(id: number, draftKey?: number) {
    notes = notes.filter(note => note.id !== id);
    if (draftKey !== undefined) drafts = drafts.filter(draft => draft.key !== draftKey);
  }

  onMount(() => {
    void refresh();
    // Pop-out windows save independently; their edits arrive live (and on focus as a fallback).
    const focus = () => void refresh();
    window.addEventListener("focus", focus);
    const stopLive = onBlockChange(change => { if (!change || change.root?.type === "quick_note") void refresh(); });
    return () => { window.removeEventListener("focus", focus); stopLive(); };
  });
</script>

<section class="quick-notes" aria-labelledby="quick-notes-title">
  <header class="head">
    <div>
      <p class="eyebrow">Study · scratchpad</p>
      <h1 id="quick-notes-title">Quick notes</h1>
      <p class="lede">Jot it down — every note saves as you type. Pop one out to float it, see-through, over anything.</p>
    </div>
    <div class="actions">
      <label class="search">
        <span class="sr-only">Search quick notes</span>
        <input type="search" placeholder="Search notes…" bind:value={query} />
      </label>
      <button type="button" class="pill" onclick={() => popOutQuickNote()}>↗ Floating note</button>
      <button type="button" class="pill primary" onclick={add}>+ New note</button>
    </div>
  </header>

  {#if error}<p class="error" role="alert">{error} <button type="button" class="pill" onclick={refresh}>Try again</button></p>{/if}

  <div class="board">
    {#each drafts as draft (draft.key)}
      <QuickNoteCard autofocus onsaved={note => saved(note, draft.key)} ondeleted={id => deleted(id, draft.key)}
        note={draft.id === null ? null : notes.find(note => note.id === draft.id) ?? null} />
    {/each}
    {#each visible as note (note.id)}
      <QuickNoteCard {note} onsaved={note => saved(note)} ondeleted={id => deleted(id)} />
    {/each}
  </div>

  {#if !loading && !drafts.length && !visible.length}
    <div class="empty">
      <p class="eyebrow">{needle ? "No matches" : "Nothing here yet"}</p>
      <p>{needle ? "No quick note contains that text." : "Start a note here, or float one on top of your other windows."}</p>
      {#if !needle}<button type="button" class="pill primary" onclick={add}>+ New note</button>{/if}
    </div>
  {/if}
</section>

<style>
  .quick-notes { display: flex; flex-direction: column; gap: 22px; padding-bottom: 40px; min-width: 0; }
  .head { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; flex-wrap: wrap; }
  .head h1 { margin: 4px 0 6px; }
  .lede { margin: 0; color: var(--muted); max-width: 60ch; }
  .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .search input { min-width: 220px; border-radius: 999px; padding: 8px 14px; font-size: 13px; }
  .board {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
    align-items: start;
  }
  .empty {
    display: grid; justify-items: center; gap: 6px;
    padding: 60px 20px; text-align: center; color: var(--muted);
  }
  .empty p { margin: 0; }
  .error { display: flex; align-items: center; gap: 10px; color: var(--error); margin: 0; }
</style>

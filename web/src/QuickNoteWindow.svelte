<script lang="ts">
  import { onMount } from "svelte";
  import QuickNoteCard from "./lib/QuickNoteCard.svelte";
  import { getQuickNote, noteTitle, type QuickNote } from "./lib/quickNotes";

  const match = /^#quick-note\/([1-9][0-9]{0,15})$/.exec(window.location.hash);
  let note = $state<QuickNote | null>(null);
  let ready = $state(!match);
  let missing = $state(false);

  onMount(() => {
    if (!match) return;
    getQuickNote(Number(match[1]))
      .then(found => { note = found; })
      .catch(() => { missing = true; history.replaceState(null, "", "#quick-note"); })
      .finally(() => { ready = true; });
  });
</script>

<svelte:head><title>{note ? noteTitle(note.body) : "Quick note"} · rika!</title></svelte:head>
<main class="quick-note-window">
  {#if ready}
    <QuickNoteCard {note} variant="window" autofocus />
    {#if missing}<p class="gone" role="status">That note was deleted — this is a fresh one.</p>{/if}
  {/if}
</main>

<style>
  .quick-note-window { position: relative; height: 100dvh; padding: 0; border: 0; overflow: hidden; }
  .gone {
    position: absolute; left: 16px; right: 16px; bottom: 34px; margin: 0;
    font: 12px var(--font-ui); color: var(--muted);
  }
</style>

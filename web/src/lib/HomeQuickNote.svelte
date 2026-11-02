<script lang="ts">
  import { onMount } from "svelte";
  import HomeWidget from "./HomeWidget.svelte";
  import { createQuickNote, listQuickNotes, noteTitle, type QuickNote } from "./quickNotes";
  import type { View } from "./workspace";

  /** Q20: capture a quick note from home with Ctrl+Enter; the newest few stay visible. */
  let { id, onview, limit = 3 }: { id: string; onview: (view: View) => unknown; limit?: number } = $props();
  let text = $state("");
  let notes = $state<QuickNote[]>([]);
  let error = $state("");

  const recent = $derived(
    [...notes].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id).slice(0, limit),
  );
  const time = (stamp: string) =>
    new Date(stamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

  async function refresh() {
    try {
      notes = await listQuickNotes();
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  /** The field clears at once so the next thought can start; a failed save puts the text back. */
  async function save() {
    const body = text.trim();
    if (!body) return;
    text = "";
    try {
      const note = await createQuickNote(body, "rose");
      notes = [note, ...notes];
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
      if (!text.trim()) text = body;
    }
  }
  onMount(() => {
    void refresh();
    // Notes edited in the Quick notes view or a pop-out show up when focus returns.
    const focus = () => void refresh();
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  });
</script>

<HomeWidget {id} title="Quick note">
  <form onsubmit={(event) => { event.preventDefault(); void save(); }}>
    <textarea
      bind:value={text}
      rows="2"
      placeholder="Jot something…"
      aria-label="Quick note"
      aria-describedby={`${id}-hint`}
      onkeydown={(event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          void save();
        }
      }}
    ></textarea>
    <span id={`${id}-hint`} class="sr-only">Ctrl+Enter saves the note.</span>
    <button class="pill primary" title="Save (Ctrl+Enter)" disabled={!text.trim()}>Save</button>
  </form>
  {#if error}<p class="note" role="alert">{error}</p>{/if}
  {#if recent.length}
    <ul aria-label="Recent quick notes">
      {#each recent as note (note.id)}
        <li><button onclick={() => onview("quick-notes")}><span>{noteTitle(note.body)}</span><time datetime={note.created_at}>{time(note.created_at)}</time></button></li>
      {/each}
    </ul>
  {/if}
</HomeWidget>

<style>
  form {
    position: relative;
  }
  textarea {
    width: 100%;
    min-height: 54px;
    padding: 8px 10px 30px;
    border: 1px solid var(--hairline);
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-inset) 50%, transparent);
    color: var(--ink);
    font: 13px/1.4 var(--font-ui);
    resize: none;
  }
  textarea:focus-visible {
    border-color: color-mix(in srgb, var(--violet) 60%, var(--hairline));
  }
  .pill {
    position: absolute;
    right: 6px;
    bottom: 8px;
    min-height: 22px;
    padding: 1px 10px;
    font-size: 10px;
  }
  .pill:disabled {
    opacity: 0.4;
  }
  ul {
    margin: 8px 0 0;
    padding: 0;
    list-style: none;
  }
  li + li {
    border-top: 1px dashed color-mix(in srgb, var(--hairline) 70%, transparent);
  }
  li button {
    width: 100%;
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding: 5px 2px;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: 12px var(--font-ui);
    text-align: left;
    cursor: pointer;
  }
  li button:hover span {
    color: var(--violet);
  }
  li span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  time {
    flex: none;
    color: var(--subtle);
    font: 10px var(--font-mono);
  }
  .note {
    margin: 6px 0 0;
    color: var(--error);
    font-size: 12px;
  }
</style>

<script lang="ts">
  import { Button } from "@nunera/bracket";
  import OverflowMenu, { type OverflowItem } from "../ui/OverflowMenu.svelte";
  import type { Timer } from "./types";

  /** Session notes, with distractions behind "+ Distraction": a local draft until saved with the running timer. */
  let { timer, notes = $bindable(), distractions = $bindable(), notesDirty = $bindable(), noteRevision = $bindable(), disabled, confirm, onannotate }: {
    timer: Timer;
    notes: string;
    distractions: string;
    notesDirty: boolean;
    noteRevision: number;
    disabled: boolean;
    confirm: (message: string) => boolean;
    onannotate: () => void;
  } = $props();

  const active = $derived(timer.status === "running" || timer.status === "paused");
  let distracted = $state(false);
  let compare = $state(false);
  const showDistractions = $derived(distracted || !!distractions);
  function markDirty() {
    if (!notesDirty) noteRevision = timer.revision;
    notesDirty = true;
  }
  const actions = $derived<OverflowItem[]>([
    { label: compare ? "Hide saved version" : "Show saved version", onselect: () => (compare = !compare) },
    { label: "Use saved notes", onselect: () => {
      if (confirm("Discard your local note draft and use the latest saved notes?")) {
        notes = timer.notes; distractions = timer.distractions; noteRevision = timer.revision; notesDirty = false; compare = false;
      }
    } },
    ...(noteRevision !== timer.revision && active ? [{ label: "Keep reviewed draft", onselect: () => {
      if (confirm("Keep your local draft and replace the saved notes on the next save?")) noteRevision = timer.revision;
    } }] : []),
  ]);
</script>

<div class="session-notes">
  <div class="head">
    <label class="eyebrow" for="study-session-notes">Session notes</label>
    <span class="tools">
      {#if !showDistractions}<button type="button" class="add" onclick={() => (distracted = true)}>+ Distraction</button>{/if}
      {#if notesDirty}<OverflowMenu label="Note draft actions" items={actions} />{/if}
    </span>
  </div>
  <textarea id="study-session-notes" rows="3" bind:value={notes} oninput={markDirty} placeholder="What did I recall without looking?"></textarea>
  {#if showDistractions}
    <label class="eyebrow" for="study-distractions">Distractions</label>
    <!-- svelte-ignore a11y_autofocus -->
    <textarea id="study-distractions" rows="2" bind:value={distractions} oninput={markDirty} autofocus={distracted && !distractions}></textarea>
  {/if}
  {#if compare && notesDirty}
    <div class="saved" aria-label="Saved version">
      <p class="preserve">{timer.notes || "No saved notes."}</p>
      {#if timer.distractions}<p class="preserve">{timer.distractions}</p>{/if}
    </div>
  {/if}
  {#if active && notesDirty}<Button size="sm" {disabled} onclick={onannotate}>Save notes</Button>{/if}
</div>

<style>
  .session-notes {
    display: grid;
    gap: 8px;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px;
  }
  .tools {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .add {
    padding: 2px 8px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 12px var(--font-ui);
  }
  .add:hover {
    color: var(--ink);
    background: var(--wash);
  }
  textarea {
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
  }
  .saved {
    padding: 8px 10px;
    border-left: 2px solid var(--hairline);
  }
  .saved p {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  :global(.study) .session-notes :global(.br-btn) {
    justify-self: start;
  }
</style>

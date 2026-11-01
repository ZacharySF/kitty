<script lang="ts">
  import { Button } from "@nunera/bracket";
  import type { Timer } from "./types";

  /** Session notes and distractions: a local draft until saved with the running timer. */
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
  function markDirty() {
    if (!notesDirty) noteRevision = timer.revision;
    notesDirty = true;
  }
</script>

<div class="session-notes">
  <label class="field">
    <span class="eyebrow">Session notes{notesDirty ? " · unsaved draft" : ""}</span>
    <textarea rows="3" bind:value={notes} oninput={markDirty} placeholder="What did I recall without looking? What needs another pass?"></textarea>
  </label>
  <label class="field">
    <span class="eyebrow">Distractions</span>
    <textarea rows="2" bind:value={distractions} oninput={markDirty} placeholder="Capture it here, then return to the task."></textarea>
  </label>
  {#if notesDirty}
    <p class="muted">Unsaved local note draft. Save during the session or copy it into a completed session from History.</p>
    <details>
      <summary>Compare with latest saved notes</summary>
      <p class="preserve">{timer.notes || "No saved notes."}</p>
      <p class="preserve">{timer.distractions || "No saved distractions."}</p>
    </details>
    <div class="row">
      <Button size="sm" onclick={() => {
        if (confirm("Discard your local note draft and use the latest saved notes?")) {
          notes = timer.notes; distractions = timer.distractions; noteRevision = timer.revision; notesDirty = false;
        }
      }}>Use saved notes</Button>
      {#if noteRevision !== timer.revision && active}
        <Button size="sm" onclick={() => {
          if (confirm("You reviewed the latest saved notes. Keep your local draft and explicitly allow replacing them on the next save?")) noteRevision = timer.revision;
        }}>Keep reviewed local draft</Button>
      {/if}
    </div>
  {/if}
  {#if active}<Button size="sm" {disabled} onclick={onannotate}>Save session notes</Button>{/if}
</div>

<style>
  .session-notes {
    display: grid;
    gap: 0.5rem;
  }
  p {
    margin: 0;
    font-size: 12px;
  }
</style>

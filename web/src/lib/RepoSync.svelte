<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import {
    backupBad, backupNow, backupStatus, connectBackup, disconnectBackup, loadBackup, pauseBackup, type BackupState,
  } from "./reposync";

  let backup = $state<BackupState | null>(null);
  let error = $state("");
  let busy = $state(false);
  let draft = $state("");
  let now = $state(Date.now());
  /** Bumped by every change so an older poll response never overwrites a newer state. */
  let generation = 0;

  async function refresh() {
    const started = generation;
    try {
      const next = await loadBackup();
      if (started === generation) { backup = next; now = Date.now(); }
    } catch (cause) { if (started === generation && !backup) error = (cause as Error).message; }
  }
  onMount(() => {
    void refresh();
    const poll = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 3000);
    return () => clearInterval(poll);
  });

  async function run(action: () => Promise<BackupState>) {
    if (busy) return;
    busy = true; error = "";
    generation += 1;
    try { backup = await action(); now = Date.now(); draft = ""; }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  function disconnect() {
    if (!backup?.repo) return;
    if (!window.confirm(`Stop backing up to ${backup.repo}? What is already on GitHub stays there.`)) return;
    void run(disconnectBackup);
  }
</script>

<Panel title="GitHub backup" aria-label="GitHub backup">
<div class="github">
<p class="muted">A copy of your notes in a private GitHub repository you choose. It leaves your devices and is stored on GitHub unencrypted, so GitHub can read it. Notes only; no restore from it yet.</p>
{#if backup && !backup.enabled}
  <p class="muted" role="status">{backup.reason}</p>
{:else if backup?.repo}
  <div class="row">
    <span class="who">
      <strong>{backup.repo}</strong>
      <small class={backupBad(backup) ? "bad" : ""} role="status">{backupStatus(backup, now)}</small>
    </span>
    <span class="actions">
      {#if !backup.paused}<Button size="sm" disabled={busy || backup.running} aria-label="Back up to GitHub now" onclick={() => run(backupNow)}>Sync now</Button>{/if}
      <Button size="sm" disabled={busy} aria-pressed={backup.paused} onclick={() => run(() => pauseBackup(!backup!.paused))}>{backup.paused ? "Resume" : "Pause"}</Button>
      <Button size="sm" variant="ghost" disabled={busy} aria-label="Disconnect the GitHub backup" onclick={disconnect}>Disconnect</Button>
    </span>
  </div>
{:else if backup}
  <form class="entry" onsubmit={(event) => { event.preventDefault(); void run(() => connectBackup(draft)); }}>
    <label>Repository <input aria-label="GitHub repository" autocomplete="off" spellcheck="false" placeholder="owner/name" disabled={busy} bind:value={draft} /></label>
    <Button size="sm" intent="primary" type="submit" disabled={busy || !draft.trim()}>{busy ? "Checking…" : "Connect"}</Button>
  </form>
  <p class="muted">The repository must be private; rika! checks before every push. It pushes with the git sign-in this computer already has.</p>
{/if}
{#if error}<p role="alert">{error}</p>{/if}
</div>
</Panel>

<style>
  p { margin: 0; }
  .github { display: grid; gap: .8rem; }
  .muted { color: var(--muted); font-size: .8rem; line-height: 1.5; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; padding: .65rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; }
  .who { display: grid; gap: .15rem; flex: 1; min-width: 0; }
  .who small { color: var(--muted); line-height: 1.5; overflow-wrap: anywhere; }
  .who .bad { color: var(--danger, var(--warning)); }
  .actions { display: flex; gap: .5rem; flex-wrap: wrap; }
  .entry { display: flex; flex-wrap: wrap; align-items: end; gap: .5rem; width: 100%; }
  .entry label { display: grid; gap: .2rem; font-size: .8rem; flex: 1; min-width: 12rem; }
</style>

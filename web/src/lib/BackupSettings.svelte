<!-- Settings → Backup: History, Sync and Backup named apart; one archive of the whole workspace,
     previewed and restored into a new folder (never this one); the GitHub copy of the notes. -->
<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import RepoSync from "./RepoSync.svelte";
  import {
    createBackup, loadBackup, previewBackup, restoreBackup, size, stamp,
    type BackupState, type Preview, type Restored,
  } from "./backup";

  let backup = $state<BackupState | null>(null);
  let folder = $state("");
  let archive = $state("");
  let into = $state("");
  let busy = $state<"" | "create" | "preview" | "restore">("");
  let seen = $state<Preview | null>(null);
  let restored = $state<Restored | null>(null);
  let notice = $state("");
  let error = $state("");
  /** The preview belongs to these two fields; editing either asks for a new one. */
  let previewed = $state("");
  const key = $derived(`${archive.trim()}\n${into.trim()}`);
  const current = $derived(seen && previewed === key ? seen : null);

  onMount(async () => {
    try {
      backup = await loadBackup();
      folder = backup.folder;
      if (backup.last) archive = backup.last.path;
    } catch (cause) { error = (cause as Error).message; }
  });

  async function run(kind: typeof busy, action: () => Promise<void>) {
    if (busy) return;
    busy = kind; error = ""; notice = "";
    try { await action(); }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = ""; }
  }
  const create = () => run("create", async () => {
    const made = await createBackup(folder);
    if (backup) backup = { ...backup, last: made };
    archive = made.path;
    notice = `Saved ${size(made.bytes)}`;
  });
  const preview = () => run("preview", async () => {
    restored = null;
    seen = await previewBackup(archive, into);
    previewed = key;
  });
  const restore = () => run("restore", async () => {
    restored = await restoreBackup(archive, into);
    seen = null;
  });
  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); notice = "Copied"; }
    catch { notice = ""; }
  }
</script>

<Panel title="Backup" aria-label="Backup">
  <div class="rows">
    <div class="set-row">
      <span class="set-label">History</span>
      <span class="set-desc">Earlier versions of a page, inside this workspace. Not a backup.</span>
    </div>
    <div class="set-row">
      <span class="set-label">Sync</span>
      <span class="set-desc">Copies changes to paired devices, mistakes too. Not a backup.</span>
      <span class="set-end"><a class="link" href="#settings/devices">Devices</a></span>
    </div>
    <div class="set-row">
      <label class="set-label" for="backup-folder">Back up</label>
      <input id="backup-folder" class="path" aria-label="Backup folder" autocomplete="off" spellcheck="false"
        placeholder="~/rika-backups" disabled={!backup || !!busy} bind:value={folder} />
      <span class="set-end">
        <Button size="sm" intent="primary" disabled={!backup || !!busy || !folder.trim()} onclick={create}>{busy === "create" ? "Backing up…" : "Back up now"}</Button>
      </span>
    </div>
    <div class="set-row">
      <span class="set-label">Last backup</span>
      <span class="set-desc" data-testid="last-backup">
        {#if backup?.last}{stamp(backup.last.created_at)} · {size(backup.last.bytes)} · <span class="where">{backup.last.path}</span>
        {:else if backup}None yet{/if}
      </span>
    </div>
    <div class="set-row restore">
      <span class="set-label">Restore</span>
      <input class="path" aria-label="Backup file" autocomplete="off" spellcheck="false" placeholder="Backup file"
        disabled={!!busy} bind:value={archive} />
      <input class="path" aria-label="Restore into" autocomplete="off" spellcheck="false" placeholder="New, empty folder"
        disabled={!!busy} bind:value={into} />
      <span class="set-end">
        <Button size="sm" disabled={!!busy || !archive.trim() || !into.trim()} onclick={preview}>{busy === "preview" ? "Checking…" : "Preview"}</Button>
      </span>
    </div>
    {#if current}
      <div class="result" role="group" aria-label="Restore preview">
        <dl class="counts">
          {#each Object.entries(current.counts) as [label, count] (label)}<div><dt>{label}</dt><dd>{count}</dd></div>{/each}
          <div><dt>Files</dt><dd>{current.files.count} · {size(current.files.bytes)}</dd></div>
        </dl>
        {#each current.warnings as warning (warning)}<p class="set-desc warn">{warning}</p>{/each}
        {#each current.problems as problem (problem)}<p class="bad" role="alert">{problem}</p>{/each}
        <span class="go">
          <span class="set-desc">This workspace stays as it is.</span>
          <Button size="sm" intent="primary" disabled={!!busy || !current.can_restore} onclick={restore}>{busy === "restore" ? "Restoring…" : "Restore"}</Button>
        </span>
      </div>
    {/if}
    {#if restored}
      <div class="result" role="status" aria-label="Restored">
        <span class="set-desc">Restored to <span class="where">{restored.path}</span>. Keys, sign-ins and pairings are not in a backup.</span>
        <span class="open"><code>{restored.open}</code><Button size="sm" variant="ghost" onclick={() => copy(restored!.open)}>Copy</Button></span>
        <span class="open"><code>{restored.open_cli}</code><Button size="sm" variant="ghost" onclick={() => copy(restored!.open_cli)}>Copy</Button></span>
      </div>
    {/if}
  </div>
  {#if notice}<p class="set-note" role="status">{notice}</p>{/if}
  {#if error}<p class="set-note bad" role="alert">{error}</p>{/if}
</Panel>

<RepoSync />

<style>
  .path { flex: 1; min-width: 10rem; }
  .restore { flex-wrap: wrap; }
  .where { overflow-wrap: anywhere; }
  .link { color: var(--ink); font-size: 13px; }
  .result { display: grid; gap: 8px; padding: 10px 0; border-top: 1px solid var(--hairline); font-size: 13px; }
  .counts { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: 2px 18px; margin: 0; }
  .counts div { display: flex; justify-content: space-between; gap: 8px; border-bottom: 1px solid var(--hairline); padding: 3px 0; }
  .counts dt { color: var(--muted); }
  .counts dd { margin: 0; font-variant-numeric: tabular-nums; }
  .warn { margin: 0; }
  .bad { margin: 0; color: var(--danger, var(--warning)); }
  .go, .open { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .open code { overflow-wrap: anywhere; font-size: 12px; }
</style>

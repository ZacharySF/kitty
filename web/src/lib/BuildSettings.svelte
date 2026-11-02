<script lang="ts">
  /**
   * Settings → Build (Q8): the build's name, export, import from a file or an https
   * link with a diff before installing, rollback, and the build editor.
   */
  import { Badge, Button, Panel } from "@nunera/bracket";
  import { api, ApiError, type Problem, type TaskRecord } from "./api";
  import { buildStore, reloadBuild, setPreferences } from "./build.svelte";
  import { describeValue, type HistoryEntry } from "./buildCore";

  type Change = { path: string; before: unknown; after: unknown; restart: boolean; sensitive: boolean };
  type Preview = {
    base: string;
    about: { name: string; author: string; description: string; kitty_version: string };
    changes: Change[];
    restart: boolean;
    warnings: string[];
  };
  type Incoming = { text: string; source: string; preview: Preview | null; problems: Problem[]; error: string };

  let url = $state("");
  let busy = $state("");
  let error = $state("");
  let notice = $state("");
  let incoming = $state<Incoming | null>(null);
  let acceptSensitive = $state(false);
  let restartPrompt = $state(false);
  let held = $state.raw<Change[]>([]);
  let history = $state.raw<HistoryEntry[]>(buildStore.history);

  const sensitive = $derived(incoming?.preview?.changes.some((change) => change.sensitive) ?? false);
  const fileProblems = $derived(buildStore.invalid ? buildStore.problems : []);

  // Every saved build (install, rollback) adds to the history; refresh it when the file changes.
  $effect(() => {
    void buildStore.revision;
    let cancelled = false;
    api<{ history: HistoryEntry[] }>("/build/history")
      .then((data) => { if (!cancelled) history = data.history; })
      .catch(() => { /* The list stays as it was. */ });
    return () => { cancelled = true; };
  });

  async function about(field: "name" | "author" | "description", value: string) {
    if (value === buildStore.build[field]) return;
    if (await setPreferences({ [field]: value })) notice = "Saved to your build.";
  }

  async function preview(text: string, source: string) {
    incoming = { text, source, preview: null, problems: [], error: "" };
    acceptSensitive = false;
    held = [];
    restartPrompt = false;
    notice = "";
    let answer: Partial<Incoming>;
    try {
      answer = { preview: await api<Preview>("/build/preview", { text }) };
    } catch (cause) {
      answer = { error: (cause as Error).message, problems: cause instanceof ApiError ? cause.problems : [] };
    }
    // Cancelled, or another build chosen meanwhile.
    if (incoming?.text === text) incoming = { ...incoming, ...answer };
  }

  async function fromFile(event: Event & { currentTarget: HTMLInputElement }) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    error = "";
    try {
      await preview(await file.text(), file.name);
    } catch (cause) {
      error = `Could not read ${file.name}: ${(cause as Error).message}`;
    }
  }

  async function fromLink(event: SubmitEvent) {
    event.preventDefault();
    if (busy) return;
    busy = "fetch";
    error = "";
    try {
      let task = (await api<{ task: TaskRecord }>("/build/fetch", { url })).task;
      while (task.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 400));
        task = await api<TaskRecord>(`/tasks/${task.id}`);
      }
      if (task.status === "failed") throw new Error(task.error || "Could not fetch that build");
      const result = task.result as { text: string; source: string };
      await preview(result.text, result.source);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }

  async function install() {
    if (!incoming?.preview || busy) return;
    busy = "install";
    error = "";
    try {
      const result = await api<{ ok: boolean; restart: boolean; held: Change[] }>("/build/install", {
        text: incoming.text, base: incoming.preview.base, accept_sensitive: acceptSensitive, source: incoming.source,
      });
      held = result.held;
      restartPrompt = result.restart;
      incoming = null;
      notice = "Installed. Every window now uses this build; your previous build is in the history below.";
      await reloadBuild();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }

  async function rollback(id?: string) {
    if (busy) return;
    busy = "rollback";
    error = "";
    try {
      await api("/build/rollback", id ? { id } : {});
      notice = "Rolled back. The build you replaced is in the history too.";
      held = [];
      await reloadBuild();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }

  const when = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  };
</script>

<Panel title="Build" aria-label="Build settings">
    <p class="muted">Your build is one file, <code>config/build.json</code>: modules, settings, theme tokens, custom CSS and interface preferences. Changes apply to every window at once.</p>
    {#if fileProblems.length}
      <div class="problem" role="alert">
        <strong>The build file has an error; the last good build stays applied.</strong>
        <ul>{#each fileProblems as problem}<li>Line {problem.line}, column {problem.column}: {problem.message}</li>{/each}</ul>
      </div>
    {/if}
    <div class="about">
      <label>Name<input value={buildStore.build.name} maxlength="80" onchange={(event) => void about("name", event.currentTarget.value)} /></label>
      <label>Author<input value={buildStore.build.author} maxlength="80" onchange={(event) => void about("author", event.currentTarget.value)} /></label>
      <label class="wide">Description<textarea rows="2" value={buildStore.build.description} onchange={(event) => void about("description", event.currentTarget.value)}></textarea></label>
    </div>
    <div class="links">
      <Button intent="primary" size="sm" onclick={() => (buildStore.editorOpen = true)}>Open build editor</Button>
      <a class="button" href="/api/build/export" download="kitty-build.json">Export build</a>
      <label class="button file">Import from file<input type="file" accept=".json,application/json" onchange={fromFile} /></label>
    </div>
    <form class="link" onsubmit={fromLink}>
      <label>Import from link<input type="url" placeholder="https://…/kitty-build.json" pattern="https://.*" bind:value={url} required /></label>
      <Button size="sm" type="submit" disabled={!!busy || !url}>{busy === "fetch" ? "Fetching…" : "Preview"}</Button>
    </form>

    {#if incoming}
      <div class="incoming" aria-label="Build to install">
        <h3>{incoming.preview?.about.name || "Unnamed build"}{#if incoming.preview?.about.author} · by {incoming.preview.about.author}{/if}</h3>
        <p class="muted">From {incoming.source || "a file"}{#if incoming.preview?.about.description} · {incoming.preview.about.description}{/if}</p>
        {#if incoming.error}
          <div class="problem" role="alert">
            <strong>{incoming.error}</strong>
            {#if incoming.problems.length}<ul>{#each incoming.problems as problem}<li>Line {problem.line}: {problem.message}</li>{/each}</ul>{/if}
          </div>
        {:else if !incoming.preview}
          <p class="muted" role="status">Comparing with your build…</p>
        {:else}
          {#each incoming.preview.warnings as warning}<p class="muted">{warning}</p>{/each}
          {#if incoming.preview.changes.length}
            <table class="diff">
              <thead><tr><th scope="col">Setting</th><th scope="col">Now → after install</th><th scope="col"><span class="sr-only">Notes</span></th></tr></thead>
              <tbody>
                {#each incoming.preview.changes as change (change.path)}
                  <tr data-path={change.path}>
                    <th scope="row"><code>{change.path}</code></th>
                    <td><span class="before">{describeValue(change.path, change.before)}</span> → <span class="after">{describeValue(change.path, change.after)}</span></td>
                    <td class="badges">
                      {#if change.restart}<Badge label="Restart" intent="warning" size="sm" />{/if}
                      {#if change.sensitive}<Badge label="Privacy / cost" intent="danger" size="sm" />{/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="muted" role="status">This build matches yours; installing changes nothing.</p>
          {/if}
          {#if sensitive}
            <label class="check"><input type="checkbox" bind:checked={acceptSensitive} />Also apply privacy and cost changes</label>
            {#if !acceptSensitive}<p class="muted">Changes marked Privacy / cost stay as they are unless you tick this.</p>{/if}
          {/if}
          {#if incoming.preview.restart}<p class="muted">Module changes apply after restarting kitty.</p>{/if}
          <div class="links">
            <Button intent="primary" size="sm" disabled={!!busy} onclick={install}>{busy === "install" ? "Installing…" : "Install"}</Button>
            <Button size="sm" variant="ghost" onclick={() => (incoming = null)}>Cancel</Button>
          </div>
        {/if}
      </div>
    {/if}

    {#if restartPrompt}
      <p class="restart" role="status">This build changes modules. They apply after you restart kitty: quit kitty and start it again. Until then the running session is unchanged.</p>
    {/if}
    {#if held.length}
      <div class="muted" role="status">Kept as they were (privacy or cost):
        <ul>{#each held as change}<li><code>{change.path}</code>: stays {describeValue(change.path, change.before)}</li>{/each}</ul>
      </div>
    {/if}
    {#if notice}<p class="muted" role="status">{notice}</p>{/if}
    {#if error}<p role="alert">{error}</p>{/if}

    <div class="history">
      <h3>History</h3>
      <Button size="sm" intent="primary" disabled={!history.length || !!busy} onclick={() => rollback()}>Roll back to previous build</Button>
      {#if history.length}
        <ol>
          {#each history as entry (entry.id)}
            <li>
              <span><strong>{entry.name || "Unnamed build"}</strong> · {entry.reason || "saved"}{#if entry.source} · {entry.source}{/if} · {when(entry.saved_at)}{#if !entry.valid} · cannot be restored{/if}</span>
              <Button size="sm" variant="ghost" disabled={!entry.valid || !!busy} aria-label={`Roll back to the build saved ${when(entry.saved_at)}`} onclick={() => rollback(entry.id)}>Roll back</Button>
            </li>
          {/each}
        </ol>
      {:else}
        <p class="muted">No earlier builds yet. Installing or rolling back keeps the build it replaces here.</p>
      {/if}
    </div>
</Panel>

<style>
  .muted { color: var(--muted); font-size: .8rem; }
  .about { display: grid; grid-template-columns: 1fr 1fr; gap: .6rem; margin: .75rem 0; }
  .about label, .link label { display: grid; gap: .25rem; font-size: .8rem; }
  .about .wide { grid-column: 1 / -1; }
  .links { display: flex; flex-wrap: wrap; align-items: center; gap: .65rem; margin: .5rem 0; }
  .file { position: relative; overflow: hidden; cursor: pointer; }
  .file input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
  .link { display: flex; align-items: end; gap: .65rem; margin: .5rem 0; }
  .link label { flex: 1; }
  .incoming { margin: .75rem 0; padding: .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; }
  h3 { margin: 0 0 .25rem; font-size: .95rem; }
  .diff { width: 100%; border-collapse: collapse; margin: .5rem 0; font-size: .8rem; }
  .diff th, .diff td { padding: .3rem .4rem; border-bottom: 1px solid var(--border-fine, var(--border)); text-align: left; vertical-align: top; }
  .diff .before { color: var(--muted); }
  .badges { white-space: nowrap; }
  .check { display: flex; align-items: center; gap: .5rem; margin: .5rem 0; }
  .problem { margin: .5rem 0; padding: .55rem .75rem; border: 1px solid var(--danger, var(--border)); border-radius: 8px; font-size: .85rem; }
  .problem ul { margin: .25rem 0 0; padding-left: 1.2rem; }
  .restart { margin: .75rem 0; padding: .55rem .75rem; border: 1px solid var(--warning); border-radius: 8px; font-size: .85rem; }
  .history { margin-top: 1rem; display: grid; gap: .5rem; justify-items: start; }
  .history ol { display: grid; gap: .35rem; margin: 0; padding: 0; list-style: none; width: 100%; }
  .history li { display: flex; justify-content: space-between; align-items: center; gap: .75rem; font-size: .8rem; }
</style>

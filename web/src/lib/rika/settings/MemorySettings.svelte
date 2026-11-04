<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "../../api";
  import type { MemoryState, Preference } from "./settings";

  /**
   * Q18 A: Settings → Rika → Memory. A local search index over the workspace (search it to
   * see what a request could fetch), counted per source, and the preferences Rika keeps
   * about you: edit one in place (saved on Enter or leaving the field), forget it, or tell
   * Rika something new. Everything stays on this device; each request fetches only what
   * it needs.
   */
  const SEARCH_MS = 250;
  /** Results listed at once: a sample of what a request could fetch, not a browser. */
  const SHOWN = 8;
  let memory = $state<MemoryState | null>(null);
  let query = $state("");
  let results = $state<MemoryState["results"]>([]);
  let searched = $state("");
  let error = $state("");
  let busy = $state("");
  let adding = $state("");
  let edits = $state<Record<number, string>>({});
  let searchTimer = 0;
  let sequence = 0;

  const labels = $derived(new Map((memory?.index.sources ?? []).map((source) => [source.source, source.label])));
  const searching = $derived(query.trim() !== searched);

  async function refresh() {
    try {
      memory = await api<MemoryState>("/rika/memory");
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(() => {
    // An index never built yet (a fresh workspace) is built once, so the counts are real.
    void refresh().then(() => { if (memory?.index.sources.length && memory.index.sources.every((source) => !source.refreshed_at)) rebuild(); });
    return () => window.clearTimeout(searchTimer);
  });

  /** Debounced: each pause in typing asks the index once; a stale answer is dropped. */
  function search(value: string) {
    query = value;
    window.clearTimeout(searchTimer);
    const text = value.trim();
    if (!text) {
      sequence += 1;
      results = [];
      searched = "";
      return;
    }
    searchTimer = window.setTimeout(async () => {
      const ticket = ++sequence;
      try {
        const found = await api<MemoryState>(`/rika/memory?q=${encodeURIComponent(text)}`);
        if (ticket !== sequence) return;
        memory = found;
        results = found.results;
        searched = text;
        error = "";
      } catch (cause) {
        if (ticket === sequence) error = (cause as Error).message;
      }
    }, SEARCH_MS);
  }

  async function run(key: string, work: () => Promise<void>) {
    busy = key;
    try {
      await work();
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }
  const replace = (preference: Preference) => {
    if (memory) memory = { ...memory, preferences: memory.preferences.map((item) => (item.id === preference.id ? preference : item)) };
  };
  function save(preference: Preference) {
    const text = edits[preference.id]?.trim();
    delete edits[preference.id];
    if (!text || text === preference.text) return;
    void run(`save:${preference.id}`, async () => replace((await api<{ preference: Preference }>(`/rika/preferences/${preference.id}`, { text })).preference));
  }
  function forget(preference: Preference) {
    void run(`delete:${preference.id}`, async () => {
      const { deleted } = await api<{ deleted: number }>(`/rika/preferences/${preference.id}/delete`, {});
      if (memory) memory = { ...memory, preferences: memory.preferences.filter((item) => item.id !== deleted) };
    });
  }
  function add(event: SubmitEvent) {
    event.preventDefault();
    const text = adding.trim();
    if (!text) return;
    void run("add", async () => {
      const { preference } = await api<{ preference: Preference }>("/rika/preferences", { text });
      if (memory) memory = { ...memory, preferences: [...memory.preferences, preference] };
      adding = "";
    });
  }
  function rebuild() {
    void run("rebuild", async () => {
      const { index } = await api<{ index: MemoryState["index"] }>("/rika/memory/rebuild", {});
      if (memory) memory = { ...memory, index };
    });
  }
  const day = (value: string | null) => {
    const date = new Date(value ?? "");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString([], { day: "numeric", month: "short" });
  };
  const when = (value: string | null) => {
    const date = new Date(value ?? "");
    return Number.isNaN(date.getTime()) ? "never" : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  const origin = (preference: Preference) =>
    preference.updated_at !== preference.created_at ? `Edited by you · ${day(preference.updated_at)}`
      : preference.source === "stated" ? `You told Rika · ${day(preference.created_at)}` : `Added by you · ${day(preference.created_at)}`;
</script>

<Panel title="Memory" aria-label="Memory">
  {#if memory}
    <div class="rows">
      <div class="set-row">
        <span class="set-desc">What Rika knows stays on this device. Each request fetches only the few items it needs.</span>
      </div>
      <div class="set-row section">
        <span class="set-label">Index</span>
        <span class="set-desc count">{memory.index.total.toLocaleString("en")} items</span>
        <span class="set-end"><Button size="sm" variant="ghost" disabled={busy === "rebuild"} onclick={rebuild}>{busy === "rebuild" ? "Rebuilding…" : "Rebuild index"}</Button></span>
      </div>
      <ul class="sources" aria-label="Indexed sources">
        {#each memory.index.sources as source (source.source)}
          <li title={`Refreshed ${when(source.refreshed_at)}`}><strong>{source.label}</strong><span class="count">{source.items.toLocaleString("en")}</span></li>
        {/each}
      </ul>
      <div class="set-row search">
        <input type="search" aria-label="Search what Rika knows" placeholder="Search everything Rika can see" value={query} oninput={(event) => search(event.currentTarget.value)} />
      </div>
      {#if query.trim()}
        <ul class="hits" aria-label="Search results" aria-busy={searching}>
          {#each results.slice(0, SHOWN) as hit (`${hit.source}:${hit.ref}`)}
            <li>
              <em>{labels.get(hit.source) ?? hit.source}</em>
              {#if hit.view}<a href={`#${hit.view}`}>{hit.title}</a>{:else}<strong>{hit.title}</strong>{/if}
              {#if hit.snippet && hit.snippet !== hit.title}<span class="snippet">{hit.snippet}</span>{/if}
            </li>
          {:else}
            {#if !searching}<li class="none">Nothing indexed matches “{query.trim()}”.</li>{/if}
          {/each}
          {#if results.length > SHOWN}<li class="none">and {results.length - SHOWN} more; a longer search narrows them.</li>{/if}
        </ul>
      {/if}
      <p class="set-note">
        {memory.notes_shared ? "Notes AI sharing is on: Rika may send the text of notes a request needs to the model." : "Note text stays local: Rika only sends note titles to the model."}
      </p>

      <div class="set-row section">
        <span class="set-label">Preferences Rika learned</span>
        <span class="set-desc">Edit or forget anything it got wrong.</span>
      </div>
      <ul class="prefs" aria-label="Preferences">
        {#each memory.preferences as preference (preference.id)}
          <li>
            <input aria-label="Preference" value={edits[preference.id] ?? preference.text} disabled={busy === `delete:${preference.id}`}
              oninput={(event) => (edits[preference.id] = event.currentTarget.value)}
              onkeydown={(event) => { if (event.key === "Enter") { event.preventDefault(); event.currentTarget.blur(); } else if (event.key === "Escape") { delete edits[preference.id]; event.currentTarget.blur(); } }}
              onblur={() => save(preference)} />
            <button type="button" class="icon-button" aria-label={`Forget “${preference.text}”`} title="Forget this" onclick={() => forget(preference)}>×</button>
            <small>{origin(preference)}</small>
          </li>
        {:else}
          <li class="none">Nothing yet. Tell Rika how you like things done and it keeps it here.</li>
        {/each}
      </ul>
      <form class="set-row add" onsubmit={add}>
        <input aria-label="Tell Rika something to remember" placeholder="Tell Rika something to remember" bind:value={adding} />
        <Button size="sm" type="submit" disabled={!adding.trim() || busy === "add"}>Add</Button>
      </form>
    </div>
  {:else if !error}
    <p class="set-note" role="status">Loading memory…</p>
  {/if}
  {#if error}<p class="set-note" role="alert">{error} <button class="text-button" onclick={refresh}>Retry</button></p>{/if}
</Panel>

<style>
  .section .set-label {
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  .count {
    color: var(--muted);
    font: 11px var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  ul {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .sources {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 2px 18px;
    padding: 0 0 10px;
  }
  .sources li {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    padding: 3px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
    font-size: 12.5px;
    cursor: help;
  }
  strong {
    color: var(--ink);
    font-weight: 500;
  }
  .search input,
  .add input {
    flex: 1;
    min-width: 0;
  }
  .hits {
    gap: 2px;
    padding: 0 0 8px;
  }
  .hits li {
    display: grid;
    grid-template-columns: 92px minmax(0, 1fr);
    column-gap: 10px;
    padding: 4px 0;
    font-size: 12.5px;
  }
  .hits em {
    grid-row: span 2;
    color: var(--subtle);
    font: 500 9.5px/1.9 var(--font-mono);
    font-style: normal;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .hits a {
    color: var(--ink);
    font-weight: 500;
  }
  .snippet {
    grid-column: 2;
    overflow: hidden;
    color: var(--muted);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .none {
    padding: 4px 0 8px;
    color: var(--muted);
    font-size: 12.5px;
  }
  .hits .none {
    display: block;
  }
  .prefs li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: 6px;
    padding: 5px 0;
  }
  .prefs li.none {
    display: block;
  }
  .prefs input {
    min-width: 0;
  }
  .prefs small {
    grid-column: 1;
    padding: 2px 2px 0;
    color: var(--muted);
    font-size: 11px;
  }
  .add {
    gap: 8px;
  }
</style>

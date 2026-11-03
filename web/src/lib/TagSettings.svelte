<!-- Settings → Tags (Q13): the global mode (Suggest or Always attach), new tags and each tag's keywords. -->
<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import TagEditor from "./TagEditor.svelte";
  import { MODE_LABELS, createTag, deleteTag, listTags, parseTerms, setTagMode, type TagMode, type TagsState } from "./tags";

  let tagsState = $state<TagsState | null>(null);
  let error = $state("");
  let notice = $state("");
  let busy = $state(false);
  let open = $state<string | null>(null);
  let name = $state("");
  let keywords = $state("");
  const MODES: TagMode[] = ["suggest", "always"];

  async function refresh() {
    try { tagsState = await listTags(); error = ""; }
    catch (cause) { error = (cause as Error).message; }
  }
  onMount(() => { void refresh(); });

  async function run(action: () => Promise<unknown>, done: string) {
    if (busy) return;
    busy = true; error = ""; notice = "";
    try { await action(); notice = done; await refresh(); }
    catch (cause) { error = (cause as Error).message; await refresh(); }
    finally { busy = false; }
  }
  function chooseMode(mode: TagMode) {
    const current = tagsState;
    if (!current || current.mode === mode) return;
    void run(() => setTagMode(mode, current.revision), `Tags now ${mode === "always" ? "attach automatically" : "are suggested"}`);
  }
  async function create(event: SubmitEvent) {
    event.preventDefault();
    const title = name.trim();
    if (!title) return;
    await run(async () => {
      const tag = await createTag({ name: title, keywords: parseTerms(keywords) });
      open = tag.id;
    }, `#${title} created`);
    if (!error) { name = ""; keywords = ""; }
  }
  function remove(id: string, title: string) {
    if (!window.confirm(`Move #${title} to Trash? Blocks keep their text; the tag comes back with Trash → Restore.`)) return;
    void run(() => deleteTag(id), `#${title} moved to Trash`);
  }
</script>

<Panel title="Tags" aria-label="Tags">
    <div class="tags">
      <p class="muted">When a block mentions a tag's name, alias or keyword, rika! can suggest the tag or attach it. Each tag can override this.</p>
      {#if tagsState}
        <fieldset class="modes" disabled={busy}>
          <legend>When a keyword appears</legend>
          {#each MODES as mode}
            <label class="mode">
              <input type="radio" name="tag-mode" value={mode} checked={tagsState.mode === mode} onchange={() => chooseMode(mode)} />
              <span><strong>{MODE_LABELS[mode]}</strong>
                <small>{mode === "suggest" ? "Underline it faintly; Tab or a click attaches the tag, Esc ignores it." : "Attach the tag right away (marked automatic); removing it is remembered."}</small></span>
            </label>
          {/each}
        </fieldset>
        <form class="new" aria-label="New tag" onsubmit={create}>
          <input aria-label="New tag name" placeholder="New tag name" bind:value={name} maxlength="100" />
          <input aria-label="New tag keywords" placeholder="keywords, comma-separated" bind:value={keywords} />
          <Button size="sm" type="submit" disabled={busy || !name.trim()}>Add tag</Button>
        </form>
        <ul class="rows">
          {#each tagsState.tags as tag (tag.id)}
            <li class="row">
              <div class="summary">
                <button class="name" aria-expanded={open === tag.id} onclick={() => (open = open === tag.id ? null : tag.id)}>#{tag.name}</button>
                <small>{tag.count} block{tag.count === 1 ? "" : "s"} · {MODE_LABELS[tag.effective_mode]}{tag.mode ? "" : " (setting)"}{tag.keywords.length ? ` · ${tag.keywords.join(", ")}` : ""}</small>
                <Button size="sm" variant="ghost" disabled={busy} onclick={() => remove(tag.id, tag.name)}>Delete</Button>
              </div>
              {#if open === tag.id}
                <TagEditor {tag} globalMode={tagsState.mode} onsaved={(saved) => { notice = `#${saved.name} saved`; void refresh(); }} />
              {/if}
            </li>
          {:else}<li class="muted">No tags yet. Writing #name in a note also makes a tag you can add keywords to.</li>{/each}
        </ul>
      {:else if !error}<p class="muted" role="status">Loading tags…</p>{/if}
      {#if notice}<p class="muted" role="status">{notice}</p>{/if}
      {#if error}<p role="alert">{error}</p>{/if}
    </div>
</Panel>

<style>
  .tags { display: grid; gap: .75rem; }
  .muted { color: var(--muted); font-size: .8rem; margin: 0; }
  .modes { display: grid; gap: .5rem; margin: 0; padding: 0; border: 0; min-width: 0; }
  legend { font-size: .8rem; color: var(--muted); margin-bottom: .35rem; }
  .mode { display: flex; align-items: start; gap: .75rem; padding: .6rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; cursor: pointer; }
  .mode span { display: grid; gap: .15rem; }
  .mode small { color: var(--muted); line-height: 1.5; }
  .mode input { margin-top: .2rem; accent-color: var(--accent); }
  .new { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
  .new input { font: inherit; font-size: .85rem; color: var(--ink); background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: .35rem .55rem; flex: 1; min-width: 160px; }
  .rows { list-style: none; margin: 0; padding: 0; display: grid; gap: .4rem; }
  .row { border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; padding: .45rem .6rem; display: grid; gap: .5rem; }
  .summary { display: flex; align-items: center; gap: .6rem; }
  .summary small { flex: 1; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .name { font: inherit; font-weight: 600; color: var(--accent); background: none; border: 0; padding: 0; cursor: pointer; }
  [role=alert] { color: var(--error); margin: 0; }
</style>

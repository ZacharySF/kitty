<!-- A tag's page (Q13): its blocks (backlinks) and the remaining unlinked mentions with Link all. -->
<script lang="ts">
  import { onMount } from "svelte";
  import { onBlockChange } from "./live";
  import TagEditor from "./TagEditor.svelte";
  import { MODE_LABELS, SOURCE_LABELS, linkAll, listTags, removeTag, tagPage, type TagMode, type TagPageData } from "./tags";

  let { tagId, onopen }: { tagId: string; onopen: (page: number, blockId: string) => void } = $props();
  let data = $state<TagPageData | null>(null);
  let globalMode = $state<TagMode>("suggest");
  let error = $state("");
  let notice = $state("");
  let busy = $state(false);
  let editing = $state(false);

  async function load() {
    const id = tagId;
    try {
      const [next, settings] = await Promise.all([tagPage(id), listTags()]);
      if (id !== tagId) return;
      data = next;
      globalMode = settings.mode;
      error = "";
    } catch (cause) {
      if (id === tagId) error = (cause as Error).message;
    }
  }
  async function more() {
    const current = data;
    if (!current || current.next_offset === null) return;
    try {
      const next = await tagPage(current.tag.id, current.next_offset);
      if (data === current) data = { ...next, blocks: [...current.blocks, ...next.blocks] };
    } catch (cause) { error = (cause as Error).message; }
  }

  $effect(() => {
    void tagId;
    data = null;
    editing = false;
    notice = "";
    void load();
  });

  // Every committed change may tag or untag a block: refresh shortly after edits settle.
  onMount(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = onBlockChange(() => {
      clearTimeout(timer);
      timer = setTimeout(() => void load(), 400);
    });
    return () => { clearTimeout(timer); stop(); };
  });

  async function act(action: () => Promise<unknown>, done: string) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await action();
      notice = done;
      await load();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<section class="tag-page" aria-label="Tag page">
  {#if error}<p role="alert">{error}</p>{/if}
  {#if data}
    {@const tag = data.tag}
    <header>
      <h3>#{tag.name}</h3>
      <small>{MODE_LABELS[tag.effective_mode]}{tag.mode ? "" : " (setting)"}{tag.keywords.length ? ` · ${tag.keywords.join(", ")}` : ""}</small>
      <button class="link" aria-expanded={editing} onclick={() => (editing = !editing)}>{editing ? "Close" : "Keywords & mode"}</button>
    </header>
    {#if editing}
      <TagEditor {tag} {globalMode} onsaved={() => { editing = false; notice = "Tag saved"; void load(); }} />
    {/if}
    <h4>Tagged blocks <span>{data.count}</span></h4>
    {#each data.blocks as item (item.id)}
      <div class="item">
        {#if item.number !== null && item.page_type === "page"}
          <button class="open" onclick={() => onopen(item.number!, item.id)}><strong>{item.title || "Untitled page"}</strong><span>{item.text}</span></button>
        {:else}
          <p class="open"><strong>{item.title}</strong><span>{item.text}</span></p>
        {/if}
        <small>{item.inline ? `#${tag.name} in the text` : SOURCE_LABELS[item.source]}</small>
        {#if !item.inline}
          <button class="remove" aria-label={`Untag ${item.text.slice(0, 40)}`} disabled={busy}
            onclick={() => void act(() => removeTag(item.id, tag.id), "Removed; it will not be tagged again")}>×</button>
        {/if}
      </div>
    {:else}<p class="empty">No blocks carry this tag yet.</p>{/each}
    {#if data.next_offset !== null}<button class="link" onclick={() => void more()}>Show more ({data.blocks.length} of {data.count})</button>{/if}
    <h4>Unlinked mentions <span>{data.unlinked_count}</span>
      {#if data.unlinked.length}<button class="link-all" disabled={busy} onclick={() => void act(() => linkAll(tag.id), "Linked every mention")}>Link all</button>{/if}
    </h4>
    {#each data.unlinked as item (item.id)}
      <div class="item">
        <button class="open" onclick={() => item.number !== null && onopen(item.number, item.id)}><strong>{item.title || "Untitled page"}</strong><span>{item.text}</span></button>
      </div>
    {:else}<p class="empty">No other blocks mention this tag's name or keywords.</p>{/each}
    {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {:else if !error}<p class="empty" role="status">Loading tag…</p>{/if}
</section>

<style>
  .tag-page { font-size: 13px; }
  header { display: grid; gap: 4px; margin-bottom: 8px; }
  h3 { margin: 0; font-size: 15px; color: var(--accent); }
  header small { color: var(--muted); }
  h4 { display: flex; align-items: center; gap: 8px; margin: 18px 0 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
  h4 span { font-variant-numeric: tabular-nums; }
  .item { display: flex; align-items: flex-start; gap: 6px; border-bottom: 1px solid var(--border); padding: 8px 0; }
  .open { flex: 1; display: flex; flex-direction: column; gap: 4px; min-width: 0; margin: 0; text-align: left; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 12px; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
  p.open { cursor: default; }
  .item small { color: var(--muted); font-size: 10px; white-space: nowrap; }
  button { font: inherit; cursor: pointer; }
  .link, .link-all { color: var(--accent); background: none; border: 0; padding: 2px 4px; text-align: left; justify-self: start; }
  .link-all { margin-left: auto; text-transform: none; letter-spacing: 0; font-size: 12px; font-weight: 600; }
  .remove { color: var(--muted); background: none; border: 0; padding: 0 4px; }
  .remove:hover { color: var(--error); }
  .empty, .notice { color: var(--muted); font-size: 12px; }
  [role=alert] { color: var(--error); }
</style>

<script lang="ts">
  // Trash (platform design Q15): deleted pages and blocks, newest first, kept for the
  // 30-day history window and then purged. Restoring brings back everything that one
  // delete sent to Trash, as a new transaction.
  import { onMount } from "svelte";
  import { api } from "./api";
  import { onBlockChange } from "./live";

  type Item = { id: string; type: string; page_id: string; deleted_at: string; title: string; root: { type: string; number: number | null; title: string } };
  const RETENTION_DAYS = 30;
  const KINDS: Record<string, string> = { page: "Note page", document: "Document", quick_note: "Quick note" };

  let { onclose, onopen }: { onclose: () => void; onopen?: (pageNumber: number) => void } = $props();
  let dialog: HTMLDialogElement;
  let items = $state<Item[] | null>(null);
  let error = $state("");
  let restored = $state<Item | null>(null);
  let busy = $state("");

  const isRoot = (item: Item) => item.id === item.page_id;
  const daysLeft = (item: Item) => Math.max(0, Math.ceil(RETENTION_DAYS - (Date.now() - new Date(item.deleted_at).getTime()) / 86_400_000));
  const when = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

  async function load() {
    try {
      items = (await api<{ items: Item[] }>("/blocks/trash")).items;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }

  async function restore(item: Item) {
    busy = item.id;
    try {
      await api("/blocks/trash/restore", { id: item.id });
      restored = item;
      error = "";
      await load();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }

  onMount(() => {
    dialog.showModal();
    void load();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = onBlockChange(() => {
      clearTimeout(timer);
      timer = setTimeout(() => void load(), 400);
    });
    return () => { clearTimeout(timer); stop(); };
  });
</script>

<dialog bind:this={dialog} class="workspace-dialog block-trash" aria-labelledby="trash-title" oncancel={onclose}>
  <header>
    <div>
      <span class="eyebrow">DELETED ITEMS ARE KEPT FOR {RETENTION_DAYS} DAYS</span>
      <h2 id="trash-title">Trash</h2>
    </div>
    <button class="icon-button" onclick={onclose} aria-label="Close Trash">×</button>
  </header>
  {#if error}<p class="problem" role="alert">{error}</p>{/if}
  {#if restored}
    <p class="done" role="status">
      Restored “{restored.title || restored.root.title || "Untitled"}”.
      {#if restored.root.type === "page" && restored.root.number && onopen}
        {@const number = restored.root.number}
        <button class="link" onclick={() => onopen?.(number)}>Open page</button>
      {/if}
    </p>
  {/if}
  {#if items === null}
    <p class="muted">Loading Trash…</p>
  {:else if !items.length}
    <p class="muted">Trash is empty.</p>
  {:else}
    <ul aria-label="Deleted items">
      {#each items as item (item.id)}
        <li>
          <div>
            <strong>{item.title || (isRoot(item) ? "Untitled" : "Empty block")}</strong>
            <span class="muted">
              {isRoot(item) ? KINDS[item.root.type] ?? item.root.type : `Block on “${item.root.title || "Untitled"}”`}
              · deleted {when(item.deleted_at)} · {daysLeft(item)} day{daysLeft(item) === 1 ? "" : "s"} left
            </span>
          </div>
          <button class="button" disabled={busy !== ""} onclick={() => void restore(item)} aria-label={`Restore ${item.title || "item"}`}>
            {busy === item.id ? "Restoring…" : "Restore"}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</dialog>

<style>
  .block-trash { max-width: 680px; }
  .eyebrow { font-size: 11px; letter-spacing: .06em; color: var(--muted); }
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  li { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 4px; border-top: 1px solid var(--border); }
  li div { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  li strong { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  li span { font-size: 12px; }
  .problem { color: var(--error); }
  .done { color: var(--success); }
  .link { font: inherit; color: var(--accent); background: none; border: 0; padding: 0; cursor: pointer; text-decoration: underline; }
</style>

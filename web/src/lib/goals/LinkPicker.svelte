<script lang="ts">
  import Icon from "../Icon.svelte";
  import Popover from "../ui/Popover.svelte";
  import Select from "../ui/Select.svelte";
  import { findLinkable } from "./api";
  import { KIND_LABEL, linkKey, type LinkKind, type Linkable } from "./goals";

  /**
   * Q14 A: links a note, task, job, study session or other item to a goal. Search what
   * the active modules hold (≤ 20 results); choosing one links it.
   */
  let { linked, onpick }: { linked: Set<string>; onpick: (item: Linkable) => Promise<boolean> } = $props();
  const KINDS = [{ value: "", label: "Everything" }, ...(Object.entries(KIND_LABEL) as [LinkKind, string][]).map(([value, label]) => ({ value, label }))];

  let open = $state(false);
  let query = $state("");
  let kind = $state("");
  let items = $state<Linkable[] | null>(null);
  let error = $state("");
  let busy = $state(false);

  $effect(() => {
    if (!open) return;
    const text = query.trim();
    const only = kind as LinkKind | "";
    let cancelled = false;
    const timer = setTimeout(() => {
      findLinkable(text, only)
        .then((found) => {
          if (cancelled) return;
          items = found;
          error = "";
        })
        .catch((cause) => {
          if (!cancelled) error = (cause as Error).message;
        });
    }, 160);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  });

  async function pick(item: Linkable) {
    busy = true;
    try {
      if (await onpick(item)) open = false;
    } finally {
      busy = false;
    }
  }
</script>

<Popover bind:open label="Link an item to this goal" placement="bottom-end" class="goal-link-picker" onclose={() => (query = "")}>
  {#snippet trigger(props)}
    <button type="button" class="pill" {...props}><Icon name="plus" size={13} />Link item</button>
  {/snippet}
  <div class="picker">
    <input type="search" bind:value={query} placeholder="Search notes, tasks, jobs…" aria-label="Search items to link" data-autofocus />
    <Select label="Kind of item" kind="list" bind:value={kind} options={KINDS} />
    {#if error}
      <p class="note" role="alert">{error}</p>
    {:else if !items}
      <p class="note" role="status">Searching…</p>
    {:else if !items.length}
      <p class="note">Nothing matches{query.trim() ? ` “${query.trim()}”` : ""}.</p>
    {:else}
      <ul aria-label="Items to link">
        {#each items as item (linkKey(item))}
          {@const already = linked.has(linkKey(item))}
          <li>
            <button type="button" disabled={busy || already} onclick={() => pick(item)}>
              <span class="kind">{KIND_LABEL[item.kind] ?? item.kind}</span>
              <span class="what"><b>{item.title}</b>{#if item.detail || already}<small>{already ? "Already linked" : item.detail}</small>{/if}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</Popover>

<style>
  /* Outranks the popover's own padding (ui/Popover.svelte). */
  :global(html:root body .kitty-popover.goal-link-picker) {
    width: min(380px, calc(100vw - 16px));
    padding: 10px;
  }
  .picker {
    display: grid;
    gap: 8px;
  }
  input {
    width: 100%;
    box-sizing: border-box;
  }
  ul {
    display: grid;
    max-height: 300px;
    margin: 0;
    padding: 0;
    overflow: auto;
    list-style: none;
  }
  li + li {
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  li button {
    display: flex;
    align-items: baseline;
    gap: 8px;
    width: 100%;
    min-height: 38px;
    padding: 6px 4px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--ink);
    text-align: left;
  }
  li button:hover:not(:disabled) {
    background: color-mix(in srgb, var(--ink) 7%, transparent);
  }
  li button:disabled {
    opacity: 0.55;
  }
  .kind {
    flex: none;
    width: 58px;
    color: var(--subtle);
    font: 500 9.5px/1.6 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .what {
    display: grid;
    min-width: 0;
  }
  b {
    overflow: hidden;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  small,
  .note {
    color: var(--muted);
    font-size: 11px;
  }
  .note {
    margin: 2px 0;
  }
</style>

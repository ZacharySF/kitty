<script lang="ts">
  import { tick } from "svelte";
  import Icon from "./Icon.svelte";
  import OverflowMenu, { type OverflowItem } from "./ui/OverflowMenu.svelte";
  import Popover from "./ui/Popover.svelte";
  import { SECTION_DRAG_TYPE, type AtlasSection } from "./atlasNodes";
  import type { View } from "./workspace";

  type Section = AtlasSection["items"][number];

  /**
   * Q13: home's Sections, a popover from the stage corner with section search, the grouped
   * list and each row's ⋯ (open, show on the map, hide or show on the atlas). Rows are drag
   * sources: a hidden one dropped on the graph comes back. A node held over the control is
   * hidden when released (the atlas does the drop). `inline` draws the list in the stage
   * when no graph can be drawn.
   */
  let {
    sections,
    hidden,
    counts,
    current = null,
    holding = null,
    inline = false,
    error = "",
    open = $bindable(false),
    locatable,
    onview,
    onlocate,
    ontoggle,
    ondrag,
    onretry,
  }: {
    sections: AtlasSection[];
    hidden: Set<string>;
    /** Linked items per section. */
    counts: Map<View, number>;
    /** The section last found on the map. */
    current?: View | null;
    /** Label of a node held over this control, which a release hides. */
    holding?: string | null;
    inline?: boolean;
    /** Why the linked counts could not be read. */
    error?: string;
    open?: boolean;
    locatable: (view: View) => boolean;
    onview: (view: View) => unknown;
    onlocate: (view: View) => void;
    ontoggle: (view: View) => void;
    ondrag: (view: View | null) => void;
    onretry: () => void;
  } = $props();

  const uid = $props.id();
  let root = $state<HTMLElement>();
  let find = $state<HTMLInputElement>();
  let query = $state("");

  const total = $derived(sections.reduce((sum, group) => sum + group.items.length, 0));
  const needle = $derived(query.trim().toLowerCase());
  /** Groups keep their colour while the list filters; a group's name matches all of it. */
  const shown = $derived(
    sections
      .map((group, index) => ({
        ...group,
        tone: `var(--c${index + 1})`,
        items: !needle || group.label.toLowerCase().includes(needle) ? group.items : group.items.filter((item) => item.label.toLowerCase().includes(needle)),
      }))
      .filter((group) => group.items.length),
  );
  /** Enter's target: a name that starts with the query beats one that only contains it. */
  const best = $derived.by(() => {
    const rows = shown.flatMap((group) => group.items);
    return rows.find((item) => item.label.toLowerCase().startsWith(needle)) ?? rows[0];
  });

  /** True when `node` is this control or its open popover. */
  export function contains(node: Node | null) {
    return !!node && !!root?.contains(node);
  }
  /** Opens the popover on its search field. */
  export async function focusSearch() {
    open = true;
    await tick();
    find?.focus();
  }

  function searchKey(event: KeyboardEvent) {
    if (event.key !== "Enter" || !best) return;
    event.preventDefault();
    // The first Enter finds the section on the map; the next one opens it.
    if (current !== best.id && locatable(best.id)) onlocate(best.id);
    else onview(best.id);
  }
  function startDrag(event: DragEvent, view: View) {
    if (!event.dataTransfer) return;
    event.dataTransfer.setData(SECTION_DRAG_TYPE, view);
    event.dataTransfer.effectAllowed = "move";
    ondrag(view);
  }
  function actions(item: Section): OverflowItem[] {
    return [
      { label: "Open", onselect: () => onview(item.id) },
      ...(locatable(item.id) ? [{ label: "Show on the map", onselect: () => onlocate(item.id) }] : []),
      { label: hidden.has(item.id) ? "Show on the atlas" : "Hide from the atlas", onselect: () => ontoggle(item.id) },
    ];
  }
</script>

{#snippet list()}
  <nav class="list" class:inline aria-label="Workspace sections">
    {#each shown as group (group.label)}
      <section aria-labelledby={`${uid}-${group.label}`}>
        <h3 id={`${uid}-${group.label}`}><span class="swatch" style:background={group.tone}></span>{group.label}</h3>
        <ul>
          {#each group.items as item (item.id)}
            <li class:hidden-section={hidden.has(item.id)} class:on={current === item.id}>
              <a
                href={`#${item.id}`}
                draggable="true"
                ondragstart={(event) => startDrag(event, item.id)}
                ondragend={() => ondrag(null)}
                onclick={(event) => {
                  event.preventDefault();
                  onview(item.id);
                }}
                ><Icon name={item.icon} size={14} /><span class="name">{item.label}</span>{#if counts.get(item.id)}<span
                    class="count"
                    aria-label={`${counts.get(item.id)} linked items`}>{counts.get(item.id)}</span
                  >{/if}</a
              >{#if hidden.has(item.id)}<span class="sr-only">Hidden from the atlas</span>{/if}
              <OverflowMenu label={`${item.label} options`} items={actions(item)} />
            </li>
          {/each}
        </ul>
      </section>
    {:else}
      <p class="empty">No section matches “{query.trim()}”.</p>
    {/each}
  </nav>
  {#if error}
    <p class="error" role="alert">Linked counts unavailable. <button type="button" class="text-button" onclick={onretry}>Retry</button></p>
  {/if}
{/snippet}

{#if inline}
  {@render list()}
{:else}
  <div class="sections" bind:this={root}>
    <Popover bind:open label="Sections" class="sections-popover" onclose={() => (query = "")}>
      {#snippet trigger(props)}
        <button type="button" class="corner-button" class:holding={!!holding} title="Sections (/)" {...props}
          ><Icon name="menu" size={14} /><span>Sections</span><span class="total">{total}</span>{#if holding}<span class="drop">Drop to hide {holding}</span>{/if}</button
        >
      {/snippet}
      <label class="find">
        <Icon name="jobs" size={13} />
        <input
          bind:this={find}
          bind:value={query}
          type="search"
          aria-label="Find a section"
          placeholder="Find a section"
          autocomplete="off"
          spellcheck="false"
          data-autofocus
          onkeydown={searchKey}
        />
        <kbd>/</kbd>
      </label>
      {@render list()}
    </Popover>
  </div>
{/if}

<style>
  .sections {
    display: contents;
  }
  .corner-button {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    height: var(--control-h);
    padding: 0 10px;
    border: 1px solid var(--brain-track, var(--hairline));
    border-radius: var(--r-control);
    background: var(--brain-ground, color-mix(in srgb, var(--solid-void, #000) 60%, transparent));
    color: var(--brain-ink, var(--ink));
    font: 500 var(--fs-control) / 1 var(--font-ui);
    backdrop-filter: var(--brain-blur, none);
    -webkit-backdrop-filter: var(--brain-blur, none);
    cursor: pointer;
  }
  .corner-button:hover,
  .corner-button[aria-expanded="true"] {
    border-color: var(--brain-edge-hot, var(--br-lineHot));
  }
  .corner-button.holding {
    border-style: dashed;
    border-color: var(--accent);
  }
  .total {
    color: var(--muted);
    font: 500 10.5px/1 var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .drop {
    color: var(--accent);
  }
  :global(:root body) :global(.kitty-popover.sections-popover) {
    width: 264px;
    max-height: min(440px, calc(100dvh - 16px));
    display: flex;
    flex-direction: column;
    gap: 4px;
    overflow: hidden;
  }
  .find {
    display: flex;
    flex: none;
    align-items: center;
    gap: 7px;
    height: var(--control-h);
    padding: 0 8px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    background: color-mix(in srgb, var(--solid-inset, #111) 45%, transparent);
    color: var(--subtle);
  }
  :global(:root body) .find input {
    flex: 1;
    min-width: 0;
    height: 100%;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: 400 var(--fs-control) / 1 var(--font-ui);
    outline: none;
  }
  .find:focus-within {
    border-color: var(--accent);
  }
  kbd {
    padding: 1px 5px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-chip);
    color: var(--subtle);
    font-size: 9.5px;
  }
  .list {
    min-height: 0;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline) transparent;
  }
  .list.inline {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 18px;
    overflow: visible;
  }
  h3 {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 0;
    padding: 8px 9px 4px;
    color: var(--muted);
    font: 600 var(--fs-label, 12px) / 1.3 var(--font-ui);
  }
  .swatch {
    width: 6px;
    height: 6px;
    border-radius: 50%;
  }
  ul {
    display: grid;
    gap: 1px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    align-items: center;
    border-radius: var(--r-item);
  }
  li:hover,
  li:focus-within,
  li.on {
    background: var(--wash);
  }
  li a {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 9px;
    height: var(--control-h);
    padding: 0 9px;
    color: var(--ink);
    font: 400 var(--fs-control) / 1 var(--font-ui);
    text-decoration: none;
    cursor: grab;
  }
  li a :global(svg) {
    flex: none;
    color: var(--muted);
  }
  .name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .count {
    color: var(--accent);
    font: 500 10.5px/1 var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  /* ⋯ shows on the row the pointer or focus is on, and while its menu is open. */
  li :global(.overflow-trigger) {
    opacity: 0;
  }
  li:hover :global(.overflow-trigger),
  li:focus-within :global(.overflow-trigger),
  li :global(.overflow-trigger[aria-expanded="true"]) {
    opacity: 1;
  }
  li.hidden-section {
    opacity: 0.45;
  }
  li.hidden-section:hover,
  li.hidden-section:focus-within {
    opacity: 0.8;
  }
  .empty,
  .error {
    margin: 6px 9px;
    color: var(--muted);
    font-size: 12px;
  }
  .error {
    color: var(--error);
  }
  @media (hover: none) {
    li :global(.overflow-trigger) {
      opacity: 1;
    }
  }
</style>

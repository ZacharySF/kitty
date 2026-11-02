<script lang="ts" module>
  export type OverflowItem =
    | { label: string; onselect: () => void; icon?: string; kbd?: string; danger?: boolean; disabled?: boolean }
    | { separator: true };
</script>

<script lang="ts">
  import Icon from "../Icon.svelte";
  import Popover, { type PopoverPlacement } from "./Popover.svelte";

  /**
   * Q6: the ⋯ menu that replaces fold-outs of rare actions. It is always the last
   * control in its header, uses the same icon everywhere and is keyboard reachable:
   * arrows, Home/End and type-ahead move between items, Enter or Space runs one,
   * Escape closes and returns focus to ⋯.
   */
  let {
    items,
    label = "More actions",
    placement = "bottom-end",
    open = $bindable(false),
  }: {
    items: OverflowItem[];
    label?: string;
    placement?: PopoverPlacement;
    open?: boolean;
  } = $props();

  let popover = $state<{ close: (refocus?: boolean) => void }>();
  let menu: HTMLElement | undefined;

  const enabled = () => [...(menu?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? [])];

  function focusItem(index: number) {
    const all = enabled();
    if (!all.length) return;
    all[(index + all.length) % all.length].focus();
  }

  function keydown(event: KeyboardEvent) {
    const all = enabled();
    const current = all.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown") focusItem(current + 1);
    else if (event.key === "ArrowUp") focusItem(current < 0 ? -1 : current - 1);
    else if (event.key === "Home") focusItem(0);
    else if (event.key === "End") focusItem(-1);
    else if (event.key === "Tab") return popover?.close(false);
    else if (event.key.length === 1 && /\S/.test(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const key = event.key.toLowerCase();
      const order = [...all.slice(current + 1), ...all.slice(0, current + 1)];
      order.find((item) => item.textContent?.trim().toLowerCase().startsWith(key))?.focus();
    } else return;
    event.preventDefault();
  }

  function choose(item: Extract<OverflowItem, { label: string }>) {
    if (item.disabled) return;
    popover?.close(true);
    item.onselect();
  }
</script>

<Popover bind:this={popover} bind:open {label} {placement} role="menu" focus="none" class="overflow-menu"
  onopen={(node) => { menu = node; focusItem(0); }}>
  {#snippet trigger(props)}
    <button type="button" class="icon-button overflow-trigger" aria-label={label} title={label} {...props}
      ><Icon name="more" size={16} /></button
    >
  {/snippet}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="items" onkeydown={keydown}>
    {#each items as item, index (index)}
      {#if "separator" in item}
        <div class="sep" role="separator"></div>
      {:else}
        <button type="button" role="menuitem" tabindex="-1" class="item" class:danger={item.danger}
          aria-disabled={item.disabled ? "true" : undefined} onclick={() => choose(item)}>
          {#if item.icon}<Icon name={item.icon} size={14} />{/if}
          <span class="text">{item.label}</span>
          {#if item.kbd}<kbd>{item.kbd}</kbd>{/if}
        </button>
      {/if}
    {/each}
  </div>
</Popover>

<style>
  .overflow-trigger:hover,
  .overflow-trigger[aria-expanded="true"] {
    background: var(--wash);
    color: var(--ink);
  }
  :global(.kitty-popover.overflow-menu) {
    min-width: 190px;
  }
  .items {
    display: grid;
    gap: 1px;
  }
  .item {
    display: flex;
    align-items: center;
    gap: 9px;
    width: 100%;
    height: var(--control-h);
    padding: 0 9px;
    border: 0;
    border-radius: var(--r-item);
    background: transparent;
    color: var(--ink);
    font: 400 var(--fs-control) / 1 var(--font-ui);
    text-align: left;
    white-space: nowrap;
    cursor: pointer;
  }
  .item:hover,
  .item:focus {
    background: var(--wash);
    outline: none;
  }
  .item[aria-disabled="true"] {
    color: var(--subtle);
    cursor: default;
  }
  .item[aria-disabled="true"]:hover {
    background: transparent;
  }
  .item.danger {
    color: var(--error);
  }
  .text {
    flex: 1;
  }
  kbd {
    margin-left: auto;
    padding: 2px 5px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-chip);
    color: var(--subtle);
    font-size: 9.5px;
    letter-spacing: 0.06em;
  }
  .sep {
    height: 1px;
    margin: 4px 6px;
    background: var(--hairline);
  }
</style>

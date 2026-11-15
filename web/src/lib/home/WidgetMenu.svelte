<script lang="ts">
  import { tick } from "svelte";
  import Icon from "../Icon.svelte";
  import type { HomeWidgetId } from "./layout";
  import { WIDGET_ICONS } from "./widgetIcons";

  /**
   * Edit mode's Widgets dropdown (home's bar and page edit mode's): the widgets that can
   * still be put on the page. A row is pressed to add its widget, or dragged onto the page
   * (`application/x-rika-widget` carries the id). The panel is drawn inline against the
   * button, so the edit modes' own pointer handling sees it as part of their bar.
   */
  type Entry = { id: HomeWidgetId; title: string };
  let { widgets, placement = "bottom", open = $bindable(false), onpick, ondrag }: {
    widgets: readonly Entry[];
    placement?: "top" | "bottom";
    open?: boolean;
    onpick: (id: HomeWidgetId) => void;
    /** A row's drag started (its id) or ended (null), for a page that places the widget while it is dragged. */
    ondrag?: (id: HomeWidgetId | null) => void;
  } = $props();

  let root = $state<HTMLElement>();
  let trigger = $state<HTMLButtonElement>();
  /** The rows as they were when a drag began: the dragged row stays until its drag ends, wherever its widget lands meanwhile. */
  let held = $state.raw<readonly Entry[] | null>(null);
  const rows = $derived(held ?? widgets);
  /** While a row is in the air the open panel lets the page under it be seen and dropped on. */
  let lifted = $state(false);

  const items = () => [...(root?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
  async function toggle() {
    open = !open;
    if (!open) return;
    await tick();
    items()[0]?.focus();
  }
  function close(refocus = false) {
    open = false;
    if (refocus) trigger?.focus();
  }
  function pick(id: HomeWidgetId) {
    close(true);
    onpick(id);
  }
  function key(event: KeyboardEvent) {
    if (event.key === "Escape") {
      // Escape closes the menu and nothing else (not the edit mode around it).
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (!step || !root?.contains(event.target as Node)) return;
    event.preventDefault();
    event.stopPropagation();
    const all = items();
    all[(all.indexOf(document.activeElement as HTMLElement) + step + all.length) % all.length]?.focus();
  }
  function outside(event: PointerEvent) {
    if (!held && !root?.contains(event.target as Node)) close();
  }
  $effect(() => {
    if (!open) return;
    window.addEventListener("keydown", key, true);
    window.addEventListener("pointerdown", outside, true);
    return () => {
      window.removeEventListener("keydown", key, true);
      window.removeEventListener("pointerdown", outside, true);
    };
  });
</script>

<span class="widget-menu" bind:this={root}>
  <button type="button" class="pill" bind:this={trigger} aria-haspopup="menu" aria-expanded={open} onclick={toggle}>Widgets</button>
  {#if open}
    <div class="panel {placement}" class:lifted role="menu" aria-label="Widgets">
      {#each rows as widget (widget.id)}
        <button type="button" role="menuitem" tabindex="-1" draggable="true"
          ondragstart={(event) => {
            held = widgets;
            event.dataTransfer?.setData("text/plain", widget.title);
            event.dataTransfer?.setData("application/x-rika-widget", widget.id);
            if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy";
            ondrag?.(widget.id);
            // After the browser has taken its picture of the row.
            setTimeout(() => { lifted = held !== null; });
          }}
          ondragend={() => { held = null; lifted = false; close(); ondrag?.(null); }}
          onclick={() => pick(widget.id)}>
          <span class="module-icon"><Icon name={WIDGET_ICONS[widget.id]} size={17} /></span>{widget.title}
        </button>
      {:else}
        <p>Every widget is here.</p>
      {/each}
    </div>
  {/if}
</span>

<style>
  .widget-menu {
    position: relative;
    display: inline-flex;
  }
  /* The app's menus, flat: a hairline, the raised surface, no blur and no glow. */
  :global(:root body) .panel[role="menu"] {
    position: absolute;
    left: 0;
    z-index: 1;
    display: grid;
    gap: 1px;
    min-width: 220px;
    padding: 6px;
    border: 1px solid var(--hairline);
    border-radius: 14px;
    background: var(--surface-raised);
    box-shadow: none;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
  .panel.top {
    bottom: calc(100% + 16px);
  }
  .panel.bottom {
    top: calc(100% + 8px);
  }
  .panel.lifted {
    opacity: 0;
    pointer-events: none;
  }
  [role="menuitem"] {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    min-height: 36px;
    padding: 0 12px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--ink);
    font: 400 15px/1.2 var(--font-ui);
    text-align: left;
    white-space: nowrap;
    cursor: grab;
  }
  [role="menuitem"]:hover,
  [role="menuitem"]:focus-visible {
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    outline: none;
  }
  p {
    margin: 0;
    padding: 8px 12px;
    color: var(--muted);
    font-size: 15px;
    white-space: nowrap;
  }
</style>

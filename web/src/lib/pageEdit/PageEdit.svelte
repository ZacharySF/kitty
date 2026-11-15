<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { buildStore, setPreferences } from "../build.svelte";
  import WidgetMenu from "../home/WidgetMenu.svelte";
  import { spareWidgets, type HomeWidgetId } from "../home/layout";
  import { activeModules } from "../modules.svelte";
  import { applyMoves } from "./apply";
  import { pageDraft } from "./draft.svelte";
  import { MIN_SIZE, moveOf, movesFor, snap, withMove, withoutMove, withoutView, type Move } from "./moves";
  import { pathOf, resolvePath } from "./paths";
  import { WIDGET_WIDTH, addWidget, moveWidget, removeWidget, resizeWidget, widgetsFor, withoutViewWidgets, type PageWidget } from "./widgets";

  /**
   * Page edit mode (Ctrl + Shift + Space on any page but home, which has its own): the
   * page's blocks can be dragged and resized, and home's widgets brought onto it from the
   * bar's Widgets menu (pressed, or dragged to their place; PageWidgets.svelte draws them).
   * Done (or Esc) saves the result in the build's `ui.pages`. Mounted once: it also puts the
   * saved moves back on the page in view whenever that page redraws. Safe mode applies none.
   */
  let { view, tab, editing = $bindable(false) }: { view: string; /** The tab in front. */ tab: string; editing?: boolean } = $props();

  /** What counts as a block: the page's structural elements (a list is often a labelled region, not a section). */
  const BLOCKS = 'section, aside, article, header, nav, form, table, [role="region"], [data-kitty-panel]:not([data-kitty-panel="view"]), .panel, .home-widget';
  const OPEN_DIALOG = 'dialog[open], [role="dialog"][aria-modal="true"]';
  /** What a row dragged out of the Widgets menu carries (home/WidgetMenu.svelte). */
  const WIDGET_TYPE = "application/x-rika-widget";

  /** Every page's moves while editing; null otherwise (the build's are in effect). */
  let draft = $state.raw<string[] | null>(null);
  const entries = $derived(draft ?? (buildStore.safe ? [] : buildStore.build.ui.pages.moves));
  const moves = $derived(movesFor(entries, view));
  /** This page's widgets while editing (the draft every PageWidgets draws), and those still to bring in. */
  const pageWidgets = $derived(pageDraft.widgets ? widgetsFor(pageDraft.widgets, view) : []);
  const spare = $derived(spareWidgets([pageWidgets.map((item) => item.widget) as HomeWidgetId[]], activeModules.enabled));
  let menuOpen = $state(false);

  const panel = () => document.querySelector<HTMLElement>('#main-content > [data-kitty-panel="view"]:not([hidden])');
  let frame = 0;
  function apply() {
    frame = 0;
    const root = panel();
    if (root) applyMoves(root, moves);
  }
  function schedule() {
    if (!frame) frame = requestAnimationFrame(apply);
  }
  $effect(() => {
    void moves;
    void tab;
    schedule();
  });
  onMount(() => {
    const main = document.getElementById("main-content");
    if (!main) return;
    const redrawn = new MutationObserver(schedule);
    redrawn.observe(main, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    return () => {
      redrawn.disconnect();
      cancelAnimationFrame(frame);
    };
  });

  let ui = $state<HTMLElement>();
  let selected = $state.raw<HTMLElement | null>(null);
  let hovered = $state.raw<HTMLElement | null>(null);
  type Box = { left: number; top: number; width: number; height: number };
  let box = $state.raw<Box | null>(null);
  let hoverBox = $state.raw<Box | null>(null);
  /** The page widget selected, by its id; null while a block of the page (or nothing) is. */
  const selectedWidget = $derived(selected?.dataset.pageWidget ?? null);
  const selectedPath = $derived.by(() => {
    const root = selected && !selectedWidget && panel();
    return root ? pathOf(root, selected!) : null;
  });

  let wasEditing = false;
  $effect(() => {
    if (editing === wasEditing) return;
    wasEditing = editing;
    untrack(() => {
      if (!editing) void finish();
      else if (buildStore.safe || view === "today") wasEditing = editing = false;
      else {
        draft = [...buildStore.build.ui.pages.moves];
        pageDraft.widgets = [...buildStore.build.ui.pages.widgets];
        const focused = document.activeElement;
        if (focused instanceof HTMLElement && panel()?.contains(focused)) focused.blur();
      }
    });
  });
  // Another page or tab: what was arranged is kept, and editing ends.
  $effect(() => {
    void view;
    void tab;
    untrack(() => { if (editing) editing = false; });
  });
  async function finish() {
    const next = draft && pageDraft.widgets ? { moves: draft, widgets: $state.snapshot(pageDraft.widgets) } : null;
    const saved = buildStore.build.ui.pages;
    const saving = next && JSON.stringify(next) !== JSON.stringify({ moves: saved.moves, widgets: saved.widgets })
      ? setPreferences({ ui: { pages: next } }) : null;
    draft = null;
    pageDraft.widgets = null;
    selected = hovered = null;
    drag = null;
    menuOpen = false;
    await saving;
  }

  /** A block the editor can keep: big enough to hold, and its path leads back to it. */
  function holds(root: HTMLElement, block: Element): block is HTMLElement {
    if (!(block instanceof HTMLElement) || block === root || !root.contains(block)) return false;
    const size = block.getBoundingClientRect();
    if (size.width < 80 || size.height < 32) return false;
    const path = pathOf(root, block);
    return path !== null && resolvePath(root, path) === block;
  }
  /** The innermost block at or around `from`. */
  function blockFrom(from: Element | null): HTMLElement | null {
    const root = panel();
    if (!root) return null;
    for (let at = from?.closest(BLOCKS) ?? null; at && at !== root && root.contains(at); at = at.parentElement?.closest(BLOCKS) ?? null)
      if (holds(root, at)) return at;
    return null;
  }
  /** What the pointer is over: a page widget's pane first, else the innermost block. */
  function blockAt(x: number, y: number): HTMLElement | null {
    const root = panel();
    const hit = document.elementsFromPoint(x, y).find((element) => element !== root && root?.contains(element)) ?? null;
    return hit?.closest<HTMLElement>(".page-widget") ?? blockFrom(hit);
  }
  const current = $derived(selected ?? hovered);
  const parentBlock = $derived(current && !current.dataset.pageWidget ? blockFrom(current.parentElement) : null);
  function toParent() {
    if (parentBlock) selected = parentBlock;
  }
  function resetBlock() {
    if (draft && selectedPath) draft = withoutMove(draft, view, selectedPath);
    apply();
  }
  function removeSelected() {
    if (pageDraft.widgets && selectedWidget) pageDraft.widgets = removeWidget(pageDraft.widgets, view, selectedWidget);
    selected = null;
  }
  function resetPage() {
    if (draft) draft = withoutView(draft, view);
    if (pageDraft.widgets) pageDraft.widgets = withoutViewWidgets(pageDraft.widgets, view);
    apply();
  }

  /** `id` put on the page with its pane's corner at a point of the window, then selected. */
  function put(id: string, left: number, top: number) {
    const root = panel();
    if (!root || !pageDraft.widgets || !spare.some((widget) => widget.id === id)) return;
    const page = root.getBoundingClientRect();
    const x = Math.max(0, Math.min(left - page.left + root.scrollLeft, root.clientWidth - WIDGET_WIDTH.initial));
    pageDraft.widgets = addWidget(pageDraft.widgets, view, id, x, Math.max(0, top - page.top + root.scrollTop));
    void tick().then(() => { selected = panel()?.querySelector<HTMLElement>(`[data-page-widget="${id}"]`) ?? null; });
  }
  /** A row pressed in the menu: its widget lands near the top right of what is visible, each a little lower than the last. */
  function pick(id: HomeWidgetId) {
    const page = panel()?.getBoundingClientRect();
    if (!page) return;
    const bar = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--workbench-top")) || 0;
    put(id, Math.min(page.right, window.innerWidth) - WIDGET_WIDTH.initial - 24, Math.max(page.top, bar) + 24 + pageWidgets.length * 24);
  }
  /** A row dragged out of the menu may be dropped anywhere on the page. */
  const overPage = (event: DragEvent) => !!event.dataTransfer?.types.includes(WIDGET_TYPE)
    && event.target instanceof Node && !ui?.contains(event.target) && !!document.getElementById("main-content")?.contains(event.target);
  function dragOver(event: DragEvent) {
    if (!overPage(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  }
  function drop(event: DragEvent) {
    if (!overPage(event)) return;
    event.preventDefault();
    event.stopPropagation();
    // The pane lands under the pointer: centred on it, its title where the pointer is.
    put(event.dataTransfer?.getData(WIDGET_TYPE) ?? "", event.clientX - WIDGET_WIDTH.initial / 2, event.clientY - 24);
  }

  type Drag = { kind: "move" | "e" | "s" | "se"; x: number; y: number; moved: boolean }
    & ({ widget: PageWidget } | { widget: null; start: Move; width: number; height: number });
  let drag: Drag | null = null;
  function grab(event: PointerEvent, kind: Drag["kind"], block = selected) {
    if (!draft || !block) return;
    const held = { kind, x: event.clientX, y: event.clientY, moved: false };
    const widget = pageWidgets.find((item) => item.widget === block.dataset.pageWidget);
    const root = panel();
    const path = root && !block.dataset.pageWidget ? pathOf(root, block) : null;
    if (widget) drag = { ...held, widget };
    else if (path) drag = { ...held, widget: null, start: moveOf(draft, view, path), width: block.offsetWidth, height: block.offsetHeight };
  }
  function pull(event: PointerEvent) {
    if (!drag || !draft || !pageDraft.widgets) return;
    const x = event.clientX - drag.x;
    const y = event.clientY - drag.y;
    // A press that barely moves only selects.
    if (!drag.moved && Math.hypot(x, y) < 4) return;
    drag.moved = true;
    if (drag.widget) {
      // A page widget is always on the grid: it moves, or takes a width.
      const { widget, x: left, y: top, w } = drag.widget;
      pageDraft.widgets = drag.kind === "move" ? moveWidget(pageDraft.widgets, view, widget, left + x, top + y) : resizeWidget(pageDraft.widgets, view, widget, w + x);
      return;
    }
    const free = event.altKey;
    const next = { ...drag.start };
    if (drag.kind === "move") {
      next.dx = snap(drag.start.dx + x, free);
      next.dy = snap(drag.start.dy + y, free);
    }
    if (drag.kind === "e" || drag.kind === "se") next.w = Math.max(MIN_SIZE, snap(drag.width + x, free));
    if (drag.kind === "s" || drag.kind === "se") next.h = Math.max(MIN_SIZE, snap(drag.height + y, free));
    draft = withMove(draft, next);
    apply();
  }

  const inUi = (target: EventTarget | null) => target instanceof Node && !!ui?.contains(target);
  const inPage = (target: EventTarget | null) => target instanceof Node && !!panel()?.contains(target);
  function still(event: Event) {
    event.stopPropagation();
    event.preventDefault();
  }
  function press(event: PointerEvent) {
    if (inUi(event.target) || !inPage(event.target)) return;
    event.stopPropagation();
    if (event.button !== 0) return;
    selected = blockAt(event.clientX, event.clientY);
    grab(event, "move");
  }
  function move(event: PointerEvent) {
    if (drag) pull(event);
    else if (!inUi(event.target)) hovered = blockAt(event.clientX, event.clientY);
  }
  /** Nothing on the page answers a press while it is being arranged. */
  function quiet(event: Event) {
    if (!inUi(event.target) && inPage(event.target)) still(event);
  }
  function key(event: KeyboardEvent) {
    // The shortcut itself is App's; a dialog over the page, and the open Widgets menu, keep their own keys.
    if ((event.ctrlKey && event.shiftKey && event.code === "Space") || menuOpen || document.querySelector(OPEN_DIALOG)) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") { still(event); editing = false; return; }
    if (event.key === "ArrowUp") { still(event); toParent(); return; }
    if (event.key === "Tab" || inUi(event.target)) return;
    event.stopPropagation();
    if (inPage(event.target)) event.preventDefault();
  }
  const QUIET = ["click", "dblclick", "auxclick", "contextmenu", "mousedown", "mouseup", "pointerup", "dragstart", "submit"] as const;
  $effect(() => {
    if (!editing) return;
    const capture = { capture: true };
    const release = () => { drag = null; };
    window.addEventListener("pointerdown", press, capture);
    window.addEventListener("pointermove", move, capture);
    window.addEventListener("pointerup", release, capture);
    window.addEventListener("pointercancel", release, capture);
    window.addEventListener("keydown", key, capture);
    window.addEventListener("dragover", dragOver, capture);
    window.addEventListener("drop", drop, capture);
    for (const name of QUIET) window.addEventListener(name, quiet, capture);
    document.documentElement.dataset.pageEditing = "";
    // The outlines follow their blocks through scrolling and reflow.
    const boxOf = (block: HTMLElement | null, before: Box | null): Box | null => {
      if (!block?.isConnected) return null;
      const { left, top, width, height } = block.getBoundingClientRect();
      return before && before.left === left && before.top === top && before.width === width && before.height === height ? before : { left, top, width, height };
    };
    let follow = requestAnimationFrame(function track() {
      if (selected && !selected.isConnected) selected = null;
      if (hovered && !hovered.isConnected) hovered = null;
      box = boxOf(selected, box);
      hoverBox = boxOf(hovered === selected ? null : hovered, hoverBox);
      follow = requestAnimationFrame(track);
    });
    return () => {
      cancelAnimationFrame(follow);
      box = hoverBox = null;
      delete document.documentElement.dataset.pageEditing;
      window.removeEventListener("pointerdown", press, capture);
      window.removeEventListener("pointermove", move, capture);
      window.removeEventListener("pointerup", release, capture);
      window.removeEventListener("pointercancel", release, capture);
      window.removeEventListener("keydown", key, capture);
      window.removeEventListener("dragover", dragOver, capture);
      window.removeEventListener("drop", drop, capture);
      for (const name of QUIET) window.removeEventListener(name, quiet, capture);
    };
  });
  const place = (at: Box) => `left:${at.left}px;top:${at.top}px;width:${at.width}px;height:${at.height}px`;
</script>

{#if editing}
  <div class="page-edit" bind:this={ui}>
    {#if hoverBox}<div class="outline hover" style={place(hoverBox)}></div>{/if}
    {#if box}
      <div class="outline" style={place(box)}>
        <!-- The grip sits on top of the block, out of its content's way; under the top bar it goes beside the block, or inside it at the window's edge. -->
        <button type="button" class="grip" class:beside={box.top < 96 && box.left >= 24} class:inside={box.top < 96 && box.left < 24} aria-label={selectedWidget ? "Move widget" : "Move block"} onpointerdown={(event) => grab(event, "move")}>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M5 3.5h.01M5 8h.01M5 12.5h.01M11 3.5h.01M11 8h.01M11 12.5h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
        </button>
        <span class="handle e" data-resize="width" onpointerdown={(event) => grab(event, "e")}></span>
        <!-- A page widget is as tall as what it shows. -->
        {#if !selectedWidget}
          <span class="handle s" data-resize="height" onpointerdown={(event) => grab(event, "s")}></span>
          <span class="handle se" data-resize="both" onpointerdown={(event) => grab(event, "se")}></span>
        {/if}
      </div>
    {/if}
    <div class="edit-bar" role="toolbar" aria-label="Page editing">
      <span class="count">Editing this page</span>
      <i class="rule"></i>
      <WidgetMenu widgets={spare} placement="top" bind:open={menuOpen} onpick={pick} />
      <button type="button" class="text-button" disabled={!parentBlock} onclick={toParent}>Parent</button>
      {#if selectedWidget}
        <button type="button" class="text-button" onclick={removeSelected}>Remove widget</button>
      {:else}
        <button type="button" class="text-button" disabled={!moves.some((item) => item.path === selectedPath)} onclick={resetBlock}>Reset block</button>
      {/if}
      <button type="button" class="text-button" disabled={!moves.length && !pageWidgets.length} onclick={resetPage}>Reset page</button>
      <button type="button" class="pill primary" onclick={() => (editing = false)}>Done</button>
    </div>
  </div>
{/if}

<style>
  /* Over the page, under dialogs; only the handles and the bar take the pointer. */
  .page-edit {
    position: fixed;
    inset: 0;
    z-index: 40;
    pointer-events: none;
  }
  .outline {
    position: fixed;
    box-sizing: border-box;
    border: 1px dashed var(--ink);
  }
  .outline.hover {
    border-color: color-mix(in srgb, var(--ink) 40%, transparent);
  }
  .grip,
  .handle {
    position: absolute;
    pointer-events: auto;
    touch-action: none;
  }
  .grip {
    left: -1px;
    top: -24px;
    display: grid;
    place-items: center;
    width: 24px;
    height: 24px;
    padding: 0;
    border: 0;
    border-radius: 6px 6px 0 0;
    background: var(--ink);
    color: var(--br-void);
    cursor: grab;
  }
  .grip.beside {
    left: -24px;
    top: -1px;
    border-radius: 6px 0 0 6px;
  }
  .grip.inside {
    top: -1px;
    border-radius: 0 0 6px 0;
  }
  /* Each handle is a wide target around a thin bar on the edge. */
  .handle::after {
    content: "";
    position: absolute;
    inset: 0;
    margin: auto;
    border-radius: 3px;
    background: var(--ink);
  }
  .handle.e {
    right: -8px;
    top: calc(50% - 22px);
    width: 16px;
    height: 44px;
    cursor: ew-resize;
  }
  .handle.e::after {
    width: 4px;
    height: 28px;
  }
  .handle.s {
    bottom: -8px;
    left: calc(50% - 22px);
    width: 44px;
    height: 16px;
    cursor: ns-resize;
  }
  .handle.s::after {
    width: 28px;
    height: 4px;
  }
  .handle.se {
    right: -8px;
    bottom: -8px;
    width: 16px;
    height: 16px;
    cursor: nwse-resize;
  }
  .handle.se::after {
    width: 8px;
    height: 8px;
    border-radius: 2px;
  }
  :global(html[data-page-editing] #main-content > [data-kitty-panel="view"] *) {
    cursor: move !important;
    user-select: none !important;
  }
</style>

<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { buildStore, setPreferences } from "../build.svelte";
  import { applyMoves } from "./apply";
  import { MIN_SIZE, moveOf, movesFor, snap, withMove, withoutMove, withoutView, type Move } from "./moves";
  import { pathOf, resolvePath } from "./paths";

  /**
   * Page edit mode (Ctrl + Shift + Space on any page but home, which has its own): the
   * page's blocks can be dragged and resized; Done (or Esc) saves the result in the build's
   * `ui.pages.moves`. Mounted once: it also puts the saved moves back on the page in view
   * whenever that page redraws. Safe mode applies none.
   */
  let { view, tab, editing = $bindable(false) }: { view: string; /** The tab in front. */ tab: string; editing?: boolean } = $props();

  /** What counts as a block: the page's structural elements (a list is often a labelled region, not a section). */
  const BLOCKS = 'section, aside, article, header, nav, form, table, [role="region"], [data-kitty-panel]:not([data-kitty-panel="view"]), .panel, .home-widget';
  const OPEN_DIALOG = 'dialog[open], [role="dialog"][aria-modal="true"]';

  /** Every page's moves while editing; null otherwise (the build's are in effect). */
  let draft = $state.raw<string[] | null>(null);
  const entries = $derived(draft ?? (buildStore.safe ? [] : buildStore.build.ui.pages.moves));
  const moves = $derived(movesFor(entries, view));

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
  const selectedPath = $derived.by(() => {
    const root = selected && panel();
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
    const next = draft;
    const saving = next && JSON.stringify(next) !== JSON.stringify(buildStore.build.ui.pages.moves)
      ? setPreferences({ ui: { pages: { moves: next } } }) : null;
    draft = null;
    selected = hovered = null;
    drag = null;
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
  function blockAt(x: number, y: number): HTMLElement | null {
    const root = panel();
    return blockFrom(document.elementsFromPoint(x, y).find((element) => element !== root && root?.contains(element)) ?? null);
  }
  const current = $derived(selected ?? hovered);
  const parentBlock = $derived(current ? blockFrom(current.parentElement) : null);
  function toParent() {
    if (parentBlock) selected = parentBlock;
  }
  function resetBlock() {
    if (draft && selectedPath) draft = withoutMove(draft, view, selectedPath);
    apply();
  }
  function resetPage() {
    if (draft) draft = withoutView(draft, view);
    apply();
  }

  type Drag = { kind: "move" | "e" | "s" | "se"; start: Move; x: number; y: number; width: number; height: number; moved: boolean };
  let drag: Drag | null = null;
  function grab(event: PointerEvent, kind: Drag["kind"], block = selected) {
    const root = panel();
    const path = root && block ? pathOf(root, block) : null;
    if (!draft || !block || !path) return;
    drag = { kind, start: moveOf(draft, view, path), x: event.clientX, y: event.clientY, width: block.offsetWidth, height: block.offsetHeight, moved: false };
  }
  function pull(event: PointerEvent) {
    if (!drag || !draft) return;
    const x = event.clientX - drag.x;
    const y = event.clientY - drag.y;
    // A press that barely moves only selects.
    if (!drag.moved && Math.hypot(x, y) < 4) return;
    drag.moved = true;
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
    // The shortcut itself is App's; a dialog over the page keeps its own keys.
    if ((event.ctrlKey && event.shiftKey && event.code === "Space") || document.querySelector(OPEN_DIALOG)) return;
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
        <button type="button" class="grip" class:beside={box.top < 96 && box.left >= 24} class:inside={box.top < 96 && box.left < 24} aria-label="Move block" onpointerdown={(event) => grab(event, "move")}>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M5 3.5h.01M5 8h.01M5 12.5h.01M11 3.5h.01M11 8h.01M11 12.5h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
        </button>
        <span class="handle e" data-resize="width" onpointerdown={(event) => grab(event, "e")}></span>
        <span class="handle s" data-resize="height" onpointerdown={(event) => grab(event, "s")}></span>
        <span class="handle se" data-resize="both" onpointerdown={(event) => grab(event, "se")}></span>
      </div>
    {/if}
    <div class="bar" role="toolbar" aria-label="Page editing">
      <span class="count">Editing this page</span>
      <i class="rule"></i>
      <button type="button" class="text-button" disabled={!parentBlock} onclick={toParent}>Parent</button>
      <button type="button" class="text-button" disabled={!moves.some((item) => item.path === selectedPath)} onclick={resetBlock}>Reset block</button>
      <button type="button" class="text-button" disabled={!moves.length} onclick={resetPage}>Reset page</button>
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
  /* JobBrowser's selection bar: one piece at the bottom of the window. */
  .bar {
    position: fixed;
    left: 50%;
    bottom: 26px;
    display: flex;
    align-items: center;
    gap: 8px;
    max-width: calc(100vw - 32px);
    padding: 8px 10px 8px 20px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--raised);
    box-shadow: 0 18px 50px rgb(0 0 0 / 0.55);
    font-size: var(--text-small, 14px);
    transform: translateX(-50%);
    white-space: nowrap;
    pointer-events: auto;
  }
  .bar .count {
    font-weight: 600;
  }
  .bar .rule {
    width: 1px;
    height: 18px;
    margin: 0 4px;
    background: var(--border);
  }
  :global(html[data-page-editing] #main-content > [data-kitty-panel="view"] *) {
    cursor: move !important;
    user-select: none !important;
  }
  @media (max-width: 640px) {
    .bar {
      bottom: 12px;
      overflow-x: auto;
    }
  }
</style>

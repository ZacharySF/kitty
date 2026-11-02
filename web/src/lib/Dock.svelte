<script lang="ts">
  import { onMount } from "svelte";
  import AgentActivity from "./AgentActivity.svelte";
  import TerminalView from "./TerminalView.svelte";
  import { DOCK_STEPS, clampDockOffset, clampDockSize, defaultDockSize, dockedHeight, readDock, writeDock, type DockMode, type DockOffset, type DockSize, type DockStep } from "./dock";

  /**
   * The dock (Q3, Q8, Q56): rika!'s herdr terminal or herdr's agent cards. Docked
   * (the default) it spans the bottom of the window at a fixed height step chosen
   * with S/M/L, never dragged, and publishes the space it takes as --dock-space so
   * the page ends above it; hidden, a thin handle brings it back. Floated, it is a
   * glass card in the bottom-right corner that takes no page space; moving and
   * resizing preview with a transform or an outline and commit on release, and
   * hidden it is a small pill. Either way it publishes how far its top edge is
   * above the window bottom as --dock-clearance so overlays (toasts, undo bars)
   * sit above it.
   */
  let { onherdr }: {
    /** Open the full herdr session as a view in the workspace tab. */
    onherdr: () => void;
  } = $props();

  const KEY_STEP = 16;
  const STEP_LABEL: Record<DockStep, string> = { s: "Small", m: "Medium", l: "Large" };
  const saved = readDock(localStorage);
  let mode = $state<DockMode>(saved.mode);
  /** The view the handle or pill brings back after hiding. */
  let shown = $state(saved.shown);
  let float = $state(saved.float);
  let step = $state<DockStep>(saved.step);
  /** Null until resized: the card follows the default for the window. */
  let size = $state<DockSize | null>(saved.size);
  let offset = $state<DockOffset>(saved.offset);
  let viewport = $state({ width: window.innerWidth, height: window.innerHeight });
  const docked = $derived(dockedHeight(step, viewport.height));
  const box = $derived(clampDockSize(size ?? defaultDockSize(viewport.width, viewport.height), viewport.width, viewport.height));
  const place = $derived(clampDockOffset(offset, box, viewport.width, viewport.height));
  let card = $state<HTMLElement>();
  let pill = $state<HTMLButtonElement>();
  let handle = $state<HTMLButtonElement>();
  let ghost = $state<HTMLElement>();

  function remember() {
    writeDock(localStorage, { mode, shown, float, step, size, offset });
  }
  function setFloat(next: boolean) {
    float = next;
    remember();
  }
  function setStep(next: DockStep) {
    step = next;
    remember();
  }
  function choose(next: DockMode) {
    if (next !== "hidden") shown = next;
    mode = next;
    remember();
  }
  /** Commits a size; the card then shifts back on screen if it no longer fits. */
  function resizeTo(next: DockSize) {
    size = clampDockSize(next, viewport.width, viewport.height);
    offset = clampDockOffset(offset, size, viewport.width, viewport.height);
    remember();
  }

  /**
   * One pointer gesture on the card. Moves only record the pointer; one frame per
   * display refresh previews the result, and release commits it.
   */
  type Gesture = { kind: "move" | "resize"; pointer: number; x: number; y: number; dx: number; dy: number; frame: number };
  let gesture: Gesture | null = null;
  /** Mirrors the gesture's kind for the markup; the pointer data stays out of reactivity. */
  let dragging = $state<Gesture["kind"] | null>(null);
  function movedOffset(g: Gesture): DockOffset {
    return clampDockOffset({ right: place.right - g.dx, bottom: place.bottom - g.dy }, box, viewport.width, viewport.height);
  }
  /** Anchored bottom-right, the card grows up and to the left from its top-left grip. */
  function resized(g: Gesture): { size: DockSize; offset: DockOffset } {
    const next = clampDockSize({ width: box.width - g.dx, height: box.height - g.dy }, viewport.width, viewport.height);
    return { size: next, offset: clampDockOffset(place, next, viewport.width, viewport.height) };
  }
  function preview() {
    const g = gesture;
    if (!g) return;
    g.frame = 0;
    if (g.kind === "move" && card) {
      const next = movedOffset(g);
      card.style.transform = `translate(${place.right - next.right}px, ${place.bottom - next.bottom}px)`;
    } else if (g.kind === "resize" && ghost) {
      const next = resized(g);
      Object.assign(ghost.style, {
        width: `${next.size.width}px`, height: `${next.size.height}px`, right: `${next.offset.right}px`, bottom: `${next.offset.bottom}px`,
      });
    }
  }
  function gestureStart(kind: Gesture["kind"], event: PointerEvent) {
    if (event.button !== 0 || gesture) return;
    if (kind === "move" && (event.target as Element).closest("button")) return;
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    gesture = { kind, pointer: event.pointerId, x: event.clientX, y: event.clientY, dx: 0, dy: 0, frame: 0 };
    dragging = kind;
  }
  function gestureMove(event: PointerEvent) {
    const g = gesture;
    if (g?.pointer !== event.pointerId) return;
    g.dx = event.clientX - g.x;
    g.dy = event.clientY - g.y;
    if (!g.frame) g.frame = requestAnimationFrame(preview);
  }
  function gestureEnd(event: PointerEvent) {
    const g = gesture;
    if (g?.pointer !== event.pointerId) return;
    cancelAnimationFrame(g.frame);
    gesture = null;
    dragging = null;
    if (card) card.style.transform = "";
    if (event.type === "pointercancel") return;
    if (g.kind === "move") {
      offset = movedOffset(g);
      remember();
    } else {
      const next = resized(g);
      size = next.size;
      offset = next.offset;
      remember();
    }
  }
  function gripKey(event: KeyboardEvent) {
    const step = { ArrowUp: [0, KEY_STEP], ArrowDown: [0, -KEY_STEP], ArrowLeft: [KEY_STEP, 0], ArrowRight: [-KEY_STEP, 0] }[event.key];
    if (!step) return;
    event.preventDefault();
    resizeTo({ width: box.width + step[0], height: box.height + step[1] });
  }

  $effect(() => {
    // Docked: the page ends above the panel or its handle. Floating: nothing is reserved.
    const space = float ? 0 : mode === "hidden" ? (handle?.offsetHeight ?? 0) : docked;
    const clearance = float
      ? mode === "hidden" ? (pill ? window.innerHeight - pill.getBoundingClientRect().top : 0) : place.bottom + box.height
      : space;
    document.documentElement.style.setProperty("--dock-space", `${space}px`);
    document.documentElement.style.setProperty("--dock-clearance", `${Math.round(clearance)}px`);
  });
  onMount(() => {
    const onresize = () => (viewport = { width: window.innerWidth, height: window.innerHeight });
    window.addEventListener("resize", onresize);
    return () => {
      window.removeEventListener("resize", onresize);
      document.documentElement.style.removeProperty("--dock-space");
      document.documentElement.style.removeProperty("--dock-clearance");
    };
  });
</script>

{#if mode === "hidden" && float}
  <button bind:this={pill} class="dock-pill" aria-label="Show dock" title="Show dock" onclick={() => choose(shown)}>
    <span class="pill-dot" aria-hidden="true"></span>{shown === "terminal" ? "Terminal" : "Agents"}
  </button>
{:else if mode === "hidden"}
  <button bind:this={handle} class="dock-handle" aria-label="Show dock" title="Show dock" onclick={() => choose(shown)}><span aria-hidden="true"></span></button>
{:else}
  <section
    bind:this={card}
    class="dock"
    class:floating={float}
    class:moving={dragging === "move"}
    data-kitty-panel="dock"
    data-float={float}
    tabindex="-1"
    aria-label="Dock"
    style:width={float ? `${box.width}px` : null}
    style:height={`${float ? box.height : docked}px`}
    style:right={float ? `${place.right}px` : null}
    style:bottom={float ? `${place.bottom}px` : null}
  >
    {#if float}
      <button
        class="dock-grip"
        aria-label="Resize dock"
        title="Drag to resize · arrow keys work too"
        onpointerdown={(event) => gestureStart("resize", event)}
        onpointermove={gestureMove}
        onpointerup={gestureEnd}
        onpointercancel={gestureEnd}
        onkeydown={gripKey}
      ></button>
    {/if}
    <!-- Floating, the header is the card's drag handle; its buttons keep their own clicks. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <header
      class="dock-bar"
      onpointerdown={float ? (event) => gestureStart("move", event) : undefined}
      onpointermove={float ? gestureMove : undefined}
      onpointerup={float ? gestureEnd : undefined}
      onpointercancel={float ? gestureEnd : undefined}
    >
      <div class="dock-modes" role="group" aria-label="Dock contents">
        <button aria-pressed={mode === "terminal"} onclick={() => choose("terminal")}>Terminal</button>
        <button aria-pressed={mode === "activity"} onclick={() => choose("activity")}>Agents</button>
      </div>
      {#if mode === "terminal"}<button class="dock-action" onclick={onherdr}>Open herdr view</button>{/if}
      {#if !float}
        <div class="dock-modes dock-steps" role="group" aria-label="Dock height">
          {#each DOCK_STEPS as option (option)}
            <button aria-pressed={step === option} aria-label={`${STEP_LABEL[option]} dock`} title={`${STEP_LABEL[option]} dock`} onclick={() => setStep(option)}>{option.toUpperCase()}</button>
          {/each}
        </div>
      {/if}
      <button class="dock-action" aria-label={float ? "Dock to bottom" : "Float dock"} title={float ? "Dock to the bottom of the window" : "Float as a card in the corner"} onclick={() => setFloat(!float)}>{float ? "Dock" : "Float"}</button>
      <button class="dock-action" aria-label="Hide dock" title="Hide dock" onclick={() => choose("hidden")}>Hide</button>
    </header>
    <div class="dock-body">
      {#if mode === "terminal"}
        <TerminalView label="herdr terminal" />
      {:else}
        <AgentActivity onopen={() => choose("terminal")} />
      {/if}
    </div>
  </section>
  {#if dragging === "resize"}<div bind:this={ghost} class="dock-ghost" aria-hidden="true"></div>{/if}
{/if}

<style>
  /* Docked: a full-width glass panel fixed to the bottom of the window at a fixed
     height; the page reserves that space through --dock-space. */
  .dock,
  .dock-handle {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 30;
    box-sizing: border-box;
    background: color-mix(in srgb, var(--solid-void, #141414) 78%, transparent);
    backdrop-filter: blur(20px) saturate(140%);
    -webkit-backdrop-filter: blur(20px) saturate(140%);
  }
  .dock {
    display: flex;
    flex-direction: column;
    padding: 8px 16px 8px;
    border-top: 1px solid var(--hairline);
  }
  /* Floating: a glass card inset in the bottom-right corner; it never takes page space. */
  .dock.floating {
    left: auto;
    border: 1px solid var(--hairline);
    border-radius: 16px;
    box-shadow: var(--overlay-shadow);
  }
  .dock-handle {
    height: 10px;
    padding: 0;
    border: 0;
    border-top: 1px solid var(--hairline);
    cursor: pointer;
  }
  .dock-handle span {
    display: block;
    width: 48px;
    height: 3px;
    margin: 0 auto;
    border-radius: 999px;
    background: var(--muted);
    opacity: 0.6;
  }
  .dock-handle:hover span,
  .dock-handle:focus-visible span { opacity: 1; background: var(--br-structure, #deb2c3); }
  /* The dock's stacking context would trap a fullscreen terminal below the pinned
     top bar, whose blur would then paint over it. */
  .dock:has(:global([data-panel-fullscreen])) {
    z-index: 50;
  }
  .dock.moving {
    will-change: transform;
  }
  /* The resize preview: an outline of the size the card takes on release. */
  .dock-ghost {
    position: fixed;
    z-index: 31;
    box-sizing: border-box;
    border: 1px dashed var(--br-structure, #deb2c3);
    border-radius: 16px;
    background: color-mix(in srgb, var(--br-structure, #deb2c3) 8%, transparent);
    pointer-events: none;
  }
  .dock-grip {
    position: absolute;
    top: -1px;
    left: -1px;
    width: 18px;
    height: 18px;
    padding: 0;
    border: 0;
    border-top: 2px solid color-mix(in srgb, var(--muted) 45%, transparent);
    border-left: 2px solid color-mix(in srgb, var(--muted) 45%, transparent);
    border-radius: 16px 0 0 0;
    background: transparent;
    cursor: nwse-resize;
    touch-action: none;
  }
  .dock-grip:hover,
  .dock-grip:focus-visible {
    border-color: var(--br-structure, #deb2c3);
    outline: none;
  }
  .dock-pill {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 30;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 14px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-void, #141414) 78%, transparent);
    backdrop-filter: blur(20px) saturate(140%);
    -webkit-backdrop-filter: blur(20px) saturate(140%);
    box-shadow: var(--overlay-shadow);
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
    cursor: pointer;
  }
  .dock-pill:hover,
  .dock-pill:focus-visible {
    border-color: var(--br-lineHot);
  }
  .pill-dot {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--c1);
    box-shadow: 0 0 8px color-mix(in srgb, var(--c1) 70%, transparent);
  }
  .dock-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0 6px;
    user-select: none;
  }
  .dock.floating .dock-bar {
    cursor: grab;
    touch-action: none;
  }
  .dock.moving .dock-bar {
    cursor: grabbing;
  }
  .dock-modes {
    display: flex;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 40%, transparent);
  }
  .dock-bar button {
    border: 1px solid transparent;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    padding: 4px 12px;
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .dock-modes button[aria-pressed="true"] {
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    color: var(--ink);
  }
  .dock-steps button { padding: 4px 9px; }
  .dock-action { border-color: var(--hairline) !important; }
  .dock-action:first-of-type { margin-left: auto; }
  .dock-body {
    flex: 1;
    min-height: 0;
  }
  /* Same glass as the pinned top bar: lighter when the window itself is transparent. */
  :global(:root[data-window="transparent"]) :is(.dock, .dock-pill, .dock-handle) {
    background: color-mix(in srgb, var(--solid-void, #141414) 62%, transparent);
  }
</style>

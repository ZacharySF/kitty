<script lang="ts">
  import { onMount } from "svelte";
  import AgentActivity from "./AgentActivity.svelte";
  import TerminalView from "./TerminalView.svelte";
  import { DOCK_MIN_HEIGHT, MAIN_MIN_HEIGHT, clampDockHeight, firstDockHeight } from "./dock";
  import { buildStore, setPreferences } from "./build.svelte";
  import type { DockMode } from "./buildCore";

  /**
   * The dock (Q3, Q8): kitty!'s herdr terminal, herdr's agent-activity strip, or
   * only a thin handle. It is fixed to the bottom of the window and publishes the
   * space it takes as --dock-space so the page above never sits underneath it.
   * Its state is the build's `ui.dock`, so every window follows the same choice.
   */
  let { measure, onherdr }: {
    /** Bottom edge (viewport px) of the main panel's content, for the first size. */
    measure: () => number;
    /** Open the full herdr session as a view in the workspace tab. */
    onherdr: () => void;
  } = $props();

  const KEY_STEP = 16;
  const dock = $derived(buildStore.build.ui.dock);
  const mode = $derived(dock.mode);
  /** A height being dragged; saved when the drag ends. */
  let dragged = $state<number | null>(null);
  let handle = $state<HTMLButtonElement>();
  let viewport = $state(window.innerHeight);
  const height = $derived(dragged ?? dock.height);
  const size = $derived(height === null ? null : clampDockHeight(height, viewport));

  function choose(next: DockMode) {
    void setPreferences({ ui: { dock: {
      mode: next,
      shown: next === "hidden" ? dock.shown : next,
      height: size ?? (next !== "hidden" ? firstDockHeight(viewport, measure()) : null),
    } } });
  }
  function resizeTo(next: number) {
    void setPreferences({ ui: { dock: { height: clampDockHeight(next, viewport) } } });
  }

  let drag: { pointer: number; start: number; from: number } | null = null;
  function dragStart(event: PointerEvent) {
    if (event.button !== 0 || size === null) return;
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    drag = { pointer: event.pointerId, start: event.clientY, from: size };
  }
  function dragMove(event: PointerEvent) {
    if (drag?.pointer !== event.pointerId) return;
    dragged = clampDockHeight(drag.from + drag.start - event.clientY, viewport);
  }
  function dragEnd(event: PointerEvent) {
    if (drag?.pointer !== event.pointerId) return;
    drag = null;
    if (dragged !== null) resizeTo(dragged);
    dragged = null;
  }
  function dividerKey(event: KeyboardEvent) {
    if (size === null) return;
    const step = { ArrowUp: KEY_STEP, ArrowDown: -KEY_STEP, PageUp: KEY_STEP * 4, PageDown: -KEY_STEP * 4 }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    resizeTo(size + step);
  }

  $effect(() => {
    // Hidden: only the handle takes room. Shown: the dock's height.
    const space = mode === "hidden" ? (handle?.offsetHeight ?? 0) : (size ?? 0);
    document.documentElement.style.setProperty("--dock-space", `${space}px`);
  });
  onMount(() => {
    // Wait for the first layout so the empty area below the main panel is real.
    const frame = requestAnimationFrame(() => {
      if (mode !== "hidden" && height === null) resizeTo(firstDockHeight(viewport, measure()));
    });
    const onresize = () => (viewport = window.innerHeight);
    window.addEventListener("resize", onresize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onresize);
      document.documentElement.style.removeProperty("--dock-space");
    };
  });
</script>

{#if mode === "hidden"}
  <button bind:this={handle} class="dock-handle" aria-label="Show dock" title="Show dock" onclick={() => choose(dock.shown)}><span aria-hidden="true"></span></button>
{:else if size !== null}
  <section class="dock" data-kitty-panel="dock" tabindex="-1" aria-label="Dock" style:height={`${size}px`}>
    <!-- A focusable separator with a value is the WAI-ARIA window splitter, an interactive widget. -->
    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <div
      class="dock-divider"
      role="separator"
      aria-label="Resize dock"
      aria-orientation="horizontal"
      aria-valuemin={DOCK_MIN_HEIGHT}
      aria-valuemax={Math.max(DOCK_MIN_HEIGHT, viewport - MAIN_MIN_HEIGHT)}
      aria-valuenow={size}
      tabindex="0"
      onpointerdown={dragStart}
      onpointermove={dragMove}
      onpointerup={dragEnd}
      onpointercancel={dragEnd}
      onkeydown={dividerKey}
    ></div>
    <header class="dock-bar">
      <div class="dock-modes" role="group" aria-label="Dock contents">
        <button aria-pressed={mode === "terminal"} onclick={() => choose("terminal")}>Terminal</button>
        <button aria-pressed={mode === "activity"} onclick={() => choose("activity")}>Agents</button>
      </div>
      {#if mode === "terminal"}<button class="dock-action" onclick={onherdr}>Open herdr view</button>{/if}
      <button class="dock-action" aria-label="Hide dock" title="Hide dock" onclick={() => choose("hidden")}>Hide</button>
    </header>
    <div class="dock-body">
      {#if mode === "terminal"}
        <TerminalView label="herdr terminal" />
      {:else}
        <AgentActivity />
      {/if}
    </div>
  </section>
{/if}

<style>
  .dock,
  .dock-handle {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 30;
    box-sizing: border-box;
  }
  /* The dock's stacking context would trap a fullscreen terminal below the pinned
     top bar, whose blur would then paint over it. */
  .dock:has(:global([data-panel-fullscreen])) {
    z-index: 50;
  }
  .dock {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    padding: 0 16px 8px;
    border-top: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-void, #141414) 78%, transparent);
    backdrop-filter: blur(20px) saturate(140%);
    -webkit-backdrop-filter: blur(20px) saturate(140%);
  }
  .dock-divider {
    flex: none;
    height: 8px;
    margin: -4px -16px 0;
    cursor: ns-resize;
    touch-action: none;
  }
  .dock-divider:hover,
  .dock-divider:focus-visible {
    background: color-mix(in srgb, var(--br-structure, #deb2c3) 35%, transparent);
    outline: none;
  }
  .dock-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0 6px;
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
  .dock-action { border-color: var(--hairline) !important; }
  .dock-action:first-of-type { margin-left: auto; }
  .dock-body {
    flex: 1;
    min-height: 0;
  }
  .dock-handle {
    height: 10px;
    padding: 0;
    border: 0;
    border-top: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-void, #141414) 78%, transparent);
    cursor: pointer;
  }
  /* Same glass as the pinned top bar: lighter when the window itself is transparent. */
  :global(:root[data-window="transparent"]) :is(.dock, .dock-handle) {
    background: color-mix(in srgb, var(--solid-void, #141414) 62%, transparent);
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
</style>

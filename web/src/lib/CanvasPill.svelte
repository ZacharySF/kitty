<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon from "./Icon.svelte";
  import Popover from "./ui/Popover.svelte";

  /**
   * Q46/Q18: the one floating tool pill docked at the bottom of a canvas stage. The
   * Notes canvas and the mind-map editor both use it; only the tools inside differ.
   * Children are the tools (`.pill-btn`, `.pill-word`, `.pill-rule`); `more` fills the
   * ⋯ popover, always the last control. The bottom edge is a handle that collapses
   * the pill to a small tab, remembered under `storageKey`.
   */
  let {
    label,
    id,
    storageKey,
    moreLabel = "More tools",
    menuLabel = moreLabel,
    stageHeight,
    disabled = false,
    children,
    more,
  }: {
    /** The toolbar's accessible name ("Drawing tools", "Map tools"). */
    label: string;
    /** The toolbar's id, for the handle's aria-controls. */
    id: string;
    storageKey: string;
    moreLabel?: string;
    menuLabel?: string;
    /** The stage height in px, bounding the ⋯ panel. */
    stageHeight?: number;
    disabled?: boolean;
    children: Snippet;
    more?: Snippet<[() => void]>;
  } = $props();

  function stored() {
    try { return localStorage.getItem(storageKey) === "collapsed"; } catch { return false; /* Open by default. */ }
  }
  let collapsed = $state(stored());
  let moreOpen = $state(false);
  let popover = $state<{ close: (refocus?: boolean) => void }>();

  function toggle() {
    collapsed = !collapsed;
    try { localStorage.setItem(storageKey, collapsed ? "collapsed" : "open"); } catch { /* The pill still toggles. */ }
  }
  const close = () => popover?.close(true);
</script>

<div class="canvas-pill" style:--stage-height={stageHeight ? `${stageHeight}px` : undefined}>
  {#if !collapsed}
    <div {id} class="pill-bar" role="toolbar" aria-label={label}>
      {@render children()}
      {#if more}
        <Popover bind:this={popover} bind:open={moreOpen} label={menuLabel} placement="top-end" focus="first" class="pill-menu">
          {#snippet trigger(props)}
            <button class="pill-btn" aria-label={moreLabel} title={moreLabel} {disabled} {...props}><Icon name="more" size={18} /></button>
          {/snippet}
          {@render more(close)}
        </Popover>
      {/if}
    </div>
  {/if}
  <button class="pill-handle" aria-label={collapsed ? `Show ${label.toLowerCase()}` : `Hide ${label.toLowerCase()}`} aria-expanded={!collapsed}
    aria-controls={collapsed ? undefined : id} title={collapsed ? `Show ${label.toLowerCase()}` : `Hide ${label.toLowerCase()}`} onclick={toggle}><span aria-hidden="true"></span></button>
</div>

<style>
  /* Docked at the bottom centre of a positioned stage. */
  .canvas-pill {
    position: absolute;
    left: 50%;
    bottom: 0;
    z-index: 3;
    transform: translateX(-50%);
    display: flex;
    flex-direction: column;
    align-items: center;
    max-width: calc(100% - 24px);
  }
  .pill-bar {
    display: flex;
    align-items: center;
    gap: 2px;
    max-width: 100%;
    box-sizing: border-box;
    padding: 5px 8px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-raised) 72%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    box-shadow: 0 12px 30px -16px color-mix(in srgb, var(--shadow-tint) 80%, transparent);
    white-space: nowrap;
    animation: pill-rise 180ms ease-out;
  }
  @keyframes pill-rise { from { opacity: 0; transform: translateY(8px); } }
  .canvas-pill :global(.pill-btn) {
    position: relative;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    min-height: 0;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: none;
    color: var(--muted);
    font: 500 12px / 1 var(--font-ui);
    cursor: pointer;
    transition: transform 140ms ease, background-color 140ms ease, color 140ms ease;
  }
  .canvas-pill :global(.pill-btn:disabled) { opacity: 0.45; cursor: not-allowed; }
  .canvas-pill :global(.pill-btn:hover:not(:disabled)) { color: var(--ink); transform: translateY(-2px); }
  .canvas-pill :global(.pill-btn[aria-pressed="true"]),
  .canvas-pill :global(.pill-btn[aria-expanded="true"]) { color: var(--ink); background: var(--wash); }
  .canvas-pill :global(.pill-btn[aria-pressed="true"]::after) {
    content: "";
    position: absolute;
    bottom: 2px;
    left: calc(50% - 2px);
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: var(--accent);
  }
  .canvas-pill :global(.pill-word) { width: auto; padding: 0 10px; }
  .canvas-pill :global(.pill-rule) { flex-shrink: 0; width: 1px; height: 20px; margin: 0 4px; background: var(--hairline); }
  .pill-handle {
    display: grid;
    place-items: center;
    width: 64px;
    height: 14px;
    min-height: 0;
    padding: 0;
    border: 0;
    border-radius: 8px 8px 0 0;
    background: transparent;
    cursor: pointer;
  }
  .pill-handle[aria-expanded="false"] {
    height: 16px;
    border: 1px solid var(--hairline);
    border-bottom: 0;
    background: color-mix(in srgb, var(--solid-raised) 72%, transparent);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
  }
  .pill-handle span {
    width: 36px;
    height: 3px;
    border-radius: 999px;
    background: var(--muted);
    opacity: 0.6;
    transition: opacity 140ms ease, background-color 140ms ease;
  }
  .pill-handle:hover span,
  .pill-handle:focus-visible span { opacity: 1; background: var(--violet); }

  /* The ⋯ panel renders in the top layer; its content comes from the host. */
  :global(:root body .kitty-popover.pill-menu) {
    display: grid;
    gap: 8px;
    width: 270px;
    max-height: min(calc(var(--stage-height, 420px) - 72px), calc(100dvh - 16px));
    padding: 12px;
    border-radius: 14px;
    white-space: normal;
  }
  :global(.pill-menu .eyebrow) { margin: 2px 0 -4px; }
  :global(.pill-menu .pill-menu-tools) { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; }
  :global(.pill-menu .pill-menu-tools button),
  :global(.pill-menu .pill-menu-item) {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 0;
    padding: 5px 8px;
    border: 0;
    border-radius: 8px;
    background: none;
    color: var(--muted);
    font: 500 12px / 1.3 var(--font-ui);
    text-align: left;
    cursor: pointer;
  }
  :global(.pill-menu .pill-menu-tools button:hover:not(:disabled)),
  :global(.pill-menu .pill-menu-tools button[aria-pressed="true"]),
  :global(.pill-menu .pill-menu-item:hover:not(:disabled)),
  :global(.pill-menu .pill-menu-item[aria-pressed="true"]) { color: var(--ink); background: var(--wash); }
  :global(.pill-menu .pill-menu-item:disabled) { opacity: 0.45; cursor: not-allowed; }
  :global(.pill-menu kbd) { margin-left: auto; padding: 0; border: 0; background: none; box-shadow: none; color: var(--subtle); font: 10px var(--font-mono); text-decoration: none; }
  :global(.pill-menu .pill-menu-row) { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; }
  :global(.pill-menu .pill-range) { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 12px; }
  :global(.pill-menu .pill-range span) { display: flex; align-items: center; gap: 6px; }
  :global(.pill-menu .pill-range input[type="range"]) { width: 110px; padding: 0; accent-color: var(--accent); }
  :global(.pill-menu .pill-range input[type="number"]) { width: 64px; padding: 4px 6px; }
  :global(.pill-menu output) { min-width: 36px; text-align: center; color: var(--muted); font: 11px var(--font-mono); }
  @media (prefers-reduced-motion: reduce) {
    .pill-bar { animation: none; }
    .canvas-pill :global(.pill-btn), .pill-handle span { transition: none; }
    .canvas-pill :global(.pill-btn:hover:not(:disabled)) { transform: none; }
  }
</style>

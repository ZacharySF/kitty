<script lang="ts">
  import type { Snippet } from "svelte";
  import { fly } from "svelte/transition";

  /** Glass side drawer next to the calendar; non-modal so the block behind stays visible and live. */
  let { title, eyebrow, accent = "var(--accent)", onclose, children }: {
    title: string;
    eyebrow: string;
    accent?: string;
    onclose: () => void;
    children: Snippet;
  } = $props();
  const id = $props.id();
  const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** Focuses the first field on open and returns focus to where it came from on close. */
  function focusTrail(node: HTMLElement) {
    const previous = document.activeElement as HTMLElement | null;
    node.querySelector<HTMLInputElement>("input:not([type=checkbox])")?.focus();
    return { destroy: () => previous?.isConnected && previous.focus({ preventScroll: true }) };
  }
</script>

<div class="drawer" role="dialog" aria-modal="false" aria-labelledby={`${id}-title`} style:--cal={accent}
  use:focusTrail transition:fly={{ x: 40, duration: reduced ? 0 : 200 }}>
  <header>
    <div>
      <span class="eyebrow">{eyebrow}</span>
      <h2 id={`${id}-title`}>{title}</h2>
    </div>
    <button type="button" class="close" aria-label="Close event editor" onclick={onclose}>×</button>
  </header>
  {@render children()}
</div>

<style>
  .drawer {
    position: fixed;
    top: calc(var(--workbench-top, 0px) + 0.75rem);
    right: 0.75rem;
    bottom: calc(var(--dock-space, 0px) + 0.75rem);
    z-index: 90;
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: min(400px, calc(100vw - 1.5rem));
    box-sizing: border-box;
    padding: 16px 18px;
    overflow: auto;
    border: 1px solid var(--hairline);
    border-radius: 14px;
    background: color-mix(in srgb, var(--solid-raised) 86%, transparent);
    backdrop-filter: blur(18px);
    box-shadow: 0 18px 48px color-mix(in srgb, var(--shadow-tint) 55%, transparent);
    color: var(--ink);
  }
  .drawer::before {
    content: "";
    position: absolute;
    top: 0;
    left: 14px;
    right: 14px;
    height: 3px;
    border-radius: 0 0 3px 3px;
    background: var(--cal);
  }
  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 8px;
  }
  .eyebrow {
    color: var(--muted);
  }
  h2 {
    margin: 0;
    font-size: 1.05rem;
  }
  .close {
    padding: 0 0.45rem;
    border: 1px solid transparent;
    background: transparent;
    color: var(--muted);
    font-size: 1.3rem;
    line-height: 1;
  }
  .close:hover {
    color: var(--ink);
  }
</style>

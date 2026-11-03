<script lang="ts" generics="T extends { id: number; left: number }">
  import { onMount, type Snippet } from "svelte";

  /**
   * Q60: the app's toast stack, bottom-right above the Rika bar. Toasts overlap as a deck;
   * pointer or keyboard focus inside fans it out and pauses every countdown. Each toast's
   * `left` (ms) counts down in place and its life line shrinks with it; at zero it is
   * dismissed. The notification stack and Rika's reply stack share this look.
   * Content classes for `toast`: .top (.dot, .when, .x), strong, .body, .acts (.pill).
   */
  let {
    items,
    total,
    label,
    toast,
    ondismiss,
    tone,
    hold,
    offset = "0px",
    publish,
  }: {
    items: T[];
    /** The full countdown, for the life line. */
    total: number;
    label: string;
    toast: Snippet<[T]>;
    ondismiss: (item: T) => void;
    /** A CSS colour for the toast's edge, dot and life line. */
    tone?: (item: T) => string;
    /** A held toast does not count down (a reply still being written). */
    hold?: (item: T) => boolean;
    /** Extra room below the stack, e.g. another stack's height. */
    offset?: string;
    /** A root custom property that receives the stack's height plus its gap. */
    publish?: string;
  } = $props();
  let hovered = $state(false);
  let focused = $state(false);
  const paused = $derived(hovered || focused);
  const TICK = 100;
  let list = $state<HTMLElement>();

  onMount(() => {
    const timer = setInterval(() => {
      if (paused || document.hidden) return;
      for (const item of items) if (!hold?.(item)) item.left -= TICK;
      for (const item of items.filter((item) => item.left <= 0)) ondismiss(item);
    }, TICK);
    return () => clearInterval(timer);
  });
  $effect(() => {
    if (!publish) return;
    const root = document.documentElement;
    if (!list) {
      root.style.removeProperty(publish);
      return;
    }
    const node = list;
    const measure = new ResizeObserver(() => root.style.setProperty(publish, `${Math.ceil(node.getBoundingClientRect().height) + 8}px`));
    measure.observe(node);
    return () => {
      measure.disconnect();
      root.style.removeProperty(publish);
    };
  });
</script>

{#if items.length}
  <ol
    bind:this={list}
    class="toasts"
    class:spread={paused}
    aria-label={label}
    aria-live="polite"
    style:--offset={offset}
    onpointerenter={() => (hovered = true)}
    onpointerleave={() => (hovered = false)}
    onfocusin={() => (focused = true)}
    onfocusout={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) focused = false; }}
  >
    {#each items as item, index (item.id)}
      <li class="toast" style:--d={items.length - 1 - index} style:--tone={tone?.(item)}>
        {@render toast(item)}
        <span class="life" aria-hidden="true" data-countdown={item.left} style:--left={item.left / total}></span>
      </li>
    {/each}
  </ol>
{/if}

<style>
  .toasts {
    position: fixed;
    /* Rika's history drawer, while open, keeps the stack beside it rather than under it. */
    right: calc(16px + var(--rika-drawer, 0px));
    transition: right 200ms;
    bottom: calc(max(16px, calc(var(--rika-space, 0px) + 12px)) + var(--offset));
    z-index: 40;
    display: grid;
    width: min(320px, calc(100vw - 32px));
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .toast {
    --tone: var(--violet);
    position: relative;
    grid-area: 1 / 1;
    align-self: end;
    display: grid;
    gap: 3px;
    padding: 10px 12px 12px;
    overflow: hidden;
    border: 1px solid var(--hairline);
    border-left: 2px solid var(--tone);
    border-radius: 14px;
    background: color-mix(in srgb, var(--solid-raised) 88%, transparent);
    box-shadow: var(--overlay-shadow);
    backdrop-filter: var(--glass);
    -webkit-backdrop-filter: var(--glass);
    transform: translateY(calc(var(--d) * -12px)) scale(calc(1 - var(--d) * 0.05));
    opacity: calc(1 - var(--d) * 0.28);
    z-index: calc(10 - var(--d));
    transition: transform 260ms cubic-bezier(0.2, 0.8, 0.2, 1), opacity 260ms;
    animation: rise 320ms cubic-bezier(0.2, 0.8, 0.2, 1);
  }
  .spread .toast {
    transform: translateY(calc(var(--d) * -100% - var(--d) * 8px));
    opacity: 1;
  }
  @keyframes rise {
    from {
      transform: translateY(24px);
      opacity: 0;
    }
  }
  .toast :global(.top) {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    font: 9.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .toast :global(.when) {
    margin-left: auto;
    color: var(--subtle);
  }
  .toast :global(.dot) {
    width: 7px;
    height: 7px;
    flex-shrink: 0;
    border-radius: 999px;
    background: var(--tone);
    box-shadow: 0 0 6px color-mix(in srgb, var(--tone) 70%, transparent);
  }
  .toast :global(.x) {
    margin-left: 4px;
    padding: 0 2px;
    border: 0;
    background: transparent;
    color: var(--muted);
    font-size: 15px;
    line-height: 1;
    cursor: pointer;
  }
  .toast :global(.top span:not(.when) + .x) {
    margin-left: auto;
  }
  .toast :global(strong) {
    overflow: hidden;
    color: var(--ink);
    font: 600 13.5px var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .toast :global(.body) {
    color: var(--muted);
    font-size: 12px;
  }
  .toast :global(.acts) {
    display: flex;
    justify-content: end;
    gap: 5px;
    margin-top: 4px;
  }
  .toast :global(.acts .pill) {
    min-height: 24px;
    padding: 2px 10px;
    font-size: 9.5px;
  }
  .life {
    position: absolute;
    left: 0;
    bottom: 0;
    width: 100%;
    height: 2px;
    background: var(--tone);
    transform: scaleX(var(--left));
    transform-origin: left;
    transition: transform 100ms linear;
  }
  @media (prefers-reduced-motion: reduce) {
    .toast,
    .life {
      transition: none;
      animation: none;
    }
  }
</style>

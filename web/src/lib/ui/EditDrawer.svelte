<script lang="ts">
  import type { Snippet } from "svelte";
  import { fly } from "svelte/transition";
  import Icon from "../Icon.svelte";

  /**
   * Q8: opening an existing item shows every field in this side drawer (creating
   * uses QuickAdd). Generalised from the calendar's event drawer: one glass surface
   * on the right edge, non-modal so the page behind stays visible and live, a
   * hairline under the header and over the footer. It focuses the first field on
   * open, closes on Escape and returns focus to where it came from.
   */
  let {
    title,
    meta = "",
    accent = "",
    closeLabel = "Close",
    onclose,
    children,
    footer,
  }: {
    title: string;
    /** One short line under the title (a calendar and date, a subject). */
    meta?: string;
    /** A CSS colour for the item's tone bar (an event's calendar, a subject). */
    accent?: string;
    closeLabel?: string;
    onclose: () => void;
    children: Snippet;
    /** Actions, right-aligned (Cancel, Save); a destructive one may lead. */
    footer?: Snippet;
  } = $props();
  const id = $props.id();
  const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** Focuses the first field on open and returns focus to where it came from on close. */
  function focusTrail(node: HTMLElement) {
    const previous = document.activeElement as HTMLElement | null;
    node.querySelector<HTMLElement>("input:not([type=checkbox], [type=radio], [type=hidden]), textarea, [data-autofocus]")?.focus();
    return { destroy: () => previous?.isConnected && previous.focus({ preventScroll: true }) };
  }

  function keydown(event: KeyboardEvent) {
    if (event.key !== "Escape" || event.defaultPrevented) return;
    event.preventDefault();
    onclose();
  }
</script>

<div class="edit-drawer" role="dialog" aria-modal="false" tabindex="-1" aria-labelledby={`${id}-title`} style:--tone={accent || undefined}
  use:focusTrail onkeydown={keydown} transition:fly={{ x: 40, duration: reduced ? 0 : 200 }}>
  <header>
    <div class="heading">
      <h2 id={`${id}-title`}>{title}</h2>
      {#if meta}<span class="meta">{meta}</span>{/if}
    </div>
    <button type="button" class="icon-button" aria-label={closeLabel} title={closeLabel} onclick={onclose}><Icon name="close" size={14} /></button>
  </header>
  <div class="body">{@render children()}</div>
  {#if footer}<footer>{@render footer()}</footer>{/if}
</div>

<style>
  /* Outranks the shared overlay rules for dialogs and their headers. */
  :global(:root body) .edit-drawer {
    position: fixed;
    top: calc(var(--workbench-top, 0px) + 0.75rem);
    right: 0.75rem;
    bottom: 0.75rem;
    z-index: 90;
    display: flex;
    flex-direction: column;
    width: min(400px, calc(100vw - 1.5rem));
    overflow: hidden;
    border: 1px solid var(--hairline);
    border-radius: var(--r-panel);
    background: color-mix(in srgb, var(--solid-raised, #222) 92%, transparent);
    backdrop-filter: blur(24px) saturate(140%);
    -webkit-backdrop-filter: blur(24px) saturate(140%);
    box-shadow: var(--overlay-shadow);
    color: var(--ink);
  }
  :global(:root body) .edit-drawer::before {
    content: "";
    position: absolute;
    top: 0;
    left: 14px;
    right: 14px;
    height: 3px;
    border-radius: 0 0 3px 3px;
    background: var(--tone, transparent);
  }
  :global(:root body) .edit-drawer > header {
    display: flex;
    margin: 0;
    align-items: flex-start;
    gap: 9px;
    padding: 14px 12px 12px 16px;
    border-bottom: 1px solid var(--hairline);
  }
  .heading {
    flex: 1;
    min-width: 0;
    display: grid;
    gap: 2px;
  }
  :global(:root body) .edit-drawer > header h2 {
    margin: 0;
    font-size: var(--fs-heading);
    line-height: 1.3;
    overflow-wrap: anywhere;
  }
  .meta {
    color: var(--muted);
    font-size: 12px;
  }
  .body {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px 16px;
    overflow: auto;
  }
  footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--hairline);
  }
</style>

<script lang="ts">
  import { untrack } from "svelte";
  import type { ThemeTokens } from "./contract";

  /**
   * Ctrl + Y: the themes on a sliding strip, the centred one shown live on the whole window.
   * Opening steps to the next theme; Ctrl + Y or → steps on, Ctrl + Shift + Y or ← back.
   * Enter or a click keeps the centred theme, Escape puts the first one back.
   */
  let { themes, current, onpreview, onkeep, oncancel }: {
    themes: readonly ThemeTokens[];
    current: string;
    onpreview: (id: string) => void;
    onkeep: (id: string) => void;
    oncancel: () => void;
  } = $props();

  /** Opening steps once past the theme in use. */
  function opening() {
    const start = Math.max(0, themes.findIndex((theme) => theme.id === current));
    return themes.length > 1 ? (start + 1) % themes.length : start;
  }
  let index = $state(opening());
  const centred = $derived(themes[index]);
  // Only the centred theme drives the preview; what the preview itself reads and writes is not followed.
  $effect(() => {
    const id = centred?.id;
    if (id) untrack(() => onpreview(id));
  });

  let dialog = $state<HTMLDialogElement>();
  $effect(() => {
    const node = dialog;
    if (!node) return;
    node.showModal();
    node.focus();
    return () => node.close();
  });

  function step(by: number) {
    if (themes.length) index = (index + by + themes.length) % themes.length;
  }
  /** Where a theme sits from the centre, the short way round. */
  function offset(at: number) {
    const count = themes.length;
    let distance = at - index;
    if (distance > count / 2) distance -= count;
    if (distance < -count / 2) distance += count;
    return distance;
  }
  function key(event: KeyboardEvent) {
    const chord = (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "y";
    if (chord || event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      step(event.key === "ArrowLeft" || (chord && event.shiftKey) ? -1 : 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (centred) onkeep(centred.id);
    }
  }
</script>

<dialog bind:this={dialog} class="theme-carousel" aria-label="Themes" tabindex="-1"
  oncancel={(event) => { event.preventDefault(); oncancel(); }}
  onkeydown={key}>
  <div class="strip">
    {#each themes as theme, at (theme.id)}
      {@const away = offset(at)}
      <button type="button" class="card" data-theme-card aria-current={at === index ? "true" : undefined}
        aria-label={theme.label} tabindex={at === index ? 0 : -1} class:far={Math.abs(away) > 2}
        style:--away={away} style:--distance={Math.abs(away)}
        onclick={() => { index = at; onkeep(theme.id); }}>
        <span class="window" style:background={theme.roles.void} style:border-color={theme.roles.line}>
          <span class="bar" style:background={theme.roles.structure}></span>
          <span class="dots">
            {#each theme.graph.slice(0, 4) as colour, dot (dot)}<i style:background={colour}></i>{/each}
          </span>
        </span>
        <span class="name">{theme.label}</span>
      </button>
    {/each}
  </div>
  <p class="hint"><strong>{centred?.label}</strong> · Ctrl + Y next · Enter keep · Esc undo</p>
</dialog>

<style>
  :global(:root body) dialog.theme-carousel {
    position: fixed;
    inset: 0;
    width: 100vw;
    height: 100dvh;
    max-width: none;
    max-height: none;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    color: var(--ink);
    outline: none;
    overflow: hidden;
  }
  .theme-carousel[open] {
    display: grid;
    align-content: center;
    gap: 34px;
  }
  /* A light veil: the live theme behind is the point. */
  .theme-carousel::backdrop {
    background: color-mix(in srgb, var(--canvas, #000) 28%, transparent);
  }
  .strip {
    position: relative;
    height: 230px;
  }
  /* Each card sits by its distance from the centre: the centre one large, the rest smaller and dimmer. */
  .card {
    --gap: 236px;
    position: absolute;
    top: 50%;
    left: 50%;
    display: grid;
    justify-items: center;
    gap: 10px;
    width: 220px;
    padding: 0;
    border: 0;
    background: none;
    color: var(--ink);
    font: 600 14px var(--font-ui);
    cursor: pointer;
    opacity: calc(1 - var(--distance) * 0.28);
    transform: translate(calc(-50% + var(--away) * var(--gap)), -50%) scale(calc(1.18 - var(--distance) * 0.16));
    transition: transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1), opacity 220ms ease;
    z-index: calc(10 - var(--distance));
  }
  .card.far {
    opacity: 0;
    pointer-events: none;
  }
  .card:focus-visible {
    outline: none;
  }
  .card[aria-current="true"] .window {
    box-shadow: 0 0 0 2px var(--ink), var(--overlay-shadow);
  }
  .window {
    display: grid;
    grid-template-rows: 22px 1fr;
    width: 220px;
    height: 150px;
    overflow: hidden;
    border: 1px solid;
    border-radius: 14px;
    box-shadow: var(--overlay-shadow);
  }
  .bar {
    opacity: 0.85;
  }
  .dots {
    display: flex;
    align-items: end;
    gap: 8px;
    padding: 0 16px 16px;
  }
  .dots i {
    width: 22px;
    height: 22px;
    border-radius: 999px;
  }
  .name {
    padding: 3px 10px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 80%, transparent);
  }
  .hint {
    justify-self: center;
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    text-align: center;
  }
  .hint strong {
    color: var(--ink);
  }
  @media (prefers-reduced-motion: reduce) {
    .card {
      transition: none;
    }
  }
</style>

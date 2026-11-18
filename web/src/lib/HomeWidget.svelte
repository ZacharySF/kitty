<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon from "./Icon.svelte";

  /**
   * Q19: one home widget, framed "hairline minimal": no fill or box, only a
   * rule above, a small Inter title and an optional short tag on the right.
   * `fill` makes it a column that takes its whole slot (the home's Rika panel); `tail`
   * puts controls at the right of the title row.
   *
   * On a colour-coded pane (Home.svelte's `.slot.toned`) it is a tile: a label at the top
   * (its icon on a small badge), what the widget shows as its largest thing (`.figure`), and
   * `art` (a gauge, a dial, a chart) on the right from top to bottom, under the content once
   * the tile is too narrow for both (`compact` keeps it beside the content at any width, for
   * a widget that fits a square tile). `hero` keeps the content at the foot of a tile taller
   * than it. `onopen` makes the title the way into the widget's page, and on a tile the
   * whole surface presses it. `tint` is the widget's own colour: where the theme asks for it
   * (`--tile-wash`), the tile carries a wash of it from its top-left corner and its label
   * and badge take it.
   */
  let {
    id,
    title,
    eyebrow = "",
    icon = "",
    fill = false,
    hero = false,
    compact = false,
    opens = "",
    tint = "",
    onopen,
    tail,
    art,
    children,
  }: {
    id: string;
    title: string;
    eyebrow?: string;
    /** Shown only while the Icons module is on. */
    icon?: string;
    fill?: boolean;
    hero?: boolean;
    compact?: boolean;
    /** What `onopen` opens, for its tooltip: "calendar" reads "Open calendar". */
    opens?: string;
    /** A CSS colour, as `var(--tile-1)`. */
    tint?: string;
    onopen?: () => unknown;
    tail?: Snippet;
    art?: Snippet;
    children?: Snippet;
  } = $props();
</script>

{#snippet head()}
  <header>
    {#if icon}<span class="module-icon"><Icon name={icon} size={19} /></span>{/if}
    <h3 {id}>
      {#if onopen}<button type="button" class="open" title={`Open ${opens}`} onclick={onopen}>{title}<svg viewBox="0 0 8 12" width="7" height="11" aria-hidden="true"><path d="M1.5 1.5 6 6l-4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
      {:else}{title}{/if}
    </h3>
    {#if eyebrow}<span class="eyebrow">{eyebrow}</span>{/if}
    {#if tail}<span class="tail">{@render tail()}</span>{/if}
  </header>
{/snippet}

<section class="home-widget" class:fill class:hero style:--lead={tint || null} aria-labelledby={id}>
  {#if fill}
    {@render head()}
    {@render children?.()}
  {:else}
    <div class="frame" class:with-art={!!art} class:compact>
      <div class="main">
        {@render head()}
        <div class="body">{@render children?.()}</div>
      </div>
      {#if art}<div class="art">{@render art()}</div>{/if}
    </div>
  {/if}
</section>

<style>
  .home-widget {
    min-width: 0;
    border-top: 1px solid var(--hairline);
    padding: 4px 0 12px;
    color: var(--ink);
    container-type: inline-size;
  }
  .home-widget.fill {
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
    box-sizing: border-box;
    padding-bottom: 0;
  }
  .frame {
    display: flex;
    flex-direction: column;
  }
  .main {
    flex: 1 1 auto;
    min-width: 0;
  }
  .art {
    flex: none;
    display: grid;
    place-items: center;
    margin-top: 12px;
  }
  /* A compact widget's art stays beside its content, at the foot while the widget is narrow. */
  .frame.with-art.compact {
    flex-direction: row;
    gap: 14px;
  }
  .frame.compact .art {
    margin-top: 0;
    align-items: end;
  }
  /* Wide enough for both: the art stands beside the content, as tall as the widget. */
  @container (min-width: 360px) {
    .frame.with-art,
    .frame.with-art.compact {
      flex-direction: row;
      gap: 24px;
    }
    .art,
    .frame.compact .art {
      margin-top: 0;
      align-items: center;
    }
  }
  header {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 14px 0 12px;
  }
  /* Design labs (Type): widget titles in the text face, large enough to read across the room. */
  h3 {
    margin: 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font: 600 22px/1.2 var(--font-ui);
    letter-spacing: -0.01em;
  }
  /* The title as the way into the widget's page: the title's own type, then a chevron.
     Such a title is a short name, so nothing clips it (or its focus ring). */
  h3:has(.open) {
    overflow: visible;
  }
  .open {
    display: inline-flex;
    align-items: center;
    gap: 0.4em;
    max-width: 100%;
    padding: 0;
    border: 0;
    border-radius: var(--r-item);
    background: none;
    color: inherit;
    font: inherit;
    letter-spacing: inherit;
    cursor: pointer;
  }
  .open svg {
    flex: none;
    opacity: 0.7;
  }
  .open:hover svg {
    opacity: 1;
  }
  .tail {
    margin-left: auto;
    flex: none;
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .eyebrow + .tail {
    margin-left: 0;
  }
  /* The short tag on the right (a count, a date) is quiet secondary text. */
  .eyebrow {
    margin-left: auto;
    flex: none;
    color: var(--muted);
    font-size: 15px;
    font-weight: 400;
    line-height: 1;
  }
  /* The number a widget leads with, as large as the widget has room for. (`:where` keeps it
     easy for a widget's own rule to adjust.) */
  :global(:where(.home-widget) .figure) {
    margin: 0;
    color: var(--ink);
    font: 300 clamp(34px, 16cqi, 76px) / 1 var(--font-ui);
    letter-spacing: -0.03em;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  /* A tile. Its wash lies over the pane from the top-left corner; the label keeps one line at the top. */
  :global(.slot.toned) .home-widget:not(.fill) {
    position: relative;
    isolation: isolate;
    display: flex;
    flex-direction: column;
  }
  :global(.slot.toned) .home-widget:not(.fill)::before {
    content: "";
    position: absolute;
    z-index: -1;
    inset: calc(var(--tile-pad) * -1);
    border-radius: var(--tile-radius);
    background: linear-gradient(155deg, color-mix(in srgb, var(--lead, transparent) calc(var(--tile-wash, 0) * 26%), transparent), transparent 64%);
    pointer-events: none;
  }
  :global(.slot.toned) .frame {
    flex: 1 1 auto;
    min-height: 0;
  }
  :global(.slot.toned) .main {
    display: flex;
    flex-direction: column;
  }
  :global(.slot.toned) .body {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
  }
  :global(.slot.toned) .hero .body {
    flex: none;
    margin-top: auto;
  }
  :global(.slot.toned) header {
    align-items: center;
    min-height: 28px;
    padding: 0 0 12px;
    color: color-mix(in srgb, var(--lead, var(--muted)) calc(var(--tile-wash, 0) * 78%), var(--muted));
  }
  :global(.slot.toned) h3 {
    overflow: visible;
    font: 600 var(--fs-subheading) / 1.2 var(--font-ui);
    letter-spacing: 0;
  }
  /* The tile's emblem: its icon on a small badge of its own colour. */
  :global(:root[data-icons="on"] .slot.toned) .module-icon {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    margin-right: 0;
    border-radius: var(--r-field);
    background: color-mix(in srgb, currentColor 16%, transparent);
    color: inherit;
  }
  :global(.slot.toned) .tail {
    align-items: center;
  }
  :global(.slot.toned) .eyebrow {
    font-size: var(--fs-label);
  }
  :global(.slot.toned) .open {
    min-height: 24px;
  }
  /* The whole tile presses its title; the controls inside it stay above that. */
  :global(.slot.toned) .open::after {
    content: "";
    position: absolute;
    inset: calc(var(--tile-pad) * -1);
    border-radius: var(--tile-radius);
  }
  :global(.slot.toned) .body :global(:is(button, a, input, textarea, select)),
  :global(.slot.toned) .tail :global(:is(button, a)) {
    position: relative;
    z-index: 1;
  }
</style>

<script lang="ts">
  import type { Snippet } from "svelte";

  /**
   * Q19: one home widget, framed "hairline minimal": no fill or box, only a
   * rule above, a small Inter title and an optional short tag on the right.
   * `fill` makes it a column that takes its whole slot (the home's Rika panel).
   */
  let {
    id,
    title,
    eyebrow = "",
    fill = false,
    children,
  }: { id: string; title: string; eyebrow?: string; fill?: boolean; children: Snippet } = $props();
</script>

<section class="home-widget" class:fill aria-labelledby={id}>
  <header>
    <h3 {id}>{title}</h3>
    {#if eyebrow}<span class="eyebrow">{eyebrow}</span>{/if}
  </header>
  {@render children()}
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
  /* The short tag on the right (a count, a date) is quiet secondary text. */
  .eyebrow {
    margin-left: auto;
    flex: none;
    color: var(--muted);
    font-size: 15px;
    font-weight: 400;
    line-height: 1;
  }
</style>

<script lang="ts">
  import type { Snippet } from "svelte";

  /**
   * Q19: one home widget, framed "hairline minimal": no fill or box, only a
   * rule above, a small Inter title and the eyebrow on the right.
   */
  let {
    id,
    title,
    eyebrow = "",
    children,
  }: { id: string; title: string; eyebrow?: string; children: Snippet } = $props();
</script>

<section class="home-widget" aria-labelledby={id}>
  <header>
    <h3 {id}>{title}</h3>
    {#if eyebrow}<span class="eyebrow">{eyebrow}</span>{/if}
  </header>
  {@render children()}
</section>

<style>
  .home-widget {
    min-width: 0;
    border-top: 1px solid color-mix(in srgb, var(--ink) 34%, transparent);
    padding: 2px 0 6px;
    color: var(--ink);
    container-type: inline-size;
  }
  header {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 8px 0 10px;
  }
  h3 {
    margin: 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font: 600 13.5px var(--font-ui);
    letter-spacing: 0.01em;
  }
  .eyebrow {
    margin-left: auto;
    flex: none;
    font-size: 12px;
    line-height: 1;
  }
</style>

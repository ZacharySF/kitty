<script lang="ts">
  import type { Snippet } from "svelte";
  import SleepingCat from "./themes/art/SleepingCat.svelte";
  import { themeMotif } from "./themes/motif.svelte";

  /**
   * The app's empty state: the theme's illustration (contract.ts `EmptyArt`; by default a
   * line-art kitty asleep on a cushion), then a title, one line of context and optional
   * actions. Decoration only; the text carries meaning.
   */
  let {
    title,
    eyebrow = "",
    level = 2,
    children,
    actions,
  }: { title: string; eyebrow?: string; level?: 2 | 3; children?: Snippet; actions?: Snippet } = $props();
  const Art = $derived(themeMotif.module?.EmptyArt);
</script>

<div class="kitty-empty">
  <div class="art" data-empty-art={Art ? themeMotif.id : "default"}>{#if Art}<Art />{:else}<SleepingCat />{/if}</div>
  {#if eyebrow}<small class="eyebrow">{eyebrow}</small>{/if}
  {#if level === 2}<h2>{title}</h2>{:else}<h3>{title}</h3>{/if}
  {#if children}<div class="body">{@render children()}</div>{/if}
  {#if actions}<div class="actions">{@render actions()}</div>{/if}
</div>

<style>
  .kitty-empty {
    display: grid;
    justify-items: center;
    gap: 6px;
    padding: 28px 20px 24px;
    text-align: center;
  }
  .art {
    display: grid;
    place-items: center;
    margin-bottom: 6px;
  }
  h2,
  h3 {
    margin: 0;
  }
  .body {
    max-width: 58ch;
    color: var(--muted);
    line-height: 1.55;
  }
  .body :global(p) {
    margin: 0;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
    margin-top: 8px;
  }
</style>

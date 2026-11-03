<script lang="ts">
  import { mathInline, mathMarkdown } from "./tex";

  /** Math prose: `$…$`, `\( … \)`, `\[ … \]` rendered with KaTeX; `inline` keeps it in one line box. */
  let { text, inline = false }: { text: string; inline?: boolean } = $props();
  const html = $derived(inline ? mathInline(text) : mathMarkdown(text));
</script>

{#if inline}<span class="math-text">{@html html}</span>{:else}<div class="math-text">{@html html}</div>{/if}

<style>
  .math-text :global(p) {
    margin: 0;
  }
  .math-text :global(p + p) {
    margin-top: 0.6em;
  }
  .math-text :global(.katex) {
    font-size: 1.08em;
  }
  .math-text :global(.katex-display) {
    margin: 0.5em 0;
    overflow-x: auto;
    overflow-y: hidden;
  }
</style>

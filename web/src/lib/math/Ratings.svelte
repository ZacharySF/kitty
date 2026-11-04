<script lang="ts">
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { Rating } from "./api";
  import { RATINGS } from "./review";

  /**
   * Again · Hard · Good · Easy for a review you graded yourself. With `keys`, 1–4 rate
   * too (one centred card at a time owns them; the drawer's copy leaves them alone).
   */
  let { busy = false, keys = false, label = "Rate it", onrate }: { busy?: boolean; keys?: boolean; label?: string; onrate: (rating: Rating) => void } = $props();
  const pane = useWorkspaceContext();

  function onkey(event: KeyboardEvent) {
    if (!keys || busy || !pane.active || event.ctrlKey || event.metaKey || event.altKey || event.repeat || event.defaultPrevented) return;
    if ((event.target as HTMLElement | null)?.closest("input,textarea,select,math-field,[contenteditable=true],[role=dialog]")) return;
    const rating = RATINGS.find((item) => String(item.n) === event.key);
    if (!rating) return;
    event.preventDefault();
    onrate(rating.n);
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="ratings" role="group" aria-label={label}>
  {#each RATINGS as rating (rating.n)}
    <button type="button" class={`r${rating.n}`} title={`${rating.label}: ${rating.hint}`} disabled={busy} onclick={() => onrate(rating.n)}>
      {#if keys}<kbd>{rating.n}</kbd>{/if}<span>{rating.label}</span>
    </button>
  {/each}
</div>

<style>
  .ratings {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 6px;
    width: 100%;
  }
  button {
    --g: var(--success);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-width: 0;
    height: var(--control-h-lg, 32px);
    padding: 0 8px;
    border: 1px solid var(--border);
    border-bottom: 3px solid color-mix(in srgb, var(--g) 80%, var(--border));
    border-radius: 8px;
    background: color-mix(in srgb, var(--solid-raised, var(--surface)) 70%, transparent);
    color: var(--ink);
    font: 500 12.5px var(--font-ui);
    cursor: pointer;
  }
  .r1 {
    --g: var(--error);
  }
  .r2 {
    --g: var(--warning);
  }
  .r4 {
    --g: var(--c4, var(--accent));
  }
  button:hover:not(:disabled) {
    border-color: var(--g);
  }
  button:disabled {
    cursor: default;
    opacity: 0.5;
  }
  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  kbd {
    color: var(--subtle);
    font: 500 10.5px var(--font-mono);
  }
</style>

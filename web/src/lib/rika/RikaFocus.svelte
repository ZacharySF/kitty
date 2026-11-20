<script lang="ts">
  import { rikaFocus } from "./focus.svelte";
  import { cleanFocus, focusSuggestions } from "./rika";
  import { rika } from "./session.svelte";

  /**
   * Above Rika's field: what is open, which her next message is told about (focus.svelte.ts), with an
   * × to leave it out of that one message, and a few things worth asking about it. Nothing shows
   * while nothing is open.
   */
  const item = $derived(cleanFocus(rikaFocus.item));
  const suggestions = $derived(rikaFocus.skip ? [] : focusSuggestions(item?.kind));
</script>

{#if item}<div class="rika-focus">
    <span class="looking" class:skipped={rikaFocus.skip} title={item.detail ? `${item.title} · ${item.detail}` : item.title}>
      <span class="label">{rikaFocus.skip ? "Not sharing" : "Looking at"}</span> · <span class="title">{item.title}</span>
    </span>
    {#if rikaFocus.skip}<button type="button" class="text-button" onclick={() => (rikaFocus.skip = false)}
      title="Tell Rika what is open again">Share</button>
    {:else}<button type="button" class="icon-button" aria-label={`Don't share ${item.title} with the next message`}
      title="Don't share this with the next message" onclick={() => (rikaFocus.skip = true)}>×</button>{/if}
  </div>
  {#if suggestions.length}<div class="suggestions" role="group" aria-label="Ask about this">
      {#each suggestions as suggestion (suggestion)}<button type="button" class="pill" disabled={rika.streaming}
        onclick={() => void rika.send(suggestion)}>{suggestion}</button>{/each}
    </div>{/if}
{/if}

<style>
  .rika-focus {
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
    margin-bottom: 6px;
    color: var(--muted);
    font-size: 12px;
  }
  .looking {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .label {
    color: var(--subtle);
  }
  .title {
    color: var(--ink);
  }
  .skipped .title {
    color: var(--muted);
    text-decoration: line-through;
  }
  .rika-focus .icon-button,
  .rika-focus .text-button {
    flex: none;
    height: 22px;
    min-width: 22px;
    padding: 0 4px;
    font-size: 12px;
  }
  .suggestions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 8px;
  }
  :global(:root body) .suggestions .pill {
    height: 26px;
    padding: 0 10px;
    color: var(--muted);
    font-size: 12px;
  }
  :global(:root body) .suggestions .pill:hover:not(:disabled) {
    color: var(--ink);
  }
</style>
